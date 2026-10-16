import json
import os
import time
from datetime import UTC, datetime

import pytest
from helpers import chat_env as env  # noqa: F401  (fixture)
from helpers import login

from pm.agents import usage


def _ts(h=0):
    return datetime.fromtimestamp(time.time() - h * 3600, UTC).isoformat().replace("+00:00", "Z")


def _a(mid, i, o, cw=0, cr=0, model="claude-opus-4", tools=0, side=False, ts=None, cwd="/x/proj"):
    content = [{"type": "tool_use", "id": f"t{n}", "name": "Bash", "input": {}} for n in range(tools)] + [{"type": "text", "text": "SECRET-PANE-TEXT"}]
    return {"type": "assistant", "isSidechain": side, "timestamp": ts or _ts(), "cwd": cwd,
            "message": {"id": mid, "model": model, "content": content,
                        "usage": {"input_tokens": i, "output_tokens": o, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr}}}


def _write(p, *entries):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


@pytest.fixture(autouse=True)
def _clean():
    usage.clear_cache()
    yield
    usage.clear_cache()


def test_session_usage_dedup_tools_duration_and_incremental(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, {"type": "user", "message": {"role": "user", "content": "hi"}, "timestamp": _ts(2)},
           _a("m1", 10, 5, 100, 1000, tools=2, ts=_ts(2)), _a("m1", 10, 40, 100, 1000, tools=1, ts=_ts(2)),
           _a("m2", 99, 99, tools=5, side=True))
    u = usage.session_usage(p)
    c = u.counts
    assert (c.input, c.output, c.cache_write, c.cache_read, c.replies, c.tools) == (10, 40, 100, 1000, 1, 3)
    assert u.turns == 1 and u.model == "claude-opus-4" and c.cache_hit == round(100 * 1000 / 1110)
    _write(p, _a("m3", 1, 2, tools=1))
    u = usage.session_usage(p)
    assert u.counts.replies == 2 and u.counts.tools == 4 and u.counts.output == 42
    assert usage.session_usage(tmp_path / "none.jsonl") is None


def test_aggregate_models_days_top_and_range(tmp_path):
    base = tmp_path / ".claude" / "projects"
    _write(base / "-x-a" / "1.jsonl", _a("a1", 100, 50, tools=2, cwd="/x/alpha"), _a("a2", 10, 5, model="claude-haiku", cwd="/x/alpha"))
    _write(base / "-x-b" / "2.jsonl", _a("b1", 1, 1, cwd="/x/beta"), _a("bs", 500, 500, side=True, cwd="/x/beta"))
    old = base / "-x-c" / "3.jsonl"
    _write(old, _a("c1", 9000, 9000, ts="2020-01-01T00:00:00Z", cwd="/x/old"))
    os.utime(old, (1577836800, 1577836800))
    agg = usage.aggregate(tmp_path, 30)
    assert agg.sessions == 2
    assert agg.total.input == 100 + 10 + 1 + 500 and agg.total.tools == 2  # sidechain counted here
    assert [m for m, _ in agg.models][0] == "claude-opus-4"
    assert {m for m, _ in agg.models} == {"claude-opus-4", "claude-haiku"}
    assert agg.top[0][0].startswith("beta ") and agg.top[1][0].startswith("alpha ")
    assert agg.top_ids == ["2", "1"]  # session ids (transcript stems), parallel to top
    assert len(agg.days) == 1
    assert usage.aggregate(tmp_path, 30) is agg  # cached
    assert usage.aggregate(tmp_path, 0).sessions == 3


def test_fmt_duration():
    assert usage.fmt_duration(0) == "–" and usage.fmt_duration(45) == "45s" and usage.fmt_duration(3700) == "1h 01m"
    assert usage.fmt_duration(90000) == "1d 1h"


def test_chat_panel_route_and_agents_tab(env, monkeypatch, tmp_path):
    from pm.agents import detect

    s, c = env
    login(s, c)
    uuid = "11111111-2222-3333-4444-555555555555"
    home = tmp_path / "home"
    from pm.chat.sources.claude import encode_cwd
    cwd = str(s.dev_root / "alpha")
    _write(home / ".claude" / "projects" / encode_cwd(cwd) / f"{uuid}.jsonl",
           {"type": "user", "message": {"role": "user", "content": "hello"}, "timestamp": _ts(1)},
           _a("m1", 10, 5, 100, 900, tools=3, ts=_ts(1), cwd=cwd), _a("m2", 4, 6, ts=_ts(), cwd=cwd))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pm.chat.sources.claude.live_session_id", lambda pid: uuid)
    r = c.get("/a/claude-12/usage")
    assert r.status_code == 200
    t = r.text
    assert "Tool calls" in t and ">3<" in t and "Replies" in t and "claude-opus-4" in t and "89% hit" in t
    assert "SECRET-PANE-TEXT" not in t
    monkeypatch.setattr("pm.agents.usage.panel_data", lambda agent, settings=None: None)
    assert "No usage data" in c.get("/a/claude-12/usage").text
    # the token panel renders it inline (no lazy placeholder that would flash on every swap)
    from pm.chat.sources.common import Stats
    monkeypatch.setattr("pm.chat.stats.stats_for", lambda a, settings=None: Stats(turns=1, input=5))
    assert "No usage data" in c.get("/a/claude-12/stats").text

    c.cookies.clear()
    assert c.get("/a/claude-12/usage", follow_redirects=False).status_code in (303, 401)


def test_agents_usage_tab(env, monkeypatch, tmp_path):
    from pm.core import accounts
    from pm.web import agentslib

    s, c = env
    login(s, c)
    home = tmp_path / "home2"
    _write(home / ".claude" / "projects" / "-x-a" / "1.jsonl", _a("a1", 100, 50, tools=2, cwd="/x/alpha"),
           _a("a2", 10, 5, model="claude-haiku", cwd="/x/alpha"))
    monkeypatch.setattr(agentslib, "home", lambda: home)
    monkeypatch.setattr(accounts, "config_dirs", lambda settings, kind: [home / ".claude"])  # the CLI's own folder
    t = c.get("/agents?t=usage").text
    assert "By model" in t and "claude-haiku" in t and "By day" in t and "Top sessions" in t and "alpha" in t
    assert "SECRET-PANE-TEXT" not in t
    assert 'aria-current="true"' in t and "/agents?t=usage&amp;r=7" in t
    assert "No Claude transcripts" in c.get("/agents?t=usage&r=7").text or "By model" in c.get("/agents?t=usage&r=7").text
    monkeypatch.setattr(agentslib, "home", lambda: tmp_path / "empty")
    monkeypatch.setattr(accounts, "config_dirs", lambda settings, kind: [tmp_path / "empty" / ".claude"])
    assert "No Claude transcripts" in c.get("/agents?t=usage&r=all").text


def test_agents_usage_ctx_limits(env, monkeypatch, tmp_path):
    """The Usage tab context carries the Claude plan limits read from pm's state dir (not the home)."""
    from pm.agents import limits
    from pm.web import agentslib
    from pm.web.routes import agentspage as routes_agentspage

    s, c = env
    login(s, c)
    monkeypatch.setattr(agentslib, "home", lambda: tmp_path / "empty")
    (s.state_dir / limits.FILE).write_text(json.dumps({"five_hour": {"used_percentage": 42, "resets_at": time.time() + 3600}}))
    seen = {}
    real = routes_agentspage.html

    def spy(request, name, **ctx):
        seen.update(ctx)
        return real(request, name, **ctx)

    monkeypatch.setattr(routes_agentspage, "html", spy)
    assert c.get("/agents?t=usage").status_code == 200
    assert [(w.label, w.pct) for w in seen["limits"]] == [("5h limit", 42)]
    assert seen["agg"].top_ids == []


def test_aggregate_sums_config_dirs_and_caches_per_dirs(tmp_path):
    own, acct = tmp_path / "own", tmp_path / "acct"
    _write(own / "projects" / "-x-a" / "1.jsonl", _a("a1", 100, 50, cwd="/x/alpha"))
    _write(acct / "projects" / "-x-b" / "2.jsonl", _a("b1", 10, 5, cwd="/x/beta"))
    both = usage.aggregate(tmp_path, 30, config_dirs=[own, acct])
    assert both.sessions == 2 and both.total.input == 110
    one = usage.aggregate(tmp_path, 30, config_dirs=[own])
    assert one.sessions == 1 and one.total.input == 100  # the dirs are part of the cache key
    assert usage.aggregate(tmp_path, 30, config_dirs=[own, acct]) is both
    assert usage.aggregate(tmp_path, 30).sessions == 0  # default: <home>/.claude/projects only
