"""pm.agents.deliver: background delivery of outbox rows. ``send_message`` is faked: no tmux at all."""

import asyncio
import dataclasses
import logging
import time
from types import SimpleNamespace

import pytest

from pm.agents import chatfeed, deliver
from pm.agents import message as msg
from pm.chat import outbox
from pm.core.config import Settings
from pm.core.errors import Conflict, NotFound
from pm.core.models import Agent, AgentKind, AgentState

SECRET = "needle-text-xyz"


@pytest.fixture
def s(tmp_path):
    return Settings(state_dir=tmp_path / "state", tmux_socket="pmtest-unused")


class World:
    """Fake agent states + a recording send_message."""

    def __init__(self, monkeypatch):
        self.state = {"claude-1": AgentState.IDLE}
        self.sent: list[tuple[str, str]] = []
        self.fail: BaseException | None = None
        self.submitted = True
        self.events: list[tuple[str, dict]] = []
        self.now = 1000.0
        monkeypatch.setattr(msg, "send_message", self.send)
        monkeypatch.setattr(chatfeed, "_watched", type(chatfeed._watched)())

    def send(self, settings, agent, text, **kw):
        if self.fail is not None:
            raise self.fail
        self.sent.append((agent.id, text))
        return msg.SendResult(True, agent.id, agent.pane_id, len(text), submitted=self.submitted)

    def get_agent(self, aid):
        st = self.state.get(aid)
        if st is None:
            return None
        return Agent(id=aid, kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=1, cwd="/", state=st)

    def is_idle(self, a):
        return self.state.get(a.id) in (AgentState.IDLE, AgentState.WAITING)

    def deliverer(self, s):
        return deliver.Deliverer(s, self.is_idle, self.get_agent, lambda t, d: self.events.append((t, d)),
                                 clock=lambda: self.now)


def run(coro):
    return asyncio.run(coro)


def states(events):
    return [d["state"] for t, d in events if t == deliver.EVENT]


def test_idle_agent_gets_the_message(s, monkeypatch):
    w = World(monkeypatch)
    d = w.deliverer(s)
    m = outbox.add(s, "claude-1", "hello")
    assert run(d.tick()) is True
    assert w.sent == [("claude-1", "hello")]
    assert outbox.get(s, m.id).state == "inpane"
    assert states(w.events) == ["sending", "inpane"]
    assert w.events[0][1] == {"agent_id": "claude-1", "id": m.id, "state": "sending"}
    assert "claude-1" in chatfeed.watched()  # the feed ingests soon to match it


def test_busy_agent_waits_then_delivers_after_turn(s, monkeypatch):
    w = World(monkeypatch)
    w.state["claude-1"] = AgentState.RUNNING
    d = w.deliverer(s)
    m = outbox.add(s, "claude-1", "later")
    run(d.tick())
    assert w.sent == [] and outbox.get(s, m.id).state == "wait"
    run(d.tick())  # still busy: no second "wait" event
    assert states(w.events) == ["wait"]
    w.state["claude-1"] = AgentState.IDLE
    run(d.tick())
    assert w.sent == [("claude-1", "later")] and outbox.get(s, m.id).state == "inpane"


def test_waiting_prompt_is_never_typed_into(s, monkeypatch):
    w = World(monkeypatch)
    w.state["claude-1"] = AgentState.WAITING
    d = w.deliverer(s)
    m = outbox.add(s, "claude-1", "x")
    run(d.tick())
    assert w.sent == [] and outbox.get(s, m.id).state == "wait"


def test_one_message_per_turn(s, monkeypatch):
    w = World(monkeypatch)
    d = w.deliverer(s)
    a = outbox.add(s, "claude-1", "one")
    b = outbox.add(s, "claude-1", "two")
    run(d.tick())
    assert [t for _, t in w.sent] == ["one"]
    run(d.tick())  # idle again, but the agent never went busy: hold
    assert [t for _, t in w.sent] == ["one"] and outbox.get(s, b.id).state == "wait"
    w.state["claude-1"] = AgentState.RUNNING
    run(d.tick())
    assert [t for _, t in w.sent] == ["one"]
    w.state["claude-1"] = AgentState.IDLE
    run(d.tick())
    assert [t for _, t in w.sent] == ["one", "two"]
    assert outbox.get(s, a.id).state == outbox.get(s, b.id).state == "inpane"


def test_hold_times_out(s, monkeypatch):
    w = World(monkeypatch)
    d = w.deliverer(s)
    outbox.add(s, "claude-1", "one")
    outbox.add(s, "claude-1", "two")
    run(d.tick())
    run(d.tick())
    assert len(w.sent) == 1
    w.now += deliver.HOLD_S + 1
    run(d.tick())
    assert [t for _, t in w.sent] == ["one", "two"]
    run(d.tick())
    assert run(d.tick()) is True  # second hold still known
    w.now += deliver.HOLD_S + 1
    run(d.tick())
    assert d.holds == {} and run(d.tick()) is False


def test_failure_marks_failed_and_retry_redelivers(s, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    w = World(monkeypatch)
    w.fail = msg.MessageError("send failed: pane lost")
    w.fail.__cause__ = msg.NotAnAgent("pane not found")
    d = w.deliverer(s)
    m = outbox.add(s, "claude-1", SECRET)
    run(d.tick())
    f = outbox.get(s, m.id)
    assert f.state == "failed" and f.error == "pane gone" and f.tries == 1
    assert states(w.events) == ["sending", "failed"]
    assert SECRET not in caplog.text
    run(d.tick())
    assert outbox.get(s, m.id).state == "failed"  # failed is not retried by itself
    w.fail = None
    outbox.set_state(s, m.id, "saved")
    run(d.tick())
    assert outbox.get(s, m.id).state == "inpane" and w.sent == [("claude-1", SECRET)]


def test_unexpected_error_is_short(s, monkeypatch):
    w = World(monkeypatch)
    w.fail = RuntimeError(SECRET)
    d = w.deliverer(s)
    m = outbox.add(s, "claude-1", "x")
    run(d.tick())
    f = outbox.get(s, m.id)
    assert f.state == "failed" and f.error == "send failed" and SECRET not in f.error


def test_not_confirmed(s, monkeypatch):
    w = World(monkeypatch)
    w.submitted = False
    d = w.deliverer(s)
    m = outbox.add(s, "claude-1", "x")
    run(d.tick())
    r = outbox.get(s, m.id)
    assert r.state == "inpane" and r.error == "not confirmed"


def test_gone_agent(s, monkeypatch):
    w = World(monkeypatch)
    w.state.clear()
    d = w.deliverer(s)
    fresh = outbox.add(s, "claude-9", "x")
    run(d.tick())
    assert outbox.get(s, fresh.id).state == "saved"
    old = outbox.add(s, "claude-8", "y", now=time.time() - deliver.GONE_S - 5)
    run(d.tick())
    assert outbox.get(s, old.id).state == "saved"  # pm just started: the first agent scan may not be in yet
    d.started = time.time() - deliver.GONE_S - 5
    run(d.tick())
    g = outbox.get(s, old.id)
    assert g.state == "failed" and g.error == "agent gone"
    assert w.sent == []


def test_cancelled_meanwhile_is_not_sent(s, monkeypatch):
    w = World(monkeypatch)
    d = w.deliverer(s)
    m = outbox.add(s, "claude-1", "x")
    outbox.set_state(s, m.id, "cancelled")
    run(d.type_in(w.get_agent("claude-1"), m, "inpane"))
    assert w.sent == [] and outbox.get(s, m.id).state == "cancelled"


def test_send_now(s, monkeypatch):
    w = World(monkeypatch)
    w.state["claude-1"] = AgentState.RUNNING
    a = outbox.add(s, "claude-1", "now please")
    run(deliver.Deliverer(s, w.is_idle, w.get_agent).tick())
    assert outbox.get(s, a.id).state == "wait"
    agent = w.get_agent("claude-1")
    with pytest.raises(Conflict, match="deliverer not running"):  # no stand-in with its own idle rule
        run(deliver.send_now(s, agent, a.id))
    assert outbox.get(s, a.id).state == "wait" and w.sent == []
    monkeypatch.setattr(deliver, "_D", w.deliverer(s))
    r = run(deliver.send_now(s, agent, a.id))
    assert r.state == "queued" and w.sent == [("claude-1", "now please")]
    with pytest.raises(Conflict):
        run(deliver.send_now(s, agent, a.id))
    other = outbox.add(s, "claude-2", "not yours")
    with pytest.raises(NotFound):
        run(deliver.send_now(s, agent, other.id))
    with pytest.raises(NotFound):
        run(deliver.send_now(s, agent, 9999))
    idle = outbox.add(s, "claude-1", "idle now")
    w.state["claude-1"] = AgentState.IDLE
    r = run(deliver.send_now(s, dataclasses.replace(agent, state=AgentState.IDLE), idle.id))
    assert r.state == "inpane"


def test_send_now_uses_running_deliverer(s, monkeypatch):
    w = World(monkeypatch)
    w.state["claude-1"] = AgentState.RUNNING

    async def go():
        app = SimpleNamespace(state=SimpleNamespace(settings=s, hub=None))
        deliver.start(app, is_idle=w.is_idle, get_agent=w.get_agent, publish=lambda t, d: w.events.append((t, d)))
        try:
            m = outbox.add(s, "claude-1", "q")
            deliver.kick("claude-1")
            for _ in range(50):
                if outbox.get(s, m.id).state == "wait":
                    break
                await asyncio.sleep(0.02)
            r = await deliver.send_now(s, w.get_agent("claude-1"), m.id)
            return m, r, app
        finally:
            await deliver.stop(app)

    m, r, app = run(go())
    assert r.state == "queued" and states(w.events) == ["wait", "sending", "queued"]
    assert app.state.deliver_task is None and deliver._D is None


def test_kick_without_loop_is_harmless():
    deliver.kick("claude-1")


def test_loop_survives_errors(s, monkeypatch):
    w = World(monkeypatch)
    calls = []

    def boom(settings):
        calls.append(1)
        raise RuntimeError("db down")

    monkeypatch.setattr(outbox, "due_agents", boom)
    monkeypatch.setattr(deliver, "IDLE_TICK_S", 0.01)

    async def go():
        app = SimpleNamespace(state=SimpleNamespace(settings=s))
        deliver.start(app, is_idle=w.is_idle, get_agent=w.get_agent)
        await asyncio.sleep(0.1)
        await deliver.stop(app)

    run(go())
    assert len(calls) >= 2
