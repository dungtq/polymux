import time
from types import SimpleNamespace as NS

import pytest

from pm.agents import inbox
from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, AgentState, NtfyItem
from pm.notify import agentlive, mutes
from pm.notify import owner as ntfy_owner

NOW = time.time()


@pytest.fixture
def s(tmp_path):
    return Settings(state_dir=tmp_path)


def ag(i):
    return Agent(id=i, kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=1, cwd="/x",
                 state=AgentState.IDLE)


def it(n, title, **kw):
    return NtfyItem(id=f"n{n}", time=NOW - 100 + n, title=title, message=kw.pop("message", f"msg {n}"), **kw)


def _feed(monkeypatch, items, owners):
    """recent() -> items; the title names its owner via ``owners``; build_index calls are counted."""
    calls = []
    monkeypatch.setattr(ntfy_owner.ntfy_feed, "recent", lambda settings, project_path=None: list(items))
    monkeypatch.setattr(ntfy_owner, "build_index", lambda settings, since: calls.append(since) or [])
    monkeypatch.setattr(ntfy_owner, "title_owner",
                        lambda settings, title, recs=None: NS(agent_id=owners[title]) if title in owners else None)
    monkeypatch.setattr(ntfy_owner, "find", lambda index, title, when: None)
    monkeypatch.setattr(inbox.live, "last_user_task", lambda a: "")
    monkeypatch.setattr(inbox.live, "last_user_task_ts", lambda a: 0.0)
    return calls


def test_build_unread_and_last(s, monkeypatch):
    inbox.mark_all(s.state_dir, now=NOW - 1000)
    items = [it(3, "claude-a - new", message="all done"), it(2, "claude-a - older"), it(1, "claude-b - x")]
    _feed(monkeypatch, items, {"claude-a - new": "claude-a", "claude-a - older": "claude-a", "claude-b - x": "claude-b"})
    inbox.mark(s.state_dir, inbox.ntfy_key(items[2]), dismiss=False, now=NOW)
    got = agentlive.build(s, [ag("claude-a"), ag("claude-b"), ag("claude-c")], now=NOW)
    assert got["claude-a"] == {"unread": 2, "last": {"text": "all done", "src": "ntfy", "ts": items[0].time}}
    assert got["claude-b"]["unread"] == 0 and got["claude-c"] == {"unread": 0, "last": None}
    assert agentlive.build(s, []) == {}


def test_owner_ids_title_fast_path_skips_the_index(s, monkeypatch):
    calls = _feed(monkeypatch, [], {"claude-a - x": "claude-a"})
    assert agentlive.owner_ids(s, [it(1, "claude-a - x"), it(2, "claude-a - x")]) == ["claude-a"]
    assert calls == []  # every title named its agent: no transcript index
    assert agentlive.owner_ids(s, [it(1, "claude-a - x"), it(2, "plain title"), it(3, "other")]) == ["claude-a"]
    assert len(calls) == 1  # built once for the misses


def test_for_items_payload_per_live_agent(s, monkeypatch):
    inbox.mark_all(s.state_dir, now=NOW - 1000)
    items = [it(2, "claude-a - done"), it(1, "claude-gone - done"), it(3, "pm alert -- h: disk")]
    _feed(monkeypatch, items, {"claude-a - done": "claude-a", "claude-gone - done": "claude-gone",
                               "pm alert -- h: disk": "claude-a"})
    live = {"claude-a": ag("claude-a")}
    got = agentlive.for_items(s, items, live.get, now=NOW)
    assert got == [{"agent": "claude-a", "ntfy": 1, "unread": 1,
                    "last": {"text": "msg 2", "src": "ntfy", "ts": items[0].time}}]  # system message skipped
    mutes.mute(s, "claude-a - done")
    assert agentlive.for_items(s, items[:1], live.get) == []  # muted
    assert agentlive.for_items(s, [], live.get) == []


def test_payloads_never_carry_pane_or_error_text(s, monkeypatch):
    _feed(monkeypatch, [it(1, "claude-a - t")], {"claude-a - t": "claude-a"})
    got = agentlive.build(s, [ag("claude-a")], now=NOW)
    assert set(got["claude-a"]) == {"unread", "last"} and set(got["claude-a"]["last"]) == {"text", "src", "ts"}
