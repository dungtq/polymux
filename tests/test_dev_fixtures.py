import json
import os
import time
from pathlib import Path

from pm.agents import limits
from pm.chat.stats import session_stats
from pm.projects import backlog as rb
from pm.projects import projects

FIX = Path(__file__).parent / "fixtures" / "dev"
KEYS = {"running", "idle", "waiting", "failed", "ended_sid", "ended_nosid", "ended_auto", "highctx", "lowctx",
        "longnames", "protected", "ended_manual", "ended_oldmodel"}


def test_manifest():
    m = json.loads((FIX / "manifest.json").read_text())
    assert m["project"] == "fixproj"
    assert {a["key"] for a in m["agents"]} == KEYS
    for a in m["agents"]:
        assert {"key", "kind", "status", "live", "uuid", "transcript", "cwd", "label"} <= set(a)
        assert a["status"] in {"busy", "idle", "waiting", "failed"}
        assert a["cwd"].startswith("{PROJECTS}/fixproj")
        if "project" in a:
            assert (FIX / "projects" / a["project"]).is_dir()
        assert (a["transcript"] is None) == (a["uuid"] is None)
        if a["transcript"]:
            assert (FIX / a["transcript"]).is_file()


def test_transcripts():
    for p in (FIX / "transcripts").glob("*.jsonl"):
        lines = p.read_text().splitlines()
        assert 0 < len(lines) < 30
        objs = [json.loads(x) for x in lines]
        assert any(o["type"] == "assistant" and o["message"]["usage"] and o["message"]["model"] for o in objs)
        assert "tool_use" in p.read_text()
    hi = session_stats(FIX / "transcripts" / "highctx.jsonl")
    lo = session_stats(FIX / "transcripts" / "lowctx.jsonl")
    assert hi.pct == 85 and lo.pct == 5


def test_limits(tmp_path):
    want = {"default": (24, 40), "warn": (65, 70), "red": (85, 92)}
    now = time.time()
    for name, (a, b) in want.items():
        d = json.loads((FIX / "limits" / f"{name}.json").read_text())
        for w in d.values():
            w["resets_at"] = now + w.pop("resets_in_s")
        (tmp_path / limits.FILE).write_text(json.dumps(d))
        ws = limits.read_limits(tmp_path, now)
        assert [w.pct for w in ws] == [a, b]


def test_project_files():
    P = FIX / "projects" / "fixproj"
    todo = projects.todo_items((P / "TODO.md").read_text())
    assert len(todo) == 3 and sum(t["done"] for t in todo) == 1
    bl = (P / "BACKLOG.md").read_text()
    assert len(rb.backlog_items(bl)) == 4
    assert rb.counts(bl)
    assert (P / "PLAN.md").is_file()
    assert sorted(p.name for p in (P / "plans").glob("*.md")) == ["first-plan.md", "second-plan.md"]


def test_extra_keys():
    m = {a["key"]: a for a in json.loads((FIX / "manifest.json").read_text())["agents"]}
    assert len(m["longnames"]["label"]) > 100 and len(m["longnames"]["project"]) > 40
    assert m["protected"]["protected"] and m["protected"]["live"]
    assert not m["ended_manual"]["live"] and m["ended_manual"]["autoexit"] == "manual"
    assert m["ended_oldmodel"]["autoexit"] == "auto_nomodel"


def test_oldmodel_from_transcript():
    from pm.agents import head as agenthead

    assert agenthead.model_from_transcript(str(FIX / "transcripts/ended_oldmodel.jsonl")) == "claude-3-5-sonnet-20241022"
