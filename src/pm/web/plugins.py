"""Plugin loader: features and themes from separately installed packages, without edits to core.

A plugin is an entry point in the group ``pm.plugins``; it loads an object (instance, class or module) with a
``name`` and any of these hooks (a missing hook = nothing):

- ``configure(cfg: dict, settings) -> None``: its ``[plugins.<name>]`` table from pm.toml (``{}`` if none).
- ``routers() -> list[APIRouter]``: included like core routers. Every mutating route must depend on
  ``security.guard.mutation_guard`` (with a tier; a bare ``csrf_session_guard`` is not enough); plain Starlette routes
  may only be GET/HEAD; Mounts and websockets are refused. A plugin breaking this is skipped.
- ``nav_items() -> list[render.NavItem]``: appended to the one nav list.
- ``settings_sections() -> list[(title, template_name, ctx_fn)]``: ``ctx_fn(request) -> dict``; rendered at the end of
  the Settings panel.
- ``background_loops() -> list[(start, stop)]``: ``async start(app)`` / ``async stop(app)``, run only when polling
  is on (not ``disable_poll``).
- ``themes() -> list[themes.Theme]``, ``templates_dir() -> Path | None``, ``static_dir() -> Path | None``
  (served at ``/static/p/<name>``; theme and plugin static URLs get ``?v=`` like ``/static``).
- ``template_globals() -> dict``: added to the template globals (every theme sees them); every key must start with
  ``<name>_`` (e.g. ``acme_shell``), else the plugin is skipped.
- Link hooks (called per request; ``None`` = nothing): ``project_links(project) -> (title, [ExtLink]) | None`` (one
  row of external links on the project page), ``agent_cwd_url(agent, project) -> str | None`` (the agent page's cwd
  link; ``project`` may be None), ``agent_transcript_url(agent) -> str | None`` (the chat's Full Transcript link).
  The first plugin with a URL wins; only http(s) URLs are used.
- ``new_project_steps(spec: newproject.Spec) -> list[newproject.Step]`` (called per New project plan/run through
  ``new_project_steps``): extra steps (docker stack, hostname, VM, ...) run after the git steps and before the first
  agent. Each Step needs ``id`` (``newproject.STEP_ID``; pm prefixes ``<name>.``), ``text``, ``kind`` and
  ``run(settings, spec, path) -> str`` (raise to fail; the run stops there). Form fields the core does not know reach
  the plugin in ``spec.options``. Wrong types: that plugin's steps are left out (logged).

Plugins are trusted code: they run in pm's process and get ``app`` (loops) and ``settings``. The guard check
catches mistakes, not malice; install only plugins you would merge into pm itself.

A plugin that raises on load or in a hook (or returns the wrong types) is logged and skipped; pm still starts.
``plugins_enabled = false`` in pm.toml loads none. ``PM_NO_PLUGINS=1`` skips discovery of the installed ones (the
test suite sets it); entry points a caller passes explicitly still load.
This is the only module that imports code by entry point (docs/ARCHITECTURE.md).
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from importlib.metadata import entry_points
from pathlib import Path
from typing import NamedTuple

from fastapi import APIRouter, FastAPI, Request
from fastapi.routing import APIRoute
from markupsafe import Markup
from starlette.routing import Route

from pm.core.config import Settings
from pm.projects import newproject
from pm.security.guard import csrf_session_guard
from pm.web import assets, render, themes
from pm.web.assets import NoCacheStatic

log = logging.getLogger(__name__)

GROUP = "pm.plugins"
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_GUARD = csrf_session_guard.__module__, ("csrf_session_guard.<locals>.dep",)  # mutation_guard returns this dep


class ExtLink(NamedTuple):
    """One external link a plugin adds (``project_links``); ``icon`` is a CSS hook (``data-icon``), e.g. "files"."""
    label: str
    url: str
    desc: str = ""
    icon: str = ""


@dataclass
class Plugin:
    """One loaded plugin: its hook results, collected once at load."""
    name: str
    obj: object
    routers: list[APIRouter] = field(default_factory=list)
    nav: list[render.NavItem] = field(default_factory=list)
    sections: list[tuple[str, str, Callable]] = field(default_factory=list)
    loops: list[tuple[Callable, Callable]] = field(default_factory=list)
    theme_list: list[themes.Theme] = field(default_factory=list)
    templates_dir: Path | None = None
    static_dir: Path | None = None
    template_globals: dict = field(default_factory=dict)


def _hook(obj, name: str, default):
    fn = getattr(obj, name, None)
    return default if fn is None else fn()


def _items(obj, hook: str, kind, check: Callable[[object], bool] | None = None) -> list:
    out = list(_hook(obj, hook, ()) or ())
    for x in out:
        if not isinstance(x, kind) or (check is not None and not check(x)):
            raise TypeError(f"{hook}() returned {x!r}")
    return out


def _dir(obj, hook: str) -> Path | None:
    d = _hook(obj, hook, None)
    if d is None:
        return None
    if not Path(d).is_dir():
        raise TypeError(f"{hook}() is not a directory: {d!r}")
    return Path(d)


def _globals(obj, name: str) -> dict:
    """``template_globals()``: a dict whose keys all start with ``<name>_`` (no clash with core or other plugins)."""
    g = _hook(obj, "template_globals", None)
    if g is None:
        return {}
    if not isinstance(g, dict) or not all(isinstance(k, str) and k.startswith(name + "_") for k in g):
        raise TypeError(f"template_globals() must be a dict with keys starting with {name + '_'!r}")
    return dict(g)


def _is_mutation_guard(call) -> bool:
    """``mutation_guard(action, tier)``'s dependency: the core guard with a tier, lock check on, no step-up-only. A bare
    ``csrf_session_guard`` (no tier check) does not count for plugins."""
    if getattr(call, "__module__", None) != _GUARD[0] or getattr(call, "__qualname__", "") not in _GUARD[1]:
        return False
    cells = dict(zip(call.__code__.co_freevars, (c.cell_contents for c in call.__closure__ or ()), strict=True))
    return cells.get("tier") is not None and cells.get("check_locked") is True and cells.get("stepup_only") is False


def _guarded(dependant) -> bool:
    return any(_is_mutation_guard(d.call) or _guarded(d) for d in dependant.dependencies)


def _check_routes(routes, deps: list, depth: int = 0) -> None:
    """Fail closed on every route a plugin adds: mutating APIRoutes need ``mutation_guard`` (on the route or an
    including router); a plain Starlette Route may only be GET/HEAD; Mounts, websockets and anything else are refused
    (no guard can be checked on them)."""
    if depth > 10:
        raise TypeError("router nesting too deep")
    for r in routes:
        if isinstance(r, APIRoute):
            if set(r.methods or ()) & _MUTATING and not (
                    _guarded(r.dependant) or any(_is_mutation_guard(getattr(d, "dependency", None)) for d in deps)):
                raise TypeError(f"mutating route {sorted(r.methods)} {r.path} has no mutation_guard")
        elif isinstance(r, Route):
            if not r.methods or not set(r.methods) <= {"GET", "HEAD"}:
                raise TypeError(f"plain route {r.path} must be GET only (methods {r.methods})")
        elif getattr(r, "include_context", None) is not None and hasattr(getattr(r, "original_router", None), "routes"):
            _check_routes(r.original_router.routes, [*deps, *(r.include_context.dependencies or [])], depth + 1)
        else:  # Mount, Host, WebSocketRoute, APIWebSocketRoute, unknown
            raise TypeError(f"route type {type(r).__name__} {getattr(r, 'path', '')!r} is not allowed in a plugin")


def _check_guards(router: APIRouter) -> None:
    """Fail closed: every mutating plugin route carries the core mutation guard."""
    _check_routes(router.routes, list(router.dependencies or []))


def _pair(x) -> bool:
    return isinstance(x, tuple) and len(x) == 2 and all(callable(f) for f in x)


def _section(x) -> bool:
    return isinstance(x, tuple) and len(x) == 3 and isinstance(x[0], str) and isinstance(x[1], str) and callable(x[2])


def _load_one(ep, settings: Settings) -> Plugin:
    obj = ep.load()
    if isinstance(obj, type):
        obj = obj()
    name = getattr(obj, "name", None) or ep.name
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise TypeError(f"bad plugin name {name!r}")
    for hook in ("new_project_steps",):  # per-request hooks: only checked to be callable here
        if getattr(obj, hook, None) is not None and not callable(getattr(obj, hook)):
            raise TypeError(f"{hook} is not callable")
    if callable(getattr(obj, "configure", None)):
        obj.configure(dict(settings.plugins.get(name, {})), settings)
    p = Plugin(name, obj,
               routers=_items(obj, "routers", APIRouter),
               nav=_items(obj, "nav_items", render.NavItem),
               sections=_items(obj, "settings_sections", tuple, _section),
               loops=_items(obj, "background_loops", tuple, _pair),
               theme_list=_items(obj, "themes", themes.Theme),
               templates_dir=_dir(obj, "templates_dir"),
               static_dir=_dir(obj, "static_dir"),
               template_globals=_globals(obj, name))
    for r in p.routers:
        _check_guards(r)
    return p


def _discover() -> list:
    return list(entry_points(group=GROUP))


def load(settings: Settings, eps: Iterable | None = None) -> list[Plugin]:
    """Load the plugins (``eps``: entry points, injectable; None = the installed ones unless ``PM_NO_PLUGINS=1``).
    Bad plugins are skipped."""
    if not settings.plugins_enabled:
        return []
    if eps is None:
        eps = [] if os.environ.get("PM_NO_PLUGINS") == "1" else _discover()
    out: list[Plugin] = []
    for ep in eps:
        try:
            p = _load_one(ep, settings)
        except Exception:
            log.exception("plugin %r failed to load; skipped", getattr(ep, "name", ep))
            continue
        if any(p.name == q.name for q in out):
            log.warning("plugin %r loaded twice; second skipped", p.name)
            continue
        out.append(p)
        log.info("plugin %r loaded", p.name)
    return out


def _register_themes(plugins: list[Plugin]) -> None:
    known = {t.name: t for t in themes.all_themes()}
    for p in plugins:
        for t in p.theme_list:
            if known.get(t.name) != t:  # the same theme again (a second create_app) is not a duplicate
                themes.register(t)
                known.setdefault(t.name, t)


def install(app: FastAPI, settings: Settings, eps: Iterable | None = None) -> list[Plugin]:
    """Everything ``create_app`` needs, called after the core routers and before the ``/static`` mount: load the
    plugins, register their themes, switch the template env to ``settings.theme``, include their routers, mount
    theme and plugin static dirs (versioned like ``/static``), extend the nav and the template globals. Kept on
    ``app.state.plugins``."""
    plugins = load(settings, eps)
    app.state.plugins = plugins
    _register_themes(plugins)
    render.use_theme(settings.theme, [p.templates_dir for p in plugins if p.templates_dir is not None])
    for p in plugins:
        for r in p.routers:
            app.include_router(r)
    for prefix, d in themes.static_mounts():
        app.mount(prefix, NoCacheStatic(directory=d), name="static-t-" + prefix.rsplit("/", 1)[-1])
        assets.add_static(prefix, d)
    for p in plugins:
        if p.static_dir is not None:
            app.mount(f"/static/p/{p.name}", NoCacheStatic(directory=p.static_dir), name=f"static-p-{p.name}")
            assets.add_static(f"/static/p/{p.name}", p.static_dir)
        render.env.globals.update(p.template_globals)  # shared with the per-theme overlays
    render.env.globals["NAV"] = render.NAV + tuple(i for p in plugins for i in p.nav)
    return plugins


async def start_loops(app: FastAPI) -> None:
    """Start every plugin background loop (lifespan, polling on); a failing start is logged and not stopped later."""
    started = []
    for p in getattr(app.state, "plugins", ()):
        for start, stop in p.loops:
            try:
                await start(app)
            except Exception:
                log.exception("plugin %r: background loop failed to start", p.name)
                continue
            started.append((p.name, stop))
    app.state.plugin_loops = started


async def stop_loops(app: FastAPI) -> None:
    for name, stop in reversed(getattr(app.state, "plugin_loops", None) or ()):
        try:
            await stop(app)
        except Exception:
            log.exception("plugin %r: background loop failed to stop", name)
    app.state.plugin_loops = []


def of(request: Request) -> tuple | list:
    """The loaded plugins of the app serving ``request`` (empty when none)."""
    return getattr(request.app.state, "plugins", ())


_bad_urls: set[tuple[str, str]] = set()  # bounded: one entry per (plugin, hook)


def _url_ok(name: str, hook: str, url) -> bool:
    """Only http(s) URLs reach a template; anything else is logged once per plugin and hook and dropped."""
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return True
    if (name, hook) not in _bad_urls:
        _bad_urls.add((name, hook))
        log.warning("plugin %r: %s() gave a non-http(s) URL; dropped", name, hook)
    return False


def _call(p: Plugin, hook: str, *args):
    """``p``'s link hook with ``args``; missing = None; a raising hook is logged and gives None."""
    fn = getattr(p.obj, hook, None)
    if not callable(fn):
        return None
    try:
        return fn(*args)
    except Exception:
        log.exception("plugin %r: %s() failed", p.name, hook)
        return None


def project_link_groups(plugins, project) -> list[tuple[str, list[ExtLink]]]:
    """Every plugin's ``project_links(project)`` group (title, links); wrong types and bad URLs are left out."""
    out = []
    for p in plugins:
        got = _call(p, "project_links", project)
        if got is None:
            continue
        if not (isinstance(got, tuple) and len(got) == 2 and isinstance(got[0], str) and isinstance(got[1], list | tuple)
                and all(isinstance(x, ExtLink) for x in got[1])):
            log.warning("plugin %r: project_links() returned the wrong type; ignored", p.name)
            continue
        links = [x for x in got[1] if _url_ok(p.name, "project_links", x.url)]
        if links:
            out.append((got[0], links))
    return out


def first_url(plugins, hook_name: str, *args) -> str | None:
    """The first http(s) URL a plugin's ``hook_name(*args)`` gives (``agent_cwd_url``, ``agent_transcript_url``)."""
    for p in plugins:
        url = _call(p, hook_name, *args)
        if url is not None and _url_ok(p.name, hook_name, url):
            return url
    return None


def _step_ok(s) -> bool:
    return (isinstance(s, newproject.Step) and isinstance(s.id, str) and bool(newproject.STEP_ID.fullmatch(s.id))
            and isinstance(s.text, str) and isinstance(s.kind, str) and callable(s.run))


def new_project_steps(plugins, spec: newproject.Spec) -> list[newproject.Step]:
    """Every plugin's ``new_project_steps(spec)``, ids prefixed ``<plugin>.``; a plugin returning anything else (or a
    step without ``run``) contributes nothing."""
    out: list[newproject.Step] = []
    for p in plugins:
        got = _call(p, "new_project_steps", spec)
        if got is None:
            continue
        if (not isinstance(got, list | tuple) or not all(_step_ok(s) for s in got)
                or len({s.id for s in got}) != len(got)):
            log.warning("plugin %r: new_project_steps() returned the wrong type; ignored", p.name)
            continue
        out += [replace(s, id=f"{p.name}.{s.id}", text=s.text[:200], kind=s.kind[:20] or "plugin") for s in got]
    return out


def settings_sections_html(request: Request) -> Markup:
    """The plugins' Settings sections (``<section class="set-section">``), each rendered with the page context; a
    section that raises is logged and left out."""
    parts = []
    for p in of(request):
        for i, (title, template, ctx_fn) in enumerate(p.sections):
            try:
                body = render.get_template(template).render(**render.page_ctx(request), **(ctx_fn(request) or {}))
            except Exception:
                log.exception("plugin %r: settings section %r failed", p.name, title)
                continue
            parts.append(Markup('<section id="p-{}-{}" class="set-section">\n<h2 class="set-h">{}</h2>\n{}\n</section>')
                         .format(p.name, i, title, Markup(body)))
    return Markup("\n").join(parts)
