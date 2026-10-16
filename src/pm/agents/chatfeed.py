"""The one chat ingest loop (lifespan task).

For agents with an open viewer (``watch``, called by the items route; entries expire ``WATCH_TTL_S``
after the last call) and agents with an outbox row typed in but not matched yet, it runs
``itemsync.sync`` on the collectors pool: about every ``RUN_S`` while the agent works, every
``IDLE_S`` while it is idle, at once on ``kick``/``watch`` or when its idle state flips. New ``user``
items are linked to the outbox row they came from (``outbox.match`` -> ``outbox.set_state(done)`` +
``items.link_outbox``). Hub events: ``chat_items`` ``{agent_id, rev}`` when the agent's max rev grew,
``chat_outbox`` ``{agent_id, id, state}`` per linked row.

``is_idle``, ``get_agent`` and ``publish`` are injected from ``web/app.py``. No text is logged.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pm.agents import registry
from pm.chat import items, itemsync, outbox
from pm.core import executors
from pm.core.config import Settings
from pm.core.errors import Conflict, NotFound
from pm.core.models import Agent, OutboxMsg

log = logging.getLogger(__name__)

ITEMS_EVENT = "chat_items"
OUTBOX_EVENT = "chat_outbox"
MAX_WATCH = 64
WATCH_TTL_S = 60.0
RUN_S = 1.0  # sync cadence while the agent works
IDLE_S = 5.0  # ...and while it is idle
TICK_S = 0.5  # loop wake-up while something is watched (idle flips are seen within this)
QUIET_S = 5.0  # loop wake-up with nothing to watch
SENT_REFRESH_S = 5.0  # how often the "typed in, not matched yet" agent list is re-read
LINK_PAGE = 200
LINK_PAGES = 5  # bounded catch-up per sync; the rest follows on the next one

IsIdle = Callable[[Agent], bool]
GetAgent = Callable[[str], Agent | None]
Publish = Callable[[str, dict], object]

_watched: collections.OrderedDict[str, float] = collections.OrderedDict()  # agent id -> monotonic
_kicked: set[str] = set()


@dataclass
class _Seen:
    last: float = float("-inf")  # monotonic time of the last sync
    idle: bool | None = None
    rev: int = -1  # max rev already published
    cursor: int | None = None  # link cursor: items with a higher rev are not checked yet


def link_new(settings: Settings, agent_id: str, cursor: int | None) -> tuple[int | None, list[OutboxMsg]]:
    """Link the ``user`` items after ``cursor`` (None: the newest page) to their outbox rows.
    Returns (new cursor, rows that became ``done``). Blocking (DB)."""
    done: list[OutboxMsg] = []
    for _ in range(LINK_PAGES):
        batch = items.list_items(settings, agent_id, after_rev=cursor, limit=LINK_PAGE)
        if not batch:
            break
        for it in batch:
            if it.kind != "user" or it.outbox:
                continue
            m = outbox.match(settings, agent_id, it.text, it.ts)
            if m is None:
                continue
            try:
                m = outbox.set_state(settings, m.id, "done", item=it.seq)
            except (Conflict, NotFound):
                continue
            items.link_outbox(settings, agent_id, it.seq, m.id)
            done.append(m)
        cursor = max([it.rev for it in batch] + [cursor or 0])
        if len(batch) < LINK_PAGE:
            break
    return cursor, done


def sync_one(settings: Settings, agent: Agent, idle: bool, cursor: int | None) -> tuple[int, int | None, list]:
    """Ingest + link for one agent: (max rev of its items, new link cursor, rows now ``done``). Blocking."""
    itemsync.sync(settings, agent, idle=idle,
                  **registry.config_kw(settings, agent.id, agent.kind))
    cursor, done = link_new(settings, agent.id, cursor)
    return items.latest(settings, agent.id)[1], cursor, done


@dataclass
class Feed:
    settings: Settings
    is_idle: IsIdle
    get_agent: GetAgent
    publish: Publish | None = None
    clock: Callable[[], float] = time.monotonic
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    seen: dict[str, _Seen] = field(default_factory=dict)
    sent: list[str] = field(default_factory=list)
    sent_at: float = float("-inf")

    def _emit(self, type_: str, data: dict) -> None:
        if self.publish is None:
            return
        try:
            self.publish(type_, data)
        except Exception:  # noqa: BLE001 - notification is best effort
            pass

    async def targets(self) -> list[str]:
        now = self.clock()
        for aid, t in list(_watched.items()):
            if now - t > WATCH_TTL_S:
                _watched.pop(aid, None)
        if now - self.sent_at >= SENT_REFRESH_S:
            self.sent = await executors.run_collectors(outbox.sent_agents, self.settings)
            self.sent_at = now
        return list(dict.fromkeys([*_watched, *self.sent]))

    def _look(self, aid: str) -> tuple[Agent | None, bool]:
        agent = self.get_agent(aid)
        return agent, (bool(self.is_idle(agent)) if agent is not None else False)

    async def one(self, aid: str) -> None:
        # an ended agent's lookup reads the registry (sqlite): off the event loop
        agent, idle = await executors.run_collectors(self._look, aid)
        if agent is None:
            return
        now = self.clock()
        st = self.seen.setdefault(aid, _Seen())
        if aid not in _kicked and st.idle == idle and now - st.last < (IDLE_S if idle else RUN_S):
            return
        _kicked.discard(aid)
        st.idle, st.last = idle, now
        rev, st.cursor, done = await executors.run_collectors(sync_one, self.settings, agent, idle, st.cursor)
        for m in done:
            self._emit(OUTBOX_EVENT, {"agent_id": m.agent_id, "id": m.id, "state": m.state})
        if rev > st.rev:
            st.rev = rev
            self._emit(ITEMS_EVENT, {"agent_id": aid, "rev": rev})

    async def tick(self) -> bool:
        ids = await self.targets()
        for aid in ids:
            try:
                await self.one(aid)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("chatfeed %s failed: %s", aid, type(e).__name__)
        for aid in [a for a in self.seen if a not in ids]:
            self.seen.pop(aid, None)
        return bool(ids)

    async def run(self) -> None:
        while True:
            busy = False
            try:
                busy = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("chatfeed cycle failed: %s", type(e).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), TICK_S if busy else QUIET_S)
            self.wake.clear()


_F: Feed | None = None


def watch(agent_id: str) -> None:
    """A viewer is open on ``agent_id`` (call per items request). New watches sync at once."""
    new = agent_id not in _watched
    _watched[agent_id] = time.monotonic()
    _watched.move_to_end(agent_id)
    while len(_watched) > MAX_WATCH:
        _watched.popitem(last=False)
    if new:
        kick(agent_id)


def kick(agent_id: str) -> None:
    """Sync ``agent_id`` on the next pass (if it is watched or has a row typed in) and wake the loop."""
    _kicked.add(agent_id)
    if len(_kicked) > MAX_WATCH * 2:
        _kicked.intersection_update(_watched)
    if _F is not None:
        _F.wake.set()


def watched() -> list[str]:
    return list(_watched)


def start(app, *, is_idle: IsIdle, get_agent: GetAgent, publish: Publish | None = None,
          settings: Settings | None = None) -> asyncio.Task:
    """Start the feed from the app lifespan (``app.state.chatfeed_task``)."""
    global _F
    settings = settings or app.state.settings
    _F = Feed(settings, is_idle, get_agent, publish)
    app.state.chatfeed_task = asyncio.create_task(_F.run(), name="pm-chatfeed")
    return app.state.chatfeed_task


async def stop(app) -> None:
    global _F
    task = getattr(app.state, "chatfeed_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        app.state.chatfeed_task = None
    _F = None
