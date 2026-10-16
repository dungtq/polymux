"""Exit an agent and resume an ended agent session.

POST + CSRF + Origin + audit via ``mutation_guard`` (Tier.MESSAGE, same as stop). Exit sends the
agent's own quit command (``/exit``) as a normal message, like typing it in the composer;
resume starts the kind's own resume command (``claude --resume <id>``, ``codex resume <id>``, ...) from that record,
with the optional form field ``text`` as its first message ("Resume and send"; ``agentctl.opt_text``).
"""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from pm.agents import autoexit, registry, slash
from pm.agents import launch as launch_mod
from pm.agents import message as msg
from pm.agents import resume as resume_mod
from pm.agents import stop as stop_mod
from pm.core import executors
from pm.core.errors import PmError
from pm.core.models import Tier
from pm.infra import snapshot as services
from pm.projects import projects
from pm.security.audit import message_detail
from pm.security.auth import require_session
from pm.security.guard import mutation_guard
from pm.web import agentctl

router = APIRouter()


def _refuse(request: Request, action: str, target: str, status: int, text: str,
            result: str = "denied", reason: str | None = None, detail: dict | None = None):
    """Audit, then answer with a flash fragment (htmx swaps 4xx) instead of bare JSON."""
    agentctl.done(request, action, target, result, {"reason": reason or text, **(detail or {})})
    return agentctl.frag(text, ok=False, status=status)


@router.post("/a/{agent_id}/exit", response_class=HTMLResponse)
async def agent_exit(request: Request, agent_id: str,
                     _=Depends(mutation_guard("agent_exit", Tier.MESSAGE))):
    """Exit = send the agent's own quit command (``/exit``) as a normal message, exactly like
    typing it in the composer. No kill path, so no protected-session bypass is involved."""
    action = "agent_exit"
    try:
        a = await agentctl.agent(request, action, agent_id)
    except HTTPException as e:  # already audited by _agent
        return agentctl.frag(f"Exit refused: {e.detail}.", ok=False, status=e.status_code)
    name = a.id
    cmd = slash.exit_cmd(a.kind.value)
    if cmd is None:
        return _refuse(request, action, a.id, 409, f"Exit refused: {a.kind.value} has no quit command.",
                       reason="no exit command")
    try:  # the Exit button's confirm dialog is the confirmation for the danger-risk command
        slash.check_message(a.kind.value, cmd, True, running=False)
    except slash.SlashError as e:
        return _refuse(request, action, a.id, 400, f"Exit refused: {e}.", reason="slash refused")
    detail = {**message_detail(cmd), "cmd": cmd}
    settings = request.app.state.settings
    autoexit.record_manual_exit(settings, a)  # resume data first; registry keeps the id when it ends
    try:
        res = await executors.run_tmux(lambda s, ag: msg.send_message(s, ag, cmd), settings, a)
    except msg.MessageError as e:
        return _refuse(request, action, a.id, 409, f"Exit failed: {msg.describe(e)}.", "error",
                       "send failed", detail)
    except Exception:  # noqa: BLE001
        return _refuse(request, action, a.id, 502, "Exit failed (tmux error).", "error", "error")
    submitted = getattr(res, "submitted", True)
    agentctl.done(request, action, a.id, "ok", {**detail, "submitted": int(submitted)})
    agentctl.publish(request, agent=a.id, action=action)
    return agentctl.frag(f"Sent {cmd} to {name}." if submitted
                 else f"Sent {cmd} to {name} (not confirmed; check the agent).",
                 ok=submitted)


@router.post("/a/{agent_id}/exitkill", response_class=HTMLResponse)
async def agent_exit_kill(request: Request, agent_id: str,
                          _=Depends(mutation_guard("agent_exit_kill", Tier.MESSAGE))):
    """Exit and kill window: no graceful quit needed. Records the resume data, then kills the
    agent's pane by id (tmux closes the window with its last pane). Refused for protected
    sessions, pm's own session and the only pane of a session's only window."""
    action = "agent_exit_kill"
    try:
        a = await agentctl.agent(request, action, agent_id)
    except HTTPException as e:  # already audited by _agent
        return agentctl.frag(f"Exit refused: {e.detail}.", ok=False, status=e.status_code)
    settings = request.app.state.settings
    detail = {"pane": a.pane_id}
    autoexit.record_manual_exit(settings, a)
    try:
        res = await executors.run_tmux(stop_mod.kill_agent_pane, settings, a)
    except Exception:  # noqa: BLE001
        return _refuse(request, action, a.id, 502, "Kill failed (tmux error).", "error", "error", detail)
    if res.status == "refused":
        return _refuse(request, action, a.id, 409, f"Not killed: {res.detail}.",
                       reason="kill refused", detail={**detail, "why": res.detail[:80]})
    if res.status == "error":
        return _refuse(request, action, a.id, 502, "Kill failed (tmux error).", "error", "error", detail)
    agentctl.done(request, action, a.id, "ok", {**detail, "status": res.status})
    agentctl.publish(request, agent=a.id, action=action)
    return agentctl.frag(f"Killed the tmux pane of {a.id}." if res.status == "killed"
                 else f"{a.id}: pane is gone.")


@router.post("/a/{agent_id}/resume", response_class=HTMLResponse)
async def agent_resume(request: Request, agent_id: str,
                       _=Depends(mutation_guard("agent_resume", Tier.MESSAGE))):
    action = "agent_resume"
    if not agentctl.AGENT_ID.fullmatch(agent_id):
        return _refuse(request, action, "", 404, "Cannot resume: unknown agent.", reason="unknown agent")
    settings = request.app.state.settings
    rec = registry.get(settings, agent_id)
    if rec is None:
        return _refuse(request, action, agent_id, 404, "Cannot resume: unknown agent.", reason="unknown agent")
    if not rec.ended or not resume_mod.resumable_kind(rec.kind) or not rec.resume_id:
        why = ("not an ended session of a resumable agent." if not rec.ended or not resume_mod.resumable_kind(rec.kind)
               else "no session id recorded.")
        return _refuse(request, action, rec.id, 409, "Cannot resume: " + why, reason="not resumable")
    try:
        text = await agentctl.opt_text(request, action, rec.id)
    except HTTPException as e:  # already audited (length only)
        return agentctl.frag(f"Cannot resume: {e.detail}.", ok=False, status=e.status_code)
    snap = await services.snapshot_async(settings, max_age_s=2.0)
    if any(x.kind.value == rec.kind and rec.resume_id in (x.ident, x.session_uuid, registry.resume_ident(x.kind.value, x.ident))
           for x in snap.agents):
        return _refuse(request, action, rec.id, 409, "Already running.", reason="already running")
    project = next((p for p in projects.discover(settings) if p.name == rec.project), None)
    if project is None:
        return _refuse(request, action, rec.id, 404, "Cannot resume: project no longer found.", reason="unknown project")
    try:
        res = await executors.run_tmux(resume_mod.resume_session, settings, rec, project, text)
    except (launch_mod.LaunchError, PmError) as e:  # PmError: the stored account left [accounts]
        return _refuse(request, action, rec.id, 400, "Cannot resume: refused.", reason="resume refused", detail={"why": str(e)[:80]})
    except Exception:  # noqa: BLE001
        return _refuse(request, action, rec.id, 502, "Resume failed (tmux error).", "error", "resume failed")
    agentctl.done(request, action, rec.id, "ok", {"window": res.window_id, "session": rec.resume_id, "kind": rec.kind,
                                                  **agentctl.len_detail(text)})
    agentctl.publish(request, agent=rec.id, action=action)
    return agentctl.frag(f"Resumed {rec.id} in {res.window_name}.",
                 extra=f' <a href="/a/{escape(rec.id)}">open</a>')


@router.get("/a/{agent_id}/mode", dependencies=[Depends(require_session)])
async def agent_mode(request: Request, agent_id: str) -> JSONResponse:
    """Which view /a/{id} renders now (``live`` or ``ended``); read-only, polled by agentmode.js
    so the page can switch after Exit / Resume without a manual reload."""
    settings = request.app.state.settings
    mode = "gone"
    if agentctl.AGENT_ID.fullmatch(agent_id):
        snap = await services.snapshot_async(settings)
        res = registry.resolve(settings, snap, agent_id)
        if res is not None and res.redirect is None:
            mode = "ended" if res.ended is not None else "live"
    return JSONResponse({"mode": mode}, headers={"Cache-Control": "no-store"})
