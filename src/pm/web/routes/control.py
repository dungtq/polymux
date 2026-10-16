"""Tier-1 mutation routes (tmux new/rename, git fetch/pull/push, compose restart,
ensure-layout) plus the TOTP unlock endpoints.

Wiring: ``app.include_router(web.routes.control.router)``. Requires app.state:
settings, sessions (auth.SessionStore); unlocker (security.unlock.Unlocker) for
/unlock. audit/csrf are created lazily by security.guard.

Responses are tiny autoescaped HTML fragments. Audit details never carry
message/pane text, and the TOTP URI is never audited or logged.
"""

from __future__ import annotations

import asyncio
import os
import re
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from pm.agents import pinned
from pm.core import executors
from pm.core.errors import Invalid, PmError
from pm.core.models import Project, Tier
from pm.git import ctl as gitctl
from pm.git import remote as gitremote
from pm.infra import stacks, unitctl
from pm.projects import projects
from pm.security.guard import CONTROL_TEXTS, client_of, csrf_session_guard, get_csrf, mutation_guard
from pm.tmux import ctl as tmuxctl
from pm.web import agentctl, controlctl

router = APIRouter()

_PATH_ID = re.compile(r"^[sw]\d+$")
_NOSTORE = {"Cache-Control": "no-store"}


# ------------------------------------------------------------------ unlock
def _unlock_guard(action: str):
    """Session + Origin + CSRF + audit; deliberately NOT the unlocked check (404 unless require_unlock)."""
    return csrf_session_guard(action, stepup_only=True, check_locked=False, texts=CONTROL_TEXTS)


@router.get("/unlock", response_class=HTMLResponse)
def unlock_form(request: Request) -> HTMLResponse:
    st = request.app.state
    if not st.settings.require_unlock:
        raise HTTPException(404, "step-up disabled (require_unlock is off)")
    session = st.sessions.from_request(request)
    if session is None:
        raise HTTPException(401, "login required")
    tok = get_csrf(request).token(session.id)
    if not st.unlocker.provisioned():
        body = (f'<form method="post" action="/unlock/provision">'
                f'<input type="hidden" name="csrf" value="{escape(tok)}">'
                f'<button type="submit">Set up TOTP</button></form>')
    else:
        body = (f'<form method="post" action="/unlock">'
                f'<input type="hidden" name="csrf" value="{escape(tok)}">'
                f'<input name="code" inputmode="numeric" autocomplete="one-time-code" '
                f'maxlength="6" pattern="[0-9]{{6}}" required>'
                f'<button type="submit">Unlock</button></form>')
    return HTMLResponse(body, headers=_NOSTORE)


@router.post("/unlock", response_class=HTMLResponse)
async def unlock(request: Request, session=Depends(_unlock_guard("unlock"))) -> HTMLResponse:
    unlocker = request.app.state.unlocker
    code = await controlctl.form(request, "code")
    ok = await executors.run_collectors(unlocker.unlock, session, code, client=client_of(request))
    controlctl.done(request, "unlock", "", "ok" if ok else "denied", {} if ok else {"reason": "code"})
    if not ok:
        return controlctl.frag("Invalid or rate-limited code.", "err", 403)
    return controlctl.frag("Unlocked.")


@router.post("/unlock/provision", response_class=HTMLResponse)
async def unlock_provision(request: Request,
                           session=Depends(_unlock_guard("unlock_provision"))) -> HTMLResponse:
    unlocker = request.app.state.unlocker
    if unlocker.provisioned():
        raise controlctl.refuse(request, "unlock_provision", "", 409, "already provisioned")
    try:
        uri = await executors.run_collectors(unlocker.provision)
    except FileExistsError:
        raise controlctl.refuse(request, "unlock_provision", "", 409, "already provisioned") from None
    except OSError:
        raise controlctl.fail(request, "unlock_provision", "", "provision failed") from None
    controlctl.done(request, "unlock_provision", "", "ok")  # never includes the URI
    return HTMLResponse(f'<p>Add to your authenticator (shown once):</p>'
                        f'<code>{escape(uri)}</code>', headers=_NOSTORE)


# ------------------------------------------------------------------ tmux
def _norm_id(raw: str, kind: str) -> str:
    """Accept `s12`/`w5` (URL friendly) or `$12`/`@5`; return a validated tmux id."""
    raw = (raw or "").strip()
    if _PATH_ID.fullmatch(raw):
        if raw[0] != {"$": "s", "@": "w"}[kind]:
            raise tmuxctl.TmuxError("wrong id kind")
        raw = kind + raw[1:]
    return tmuxctl.check_id(raw, kind)


def _own_session_id(settings) -> str | None:
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return None
    for s in tmuxctl.list_sessions(settings):
        for w in s.windows:
            if any(p.id == pane for p in w.panes):
                return s.id
    return None


def _guard_rename(request: Request, action: str, settings, sid: str, target: str) -> None:
    """Fail closed: unknown session, protected name, or pm's own session -> refuse."""
    try:
        sessions = tmuxctl.list_sessions(settings)
        own = _own_session_id(settings)
    except tmuxctl.TmuxError:
        raise controlctl.fail(request, action, target, "tmux unavailable") from None
    match = next((s for s in sessions if s.id == sid), None)
    if match is None:
        raise controlctl.refuse(request, action, target, 404, "unknown session")
    if sid == own or tmuxctl.is_protected(match.name, settings):
        raise controlctl.refuse(request, action, target, 403, "protected session")


def _bad(request: Request, action: str, target: str) -> HTTPException:
    return controlctl.refuse(request, action, target, 400, "invalid input")


@router.post("/tmux/session/new", response_class=HTMLResponse)
async def session_new(request: Request, _=Depends(mutation_guard("tmux_session_new", Tier.SAFE))):
    """New detached session; optional ``cwd`` = its start directory (``projects.start_dir``)."""
    settings = request.app.state.settings
    name, raw_cwd = await controlctl.form(request, "name"), await controlctl.form(request, "cwd")
    try:
        tmuxctl.check_name(name)
        cwd = await executors.run_collectors(projects.start_dir, settings, raw_cwd)
    except tmuxctl.TmuxError:
        raise _bad(request, "tmux_session_new", "") from None
    except PmError as e:
        raise controlctl.refuse(request, "tmux_session_new", "", e.status, str(e)) from None
    try:
        sid = await executors.run_tmux(tmuxctl.new_session, settings, name, cwd)
    except tmuxctl.TmuxError:
        raise controlctl.fail(request, "tmux_session_new", "", "tmux error") from None
    controlctl.done(request, "tmux_session_new", sid, "ok", {"name": name, **({"cwd": cwd[:200]} if cwd else {})})
    await agentctl.tmux_changed(request, "tmux_session_new")
    return controlctl.frag(f"Session {name} created ({sid}).")


@router.post("/tmux/window/new", response_class=HTMLResponse)
async def window_new(request: Request,
                     _=Depends(mutation_guard("tmux_window_new", Tier.SAFE))):
    settings = request.app.state.settings
    session, name = await controlctl.form(request, "session"), await controlctl.form(request, "name")
    try:
        sid = _norm_id(session, "$")
        nm = name.strip() or None
        if nm:
            tmuxctl.check_name(nm)
    except tmuxctl.TmuxError:
        raise _bad(request, "tmux_window_new", "") from None
    try:
        wid = await executors.run_tmux(tmuxctl.new_window, settings, sid, nm)
    except tmuxctl.TmuxError:
        raise controlctl.fail(request, "tmux_window_new", sid, "tmux error") from None
    controlctl.done(request, "tmux_window_new", wid, "ok", {"session": sid})
    await agentctl.tmux_changed(request, "tmux_window_new")
    return controlctl.frag(f"Window created ({wid}).")


@router.post("/tmux/session/{sid}/rename", response_class=HTMLResponse)
async def session_rename(request: Request, sid: str, _=Depends(mutation_guard("tmux_session_rename", Tier.SAFE))):
    action, settings = "tmux_session_rename", request.app.state.settings
    name = await controlctl.form(request, "name")
    try:
        tid = _norm_id(sid, "$")
        tmuxctl.check_name(name)
    except tmuxctl.TmuxError:
        raise _bad(request, action, "") from None
    await executors.run_tmux(_guard_rename, request, action, settings, tid, tid)
    try:
        await executors.run_tmux(tmuxctl.rename_session, settings, tid, name)
    except tmuxctl.TmuxError:
        raise controlctl.fail(request, action, tid, "tmux error") from None
    controlctl.done(request, action, tid, "ok", {"name": name})
    await agentctl.tmux_changed(request, action)
    return controlctl.frag(f"Session renamed to {name}.")


@router.post("/tmux/window/{wid}/rename", response_class=HTMLResponse)
async def window_rename(request: Request, wid: str, _=Depends(mutation_guard("tmux_window_rename", Tier.SAFE))):
    action, settings = "tmux_window_rename", request.app.state.settings
    name = await controlctl.form(request, "name")
    try:
        tid = _norm_id(wid, "@")
        tmuxctl.check_name(name)
    except tmuxctl.TmuxError:
        raise _bad(request, action, "") from None
    try:
        sessions = tmuxctl.list_sessions(settings)
        own = _own_session_id(settings)
    except tmuxctl.TmuxError:
        raise controlctl.fail(request, action, tid, "tmux unavailable") from None
    owner = next((s for s in sessions if any(w.id == tid for w in s.windows)), None)
    if owner is None:
        raise controlctl.refuse(request, action, tid, 404, "unknown window")
    if owner.id == own or tmuxctl.is_protected(owner.name, settings):
        raise controlctl.refuse(request, action, tid, 403, "protected session")
    try:
        await executors.run_tmux(tmuxctl.rename_window, settings, tid, name)
    except tmuxctl.TmuxError:
        raise controlctl.fail(request, action, tid, "tmux error") from None
    controlctl.done(request, action, tid, "ok", {"name": name})
    await agentctl.tmux_changed(request, action)
    return controlctl.frag(f"Window renamed to {name}.")


# ------------------------------------------------------------------ projects
def _project(request: Request, action: str, name: str) -> Project:
    for p in projects.discover(request.app.state.settings):
        if p.name == name:
            return p
    raise controlctl.refuse(request, action, name, 404, "unknown project")


async def _git_action(request: Request, action: str, name: str, fn, label: str):
    """``fn(path, remote=<pm.toml push_remote>)`` in the collectors pool, audited."""
    p = _project(request, action, name)
    try:
        out = await executors.run_collectors(fn, p.path, remote=request.app.state.settings.push_remote)
    except gitctl.GitDenied:
        raise controlctl.refuse(request, action, name, 403, "git refused") from None
    except gitctl.GitError:
        raise controlctl.fail(request, action, name, "git failed") from None
    controlctl.done(request, action, name, "ok")
    return controlctl.frag(f"{label} ok." + (f" {out[:300]}" if out else ""))


@router.post("/p/{name}/git/fetch", response_class=HTMLResponse)
async def git_fetch(request: Request, name: str,
                    _=Depends(mutation_guard("git_fetch", Tier.SAFE))):
    return await _git_action(request, "git_fetch", name, gitremote.fetch, "Fetch")


@router.post("/p/{name}/git/pull", response_class=HTMLResponse)
async def git_pull(request: Request, name: str,
                   _=Depends(mutation_guard("git_pull", Tier.SAFE))):
    return await _git_action(request, "git_pull", name, gitremote.pull_ff_only, "Pull (ff-only)")


def _push_checked(path: str, *, remote: str) -> str:
    st = gitctl.status(path, remote=remote)
    if st is None or not st.has_remote:
        raise gitctl.GitDenied(f"no {remote} remote")
    return gitremote.push(path, remote=remote)


@router.post("/p/{name}/git/push", response_class=HTMLResponse)
async def git_push(request: Request, name: str,
                   _=Depends(mutation_guard("git_push", Tier.SAFE))):
    label = f"Push to {request.app.state.settings.push_remote}"
    return await _git_action(request, "git_push", name, _push_checked, label)


@router.post("/p/{name}/pin")
async def project_pin(request: Request, name: str,
                      _=Depends(mutation_guard("project_pin", Tier.SAFE))):
    """Pin/unpin a favourite project (persisted in state_dir/pins.json)."""
    _project(request, "project_pin", name)
    want = (await controlctl.form(request, "pinned")) == "1"
    try:
        await executors.run_collectors(projects.set_pin, request.app.state.settings, name, want)
    except OSError:
        raise controlctl.fail(request, "project_pin", name, "could not save pins") from None
    controlctl.done(request, "project_pin", name, "ok", {"pinned": want})
    if request.headers.get("HX-Request"):
        return HTMLResponse("", headers={**_NOSTORE, "HX-Refresh": "true"})
    return RedirectResponse("/p", status_code=303)


@router.post("/a/{agent_id}/pin")
async def agent_pin(request: Request, agent_id: str,
                    _=Depends(mutation_guard("agent_pin", Tier.SAFE))):
    """Pin/unpin an agent session: its row moves to the Pinned group (Inbox, project page). state_dir/agent_pins.json."""
    want = (await controlctl.form(request, "pinned")) == "1"
    try:
        await executors.run_collectors(pinned.set_pin, request.app.state.settings, agent_id, want)
    except Invalid:
        raise controlctl.refuse(request, "agent_pin", agent_id[:64], 400, "bad agent id") from None
    except OSError:
        raise controlctl.fail(request, "agent_pin", agent_id, "could not save pins") from None
    controlctl.done(request, "agent_pin", agent_id, "ok", {"pinned": want})
    if request.headers.get("HX-Request"):
        return HTMLResponse("", headers={**_NOSTORE, "HX-Trigger": "pm:refresh"})
    return RedirectResponse("/", status_code=303)


@router.post("/p/{name}/stack/restart", response_class=HTMLResponse)
async def stack_restart(request: Request, name: str,
                        _=Depends(mutation_guard("stack_restart", Tier.SAFE))):
    p = _project(request, "stack_restart", name)
    ok = await executors.run_collectors(stacks.restart, p.path)
    if not ok:
        raise controlctl.fail(request, "stack_restart", name, "restart failed or no compose file")
    controlctl.done(request, "stack_restart", name, "ok")
    return controlctl.frag("Stack restarted.")


def _layout_names(raw) -> list[str]:
    names: list[str] = []
    for item in raw or []:
        n = item.get("name") if isinstance(item, dict) else item  # commands never run in v1
        names.append(tmuxctl.check_name(n))
    return names


@router.post("/p/{name}/tmux/ensure", response_class=HTMLResponse)
async def tmux_ensure(request: Request, name: str,
                      _=Depends(mutation_guard("tmux_ensure", Tier.SAFE))):
    action, settings = "tmux_ensure", request.app.state.settings
    p = _project(request, action, name)
    layouts = settings.layouts or {}
    if name not in layouts:
        raise controlctl.refuse(request, action, name, 404, "no layout")
    try:
        wanted = _layout_names(layouts[name])
        tmuxctl.check_name(name)
    except (tmuxctl.TmuxError, AttributeError, TypeError):
        raise controlctl.refuse(request, action, name, 400, "invalid layout") from None

    def work() -> int:
        sess = next((s for s in tmuxctl.list_sessions(settings) if s.name == name), None)
        created = 0
        if sess is None:
            sid = tmuxctl.new_session(settings, name, cwd=p.path)
            have = {w.name for s in tmuxctl.list_sessions(settings) if s.id == sid
                    for w in s.windows}
        else:
            sid, have = sess.id, {w.name for w in sess.windows}
        for w in wanted:
            if w not in have:
                tmuxctl.new_window(settings, sid, w, cwd=p.path)
                have.add(w)
                created += 1
        return created

    try:
        created = await executors.run_tmux(work)
    except tmuxctl.TmuxError:
        raise controlctl.fail(request, action, name, "tmux error") from None
    controlctl.done(request, action, name, "ok", {"created": created})
    return controlctl.frag(f"Layout ensured ({created} window(s) created).")


# ---------------------------------------------------------------- reload pm itself
def _restart_unit(unit: str) -> None:
    unitctl.restart_unit(unit)


@router.post("/service/reload", response_class=HTMLResponse)
async def service_reload(request: Request, _=Depends(mutation_guard("pm_reload", Tier.MESSAGE))):
    s = request.app.state.settings
    unit = s.reload_unit
    if not unitctl.reload_allowed(s):
        raise controlctl.refuse(request, "pm_reload", (unit or "")[:80], 403, "reload not enabled here")
    controlctl.done(request, "pm_reload", unit, "ok")
    # let this response (and the audit line) go out before systemd stops us
    asyncio.get_running_loop().call_later(0.5, _restart_unit, unit)
    return controlctl.frag("Reloading pm... the page is unavailable for a few seconds; reload it afterwards.")
