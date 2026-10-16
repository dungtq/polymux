"""Per-agent extras for live hub events: the agent's unread ntfy count and its last message.

``build`` gives ``{agent id: {"unread": int, "last": {"text": str, "src": "user" | "ntfy", "ts": float} | None}}``:
``unread`` = the agent's unread ntfy messages (``inbox.item_unread``), ``last`` = the newer of the user's last
message and the agent's newest ntfy item (``inbox.last_message``: one line, masked, at most 120 characters).
``events`` adds them to ``agent_state`` / ``agent_added`` payloads; ``for_items`` turns new feed items into one
``inbox`` payload per live agent (injected into the feed by ``web.app``). Never pane text, never the error line.
"""

from __future__ import annotations

import time

from pm.agents import inbox
from pm.core.config import Settings
from pm.notify import mutes
from pm.notify import owner as ntfy_owner

TIMEOUT_S = 1.0  # callers wait this long for the extras, then send their event without them


def build(settings: Settings, agents, now: float | None = None) -> dict[str, dict]:
    """``{agent id: {"unread", "last"}}`` for ``agents`` (live Agents) in one owner-mapping pass over the feed."""
    agents = list(agents)
    if not agents:
        return {}
    now = time.time() if now is None else now
    store = inbox.load(settings.state_dir)
    feed = ntfy_owner.agent_feed(settings, {a.id for a in agents},
                                 unread=lambda i: inbox.item_unread(store, i, now))
    out = {}
    for a in agents:
        f = feed.get(a.id)
        out[a.id] = {"unread": f.unread if f else 0, "last": inbox.last_message(a, f.item if f else None)}
    return out


def owner_ids(settings: Settings, items) -> list[str]:
    """Agent ids that sent ``items`` (first seen first): the title's agent id first; the transcript index is built
    once, and only when a title does not name a known agent."""
    out: list[str] = []
    index = None
    for it in items:
        o = ntfy_owner.title_owner(settings, it.title)
        if o is None:
            if index is None:
                index = ntfy_owner.build_index(settings, min(i.time for i in items))
            o = ntfy_owner.find(index, it.title, it.time)
        if o is not None and o.agent_id not in out:
            out.append(o.agent_id)
    return out


def for_items(settings: Settings, items, get_agent, now: float | None = None) -> list[dict]:
    """One ``inbox`` payload ``{"agent", "ntfy": 1, "unread", "last"}`` per live agent (``get_agent(id)`` -> Agent
    or None) that sent one of the new ``items``; system and muted messages are skipped."""
    items = [i for i in mutes.drop_muted(settings, list(items), now) if not inbox.is_system(i)]
    if not items:
        return []
    agents = [a for a in (get_agent(i) for i in owner_ids(settings, items)) if a is not None]
    live = build(settings, agents, now)
    return [{"agent": a.id, "ntfy": 1, **live[a.id]} for a in agents if a.id in live]
