"""Free-text answers to an open Claude Code AskUserQuestion form ("Needs you").

POST /a/{agent_id}/ask: ``text`` (one line) and optional ``note`` (option number; 0 or empty = the "Type
something" answer). The keys are worked out by ``agents.message.answer_other`` from a fresh pane capture.
POST + CSRF + Origin + session + tier via ``mutation_guard``; the audit line carries the note number and the
text's length and hash, never the text or any option label.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from pm.agents import message as msg
from pm.core import executors
from pm.core.models import Tier
from pm.security.audit import message_detail
from pm.security.guard import mutation_guard
from pm.web import agentctl

router = APIRouter()
ACTION = "agent_ask"


@router.post("/a/{agent_id}/ask", response_class=HTMLResponse)
async def agent_ask(request: Request, agent_id: str, _=Depends(mutation_guard(ACTION, Tier.MESSAGE))):
    text = await agentctl.form(request, "text")
    raw = (await agentctl.form(request, "note")).strip() or "0"
    if not raw.isdigit() or len(raw) > 1:
        raise agentctl.stop(request, ACTION, agent_id, 400, "invalid input")
    note = int(raw)
    detail = {"note": note, **message_detail(text)}
    a = await agentctl.agent(request, ACTION, agent_id)
    try:
        await executors.run_tmux(msg.answer_other, request.app.state.settings, a, text, note=note)
    except msg.InvalidMessage:
        raise agentctl.stop(request, ACTION, a.id, 400, "invalid input", detail=detail) from None
    except msg.Refused as e:
        raise agentctl.stop(request, ACTION, a.id, 409, str(e), detail=detail) from None
    except msg.MessageError as e:
        why = msg.describe(e)
        raise agentctl.stop(request, ACTION, a.id, 409, f"send failed: {why}", "error",
                            {**detail, "error": why}, reason="send failed") from None
    agentctl.done(request, ACTION, a.id, "ok", detail)
    agentctl.publish(request, agent=a.id, action=ACTION)
    return agentctl.frag("Note sent." if note else "Answer sent.")
