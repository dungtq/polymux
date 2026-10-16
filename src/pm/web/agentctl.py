"""Agent mutation plumbing shared by the agent routes (message, outbox, stop, launch, exit, fork, next):
form access (incl. the optional first message of resume / fork), flash fragments, audit/refusal helpers,
agent and project resolution, tmux ids, the composer outbox bubble and its hub event."""

from __future__ import annotations

import json
import re
from html import escape

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from pm.agents import deliver, registry
from pm.agents import head as agenthead
from pm.agents import resume as resume_mod
from pm.chat.sources.claude import live_session_id
from pm.core import executors
from pm.core.errors import Invalid
from pm.core.models import Agent, AgentKind, OutboxMsg, Project
from pm.infra import snapshot as services
from pm.projects import projects
from pm.security.audit import AuditError
from pm.security.guard import audit_result
from pm.tmux import ctl as tmuxctl
from pm.web.render import get_template, page_ctx

_NOSTORE = {"Cache-Control": "no-store"}
AGENT_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
PATH_ID = re.compile(r"^[swp]\d+$")
PREFIX = {"s": "$", "w": "@", "p": "%"}


async def form(request: Request, key: str) -> str:
    v = (await request.form()).get(key, "")
    return v if isinstance(v, str) else ""


def frag(msg_: str, ok: bool = True, status: int = 200, extra: str = "") -> HTMLResponse:
    cls = "flash ok" if ok else "flash err"
    return HTMLResponse(f'<p class="{cls}">{escape(msg_)}</p>{extra}', status_code=status,
                        headers=_NOSTORE)


async def composer_event(resp: HTMLResponse, agent: Agent) -> HTMLResponse:
    """Add the ``HX-Trigger`` header ``pm:composer`` ``{ph, phShort, pill}`` (the composer placeholder and the
    heading pill, built by the server: ``agenthead.placeholder`` / ``live_pill``) so the open composer and the
    agent heading follow a model / effort switch."""
    full, short = await executors.run_tmux(agenthead.placeholder, agent)
    pill = await executors.run_tmux(agenthead.live_pill, agent)
    resp.headers["HX-Trigger"] = json.dumps({"pm:composer": {"ph": full, "phShort": short, "pill": pill}})
    return resp


def done(request: Request, action: str, target: str, result: str,
          detail: dict | None = None) -> None:
    try:
        audit_result(request, action, target, result, detail)
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None


def stop(request: Request, action: str, target: str, status: int, why: str,
          result: str = "denied", detail: dict | None = None,
          reason: str | None = None) -> HTTPException:
    done(request, action, target, result, {"reason": reason or why, **(detail or {})})
    return HTTPException(status, why)


async def opt_text(request: Request, action: str, target: str) -> str:
    """Optional first message (form field ``text``) for resume / fork (``resume.first_message``): refused
    with 400 (audited, length only) when it is invalid. Call before any tmux window is started."""
    text = await form(request, "text")
    try:
        return resume_mod.first_message(text)
    except Invalid as e:
        raise stop(request, action, target, 400, str(e), detail=len_detail(text)) from None


def len_detail(text: str) -> dict:
    """Audit detail of an optional message: ``{"len": <utf-8 bytes>}``, ``{}`` when empty (never the text)."""
    return {"len": len(text.encode("utf-8", "replace"))} if text else {}


def publish(request: Request, **data) -> None:
    hub = getattr(request.app.state, "hub", None)
    if hub is None:
        return
    try:
        hub.publish("inbox", data)
    except Exception:  # noqa: BLE001 - notification is best effort
        pass


async def tmux_changed(request: Request, action: str) -> None:
    """After a tmux mutation (session / window / pane new, kill, rename): rebuild the snapshot now (the poller can
    be up to 15 s behind) and publish an ``inbox`` event ``{action}`` so open lists reload. Best effort."""
    try:
        await executors.run_snapshot(services.build_snapshot, request.app.state.settings, force=True)
    except Exception:  # noqa: BLE001 - the poll loop catches up
        pass
    publish(request, action=action)


def outbox_bubble(request: Request, m: OutboxMsg, agent: Agent | None) -> HTMLResponse:
    """The composer's ``_outbox.html`` bubble of one stored message (message POST, outbox actions)."""
    html = get_template("_outbox.html").render(**page_ctx(request), m=m, agent=agent, agent_id=m.agent_id,
        uploads=request.app.state.settings.state_dir / "uploads")
    return HTMLResponse(html, headers=_NOSTORE)


def publish_outbox(request: Request, m: OutboxMsg) -> None:
    """Hub event ``chat_outbox`` ``{agent_id, id, state}`` (the text never)."""
    hub = getattr(request.app.state, "hub", None)
    if hub is not None:
        hub.publish(deliver.EVENT, {"agent_id": m.agent_id, "id": m.id, "state": m.state})


async def snapshot(request: Request):
    """Latest published snapshot (no rebuild per request). The target is re-verified live by
    ``agents.message.verify_target`` before anything is sent, so a snapshot a few seconds old
    is enough to resolve the id."""
    return await services.snapshot_async(request.app.state.settings, max_age_s=10.0)


async def agent(request: Request, action: str, agent_id: str) -> Agent:
    """The running agent for a mutation, or a refusal.

    The id is permanent but the pane is only an attribute: the id's record must still name the
    session that runs in the pane right now, else 409 (nothing is sent to an unrelated agent)."""
    if not AGENT_ID.fullmatch(agent_id):
        raise stop(request, action, "", 404, "unknown agent")
    snap = await snapshot(request)
    settings = request.app.state.settings
    a = snap.agent(agent_id)
    if a is None:
        res = registry.resolve(settings, snap, agent_id)
        if res is not None and res.ended is not None:
            raise stop(request, action, agent_id, 409, "agent ended", reason="agent ended")
        if res is not None and res.redirect:
            raise stop(request, action, agent_id, 409, "stale agent id",
                        reason="stale pane id", detail={"now": res.redirect})
        raise stop(request, action, agent_id, 404, "unknown agent")
    rec = registry.get(settings, a.id)
    live = (live_session_id(a.pid, **registry.config_kw(settings, a.id, a.kind))
            if a.kind is AgentKind.CLAUDE and a.pid > 1 else None)
    known = rec.ident if rec else None
    if known and ((a.ident and a.ident != known) or (live and live != known)):
        raise stop(request, action, a.id, 409, "agent changed",
                    reason="session mismatch", detail={"pane": a.pane_id})
    return a


def project(request: Request, action: str, name: str) -> Project:
    for p in projects.discover(request.app.state.settings):
        if p.name == name:
            return p
    raise stop(request, action, name, 404, "unknown project")


def tmux_id(raw: str, kind: str) -> str:
    raw = (raw or "").strip()
    if PATH_ID.fullmatch(raw):
        if PREFIX[raw[0]] != kind:
            raise tmuxctl.TmuxError("wrong id kind")
        raw = kind + raw[1:]
    return tmuxctl.check_id(raw, kind)
