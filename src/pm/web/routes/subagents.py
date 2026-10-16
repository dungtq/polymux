"""Agent page "Sub-agents" tab: read-only list and per-sub-agent work (Claude sessions)."""

from __future__ import annotations

import hashlib
import time

from fastapi import APIRouter, HTTPException, Request, Response

from pm.agents import subagents
from pm.core import executors
from pm.web.chatctx import live_or_ended
from pm.web.deps import AUTH, snapshot
from pm.web.render import html

router = APIRouter()


@router.get("/a/{agent_id}/subagents", dependencies=AUTH)
async def agent_subagents(request: Request, agent_id: str, id: str = "", wf: str = "", running: int = 0) -> Response:
    snap = await snapshot(request)
    agent, ended = live_or_ended(request, snap, agent_id)
    tr = agent.transcript if agent.kind.value == "claude" else None
    if id:
        work = await executors.run_collectors(subagents.read_work, tr, id, wf)
        if work is None:
            raise HTTPException(404, "unknown sub-agent")
        resp = html(request, "_subagent_work.html", agent=agent, work=work)
        # polled in place while the sub-agent runs (nodiff.js): unchanged work answers 304
        tag = '"' + hashlib.sha1(resp.body).hexdigest()[:16] + '"'
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers={"ETag": tag, "Cache-Control": "no-store"})
        resp.headers["ETag"] = tag
    elif running:
        # composer strip: only the running ones, empty body when none (the strip hides on :empty)
        subs = [s for s in await executors.run_collectors(subagents.list_subs, tr) if s.running]
        resp = html(request, "_subagents_live.html", subs=subs, now=time.time()) if subs else Response("", media_type="text/html")
        tag = '"' + hashlib.sha1(resp.body).hexdigest()[:16] + '"'
        resp.headers["ETag"] = tag
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers={"ETag": tag, "Cache-Control": "no-store"})
    else:
        subs = await executors.run_collectors(subagents.list_subs, tr)
        summ = await executors.run_collectors(subagents.summarize, subs, tr)
        resp = html(request, "_subagents.html", agent=agent, subs=subs, summ=summ, now=time.time(), ended=ended)
        # the tab polls (nodiff.js): an unchanged list answers 304 and touches nothing
        tag = '"' + hashlib.sha1(resp.body).hexdigest()[:16] + '"'
        resp.headers["ETag"] = tag
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers={"ETag": tag, "Cache-Control": "no-store"})
    resp.headers["Cache-Control"] = "no-store"
    return resp
