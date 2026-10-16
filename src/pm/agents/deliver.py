"""Background deliverer of the composer outbox (``pm.chat.outbox``).

The message POST only stores a row and calls ``kick``. This lifespan task sends it: per agent the
oldest ``saved``/``wait`` row goes in when the agent is idle (``message.send_message`` on the tmux
pool, -> ``inpane``), else ``saved`` becomes ``wait`` until the turn ends. One message at a time:
after one went in, the next waits until the agent was busy and idle again (or ``HOLD_S``).
``send_now`` types a row into the agent's own queue while it is busy (-> ``queued``).

``is_idle``, ``get_agent`` and ``publish`` are injected from ``web/app.py`` (agents may not import
notify/web). Every state change publishes hub event ``chat_outbox`` ``{agent_id, id, state}``.
Message text is never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pm.agents import chatfeed
from pm.agents import message as msg
from pm.chat import outbox
from pm.core import executors
from pm.core.config import Settings
from pm.core.errors import Conflict, NotFound
from pm.core.models import Agent, AgentState, OutboxMsg

log = logging.getLogger(__name__)

EVENT = "chat_outbox"
HOLD_S = 30.0  # after a send: wait for busy -> idle, at most this long, before the next one
GONE_S = 60.0  # a due row of an agent pm does not see for this long fails ("agent gone"); counted from
# the later of the row's last change and this deliverer's start (the first agent scan comes after a restart)
BUSY_TICK_S = 1.0  # cadence while something is due or held
IDLE_TICK_S = 15.0  # safety poll when nothing is known to be due (a kick wakes the loop at once)

IsIdle = Callable[[Agent], bool]
GetAgent = Callable[[str], Agent | None]
Publish = Callable[[str, dict], object]


@dataclass
class _Hold:
    since: float  # monotonic time of the send
    busy: bool = False  # the agent was seen busy since


@dataclass
class Deliverer:
    settings: Settings
    is_idle: IsIdle
    get_agent: GetAgent
    publish: Publish | None = None
    clock: Callable[[], float] = time.monotonic
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    holds: dict[str, _Hold] = field(default_factory=dict)
    started: float = field(default_factory=time.time)  # wall clock, like OutboxMsg.updated

    def changed(self, m: OutboxMsg) -> None:
        if self.publish is None:
            return
        try:
            self.publish(EVENT, {"agent_id": m.agent_id, "id": m.id, "state": m.state})
        except Exception:  # noqa: BLE001 - notification is best effort
            pass

    async def _set(self, oid: int, state: str, **kw) -> OutboxMsg | None:
        """Transition on the collectors pool; None when the row moved on meanwhile (cancel, match)."""
        try:
            m = await executors.run_collectors(outbox.set_state, self.settings, oid, state, **kw)
        except (Conflict, NotFound):
            return None
        self.changed(m)
        return m

    def _held(self, agent: Agent) -> bool:
        """True while the previous send of this agent has not finished its turn."""
        h = self.holds.get(agent.id)
        if h is None:
            return False
        if not self.is_idle(agent):
            h.busy = True
            return True
        if h.busy or self.clock() - h.since >= HOLD_S:
            self.holds.pop(agent.id, None)
            return False
        return True

    async def type_in(self, agent: Agent, m: OutboxMsg, done_state: str) -> OutboxMsg | None:
        """``sending`` -> type + Enter -> ``done_state`` (``inpane`` / ``queued``), or ``failed``."""
        if await self._set(m.id, "sending") is None:
            return None
        try:
            res = await executors.run_tmux(msg.send_message, self.settings, agent, m.text)
        except msg.MessageError as e:
            log.info("outbox %d: send failed (%s)", m.id, type(e).__name__)
            return await self._set(m.id, "failed", error=_short(e))
        except Exception as e:  # noqa: BLE001 - never kill the loop; type only, never text
            log.warning("outbox %d: send error %s", m.id, type(e).__name__)
            return await self._set(m.id, "failed", error="send failed")
        self.holds[agent.id] = _Hold(self.clock())
        chatfeed.watch(agent.id)  # ingest soon, so the row is matched and becomes done
        return await self._set(m.id, done_state, error="" if res.submitted else "not confirmed")

    async def step(self, agent_id: str) -> None:
        """One decision for one agent."""
        m = await executors.run_collectors(outbox.next_due, self.settings, agent_id)
        agent = self.get_agent(agent_id)
        if agent is None:
            self.holds.pop(agent_id, None)
            if m is not None and time.time() - max(m.updated, self.started) >= GONE_S:
                await self._set(m.id, "failed", error="agent gone")
            return
        if self._held(agent) or m is None:
            if m is not None and m.state == "saved":
                await self._set(m.id, "wait")
            return
        # a permission prompt / question is "waiting": never type a queued message into it
        if not self.is_idle(agent) or agent.state is AgentState.WAITING:
            if m.state == "saved":
                await self._set(m.id, "wait")
            return
        await self.type_in(agent, m, "inpane")

    async def tick(self) -> bool:
        """One pass over every agent with due rows or a held send. True when there was work."""
        due = set(await executors.run_collectors(outbox.due_agents, self.settings))
        ids = due | set(self.holds)
        for aid in sorted(ids):
            try:
                await self.step(aid)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("deliver %s failed: %s", aid, type(e).__name__)
        return bool(ids)

    async def run(self) -> None:
        while True:
            busy = False
            try:
                busy = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("deliver cycle failed: %s", type(e).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), BUSY_TICK_S if busy else IDLE_TICK_S)
            self.wake.clear()


def _short(e: BaseException) -> str:
    """Short, content-free reason for the bubble ("pane gone", "send failed: tmux: ...")."""
    if isinstance(e, msg.NotAnAgent) or isinstance(e.__cause__, msg.NotAnAgent):
        return "pane gone"
    return msg.describe(e)[:60]


_D: Deliverer | None = None


def kick(agent_id: str) -> None:
    """Wake the deliverer (a row of ``agent_id`` was stored or retried). Never blocks."""
    if _D is not None:
        _D.wake.set()


async def send_now(settings: Settings, agent: Agent, oid: int) -> OutboxMsg:
    """Type a ``saved``/``wait``/``failed`` row in at once: into the agent's own queue while it is busy
    (-> ``queued``), or as a normal send when it is idle (-> ``inpane``).

    Raises NotFound / Conflict (row gone, not this agent's, not sendable any more, or the deliverer is
    not running: its ``is_idle`` is the one idle definition)."""
    d = _D
    if d is None:
        raise Conflict("deliverer not running")
    m = await executors.run_collectors(outbox.get, settings, oid)
    if m is None or m.agent_id != agent.id:
        raise NotFound("no such message")
    if m.state not in ("saved", "wait", "failed"):
        raise Conflict(f"message is {m.state}")
    res = await d.type_in(agent, m, "inpane" if d.is_idle(agent) else "queued")
    if res is None:
        raise Conflict("message changed meanwhile")
    return res


def start(app, *, is_idle: IsIdle, get_agent: GetAgent, publish: Publish | None = None,
          settings: Settings | None = None) -> asyncio.Task:
    """Start the deliverer from the app lifespan (``app.state.deliver_task``). ``publish`` defaults to
    ``app.state.hub.publish``."""
    global _D
    settings = settings or app.state.settings
    hub = getattr(app.state, "hub", None)
    publish = publish or (hub.publish if hub is not None else None)
    _D = Deliverer(settings, is_idle, get_agent, publish)
    app.state.deliver_task = asyncio.create_task(_D.run(), name="pm-deliver")
    return app.state.deliver_task


async def stop(app) -> None:
    global _D
    task = getattr(app.state, "deliver_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        app.state.deliver_task = None
    _D = None
