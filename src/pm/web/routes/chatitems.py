"""Chat items: the append-only conversation of one agent as an HTML fragment.

``GET /a/{id}/items`` renders ``_chatitems.html``: user prompts, one bubble per assistant text block,
tool calls grouped under the reply that started them, turn-end lines, plus pm's own composer messages
that are not in the transcript yet (outbox). ``static/js/chat-items.js`` merges the fragment into the
page. Listed in ``pm.web.routes.ROUTERS``.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, Response

from pm.agents import chatfeed, registry
from pm.agents import head as agenthead
from pm.chat import items as chat_items
from pm.chat import itemsync
from pm.core import executors
from pm.web import chatctx, deps, plugins
from pm.web.deps import AUTH, snapshot
from pm.web.render import html, is_embed

router = APIRouter()


def int_arg(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    if not raw.isascii() or not raw.isdigit() or len(raw) > 18:
        raise HTTPException(400, "bad cursor")
    return int(raw)


async def _page(settings, agent, ended: bool, **q) -> chat_items.Page:
    """The items page; an ended agent whose first page is empty is ingested once here first (the feed
    has not synced it yet: ended agents have no other ingest), so its page shows the conversation."""
    page = await executors.run_collectors(chat_items.page, settings, agent.id, **q)
    if ended and not page.items and not any(v is not None for v in q.values()):
        await executors.run_collectors(
            itemsync.sync, settings, agent, idle=True,
            **registry.config_kw(settings, agent.id, agent.kind))
        page = await executors.run_collectors(chat_items.page, settings, agent.id, **q)
    return page


def _model_name(agent, settings=None) -> str:
    """Reply header name: the short live model (transcript, else --model), else the capitalised kind."""
    m = agenthead.live_model(agent, config_dirs=agenthead.kind_dirs(settings, agent.kind) if settings else None)
    return agenthead.model_short(m) if m != agent.kind.value else m.capitalize()


@router.get("/a/{agent_id}/items", dependencies=AUTH)
async def agent_items(request: Request, agent_id: str) -> Response:
    """Items fragment. No query: the newest page (+ Full Transcript link, Load older, outbox).
    ``after_rev=<n>``: new and changed items since that cursor (+ outbox); ``before=<seq>``: the
    older page. ``lo``/``hi`` (poll): the page's lowest/highest item seq. Headers: ``X-Rev`` = the next
    cursor; ``X-More: 1`` truncated poll (ask again); ``X-Reset: 1`` items were re-created (reload)."""
    q = request.query_params
    after, before = int_arg(q.get("after_rev")), int_arg(q.get("before"))
    lo, hi = int_arg(q.get("lo")), int_arg(q.get("hi"))
    snap = await snapshot(request)
    agent, ended = chatctx.live_or_ended(request, snap, agent_id)
    settings = deps.settings(request)
    chatfeed.watch(agent.id)
    page = await _page(settings, agent, ended, after_rev=after, before=before, lo=lo, hi=hi)
    mode = "older" if before is not None else ("poll" if after is not None else "full")
    transcript = plugins.first_url(plugins.of(request), "agent_transcript_url", agent) if mode == "full" else None
    resp = html(request, "_chatitems.html", mode=mode, agent=agent, ended=ended, embed=is_embed(request),
                uploads=settings.state_dir / "uploads", now=time.time(), transcript=transcript,
                model_name=_model_name(agent, settings),
                can_continue=not ended and bool(agent.project), blocks=chatctx.blocks(page.items),
                more=page.more, oldest=page.oldest, rev=page.rev, outbox=page.outbox)
    resp.headers["X-Rev"] = str(page.rev)
    resp.headers["X-Now"] = f"{time.time():.3f}"
    if mode == "poll" and page.more:
        resp.headers["X-More"] = "1"
    if page.reset:
        resp.headers["X-Reset"] = "1"
    return resp
