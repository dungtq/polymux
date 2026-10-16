"""Jinja environment and HTML rendering helpers shared by the route modules."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from fastapi import Request, Response
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, pass_context, select_autoescape
from markupsafe import Markup

from pm.agents import head as agenthead
from pm.agents import sorting
from pm.agents.inbox import last_src
from pm.agents.message import strip_builder_hint
from pm.chat.live import last_user_task, last_user_task_ts
from pm.chat.stats import human as human_tokens
from pm.chat.text import render_message
from pm.core import prefs, timefmt
from pm.core.config import frame_origins
from pm.infra import snapshot as services
from pm.security.guard import get_csrf
from pm.web import assets, themes
from pm.web.deps import project_sidebar
from pm.web.deps import settings as _settings

HERE = Path(__file__).parent.parent
STATIC_DIR = HERE / "static"
TEMPLATE_DIR = themes.CLASSIC.templates  # the default theme's templates (use_theme switches the loader)

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; manifest-src 'self'; worker-src 'self'; object-src 'none'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


def ago(ts: float | None) -> str:
    if not ts:
        return "unknown"
    d = max(0, int(time.time() - ts))
    if d < 60:
        return f"{d}s ago"
    if d < 3600:
        return f"{d // 60}m ago"
    if d < 86400:
        return f"{d // 3600}h ago"
    return f"{d // 86400}d ago"


def ts(ts: float | None) -> str:
    return timefmt.fmt_full(ts)


def ago_html(ts: float | None) -> Markup:
    """Relative time carrying the absolute UTC+N time in a title (hover / long-press)."""
    return Markup('<span title="{}">{}</span>').format(timefmt.fmt_full(ts), ago(ts))


env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html"], default_for_string=True, default=True),
)
env.globals["tz_label"] = timefmt.label
env.globals["now_epoch"] = time.time


_default_theme = themes.CLASSIC.name  # the theme ``env`` renders (use_theme)
_extra_dirs: tuple[Path, ...] = ()  # plugin template dirs, for the per-theme overlays too
_overlays: dict[str, Environment] = {}  # registered theme name -> overlay of ``env`` (bounded by the registry)


def use_theme(name: str, extra_dirs: Sequence[Path] = ()) -> None:
    """Point the default ``env`` at a theme (classic as the fallback, plugin template dirs between); drops the
    per-theme overlays, which are rebuilt with ``extra_dirs`` on demand."""
    global _default_theme, _extra_dirs
    env.loader = themes.loader_for(name, extra_dirs)
    if env.cache is not None:
        env.cache.clear()
    _default_theme = name if name in themes.names() else themes.CLASSIC.name
    _extra_dirs = tuple(extra_dirs)
    _overlays.clear()


def get_template(name: str):
    """Template ``name`` in this request's UI theme (``themes.current()``): ``env`` for the default theme or outside a
    request, else an overlay of ``env`` with that theme's loader (same globals, filters and tests)."""
    theme = themes.current()
    if theme is None or theme == _default_theme or theme not in themes.names():
        return env.get_template(name)
    e = _overlays.get(theme)
    if e is None:
        e = _overlays[theme] = env.overlay(loader=themes.loader_for(theme, _extra_dirs))
    return e.get_template(name)


class NavItem(NamedTuple):
    """One section link. top: icon link in the top bar; tab: phone tab bar ("" always shown, "t-phone" / "t-desk" one
    width only, None not there); more: in the More sheet (the More tab is "on" for these); menu: Menu "Manage" group
    (_acct.html); side: section sidebar (_side.html). icon: an _svg name in _acct.html; badge: its count pill."""
    key: str
    href: str
    label: str
    top: bool = False
    tab: str | None = None
    more: bool = False
    menu: bool = False
    side: bool = False
    icon: str = ""
    badge: str = ""


# The one nav list (presentation data), in display order: base.html (top bar, tab bar, More), _acct.html, _side.html.
NAV = (
    NavItem("inbox", "/", "Inbox", tab="", side=True, badge="inbox"),
    NavItem("projects", "/p", "Projects", tab="", side=True),
    NavItem("tmux", "/tmux", "tmux", tab="", side=True),
    NavItem("agents", "/agents", "Agents", more=True, menu=True, icon="agents"),
    NavItem("servers", "/servers", "Servers", tab="t-phone", menu=True, icon="servers", badge="system"),
    NavItem("backlog", "/backlog", "Backlog", top=True, tab="t-desk", more=True, side=True, icon="backlog"),
    NavItem("ntfy", "/ntfy", "ntfy", top=True, tab="t-desk", more=True, side=True, icon="ntfy"),
    NavItem("audit", "/audit", "Audit", top=True, tab="t-desk", more=True, side=True, icon="audit"),
    NavItem("settings", "/settings", "Settings", more=True, icon="settings"),
)
env.globals["NAV"] = NAV
env.filters["ago"] = ago_html
env.filters["ts"] = ts
env.filters["pill_model"] = agenthead.pill_model
env.filters["placeholder"] = agenthead.placeholder  # composer textarea: (full, short) "Message to <model> · <effort> in <win>"
env.filters["new_session"] = agenthead.new_session_label  # Last message cell of a claude chat with no message yet
env.filters["model_short"] = agenthead.model_short
env.filters["win_prog"] = agenthead.win_prog
env.filters["last_user"] = last_user_task
env.globals["last_src"] = last_src  # classic msg_pick macro: 'user' | 'ntfy' | '' (agents.inbox.last_src)


def agent_q(a, model: str | None = None) -> str:
    """Filter text of a row's data-q (live Agent or ended Record); model None reads it from the transcript."""
    return sorting.agent_q(a, agenthead.pill_model(a.transcript) if model is None else model)


env.filters["agent_q"] = agent_q  # {{ a | agent_q }} / {{ r | agent_q(models.get(r.id)) }}: same text as the server ?q= filter
env.filters["project_q"] = sorting.project_q  # {{ p | project_q }}: data-q of a Projects row, same text as the server ?q=
env.filters["session_q"] = sorting.session_q  # {{ s | session_q(agents) }}: data-q of a tmux session row
env.filters["last_user_ts"] = last_user_task_ts
env.filters["dt"] = timefmt.fmt_dt
env.filters["date"] = timefmt.fmt_date
env.filters["time"] = timefmt.fmt_time


@pass_context
def md(ctx, text: str | None) -> Markup:
    """Chat Markdown -> safe HTML (``render_markdown`` via ``render_message``: paste wrappers dropped,
    uploaded images as thumbnails from the context's ``uploads`` dir). Line breaks are kept."""
    return Markup(render_message(text or "", ctx.get("uploads")))


env.filters["md"] = md
env.filters["dur"] = timefmt.elapsed
env.filters["duration"] = timefmt.duration  # 45s / 3m / 2h 5m / 1d 4h
env.filters["tmux_win"] = agenthead.tmux_win
env.filters["tokens"] = human_tokens  # 950 / 1.2k / 34k / 1.5M
env.filters["pm_hint_strip"] = strip_builder_hint  # drop the "[pm builder: ...]" line a Builder message carries


def urlid(tmux_id: str) -> str:
    """tmux id -> URL form: $12 -> s12, @5 -> w5, %7 -> p7."""
    return {"$": "s", "@": "w", "%": "p"}.get(tmux_id[:1], "x") + tmux_id[1:]


env.filters["urlid"] = urlid


def term_ok(name: str) -> bool:
    """No terminal for pm's own sessions (pm*)."""
    return not str(name).startswith("pm")


env.tests["term_ok"] = term_ok


def page_ctx(request: Request) -> dict:
    st = request.app.state
    store = getattr(st, "sessions", None)
    session = store.from_request(request) if store is not None else None
    csrf = get_csrf(request).token(session.id) if session is not None else ""
    settings = st.settings
    require_unlock = bool(getattr(settings, "require_unlock", False))
    locked = require_unlock and (session is None or session.unlocked_until <= time.time())
    unlocker = getattr(st, "unlocker", None)
    from urllib.parse import urlsplit

    u = urlsplit(settings.ntfy_url)
    ntfy_ui_url = settings.ntfy_ui_url or (f"{u.scheme}://{u.netloc}" if u.netloc else "")
    return {
        "csrf": csrf,
        "clock_text": timefmt.clock_text(),
        "clock_title": timefmt.clock_title(),
        "clock_label": timefmt.label(),
        "clock_offset": timefmt.offset_attr(),
        "ntfy_ui_url": str(ntfy_ui_url),
        "locked": locked,
        "require_unlock": require_unlock,
        "terminal_enabled": bool(getattr(settings, "terminal_enabled", False)),
        "provisioned": bool(unlocker.provisioned()) if unlocker is not None else True,
        # every composer: "enter" (Enter sends) or "mod-enter" (Ctrl/Cmd+Enter sends). A callable, so the prefs
        # file is read only when a template that has a composer calls it, not on every polled fragment.
        "composer_send_key": lambda: prefs.composer_prefs(settings)["send_key"],
        "ui_theme": themes.current() or _default_theme,
        "ui_themes": [(t.name, t.label or t.name) for t in themes.all_themes()],
        "push_remote": settings.push_remote,  # the one remote pm fetches, pulls and pushes (pm.toml)
        "request": request,
    }


CSP_EMBED = CSP.replace("frame-ancestors 'none'", "frame-ancestors 'self'")


def csp_frames(hosts) -> str:
    """The page CSP plus ``frame-src 'self' <hosts>`` (Builder page only); hosts re-checked as https origins."""
    return f"{CSP}; frame-src {' '.join(("'self'", *frame_origins(hosts)))}"


_SECTION_NAVS = ("inbox", "projects", "tmux", "servers", "agents", "backlog", "audit", "notifications", "settings")


def html(request: Request, name: str, status: int = 200, csp: str | None = None, **ctx) -> HTMLResponse:
    ctx = {**page_ctx(request), **ctx}
    if not ctx.get("sidebar") and not ctx.get("embed") and ctx.get("nav") in _SECTION_NAVS:
        snap = services.published(_settings(request))  # global menu: the Projects list from the latest snapshot
        ctx["side_projects"] = project_sidebar(snap, "") if snap is not None else None
    # embed pages (right-hand drawer iframe) may be framed by pm itself, nothing else
    return HTMLResponse(
        assets.version_static(get_template(name).render(**ctx), STATIC_DIR),
        status_code=status,
        headers={"Cache-Control": "no-store",
                 "Content-Security-Policy": csp or (CSP_EMBED if ctx.get("embed") else CSP)},
    )


def is_embed(request: Request) -> bool:
    return request.query_params.get("embed") == "1"


_VOLATILE = re.compile(r"<time\b[^>]*>.*?</time>", re.S)


def etag_of(html: str) -> str:
    """Strong validator of a rendered fragment; <time> stamps do not count as a change."""
    return '"' + hashlib.sha256(_VOLATILE.sub("", html).encode()).hexdigest()[:16] + '"'


def fragment(request: Request, html: str, headers: dict[str, str] | None = None) -> Response:
    """HTML fragment with an ETag; 204 (htmx: no swap) when the client already has it."""
    etag = etag_of(html)
    h = {"Cache-Control": "no-store", "ETag": etag, "Vary": "HX-Request", **(headers or {})}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=204, headers=h)
    return HTMLResponse(html, headers={**h, "Content-Security-Policy": CSP})


def git_remotes_html(request: Request, name: str, path, notice: str = "", ok: bool = True) -> str:
    """The ``_git_remotes.html`` panel (remotes list, add form, ssh key) for one project."""
    from pm.git import ctl as gitctl
    from pm.git import remote as gitremote
    from pm.infra import sshkeys
    st = request.app.state.settings
    hosts = tuple(st.git_allowed_hosts) or gitctl.DEFAULT_GIT_HOSTS
    return get_template("_git_remotes.html").render(
        **page_ctx(request), project_name=name, remotes=gitremote.remotes(path), notice=notice,
        notice_ok=ok, hosts=hosts, pub=sshkeys.managed_key(st))
