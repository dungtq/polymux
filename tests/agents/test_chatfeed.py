"""pm.agents.chatfeed: the one ingest loop. itemsync / items are faked; the outbox is the real table."""

import asyncio
import collections
import time
from types import SimpleNamespace

import pytest

from pm.agents import chatfeed
from pm.chat import items, itemsync, outbox
from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, AgentState, ChatItem


@pytest.fixture
def s(tmp_path):
    return Settings(state_dir=tmp_path / "state", tmux_socket="pmtest-unused")


class Store:
    """Fake chat_items for one or more agents: list_items(after_rev) / latest / link_outbox."""

    def __init__(self, monkeypatch):
        self.items: dict[str, list[ChatItem]] = collections.defaultdict(list)
        self.rev = 0
        self.syncs: list[tuple[str, bool]] = []
        self.state = {"claude-1": AgentState.RUNNING}
        self.events: list[tuple[str, dict]] = []
        self.now = 0.0
        monkeypatch.setattr(itemsync, "sync", self.sync)
        monkeypatch.setattr(items, "list_items", self.list_items)
        monkeypatch.setattr(items, "latest", self.latest)
        monkeypatch.setattr(items, "link_outbox", self.link)
        monkeypatch.setattr(chatfeed, "_watched", collections.OrderedDict())
        monkeypatch.setattr(chatfeed, "_kicked", set())

    def add(self, aid, kind, text="", ts=0.0):
        self.rev += 1
        it = ChatItem(seq=self.rev, rev=self.rev, uid=f"u{self.rev}", conv="c", kind=kind, turn="", ts=ts, text=text)
        self.items[aid].append(it)
        return it

    def sync(self, settings, agent, *, idle=False):
        self.syncs.append((agent.id, idle))
        return self.latest(settings, agent.id)[1]

    def list_items(self, settings, aid, *, after_rev=None, before_seq=None, limit=200):
        rows = self.items[aid]
        if after_rev is None:
            return rows[-limit:]
        return sorted([i for i in rows if i.rev > after_rev], key=lambda i: i.rev)[:limit]

    def latest(self, settings, aid):
        rows = self.items[aid]
        return (max((i.seq for i in rows), default=0), max((i.rev for i in rows), default=0))

    def link(self, settings, aid, seq, oid):
        self.rev += 1
        self.items[aid] = [i if i.seq != seq else ChatItem(**{**i.__dict__, "outbox": oid, "rev": self.rev})
                           for i in self.items[aid]]
        return True

    def get_agent(self, aid):
        st = self.state.get(aid)
        return None if st is None else Agent(id=aid, kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1",
                                             pid=1, cwd="/", state=st)

    def is_idle(self, a):
        return self.state[a.id] in (AgentState.IDLE, AgentState.WAITING)

    def feed(self, s):
        return chatfeed.Feed(s, self.is_idle, self.get_agent, lambda t, d: self.events.append((t, d)),
                             clock=lambda: self.now)


def run(coro):
    return asyncio.run(coro)


def sent_row(s, aid, text, now):
    m = outbox.add(s, aid, text, now=now)
    outbox.set_state(s, m.id, "sending", now=now)
    return outbox.set_state(s, m.id, "inpane", now=now)


def test_unwatched_agents_are_not_synced(s, monkeypatch):
    st = Store(monkeypatch)
    f = st.feed(s)
    assert run(f.tick()) is False and st.syncs == []


def test_watch_syncs_and_publishes_rev(s, monkeypatch):
    st = Store(monkeypatch)
    st.add("claude-1", "text", "hi")
    f = st.feed(s)
    chatfeed.watch("claude-1")
    assert run(f.tick()) is True
    assert st.syncs == [("claude-1", False)]
    assert st.events == [("chat_items", {"agent_id": "claude-1", "rev": 1})]
    run(f.tick())  # within RUN_S: no second sync, nothing published
    assert len(st.syncs) == 1 and len(st.events) == 1
    st.now += chatfeed.RUN_S
    run(f.tick())
    assert len(st.syncs) == 2 and len(st.events) == 1  # rev did not grow
    st.add("claude-1", "tool")
    st.now += chatfeed.RUN_S
    run(f.tick())
    assert st.events[-1] == ("chat_items", {"agent_id": "claude-1", "rev": 2})


def test_idle_cadence_flip_and_kick(s, monkeypatch):
    st = Store(monkeypatch)
    st.state["claude-1"] = AgentState.IDLE
    f = st.feed(s)
    chatfeed.watch("claude-1")
    run(f.tick())
    assert st.syncs == [("claude-1", True)]
    st.now += chatfeed.RUN_S * 2
    run(f.tick())
    assert len(st.syncs) == 1  # idle: IDLE_S cadence
    st.state["claude-1"] = AgentState.RUNNING  # idle flip: at once
    run(f.tick())
    assert st.syncs[-1] == ("claude-1", False)
    chatfeed.kick("claude-1")
    run(f.tick())
    assert len(st.syncs) == 3
    st.state["claude-1"] = AgentState.IDLE
    st.now += 0.1
    run(f.tick())
    assert st.syncs[-1] == ("claude-1", True)  # turn end: sync(idle=True)


def test_watch_expires_and_is_bounded(s, monkeypatch):
    st = Store(monkeypatch)
    f = st.feed(s)
    chatfeed.watch("claude-1")
    chatfeed._watched["claude-1"] -= chatfeed.WATCH_TTL_S + 1
    f.clock = time.monotonic
    assert run(f.tick()) is False and st.syncs == []
    for i in range(chatfeed.MAX_WATCH + 10):
        chatfeed.watch(f"a-{i}")
    assert len(chatfeed.watched()) == chatfeed.MAX_WATCH
    assert chatfeed.watched()[-1] == f"a-{chatfeed.MAX_WATCH + 9}"


def test_gone_agent_is_skipped(s, monkeypatch):
    st = Store(monkeypatch)
    f = st.feed(s)
    chatfeed.watch("ghost-1")
    run(f.tick())
    assert st.syncs == []


def test_links_user_item_to_outbox(s, monkeypatch):
    st = Store(monkeypatch)
    now = time.time()
    m = sent_row(s, "claude-1", "please  fix it", now)
    pre = st.add("claude-1", "user", "please fix it", ts=now - 3600)  # an old, identical prompt (no link)
    f = st.feed(s)
    chatfeed.watch("claude-1")
    run(f.tick())
    assert outbox.get(s, m.id).state == "inpane"
    assert all(i.outbox == 0 for i in st.items["claude-1"])
    it = st.add("claude-1", "user", "please fix it", ts=now + 1)
    st.now += chatfeed.RUN_S
    run(f.tick())
    done = outbox.get(s, m.id)
    assert done.state == "done" and done.item == it.seq
    linked = {i.seq: i.outbox for i in st.items["claude-1"]}
    assert linked == {pre.seq: 0, it.seq: m.id}
    assert ("chat_outbox", {"agent_id": "claude-1", "id": m.id, "state": "done"}) in st.events
    assert st.events[-1] == ("chat_items", {"agent_id": "claude-1", "rev": st.rev})  # rev after the link


def test_sent_rows_keep_an_agent_ingested_without_viewer(s, monkeypatch):
    st = Store(monkeypatch)
    now = time.time()
    m = sent_row(s, "claude-1", "queued one", now)
    st.add("claude-1", "user", "queued one", ts=now + 2)
    f = st.feed(s)
    run(f.tick())
    assert st.syncs == [("claude-1", False)] and outbox.get(s, m.id).state == "done"


def test_start_stop_and_loop_errors(s, monkeypatch):
    st = Store(monkeypatch)

    def boom(settings, agent, *, idle=False):
        raise RuntimeError("bad file")

    monkeypatch.setattr(itemsync, "sync", boom)

    async def go():
        app = SimpleNamespace(state=SimpleNamespace(settings=s))
        chatfeed.start(app, is_idle=st.is_idle, get_agent=st.get_agent, publish=lambda t, d: None)
        chatfeed.watch("claude-1")
        await asyncio.sleep(0.05)
        task = app.state.chatfeed_task
        assert not task.done()
        await chatfeed.stop(app)
        return app

    app = run(go())
    assert app.state.chatfeed_task is None and chatfeed._F is None


def test_mid_turn_prompt_marks_queued_row_done(s, tmp_path):
    """Send now -> Claude's queue -> a queued_command attachment in the JSONL: a user item, row done."""
    import json

    up = "/home/me/.local/state/pm/uploads/83666f669a429d0993333c84.png"
    msg = f"move the Edit button\n\n- to the top right\n- for every file\n{up}"
    m = outbox.add(s, "claude-1", msg)
    outbox.set_state(s, m.id, "sending")
    outbox.set_state(s, m.id, "queued")
    now = time.time()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now + 30))
    p = tmp_path / "11111111-2222-3333-4444-555555555555.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in [
        {"type": "user", "uuid": "u1", "timestamp": stamp, "message": {"role": "user", "content": "go"}},
        {"type": "queue-operation", "operation": "enqueue", "content": msg, "timestamp": stamp},
        {"type": "attachment", "uuid": "q1", "timestamp": stamp,
         "attachment": {"type": "queued_command", "prompt": msg, "commandMode": "prompt",
                        "origin": {"kind": "human"}, "humanTurn": True}},
    ]) + "\n")
    agent = Agent(id="claude-1", kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=0, cwd="/nowhere",
                  transcript=str(p))
    itemsync._SEEN.clear()
    _rev, _cursor, done = chatfeed.sync_one(s, agent, False, None)
    assert [d.id for d in done] == [m.id]
    got = outbox.get(s, m.id)
    q = next(i for i in items.list_items(s, "claude-1") if i.uid == "q1")
    assert got.state == "done" and got.item == q.seq and q.outbox == m.id
