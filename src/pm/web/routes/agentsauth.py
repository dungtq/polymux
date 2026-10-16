"""Agents page > one agent > Auth: login status, Log in (CLI login flow in a tmux window), Log out.

Fragment ``_agents_auth.html`` (``#ag-auth``) is loaded lazily by ``agents_detail.html``. While a login
runs, only its output block (``#ag-auth-out``) polls, so a code being typed in the form survives; when
the login ends the poll answer retargets the whole fragment. The pasted code is never logged, audited
or echoed: the audit detail carries its length only.

``GET /agents/auth.json`` is the login state of every agent at once (the Installed table): from the status
cache only, no CLI probe; ``?fresh=1`` re-probes the installed, supported ones. Each agent row carries its
default state plus ``accounts``, the named accounts (claude / codex, ``[accounts]`` in pm.toml) with theirs.

Every auth route takes an optional ``account`` (query on GET, form field on POST; default ``""`` = the CLI's
own login). It is checked against the configured names (``account_env``): unknown -> 400. The fragment shows
one row per account; only the account named in a request is re-probed on ``fresh``.

Accounts are defined here too (claude / codex): ``POST /agents/{name}/auth/add`` (field ``name``), ``/remove`` and
``/default`` (field ``account``; ``""`` on ``/default`` = the CLI's own folder). Names are validated by
``pm.core.accounts`` only (Invalid -> 400, the fragment is re-rendered with the message); audit carries kind + name.

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
from pm.core import accounts, executors
from pm.core.accounts import account_env
from pm.core.config import ACCOUNT_KINDS
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


def _accounts(request: Request, agent: AgentInfo) -> tuple[str, ...]:
    """Account names of one agent, default (``""``) first; only claude / codex have named ones."""
    named = accounts.names(_settings(request), agent.name) if agent.name in ACCOUNT_KINDS else ()
    return ("", *named)


def _account(request: Request, agent: AgentInfo, value: str) -> str:
    """The ``account`` field, checked against the configured names (Invalid -> 400)."""
    value = (value or "").strip()
    try:
        account_env(_settings(request), agent.name, value)
    except PmError as e:
        raise HTTPException(e.status, str(e) or "unknown account") from None
    return value


def _qaccount(request: Request, agent: AgentInfo) -> str:
    return _account(request, agent, request.query_params.get("account", ""))


async def _faccount(request: Request, agent: AgentInfo) -> str:
    return _account(request, agent, await controlctl.form(request, "account"))


def _detail(account: str, extra: dict | None = None) -> dict | None:
    d = {**(extra or {}), **({"account": account} if account else {})}
    return d or None


async def _status(request: Request, agent: AgentInfo, account: str = "", fresh: bool = False):
    """Status, or an "unknown" one carrying the domain error (shown muted in the fragment)."""
    try:
        return await executors.run_collectors(agentsauth.status, agent.name, _bin(agent), fresh=fresh,
                                              account=account, settings=_settings(request))
    except PmError as e:
        return agentsauth.AuthStatus(name=agent.name, supported=True, error=str(e)[:200] or "status failed")


async def _view(request: Request, agent: AgentInfo, account: str = ""):
    try:
        return await executors.run_tmux(agentsauth.login_view, _settings(request), agent.name, account=account)
    except PmError as e:
        raise HTTPException(e.status, str(e) or "refused") from None


def _row(account: str, st, view) -> dict:
    return {"account": account, "label": account or "default", "st": st, "view": view,
            "links": [u for u in view.urls if u.startswith(("https://", "http://"))],
            "q": ("?account=" + quote(account)) if account else "",
            "oid": "ag-auth-out-" + account if account else "ag-auth-out"}


def _render(request: Request, agent: AgentInfo, rows: list[dict], part: str = "", notice: str = "",
            headers: dict[str, str] | None = None, status: int = 200) -> Response:
    st = _settings(request)
    kind = agent.name in ACCOUNT_KINDS
    html = get_template("_agents_auth.html").render(
        **page_ctx(request), agent=agent, rows=rows, part=part, notice=notice,
        sup=agentsauth.supported(agent.name), base="/agents/" + quote(agent.name),
        manage=kind and bool(agent.binary) and part == "",
        readonly=[a for a in _accounts(request, agent)[1:] if accounts.is_readonly(st, agent.name, a)] if kind else [],
        gdef=accounts.global_default(st, agent.name) if kind else "")
    resp = fragment(request, html, headers)
    if status != 200:
        resp.status_code = status
    return resp


async def _full(request: Request, agent: AgentInfo, account: str = "", notice: str = "", fresh: bool = False,
                headers: dict[str, str] | None = None, status: int = 200) -> Response:
    """Whole fragment: per account the login view, plus the status unless its login is still running.

    ``fresh`` re-probes only ``account``'s row; the others come from the status cache."""
    async def one(acc: str) -> dict:
        view = await _view(request, agent, acc)
        st = None if view.active and not view.dead else await _status(request, agent, acc, fresh and acc == account)
        return _row(acc, st, view)
    rows = list(await asyncio.gather(*(one(a) for a in _accounts(request, agent))))
    return _render(request, agent, rows, notice=notice, headers=headers, status=status)


def _guard(agent: AgentInfo) -> None:
    if not agentsauth.supported(agent.name) or not agent.binary:
        raise HTTPException(400, "login not supported for this agent")


def _state_row(agent: AgentInfo, st, account: str = "") -> dict:
    """One agent's (or one named account's) row of /agents/auth.json; "unknown" with no error when nothing is cached."""
    sup = agentsauth.supported(agent.name) and bool(agent.binary)
    st = st or agentsauth.AuthStatus(name=agent.name, supported=sup)
    row = {"name": account or agent.name, "supported": sup, "state": st.state, "account": st.account,
           "method": st.method, "plan": st.plan, "error": st.error}
    return row


async def _states(request: Request, agents: list[AgentInfo], fresh: bool) -> list[list]:
    """Per agent the statuses of its accounts, default first (None = nothing cached / not probeable)."""
    async def one(a: AgentInfo) -> list:
        if not (a.installed and agentsauth.supported(a.name) and a.binary):
            return [None for _ in _accounts(request, a)]
        if fresh:
            return [await _status(request, a, acc, True) for acc in _accounts(request, a)]
        return [agentsauth.cached(a.name, _bin(a), account=acc) for acc in _accounts(request, a)]
    return await asyncio.gather(*(one(a) for a in agents))


@router.get("/agents/auth.json", dependencies=AUTH)
async def auth_states(request: Request):
    inv = await executors.run_collectors(lambda: agentsinv.get_inventory(background=True))
    fresh = request.query_params.get("fresh") == "1"
    agents = list(inv.agents)
    sts = await _states(request, agents, fresh)
    rows = []
    for a, group in zip(agents, sts):
        row = _state_row(a, group[0])
        row["accounts"] = [_state_row(a, st, acc) for acc, st in zip(_accounts(request, a)[1:], group[1:])]
        rows.append(row)
    return JSONResponse({"agents": rows}, headers={"Cache-Control": "no-store"})


@router.get("/agents/{name}/auth", dependencies=AUTH)
async def auth_panel(request: Request, name: str):
    agent = await _agent(name)
    account = _qaccount(request, agent)
    if not agentsauth.supported(agent.name) or not agent.binary:
        return _render(request, agent, [_row("", None, agentsauth.LoginView(active=False))])
    qp = request.query_params
    if qp.get("poll") != "1":
        return await _full(request, agent, account, fresh=qp.get("fresh") == "1")
    view = await _view(request, agent, account)
    if view.active and not view.dead:
        return _render(request, agent, [_row(account, None, view)], part="out")
    # login just ended: re-check, swap the whole fragment in place of the polling output block
    return await _full(request, agent, account, fresh=True, headers=_RETARGET)


@router.post("/agents/{name}/auth/login", dependencies=[Depends(mutation_guard("agents_auth_login", Tier.SAFE))])
async def auth_login(request: Request, name: str):
    agent = await _agent(name)
    _guard(agent)
    account = await _faccount(request, agent)
    try:
        await executors.run_tmux(agentsauth.login_start, _settings(request), agent.name, _bin(agent), account=account)
    except PmError as e:
        controlctl.done(request, "agents_auth_login", agent.name, "error", _detail(account, {"reason": str(e)[:80]}))
        return await _full(request, agent, account, notice=str(e) or "Login could not start.")
    controlctl.done(request, "agents_auth_login", agent.name, "ok", _detail(account))
    return await _full(request, agent, account)


@router.post("/agents/{name}/auth/send", dependencies=[Depends(mutation_guard("agents_auth_send", Tier.SAFE))])
async def auth_send(request: Request, name: str):
    agent = await _agent(name)
    _guard(agent)
    account = await _faccount(request, agent)
    code = (await controlctl.form(request, "code")).strip()
    if not code or len(code) > CODE_MAX:
        controlctl.done(request, "agents_auth_send", agent.name, "denied", _detail(account, {"len": len(code)}))
        return await _full(request, agent, account, notice="Paste the code first." if not code else "Code is too long.")
    try:
        await executors.run_tmux(agentsauth.login_send, _settings(request), agent.name, code, account=account)
    except PmError as e:  # message comes from the domain, never the code itself
        controlctl.done(request, "agents_auth_send", agent.name, "error", _detail(account, {"len": len(code)}))
        return await _full(request, agent, account, notice=str(e) or "Could not send the code.")
    controlctl.done(request, "agents_auth_send", agent.name, "ok", _detail(account, {"len": len(code)}))
    return await _full(request, agent, account)


@router.post("/agents/{name}/auth/cancel", dependencies=[Depends(mutation_guard("agents_auth_cancel", Tier.SAFE))])
async def auth_cancel(request: Request, name: str):
    agent = await _agent(name)
    _guard(agent)
    account = await _faccount(request, agent)
    try:
        await executors.run_tmux(agentsauth.login_cancel, _settings(request), agent.name, account=account)
    except PmError as e:
        controlctl.done(request, "agents_auth_cancel", agent.name, "error", _detail(account, {"reason": str(e)[:80]}))
        return await _full(request, agent, account, notice=str(e) or "Could not cancel the login.")
    controlctl.done(request, "agents_auth_cancel", agent.name, "ok", _detail(account))
    return await _full(request, agent, account, fresh=True)


@router.post("/agents/{name}/auth/logout", dependencies=[Depends(mutation_guard("agents_auth_logout", Tier.SAFE))])
async def auth_logout(request: Request, name: str):
    agent = await _agent(name)
    _guard(agent)
    account = await _faccount(request, agent)
    try:
        await executors.run_collectors(agentsauth.logout, agent.name, _bin(agent), account=account,
                                       settings=_settings(request))
    except PmError as e:
        controlctl.done(request, "agents_auth_logout", agent.name, "error", _detail(account, {"reason": str(e)[:80]}))
        return await _full(request, agent, account, notice=str(e) or "Log out failed.", fresh=True)
    finally:
        agentsauth.clear_cache(agent.name, account=account)
    controlctl.done(request, "agents_auth_logout", agent.name, "ok", _detail(account))
    return await _full(request, agent, account, fresh=True)


def _kind_guard(agent: AgentInfo) -> None:
    if agent.name not in ACCOUNT_KINDS:
        raise HTTPException(400, "no named accounts for this agent")


async def _manage(request: Request, name: str, action: str, field: str, fn) -> Response:
    """Shared body of add / remove / default: run the core call, audit kind + account name, re-render."""
    agent = await _agent(name)
    _kind_guard(agent)
    value = (await controlctl.form(request, field)).strip()
    try:
        fn(_settings(request), agent.name, value)
    except PmError as e:
        controlctl.done(request, action, agent.name, "error", {"reason": str(e)[:80]})
        return await _full(request, agent, notice=str(e) or "Refused.", status=e.status)
    except OSError:
        controlctl.done(request, action, agent.name, "error", {"reason": "accounts file not writable"})
        return await _full(request, agent, notice="Could not save the accounts.", status=500)
    controlctl.done(request, action, agent.name, "ok", {"account": value} if value else None)
    return await _full(request, agent)


@router.post("/agents/{name}/auth/add", dependencies=[Depends(mutation_guard("agents_auth_add", Tier.SAFE))])
async def auth_add(request: Request, name: str):
    return await _manage(request, name, "agents_auth_add", "name", accounts.add)


@router.post("/agents/{name}/auth/remove", dependencies=[Depends(mutation_guard("agents_auth_remove", Tier.SAFE))])
async def auth_remove(request: Request, name: str):
    return await _manage(request, name, "agents_auth_remove", "account", accounts.remove)


@router.post("/agents/{name}/auth/default", dependencies=[Depends(mutation_guard("agents_auth_default", Tier.SAFE))])
async def auth_default(request: Request, name: str):
    return await _manage(request, name, "agents_auth_default", "account", accounts.set_default)
