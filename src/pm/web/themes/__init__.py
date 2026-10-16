"""Theme registry: which template dirs (and static dir) a theme name maps to.

A theme is ``themes/<name>/{theme.toml, templates/, static/}``. ``classic`` (today's UI) is always registered and is
the fallback for every template another theme does not override. Plugins add themes with ``register``; nothing here
raises on a bad name, it logs and falls back to classic.

The UI theme is chosen per request (``UIThemeMiddleware``: ``?ui=`` > cookie ``pm_ui`` > pm.toml ``theme``) and kept
in a context variable for the request (``current()``); ``render.get_template`` reads it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from jinja2 import BaseLoader, ChoiceLoader, FileSystemLoader
from starlette.requests import HTTPConnection

log = logging.getLogger(__name__)

HERE = Path(__file__).parent


@dataclass(frozen=True)
class Theme:
    name: str
    templates: Path
    static: Path | None = None  # served at /static/t/<name>; None = the theme uses the shared /static only
    label: str = ""


CLASSIC = Theme("classic", HERE / "classic" / "templates", None, "Classic")  # keeps using /static
BUILTIN: dict[str, Theme] = {CLASSIC.name: CLASSIC}
_registry: dict[str, Theme] = dict(BUILTIN)
_warned: set[str] = set()
_current: ContextVar[str | None] = ContextVar("pm_ui_theme", default=None)

PARAM = "ui"  # ?ui=<name>
COOKIE = "pm_ui"
_COOKIE_OK = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")  # only such names go into a Set-Cookie header


def register(theme: Theme) -> None:
    """Add a theme (plugins). A name already registered is logged and skipped, never replaced."""
    if theme.name in _registry:
        log.warning("theme %r already registered; skipped", theme.name)
        return
    _registry[theme.name] = theme


def get(name: str) -> Theme:
    """The theme called ``name``; an unknown name logs one warning and gives classic."""
    t = _registry.get(name)
    if t is None:
        if name not in _warned:
            _warned.add(name)
            log.warning("unknown theme %r; using classic", name)
        return CLASSIC
    return t


def all_themes() -> list[Theme]:
    return list(_registry.values())


def loader_for(name: str, extra_dirs: Sequence[Path] = ()) -> BaseLoader:
    """Theme templates first, then ``extra_dirs`` (plugin templates), then classic as the fallback."""
    theme = get(name)
    dirs = [theme.templates, *extra_dirs]
    if theme.templates != CLASSIC.templates:
        dirs.append(CLASSIC.templates)
    return ChoiceLoader([FileSystemLoader(d) for d in dirs])


def static_mounts() -> list[tuple[str, Path]]:
    """(URL prefix, dir) for every theme with its own static files: ``/static/t/<name>``."""
    return [(f"/static/t/{t.name}", t.static) for t in _registry.values() if t.static is not None]


def names() -> list[str]:
    """The registered theme names, classic first."""
    return list(_registry)


def current() -> str | None:
    """The UI theme chosen for this request; None outside a request (= the process default)."""
    return _current.get()


def choose(param: str | None, cookie: str | None, default: str) -> tuple[str, bool]:
    """Return (name, persist). A registered ``param`` wins and is persisted; else a registered ``cookie``; else
    ``default`` (classic when ``default`` is not registered). Raw client values are only compared with ``names()``,
    never passed to ``get()`` (its warn-once set would grow without bound)."""
    known = names()
    if param is not None and param in known:
        return param, True
    if cookie is not None and cookie in known:
        return cookie, False
    return (default if default in known else CLASSIC.name), False


class UIThemeMiddleware:
    """Pure ASGI middleware: sets ``current()`` for each request from ``?ui=``, cookie ``pm_ui`` and ``default``.

    A valid ``?ui=`` that differs from the cookie is remembered: ``Set-Cookie: pm_ui=<name>`` (one year, Secure,
    SameSite=Lax, not HttpOnly; the same attributes as the list sort cookies). A GET that sets a look-only cookie is
    acceptable: it changes how pages look, nothing else, so any link may switch the look.
    """

    def __init__(self, app, default: str = CLASSIC.name):
        self.app = app
        self.default = default

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        conn = HTTPConnection(scope)
        cookie = conn.cookies.get(COOKIE)
        name, persist = choose(conn.query_params.get(PARAM), cookie, self.default)
        if persist and name != cookie and scope["type"] == "http" and _COOKIE_OK.fullmatch(name):
            send = _with_cookie(send, name)
        token = _current.set(name)
        try:
            await self.app(scope, receive, send)
        finally:
            _current.reset(token)


def _with_cookie(send, name: str):
    header = (b"set-cookie", f"{COOKIE}={name}; Path=/; Max-Age=31536000; Secure; SameSite=Lax".encode())

    async def wrapped(message):
        if message["type"] == "http.response.start":
            message = {**message, "headers": [*message.get("headers", ()), header]}
        await send(message)
    return wrapped
