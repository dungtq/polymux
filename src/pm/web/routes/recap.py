"""Read-only chat extras: per-chat recap and history of earlier agents in the same project/cwd."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from pm.agents import recap
from pm.core import executors
from pm.core.markdown_render import render_markdown
from pm.web import deps
from pm.web.chatctx import live_or_ended
from pm.web.deps import AUTH, snapshot
from pm.web.render import html

router = APIRouter()


@router.get("/a/{agent_id}/recap", dependencies=AUTH)
async def agent_recap(request: Request, agent_id: str) -> Response:
    snap = await snapshot(request)
    agent, _ended = live_or_ended(request, snap, agent_id)
    rc = await executors.run_tmux(recap.recap_for, agent)
    rows, total = await executors.run_tmux(recap.history_for, deps.settings(request), agent)
    return html(request, "_recap.html", agent=agent, rc=rc, hist=rows, hist_total=total,
                 dur=recap.human_duration)


@router.get("/a/{agent_id}/recap-row", dependencies=AUTH)
async def agent_recap_row(request: Request, agent_id: str) -> Response:
    """Recap for the expanded row of an agent list: Claude's own recap, else the last reply."""
    snap = await snapshot(request)
    agent, _ended = live_or_ended(request, snap, agent_id)
    rc = await executors.run_tmux(recap.recap_for, agent)
    return html(request, "_recap_row.html", agent=agent, rc=rc, dur=recap.human_duration,
                away_html=render_markdown(rc.away), reply_html=render_markdown(rc.reply_raw or rc.reply))
