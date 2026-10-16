"""web/app.py ``_chat_access``: chatfeed sees an ended agent (registry stand-in) as idle; the deliverer's
live-only pair is unchanged. Real registry records; chatfeed runs with fake itemsync / items."""

import asyncio
import collections
from dataclasses import replace
from types import SimpleNamespace

from pm.agents import chatfeed, registry
from pm.chat import items, itemsync
from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, AgentState
from pm.web import app as webapp

REAL_REGISTRY = True


def _ag(state=AgentState.RUNNING):
    return Agent(id="claude-140", kind=AgentKind.CLAUDE, pane_id="%140", session_id="$1", pid=100, cwd="/x",
                 state=state, state_since=1.0, ident="sess-a", project="alpha")


def _setup(tmp_path, monkeypatch):
    s = Settings(state_dir=tmp_path / "state", tmux_socket="pmtest-unused")
    rec = registry.assign(s, [_ag()], now=1000.0)[0]
    registry.assign(s, [], complete=True, now=1100.0)  # it ended
    live: dict[str, Agent] = {}
    snap = SimpleNamespace(agent=lambda aid: live.get(aid), agents=[])
    monkeypatch.setattr(webapp.services, "published", lambda settings: snap)

    def get_agent(aid):
        return live.get(aid)

    def is_idle(a):
        cur = get_agent(a.id) or a
        return cur.state in (AgentState.IDLE, AgentState.WAITING)

    return s, rec.id, live, get_agent, is_idle


def test_ended_agent_is_the_registry_stand_in_and_idle(tmp_path, monkeypatch):
    s, aid, live, get_agent, is_idle = _setup(tmp_path, monkeypatch)
    chat_agent, chat_idle = webapp._chat_access(s, get_agent, is_idle)
    assert get_agent(aid) is None  # the deliverer never sees it
    a = chat_agent(aid)
    assert a is not None and a.id == aid and a.pid == 0 and a.project == "alpha"
    assert chat_idle(a) is True
    assert chat_agent("claude-unknown") is None
    live[aid] = replace(_ag(), id=aid)  # running again
    assert chat_agent(aid).pid == 100 and chat_idle(chat_agent(aid)) is False


def test_feed_ingests_an_ended_agent_as_idle(tmp_path, monkeypatch):
    s, aid, _live, get_agent, is_idle = _setup(tmp_path, monkeypatch)
    syncs = []
    monkeypatch.setattr(itemsync, "sync", lambda settings, agent, *, idle=False: syncs.append((agent.id, idle)))
    monkeypatch.setattr(items, "list_items", lambda *a, **k: [])
    monkeypatch.setattr(items, "latest", lambda settings, a: (3, 7))
    monkeypatch.setattr(chatfeed, "_watched", collections.OrderedDict())
    monkeypatch.setattr(chatfeed, "_kicked", set())
    events = []
    chat_agent, chat_idle = webapp._chat_access(s, get_agent, is_idle)
    f = chatfeed.Feed(s, chat_idle, chat_agent, lambda t, d: events.append((t, d)))
    chatfeed.watch(aid)
    assert asyncio.run(f.tick()) is True
    assert syncs == [(aid, True)]  # idle: the open turn gets its end item
    assert events == [("chat_items", {"agent_id": aid, "rev": 7})]
