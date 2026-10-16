"""Composer outbox actions on one stored message (docs/CHAT-SYNC.md):
``POST /a/{agent_id}/outbox/{oid}/{now|cancel|retry|edit}``.

``now`` types the message into the agent's own queue (``deliver.send_now``), ``cancel`` drops it,
``retry`` puts a failed one back to ``saved`` for the deliverer, ``edit`` cancels it and returns
JSON ``{"text": ...}`` for the composer. The others answer with the re-rendered ``_outbox.html``
bubble. Guarded by ``mutation_guard``; the audit line carries the id and action, never the text.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from pm.agents import deliver
from pm.chat import outbox
from pm.core import executors
from pm.core.errors import PmError
from pm.core.models import Agent, OutboxMsg, Tier
from pm.infra import snapshot as services
from pm.security.guard import mutation_guard
from pm.web import agentctl

router = APIRouter()

ACTION = "agent_outbox"
OPS = ("now", "cancel", "retry", "edit")
_NOSTORE = {"Cache-Control": "no-store"}
_TO = {"cancel": "cancelled", "edit": "cancelled", "retry": "saved"}


async def _apply(request: Request, op: str, m: OutboxMsg) -> tuple[OutboxMsg, Agent | None]:
    settings = request.app.state.settings
    if op == "now":
        agent = await agentctl.agent(request, ACTION, m.agent_id)
        return await deliver.send_now(settings, agent, m.id), agent
    m = await executors.run_collectors(outbox.set_state, settings, m.id, _TO[op])
    if op == "retry":
        deliver.kick(m.agent_id)
    snap = services.published(settings)
    return m, snap.agent(m.agent_id) if snap is not None else None


@router.post("/a/{agent_id}/outbox/{oid}/{op}")
async def outbox_action(request: Request, agent_id: str, oid: str, op: str,
                        _=Depends(mutation_guard(ACTION, Tier.MESSAGE))):
    detail: dict[str, str | int] = {"op": op[:8], "id": oid[:12]}
    if op not in OPS or not oid.isdigit() or not agentctl.AGENT_ID.fullmatch(agent_id):
        raise agentctl.stop(request, ACTION, agent_id[:64], 404, "not found", detail=detail)
    m = await executors.run_collectors(outbox.get, request.app.state.settings, int(oid))
    if m is None or m.agent_id != agent_id:
        raise agentctl.stop(request, ACTION, agent_id, 404, "unknown message", detail=detail)
    try:
        m, agent = await _apply(request, op, m)
    except PmError as e:
        raise agentctl.stop(request, ACTION, agent_id, e.status, str(e) or "refused", detail=detail) from None
    agentctl.done(request, ACTION, agent_id, "ok", {**detail, "state": m.state, "len": len(m.text)})
    agentctl.publish_outbox(request, m)
    if op == "edit":
        return JSONResponse({"text": m.text}, headers=_NOSTORE)
    return agentctl.outbox_bubble(request, m, agent)
