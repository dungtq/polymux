"""Usage panel of one agent chat: GET /a/{id}/usage (HTML partial, read-only)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from pm.agents import registry, usage
from pm.chat.stats import human
from pm.core import executors
from pm.web import chatctx, deps
from pm.web.deps import AUTH, snapshot
from pm.web.render import html

router = APIRouter()


@router.get("/a/{agent_id}/usage", dependencies=AUTH)
async def agent_usage(request: Request, agent_id: str) -> Response:
    snap = await snapshot(request)
    agent, ended = chatctx.live_or_ended(request, snap, agent_id)
    settings = deps.settings(request)
    data = None if ended else await executors.run_tmux(usage.panel_data, agent, settings,
                                                 **registry.config_kw(settings, agent.id, agent.kind))
    return html(request, "_usage_chat.html", agent=agent, u=data, human=human)
