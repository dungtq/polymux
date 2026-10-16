"""Settings panel rendering shared by web.routes.settings, web.routes.ssh and web.routes.autoexit.

``context(request)`` is the panel's data (GET /settings renders it on first paint, GET /settings/panel as a
fragment). Every Settings mutation answers through ``reply``: the re-rendered HTML (classic htmx swaps it) with
an ``HX-Trigger: {"pm:notice": {"text", "ok"}}`` header, or JSON ``{ok, notice}`` when the POST explicitly
accepts ``application/json``. Refusals keep status 200 and say ``ok: false``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from pm.core import prefs
from pm.core.models import AgentKind
from pm.git import ctl as gitctl
from pm.infra import servers_store, sshkeys
from pm.security.passkey import manager_of
from pm.web.plugins import settings_sections_html
from pm.web.render import get_template, page_ctx

AGENTS = tuple(k.value for k in AgentKind if k != AgentKind.OTHER)


def context(request: Request) -> dict:
    """Template context of ``_settings_panel.html`` (without ``page_ctx`` and ``notice``)."""
    st = request.app.state
    cur = st.sessions.from_request(request)
    return dict(
        sessions=st.sessions.listing(cur.id if cur else None), passkeys=manager_of(request.app).list_credentials(),
        prefs=prefs.load(st.settings.prefs_file), agents=AGENTS,
        pm_key=sshkeys.managed_key(st.settings), user_keys=sshkeys.user_keys(st.settings),
        servers=servers_store.load(st.settings), srv_results=servers_store.RESULTS,
        git_hosts=st.settings.git_allowed_hosts, gitdev=gitctl.REMOTE,
        notify_rules=prefs.NOTIFY_RULES, composer=prefs.composer_prefs(st.settings), now=time.time(),
        plugin_sections=settings_sections_html(request))


def render(request: Request, notice: str = "") -> str:
    return get_template("_settings_panel.html").render(**page_ctx(request), **context(request), notice=notice)


def wants_json(request: Request) -> bool:
    """Only an explicit ``Accept: application/json`` (htmx and plain forms keep getting HTML)."""
    return any(p.split(";", 1)[0].strip().lower() == "application/json"
               for p in request.headers.get("accept", "").split(","))


def reply(request: Request, notice: str, ok: bool = True, body: Callable[[], str] | None = None) -> Response:
    """A Settings mutation's answer; ``body`` renders the HTML (not called for a JSON request)."""
    headers = {"Cache-Control": "no-store"}
    if notice:
        headers["HX-Trigger"] = json.dumps({"pm:notice": {"text": notice, "ok": ok}}, ensure_ascii=True)
    if request.method == "POST" and wants_json(request):  # a GET (lazy section) always gets its HTML
        return JSONResponse({"ok": ok, "notice": notice}, headers=headers)
    return HTMLResponse(body() if body else "", headers=headers)


def html(request: Request, notice: str, ok: bool = True) -> Response:
    return reply(request, notice, ok, lambda: render(request, notice))
