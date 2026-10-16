import json
import time
from datetime import UTC, datetime, timezone

from pm.core.models import NtfyItem
from pm.notify import owner as ntfy_owner


def _line(ts: float, cmd: str) -> str:
    iso = datetime.fromtimestamp(ts, UTC).isoformat()
    return json.dumps({"type": "assistant", "timestamp": iso, "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": cmd}}]}})


def test_scan_reads_titles(tmp_path):
    now = time.time()
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join([
        _line(now, "ntfy publish --title 'claude-7cd08a - Fix it' --tags /x https://n/agent 'm'"),
        _line(now, 'ntfy publish --title "claude-7cd08a - Other" https://n/agent m'),
        _line(now, "ls"), "not json ntfy --title",
    ]))
    assert [t for _, t in ntfy_owner.scan(str(f))] == ["claude-7cd08a - Fix it", "claude-7cd08a - Other"]


def test_find_nearest_title_within_skew():
    o1, o2 = (ntfy_owner.Owner("a", "dev-pm-1-claude", "%1", True),
              ntfy_owner.Owner("b", "dev-pm-3-claude", "%3", False))
    idx = [(1000.0, "T", o1), (1100.0, "T", o2), (1100.0, "U", o1)]
    assert ntfy_owner.find(idx, "T", 1095.0) is o2
    assert ntfy_owner.find(idx, "T", 1000.0) is o1
    assert ntfy_owner.find(idx, "T", 5000.0) is None
    assert ntfy_owner.find(idx, "T", 1300.0) is o2  # slow chained publish
    assert ntfy_owner.find(idx, "T", 900.0) is None  # message before the call
    assert ntfy_owner.find(idx, "V", 1000.0) is None
    assert o1.short == "dev-pm-1" and NtfyItem  # kind suffix dropped


def test_build_index_includes_subagent_files(tmp_path, monkeypatch):
    now = time.time()
    main = tmp_path / "s.jsonl"
    main.write_text("")
    sub = tmp_path / "s" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-1.jsonl").write_text(_line(now, "ntfy publish --title 'claude-7cd08a - Sub' u"))
    rec = type("R", (), {"id": "a", "label": "dev-pm-1-claude", "last_pane": "%1", "ended": False,
                         "transcript": str(main)})()
    monkeypatch.setattr(ntfy_owner.registry, "with_transcripts", lambda s, since: [rec])
    idx = ntfy_owner.build_index(None, now - 60)
    assert ntfy_owner.find(idx, "claude-7cd08a - Sub", now + 5).agent_id == "a"
