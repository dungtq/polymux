"""In-memory event hub, agent state tracker and the poll loop.

Payloads carry agent id / state / project / kind / since / label. ``agent_state`` and ``agent_added`` may also
carry the agent's ``unread`` (unread ntfy messages, int) and ``last`` (``{"text", "src": "user" | "ntfy", "ts"}``,
one masked line of at most 120 characters, or null): added by ``with_live`` (``notify.agentlive``) when a client
is listening and they are ready within ``agentlive.TIMEOUT_S``, else the payload goes out without them. ``inbox``
events: ``{"waiting", "ids"}`` (tracker), ``{"read": 1}`` (read state changed), ``{"ntfy": 1}`` (feed changed),
or ``{"ntfy": 1, "agent", "unread", "last"}`` (an agent's new ntfy message, ``feed._flush``). Pane text and
error lines never enter here.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import hmac
import logging
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from pm.core import executors
from pm.core.config import Settings
from pm.core.models import Agent, AgentState
from pm.infra import snapshot as services
from pm.notify import agentlive
from pm.notify import ntfy as notify

log = logging.getLogger(__name__)

AGENT_STATE = "agent_state"
AGENT_ADDED = "agent_added"
AGENT_REMOVED = "agent_removed"
INBOX = "inbox"
UI = "ui"  # a theme's own UI state changed: {"area": str, "rev": int} (e.g. open tabs)
EVENT_TYPES = frozenset({AGENT_STATE, AGENT_ADDED, AGENT_REMOVED, INBOX, UI})

HOOK_STATES = {
    "Notification": AgentState.WAITING,
    "Stop": AgentState.IDLE,
    "UserPromptSubmit": AgentState.RUNNING,
}
HOOK_HOLD_S = 8.0
MAX_SSE_TOTAL = 50
STALE_SUB_S = 90.0  # a live stream touches `seen` at least every heartbeat (15 s)
MAX_SSE_PER_CLIENT = 12  # one person may keep many tabs open (seen: 11 from one IP)
IDLE_POLL_S = 5.0  # poll cadence after IDLE_AFTER_S without any client demand
Clock = Callable[[], float]


# ------------------------------------------------------------------ hub
@dataclass(frozen=True)
class Event:
    id: int
    type: str
    data: dict


class HubFull(Exception):
    """No room for another stream; ``.scope`` is "total" or "client"."""

    def __init__(self, scope: str = "total"):
        super().__init__(scope)
        self.scope = scope


class Subscription:
    def __init__(self, hub: Hub, maxsize: int, client: str = ""):
        self.hub = hub
        self.client = client
        self.queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize)
        self.dropped = False
        self.closed = False
        self.evicted = False
        self.seen = time.monotonic()  # last sign of life from the stream generator (reap)

    async def get(self, timeout: float | None = None) -> Event | None:
        """Next event; None on timeout, drop or hub close (check `.done`)."""
        try:
            return await asyncio.wait_for(self.queue.get(), timeout)
        except TimeoutError:
            return None

    @property
    def done(self) -> bool:
        return self.dropped or self.closed or self.evicted

    def close(self) -> None:
        self.hub._drop(self)


class Hub:
    def __init__(self, *, ring: int = 256, queue_size: int = 64, max_subscribers: int = MAX_SSE_TOTAL,
                 max_per_client: int = MAX_SSE_PER_CLIENT):
        self._ring: collections.deque[Event] = collections.deque(maxlen=ring)
        self._subs: dict[Subscription, None] = {}  # insertion order = age (oldest first)
        self._per_client: dict[str, int] = {}
        self._next = 1
        self.queue_size = queue_size
        self.max_subscribers = max_subscribers
        self.max_per_client = max_per_client
        self.closed = False

    def _drop(self, sub: Subscription) -> None:
        if sub in self._subs:
            self._subs.pop(sub, None)
            n = self._per_client.get(sub.client, 0) - 1
            if n > 0:
                self._per_client[sub.client] = n
            else:
                self._per_client.pop(sub.client, None)

    @property
    def last_id(self) -> int:
        return self._next - 1

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def publish(self, type_: str, data: dict) -> Event:
        ev = Event(self._next, type_, dict(data))
        self._next += 1
        self._ring.append(ev)
        for sub in list(self._subs):
            try:
                sub.queue.put_nowait(ev)
            except asyncio.QueueFull:
                sub.dropped = True  # slow consumer: drop, it reconnects with Last-Event-ID
                self._drop(sub)
                with contextlib.suppress(asyncio.QueueFull):
                    sub.queue.get_nowait()
                    sub.queue.put_nowait(None)
        return ev

    def replay(self, last_id: int | None) -> list[Event]:
        if last_id is None:
            return []
        return [e for e in self._ring if e.id > last_id]

    def _evict(self, sub: Subscription) -> None:
        """End ``sub`` (the stream sends a final ``bye``) and release its slots at once."""
        sub.evicted = True
        self._drop(sub)
        with contextlib.suppress(asyncio.QueueFull, asyncio.QueueEmpty):
            while True:
                sub.queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            sub.queue.put_nowait(None)

    def reap(self, stale_s: float = STALE_SUB_S) -> int:
        """Drop subscriptions whose generator never ran / stopped answering (belt and braces
        next to the try/finally in the route). Returns how many were reaped."""
        now = time.monotonic()
        dead = [s for s in self._subs if now - s.seen > stale_s]
        for s in dead:
            s.closed = True
            self._drop(s)
        return len(dead)

    def subscribe(self, last_id: int | None = None, client: str = "") -> tuple[Subscription, list[Event]]:
        """Register and return (subscription, backlog). Backlog + queue never overlap or gap.
        At the per-client cap the client's OLDEST stream is evicted (a new tab always wins).
        Raises HubFull when the hub is closed, globally full, or nothing of ``client`` can go."""
        self.reap()
        if self.closed or len(self._subs) >= self.max_subscribers:
            raise HubFull("total")
        if client:
            while self._per_client.get(client, 0) >= self.max_per_client:
                old = next((s for s in self._subs if s.client == client), None)
                if old is None:
                    raise HubFull("client")
                self._evict(old)
        sub = Subscription(self, self.queue_size, client)
        backlog = self.replay(last_id)
        self._subs[sub] = None
        self._per_client[client] = self._per_client.get(client, 0) + 1
        return sub, backlog

    def close(self) -> None:
        self.closed = True
        for sub in list(self._subs):
            sub.closed = True
            with contextlib.suppress(asyncio.QueueFull):
                sub.queue.put_nowait(None)
        self._subs.clear()
        self._per_client.clear()


# ------------------------------------------------------------------ tracker
def _public(a: Agent) -> dict:
    return {"id": a.id, "state": str(a.state), "project": a.project, "kind": str(a.kind),
            "since": a.state_since, "label": a.label or a.id}


class AgentTracker:
    """Merge poll results with hook events; emit only on change."""

    def __init__(self, clock: Clock = time.time, hold_s: float = HOOK_HOLD_S):
        self.clock = clock
        self.hold_s = hold_s
        self._agents: dict[str, Agent] = {}
        self._hooks: dict[tuple[str, str], tuple[AgentState, float]] = {}
        self._waiting: frozenset[str] | None = None

    @property
    def agents(self) -> list[Agent]:
        return list(self._agents.values())

    def _hook_for(self, a: Agent, now: float) -> AgentState | None:
        for key in (("sid", a.session_uuid or ""), ("cwd", a.cwd)):
            hit = self._hooks.get(key)
            if hit and now < hit[1]:
                return hit[0]
        return None

    def _match(self, session_id: str | None, cwd: str | None) -> list[Agent]:
        if session_id:
            m = [a for a in self._agents.values() if a.session_uuid == session_id]
            if m:
                return m
        if cwd:
            return [a for a in self._agents.values() if a.cwd == cwd]
        return []

    def _inbox(self) -> list[tuple[str, dict]]:
        waiting = frozenset(i for i, a in self._agents.items() if a.state == AgentState.WAITING)
        if waiting == self._waiting:
            return []
        first = self._waiting is None
        self._waiting = waiting
        if first and not waiting:
            return []
        return [(INBOX, {"waiting": len(waiting), "ids": sorted(waiting)})]

    def update(self, polled: list[Agent]) -> list[tuple[str, dict]]:
        now = self.clock()
        out: list[tuple[str, dict]] = []
        new: dict[str, Agent] = {}
        for a in polled:
            old = self._agents.get(a.id)
            state = self._hook_for(a, now) or a.state
            if old is None:
                since = a.state_since  # real source only (Claude json); 0 = unknown, never "first seen"
            elif old.state == state:
                since = old.state_since or a.state_since
            else:
                since = now
            active = max(a.last_active, old.last_active if old else 0.0)
            m = replace(a, state=state, state_since=since, last_active=active)
            new[a.id] = m
            if old is None:
                out.append((AGENT_ADDED, _public(m)))
            elif old.state != m.state:
                out.append((AGENT_STATE, _public(m)))
        for aid, old in self._agents.items():
            if aid not in new:
                out.append((AGENT_REMOVED, {"id": aid, "project": old.project, "label": old.label or aid}))
        self._agents = new
        self._hooks = {k: v for k, v in self._hooks.items() if v[1] > now}
        return out + self._inbox()

    def apply_hook(self, event: str, session_id: str | None, cwd: str | None) -> list[tuple[str, dict]]:
        state = HOOK_STATES.get(event)
        if state is None or not (session_id or cwd):
            return []
        now = self.clock()
        if session_id:
            self._hooks[("sid", session_id)] = (state, now + self.hold_s)
        if cwd:
            self._hooks[("cwd", cwd)] = (state, now + self.hold_s)
        out: list[tuple[str, dict]] = []
        for a in self._match(session_id, cwd):
            if a.state != state:
                m = replace(a, state=state, state_since=now)
                self._agents[a.id] = m
                out.append((AGENT_STATE, _public(m)))
        return out + (self._inbox() if out else [])


def publish_all(hub: Hub, events: list[tuple[str, dict]]) -> None:
    for t, d in events:
        hub.publish(t, d)


LIVE_TYPES = (AGENT_STATE, AGENT_ADDED)


async def with_live(settings: Settings, hub: Hub, agents, evs: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    """``evs`` with ``unread`` / ``last`` (``agentlive.build``) added to the agent_state / agent_added payloads.
    Unchanged when no client is listening, or when the extras fail or take over ``agentlive.TIMEOUT_S``."""
    ids = {d.get("id") for t, d in evs if t in LIVE_TYPES}
    if not ids or hub.subscriber_count == 0:
        return evs
    want = [a for a in agents if a.id in ids]
    try:
        extra = await asyncio.wait_for(executors.run_collectors(agentlive.build, settings, want), agentlive.TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - timeout included; type only, never text
        log.info("live extras skipped: %s", type(exc).__name__)
        return evs
    return [(t, {**d, **extra[d["id"]]}) if t in LIVE_TYPES and d.get("id") in extra else (t, d) for t, d in evs]


# ------------------------------------------------------------------ poll loop
def next_interval(settings: Settings, hub: Hub | None, tracker: AgentTracker | None) -> float:
    """Seconds until the next poll: the configured cadence while a client is connected
    (SSE), a page or API call was served in the last minute, or an agent is waiting;
    IDLE_POLL_S (never faster than the configured one) when nobody asked for a minute."""
    base = float(settings.poll_interval_s)
    if hub is not None and hub.subscriber_count > 0:
        return base
    if not services.is_idle():
        return base
    try:
        if tracker is not None and any(a.state == AgentState.WAITING for a in tracker.agents):
            return base
    except Exception:  # noqa: BLE001
        pass
    return max(base, IDLE_POLL_S)


async def _notify_all(settings: Settings, changes: list[tuple[Agent, AgentState | None, AgentState]]) -> None:
    for a, old, new in changes:  # httpx POST per change: off the poll path, own pool
        try:
            await executors.run_collectors(notify.notify_state_change, settings, a, old, new)
        except Exception as exc:  # noqa: BLE001
            log.warning("notify failed: %s", type(exc).__name__)


async def poll_once(settings: Settings, hub: Hub, tracker: AgentTracker, seeded: bool,
                    bg: set | None = None) -> None:
    """One producer cycle: build + publish the snapshot on the dedicated snapshot thread,
    then update the tracker (events) on the loop. Notifications go to a background task."""
    snap = await executors.run_snapshot(services.build_and_publish, settings)
    before = {a.id: a.state for a in tracker.agents}
    evs = tracker.update(list(snap.agents))
    publish_all(hub, await with_live(settings, hub, tracker.agents, evs))
    if seeded:
        changes = [(a, before.get(a.id), a.state) for a in tracker.agents if before.get(a.id) != a.state]
        if changes:
            t = asyncio.create_task(_notify_all(settings, changes), name="pm-notify")
            if bg is not None:
                bg.add(t)
                t.add_done_callback(bg.discard)


async def poll_loop(settings: Settings, hub: Hub, tracker: AgentTracker) -> None:
    seeded = False  # first poll only seeds state: no ntfy burst after a restart
    bg: set = set()
    while True:
        t0 = time.monotonic()
        try:
            await poll_once(settings, hub, tracker, seeded, bg)
            seeded = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never crash; type only, never text
            log.warning("poll failed: %s", type(exc).__name__)
        # sleep in short ticks so a page/SSE after a quiet spell shortens the wait at once
        while True:
            wait = next_interval(settings, hub, tracker) - (time.monotonic() - t0)
            if wait <= 0:
                break
            await asyncio.sleep(min(wait, 0.25))


def start(app, settings: Settings | None = None) -> asyncio.Task:
    """Call from the app lifespan. Sets app.state.hub / tracker / poll_task."""
    settings = settings or app.state.settings
    st = app.state
    if getattr(st, "hub", None) is None:
        st.hub = Hub()
    if getattr(st, "tracker", None) is None:
        st.tracker = AgentTracker()
    ensure_hook_secret(settings)
    st.poll_task = asyncio.create_task(poll_loop(settings, st.hub, st.tracker), name="pm-poll")
    return st.poll_task


async def stop(app) -> None:
    task = getattr(app.state, "poll_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        app.state.poll_task = None
    hub = getattr(app.state, "hub", None)
    if hub:
        hub.close()


# ------------------------------------------------------------------ hook secret
def hook_secret_path(settings: Settings) -> Path:
    return settings.state_dir / "hook_secret"


def ensure_hook_secret(settings: Settings) -> bytes:
    path = hook_secret_path(settings)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_urlsafe(32) + "\n")
        except FileExistsError:
            pass
    return path.read_text().strip().encode()


def verify_hook_secret(candidate: str | None, settings: Settings) -> bool:
    real = ensure_hook_secret(settings)
    if not candidate or not real:
        return False
    return hmac.compare_digest(candidate.strip().encode(), real)
