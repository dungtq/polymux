"""Last-response time ("Done at") and the Idle rule (default 8h, ``Settings.idle_after_s``)."""

import json
import os
import time

from pm.agents import inbox
from pm.chat.sources import lastresp
from pm.core.models import Agent, AgentKind, AgentState
from pm.web import stats


def _ag(state="idle", done=0.0, active=0.0, since=0.0, kind=AgentKind.CLAUDE):
    return Agent(id="claude-1", kind=kind, pane_id="%1", session_id="$1", pid=1, cwd="/x",
                 state=AgentState(state), state_since=since, last_active=active, done_at=done)


def _line(kind, ts, **kw):
    return json.dumps({"type": kind, "timestamp": ts, **kw})


def test_claude_last_response_ignores_later_non_assistant_entries(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join([
        _line("assistant", "2026-10-11T10:00:00Z"),
        _line("assistant", "2026-10-11T11:00:00Z", isSidechain=True),  # sub-agent: not the agent's reply
        _line("user", "2026-10-11T12:00:00Z"),
        _line("system", "2026-10-11T13:00:00Z"),
    ]) + "\n")
    os.utime(f, (time.time(), time.time()))  # a fresh mtime must not matter
    want = lastresp.parse_ts("2026-10-11T10:00:00Z")
    assert lastresp.last_response_ts(AgentKind.CLAUDE, f) == want


def test_last_response_cache_follows_file_and_unknown_is_zero(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(_line("assistant", "2026-10-11T10:00:00Z") + "\n")
    a = lastresp.last_response_ts(AgentKind.CLAUDE, f)
    with open(f, "a") as fh:
        fh.write(_line("assistant", "2026-10-11T11:00:00Z") + "\n")
    assert lastresp.last_response_ts(AgentKind.CLAUDE, f) == a + 3600
    assert lastresp.last_response_ts(AgentKind.CLAUDE, None) == 0.0
    assert lastresp.last_response_ts(AgentKind.CLAUDE, tmp_path / "missing") == 0.0


def test_done_ts_falls_back():
    assert _ag(done=5, active=9, since=7).done_ts() == 5
    assert _ag(active=9, since=7).done_ts() == 9
    assert _ag(since=7).done_ts() == 7
    assert _ag().done_ts() == 0.0


H = 3600


def test_idle_rule_uses_last_response_not_activity(tmp_path):
    now = time.time()
    st = inbox.load(tmp_path)
    # recent tmux/file activity must not keep an agent that answered 9h ago out of Idle
    assert inbox.bucket(_ag(done=now - 9 * H, active=now - 5), st, now) == "idle"
    assert inbox.bucket(_ag(done=now - 7 * H), st, now) == "finished"  # read or not
    assert inbox.bucket(_ag(done=now - 8 * H - 100), st, now) == "idle"
    assert inbox.bucket(_ag(done=now - 7200), st, now, window_s=H) == "idle"  # custom 1h window
    assert inbox.bucket(_ag(), st, now) == "idle"  # unknown
    inbox.mark(tmp_path, "claude-1:idle", dismiss=True, now=now - 10)
    assert inbox.bucket(_ag(done=now - 100), inbox.load(tmp_path), now) == "idle"  # dismissed


def test_stats_counts_follow_the_rule():
    now = time.time()
    snap = type("S", (), {"agents": [_ag(done=now - 60), _ag(done=now - 9 * H, active=now),
                                     _ag("failed"), _ag("unknown")]})()
    assert stats.stats_inbox(snap) == "Inbox · 4 agents · 1 finished · 2 idle · 1 waiting"
    snap = type("S", (), {"agents": [_ag(done=now - 7200), _ag("failed", done=now - 7200)]})()
    assert stats.stats_inbox(snap) == "Inbox · 2 agents · 1 finished · 1 waiting"
    assert stats.stats_inbox(snap, idle_after_s=H) == "Inbox · 2 agents · 2 idle"


def test_project_live_has_idle_fold(monkeypatch):
    from pm.web.routes import pages
    now = time.time()
    agents = [_ag(done=now - 60), _ag(done=now - 9 * H, active=now)]
    agents[1] = Agent(**{**agents[1].__dict__, "id": "claude-2", "pane_id": "%2"})
    store = inbox.Store({"all": 0.0, "read": {}, "dismiss": {}})
    monkeypatch.setattr(pages.inbox, "load", lambda _d: store)
    req = type("R", (), {"app": None})()
    monkeypatch.setattr(pages.deps, "settings", lambda _r: type("S", (), {"state_dir": "/nonexistent", "idle_after_s": 28800})())
    act, idle = pages._split_idle(req, agents)
    assert [a.id for a in act] == ["claude-1"] and [a.id for a in idle] == ["claude-2"]
    monkeypatch.setattr(pages.deps, "settings", lambda _r: type("S", (), {"state_dir": "/nonexistent", "idle_after_s": 60})())
    act, idle = pages._split_idle(req, [_ag(done=now - 120)])  # custom (clamped to >= 60s) window
    assert act == [] and len(idle) == 1


def test_stale_failed_goes_to_idle(tmp_path):
    now = time.time()
    st = inbox.load(tmp_path)
    assert inbox.bucket(_ag("failed", done=now - 600), st, now) == "waiting"
    assert inbox.bucket(_ag("failed", done=now - 7 * 86400, kind=AgentKind.DEVIN), st, now) == "idle"
    assert inbox.bucket(_ag("failed", done=now - 9 * H, since=now - 5), st, now) == "waiting"  # fresh failure
    assert inbox.bucket(_ag("failed", done=now - 7200), st, now) == "waiting"  # inside the 8h default
    assert inbox.bucket(_ag("failed", done=now - 7200), st, now, window_s=H) == "idle"
    assert inbox.bucket(_ag("failed"), st, now) == "waiting"  # unknown time: keep showing it
    snap = type("S", (), {"agents": [_ag("failed", done=now - 9 * H)]})()
    assert stats.stats_inbox(snap) == "Inbox · 1 agent · 1 idle"
