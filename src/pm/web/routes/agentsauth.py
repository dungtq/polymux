"""Agents page > one agent > Auth: login status, Log in (CLI login flow in a tmux window), Log out.

Fragment ``_agents_auth.html`` (``#ag-auth``) is loaded lazily by ``agents_detail.html``. While a login
runs, only its output block (``#ag-auth-out``) polls, so a code being typed in the form survives; when
the login ends the poll answer retargets the whole fragment. The pasted code is never logged, audited
or echoed: the audit detail carries its length only.

``GET /agents/auth.json`` is the login state of every agent at once (the Installed table): from the status
cache only, no CLI probe; ``?fresh=1`` re-probes the installed, supported ones.

Mutations: POST + CSRF + Origin + session + tier SAFE (``mutation_guard``) + audit (fail closed).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from pm.agents.inventory import auth as agentsauth
from pm.agents.inventory import read as agentsinv
from pm.core import executors
from pm.core.errors import PmError
from pm.core.models import AgentInfo, Tier
from pm.security.guard import mutation_guard
from pm.web import controlctl
from pm.web.deps import AUTH
from pm.web.render import fragment, get_template, page_ctx

router = APIRouter()

CODE_MAX = 4096
_RETARGET = {"HX-Retarget": "#ag-auth", "HX-Reswap": "outerHTML"}


async def _agent(name: str) -> AgentInfo:
    inv = await executors.run_collectors(lambda: agentsinv.get_inventory(background=True))
    agent = agentsinv.find_agent(inv, name)
    if agent is None:
        raise HTTPException(404, "unknown agent")
    return agent


def _bin(agent: AgentInfo) -> str:
    """Inventory shows binaries home-relative (``~/.local/bin/claude``); the domain needs an absolute path."""
    return str(Path(agent.binary).expanduser()) if agent.binary else ""


def _settings(request: Request):
    return request.app.state.settings


async def _status(agent: AgentInfo, fresh: bool = False):
    """Status, or an "unknown" one carrying the domain error (shown muted in the fragment)."""
    try:
        return await executors.run_collectors(agentsauth.status, agent.name, _bin(agent), fresh=fresh)
    except PmError as e:
        return agentsauth.AuthStatus(name=agent.name, supported=True, error=str(e)[:200] or "status failed")


async def _view(request: Request, agent: AgentInfo):
    try:
        return await executors.run_tmux(agentsauth.login_view, _settings(request), agent.name)
    except PmError as e:
        raise HTTPException(e.status, str(e) or "refused") from None


def _render(request: Request, agent: AgentInfo, st, view, part: str = "", notice: str = "",
            headers: dict[str, str] | None = None) -> Response:
    html = get_template("_agents_auth.html").render(
        **page_ctx(request), agent=agent, st=st, view=view, part=part, notice=notice,
        links=[u for u in view.urls if u.startswith(("https://", "http://"))],
        sup=agentsauth.supported(agent.name), base="/agents/" + quote(agent.name))
    return fragment(request, html, headers)


async def _full(request: Request, agent: AgentInfo, notice: str = "", fresh: bool = False,
                headers: dict[str, str] | None = None) -> Response:
    """Whole fragment: login view, plus the status unless a login is still running."""
    view = await _view(request, agent)
    st = None if view.active and not view.dead else await _status(agent, fresh)
    return _render(request, agent, st, view, notice=notice, headers=headers)


def _guard(agent: AgentInfo) -> None:
    if not agentsauth.supported(agent.name) or not agent.binary:
        raise HTTPException(400, "login not supported for this agent")


def _state_row(agent: AgentInfo, st) -> dict:
    """One agent's row of /agents/auth.json; "unknown" with no error when nothing is cached yet."""
    sup = agentsauth.supported(agent.name) and bool(agent.binary)
    st = st or agentsauth.AuthStatus(name=agent.name, supported=sup)
    return {"name": agent.name, "supported": sup, "state": st.state, "account": st.account, "method": st.method,
            "plan": st.plan, "error": st.error}


async def _states(agents: list[AgentInfo], fresh: bool) -> list:
    async def one(a: AgentInfo):
        if not (a.installed and agentsauth.supported(a.name) and a.binary):
            return None
        if fresh:
            return await _status(a, True)
        return agentsauth.cached(a.name, _bin(a))
    return await asyncio.gather(*(one(a) for a in agents))


@router.get("/agents/auth.json", dependencies=AUTH)
async def auth_states(request: Request):
    inv = await executors.run_collectors(lambda: agentsinv.get_inventory(background=True))
    fresh = request.query_params.get("fresh") == "1"
    sts = await _states(list(inv.agents), fresh)
    return JSONResponse({"agents": [_state_row(a, st) for a, st in zip(inv.agents, sts)]},
                        headers={"Cache-Control": "no-store"})


@router.get("/agents/{name}/auth", dependencies=AUTH)
async def auth_panel(request: Request, name: str):
    agent = await _agent(name)
    if not agentsauth.supported(agent.name) or not agent.binary:
        return _render(request, agent, None, agentsauth.LoginView(active=False))
    qp = request.query_params
    if qp.get("poll") != "1":
        return await _full(request, agent, fresh=qp.get("fresh") == "1")
    view = await _view(request, agent)
    if view.active and not view.dead:
        return _render(request, agent, None, view, part="out")
    # login just ended: re-check, swap the whole fragment in place of the polling output block
    return _render(request, agent, await _status(agent, True), view, headers=_RETARGET)


@router.post("/agents/{name}/auth/login", dependencies=[Depends(mutation_guard("agents_auth_login", Tier.SAFE))])
async def auth_login(request: Request, name: str):
    agent = await _agent(name)
    _guard(agent)
    try:
        await executors.run_tmux(agentsauth.login_start, _settings(request), agent.name, _bin(agent))
    except PmError as e:
        controlctl.done(request, "agents_auth_login", agent.name, "error", {"reason": str(e)[:80]})
        return await _full(request, agent, notice=str(e) or "Login could not start.")
    controlctl.done(request, "agents_auth_login", agent.name, "ok")
    return await _full(request, agent)


@router.post("/agents/{name}/auth/send", dependencies=[Depends(mutation_guard("agents_auth_send", Tier.SAFE))])
async def auth_send(request: Request, name: str):
    agent = await _agent(name)
    _guard(agent)
    code = (await controlctl.form(request, "code")).strip()
    if not code or len(code) > CODE_MAX:
        controlctl.done(request, "agents_auth_send", agent.name, "denied", {"len": len(code)})
        return await _full(request, agent, notice="Paste the code first." if not code else "Code is too long.")
    try:
        await executors.run_tmux(agentsauth.login_send, _settings(request), agent.name, code)
    except PmError as e:  # message comes from the domain, never the code itself
        controlctl.done(request, "agents_auth_send", agent.name, "error", {"len": len(code)})
        return await _full(request, agent, notice=str(e) or "Could not send the code.")
    controlctl.done(request, "agents_auth_send", agent.name, "ok", {"len": len(code)})
    return await _full(request, agent)


@router.post("/agents/{name}/auth/cancel", dependencies=[Depends(mutation_guard("agents_auth_cancel", Tier.SAFE))])
async def auth_cancel(request: Request, name: str):
    agent = await _agent(name)
    _guard(agent)
    try:
        await executors.run_tmux(agentsauth.login_cancel, _settings(request), agent.name)
    except PmError as e:
        controlctl.done(request, "agents_auth_cancel", agent.name, "error", {"reason": str(e)[:80]})
        return await _full(request, agent, notice=str(e) or "Could not cancel the login.")
    controlctl.done(request, "agents_auth_cancel", agent.name, "ok")
    return await _full(request, agent, fresh=True)


@router.post("/agents/{name}/auth/logout", dependencies=[Depends(mutation_guard("agents_auth_logout", Tier.SAFE))])
async def auth_logout(request: Request, name: str):
    agent = await _agent(name)
    _guard(agent)
    try:
        await executors.run_collectors(agentsauth.logout, agent.name, _bin(agent))
    except PmError as e:
        controlctl.done(request, "agents_auth_logout", agent.name, "error", {"reason": str(e)[:80]})
        return await _full(request, agent, notice=str(e) or "Log out failed.", fresh=True)
    finally:
        agentsauth.clear_cache(agent.name)
    controlctl.done(request, "agents_auth_logout", agent.name, "ok")
    return await _full(request, agent, fresh=True)
