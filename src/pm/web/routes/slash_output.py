"""Command output card: capture what an info slash command printed in the TUI.

Info commands (``/context``, ``/session-stats``...) print to the terminal only, never to the
session store. The message route takes a masked pre-send capture and hands the client an
opaque token; ``GET /a/{id}/slash-output?since=<token>`` then polls the pane for ~2.5 s and
returns the lines that were not on screen before. Pane text goes through
``services.pane_text`` (ANSI-stripped and masked). It is held in memory for ``TTL_S`` seconds
and is never logged, audited, indexed or sent to ntfy.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from collections import Counter
from html import escape

from fastapi import APIRouter, HTTPException, Request, Response

from pm.core import executors
from pm.infra import snapshot as services
from pm.web import deps
from pm.web.deps import AUTH, snapshot
from pm.web.render import html

router = APIRouter()
TTL_S = 30.0
MAX_TOKENS = 32
POLL_S = 0.5
WINDOW_S = 2.5
WINDOW_PENDING_S = 8.0  # cap while the output still shows a "fetching" placeholder (e.g. devin /usage)
SCAN_LINES = 80
MAX_OUT_LINES = 60
MAX_OUT_CHARS = 6000
_PENDING = re.compile(r"(?i)\b(fetching|loading|waiting)\b|…|\.\.\.\s*$")
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,32}$")
_AGENT_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _store(request: Request) -> dict:
    st = request.app.state
    if not hasattr(st, "slash_snaps"):
        st.slash_snaps = {}
    return st.slash_snaps


def _lines(text: str | None) -> list[str]:
    return [ln.rstrip() for ln in (text or "").splitlines() if ln.strip()]


def new_lines(before: list[str], after: list[str]) -> list[str]:
    """Lines of ``after`` that were not on screen in ``before`` (multiset difference, order kept)."""
    seen = Counter(before)
    out = []
    for ln in after:
        if seen[ln] > 0:
            seen[ln] -= 1
        else:
            out.append(ln)
    return out


def _pending(lines: list[str]) -> bool:
    """True when the capture still holds a placeholder line (command still fetching its output)."""
    return any(_PENDING.search(ln) for ln in lines)


def _bound(lines: list[str]) -> list[str]:
    out, size = [], 0
    for ln in lines[:MAX_OUT_LINES]:
        size += len(ln) + 1
        if size > MAX_OUT_CHARS:
            break
        out.append(ln[:400])
    return out


async def snapshot_before(request: Request, agent) -> str | None:
    text = await executors.run_tmux(services.pane_text, deps.settings(request), agent.pane_id, SCAN_LINES)
    if text is None:
        return None
    store = _store(request)
    now = time.monotonic()
    for k in [k for k, v in store.items() if now - v[2] > TTL_S]:
        del store[k]
    while len(store) >= MAX_TOKENS:
        del store[next(iter(store))]
    token = secrets.token_urlsafe(12)
    store[token] = (agent.id, _lines(text), now)
    return token


def card_trigger(agent_id: str, token: str) -> str:
    """Fragment appended to the "Sent." flash: htmx loads the card as soon as it is swapped in."""
    return (f'<span hidden hx-get="/a/{escape(agent_id)}/slash-output?since={escape(token)}" '
            'hx-trigger="load" hx-target="[data-cmp-slash-card]" hx-swap="innerHTML"></span>')


@router.get("/a/{agent_id}/slash-output", dependencies=AUTH)
async def slash_output(request: Request, agent_id: str, since: str = "") -> Response:
    if not _AGENT_ID.fullmatch(agent_id) or not _TOKEN.fullmatch(since):
        raise HTTPException(404, "unknown")
    store = _store(request)
    entry = store.get(since)
    if entry is None or entry[0] != agent_id or time.monotonic() - entry[2] > TTL_S:
        store.pop(since, None)
        raise HTTPException(404, "expired")
    snap = await snapshot(request)
    agent = snap.agent(agent_id)
    if agent is None:
        raise HTTPException(404, "unknown agent")
    before, settings = entry[1], deps.settings(request)
    best: list[str] = []
    last: list[str] | None = None
    start = time.monotonic()
    await asyncio.sleep(POLL_S)
    while True:
        text = await executors.run_tmux(services.pane_text, settings, agent.pane_id, SCAN_LINES)
        if text is None:
            break
        cur = new_lines(before, _lines(text))
        if len(cur) >= len(best) or (cur and _pending(best) and not _pending(cur)):
            best = cur  # second arm: the placeholder resolved into shorter real output
        pending = _pending(best)
        if cur and cur == last and not pending:
            break
        last = cur
        if time.monotonic() - start >= (WINDOW_PENDING_S if pending else WINDOW_S):
            break
        await asyncio.sleep(POLL_S)
    store.pop(since, None)
    resp = html(request, "_slash_card.html", agent=agent, lines=_bound(best))
    resp.headers["Cache-Control"] = "no-store"
    return resp
