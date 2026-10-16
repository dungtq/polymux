"""Live strip facts (``#chat`` under the items, docs/CHAT-SYNC.md "live strip").

Besides the ``ChatView`` (ask card, state, Claude's own queued message) the strip shows the open
turn's tool count and, for a tool running longer than ``WAIT_S``, its name, start and the masked
last lines of the pane above the agent's input box, plus the background tasks still running in
the current conversation (across turns). Never the reply text. Blocking; callers use an
executor.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace

from pm.chat import items as chat_items
from pm.chat import outbox
from pm.chat.live import PANE_LINES, chat_view
from pm.core.config import Settings
from pm.core.models import Agent, ChatView
from pm.security.mask import render_pane
from pm.tmux import ctl as tmuxctl

WAIT_S = 10.0  # a tool running longer than this shows "Waiting" and the pane tail
TAIL_LINES = 5
BG_ROWS = 10  # most background tasks listed
STRIP_ITEMS = 120  # newest items read for the open turn (tool count, running tools)
BOX_LINES = 8  # the agent's input box starts within this many lines of the pane bottom
_RULE = re.compile(r"^\s*[╭╰]?[─━═]{12,}")
_PROMPT = re.compile(r"^\s*│?\s*[❯>]")


@dataclass(frozen=True)
class BgRow:
    """One background task still running: tool name, its masked target line and its start."""

    name: str
    line: str
    ts: float


@dataclass(frozen=True)
class Strip:
    """What the live strip shows besides the ChatView: the open turn's tool calls, the tool that
    has been running for more than WAIT_S (its name and start) and the masked pane tail under it."""

    tools: int = 0
    wait_name: str = ""
    wait_start: float = 0.0
    tail: str = ""
    bg: tuple[BgRow, ...] = ()


def _box_top(lines: list[str]) -> int:
    """Index of the input box's top rule: a horizontal rule within the last ``BOX_LINES`` non-blank
    lines whose next line is the prompt (``❯`` / ``>``); ``len(lines)`` when there is none, so a wide
    ``───`` line in command output never cuts the tail."""
    end = len(lines)
    while end and not lines[end - 1].strip():
        end -= 1
    for i in range(end - 2, max(-1, end - 1 - BOX_LINES), -1):
        if _RULE.match(lines[i]) and _PROMPT.match(lines[i + 1]):
            return i
    return len(lines)


def pane_tail(text: str, n: int = TAIL_LINES) -> str:
    """Last ``n`` non-blank lines of the pane above the agent's input box, ANSI-stripped and masked."""
    lines = [ln.rstrip() for ln in render_pane(text or "").splitlines()]
    body = [ln[:200] for ln in lines[:_box_top(lines)] if ln.strip()]
    return "\n".join(body[-n:])


def open_turn(items) -> list:
    """Tool items of the turn in progress: after the newest ``end`` item, same turn as the newest item."""
    if not items or items[-1].kind == "end":
        return []
    last = max((i for i, it in enumerate(items) if it.kind == "end"), default=-1)
    turn = items[-1].turn
    return [it for it in items[last + 1:] if it.kind == "tool" and it.turn == turn]


def read(settings: Settings, agent: Agent, ended: bool, *, chatlog=None) -> tuple[ChatView, Strip]:
    """Blocking. The ChatView plus the open turn's tool facts; the pane is captured once (chat_view
    reuses it) and its tail is only kept while a tool waits. Ended agents never read a pane.

    ``chatlog`` (``pm.chat.log`` or None, injected by the route): its ``sync`` keeps the stored chat
    log (search, Continue…) ingesting while the agent page is open."""
    if ended:
        return chat_view(settings, agent, ""), Strip()
    _sync_log(chatlog, settings, agent)
    recent = chat_items.list_items(settings, agent.id, limit=STRIP_ITEMS)
    tools = open_turn(recent)
    bg = _background(settings, agent.id, recent[-1].conv if recent else "")
    now = time.time()
    wait = min((t for t in tools if t.status == "run" and t.note != "background" and now - t.ts > WAIT_S),
               key=lambda t: t.ts, default=None)
    pane = None
    if wait is not None:
        try:
            pane = tmuxctl.capture_pane(settings, agent.pane_id, PANE_LINES)
        except Exception:  # noqa: BLE001 - no tail then; chat_view captures on its own
            pane = None
    view = _own_queued(settings, agent, chat_view(settings, agent, pane), now)
    if wait is None:
        return view, Strip(tools=len(tools), bg=bg)
    return view, Strip(len(tools), wait.name or "tool", wait.ts, pane_tail(pane or ""), bg)


def _background(settings: Settings, agent_id: str, conv: str) -> tuple[BgRow, ...]:
    """Running background tasks of ``conv`` (the newest item's conversation), newest first."""
    if not conv:
        return ()
    try:
        con = chat_items.connect(settings)
        try:
            rows = chat_items.running_background(con, agent_id, conv, BG_ROWS)
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - the live strip must render even when the store is broken
        return ()
    return tuple(BgRow(it.name or "tool", it.line, it.ts) for it in rows)


def _own_queued(settings: Settings, agent: Agent, view: ChatView, now: float) -> ChatView:
    """Drop Claude's queued block when it is a pm outbox message sent with Send now: its own bubble
    already shows "Queued in Claude", so the strip would show it twice. The pane joins wrapped
    lines and shows image paths its own way, so the match is loose (``outbox.match_queued``)."""
    if not getattr(view, "queued", ""):
        return view
    try:
        m = outbox.match_queued(settings, agent.id, view.queued, now)
    except Exception:  # noqa: BLE001 - show Claude's block then
        return view
    return replace(view, queued="") if m is not None else view


def _sync_log(chatlog, settings: Settings, agent: Agent) -> None:
    if chatlog is None:
        return
    try:
        chatlog.sync(settings, agent)
    except Exception:  # noqa: BLE001 - the live strip must render even when the store is broken
        pass
