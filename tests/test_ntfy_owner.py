import json
import os
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


def test_agent_feed_resolves_title_ids_in_one_registry_query(tmp_path, monkeypatch):
    from pm.agents import registry
    from pm.core.config import Settings
    from pm.notify import feed as ntfy_feed

    s = Settings(state_dir=tmp_path / "state")
    con = registry._connect(s)
    for aid, ended in (("claude-aaaaaa", None), ("devin-bbbbbb", 5.0)):
        con.execute("INSERT INTO agent_records VALUES (?,?,NULL,1,1.0,2.0,'%1','p','/x',?,'',?,'')",
                    (aid, aid.split("-")[0], aid + "-label", ended))
    con.commit()
    con.close()
    titles = ["claude-aaaaaa - a", "devin-bbbbbb - b", "codex-cccccc - unknown", "plain title"]
    items = [NtfyItem(id=str(n), time=1000.0 - n, title=titles[n % 4], message="m") for n in range(40)]
    monkeypatch.setattr(ntfy_feed, "recent", lambda settings, project_path=None: items)
    monkeypatch.setattr(ntfy_owner, "build_index", lambda settings, since: [])
    before = {i.id: ntfy_owner.owner_for(s, [], i.title, i.time) for i in items}  # old path: one get() per item
    connects = []
    real = registry._connect
    monkeypatch.setattr(registry, "_connect", lambda settings: connects.append(1) or real(settings))
    got = ntfy_owner.agent_feed(s, {"claude-aaaaaa", "devin-bbbbbb", "codex-cccccc"}, unread=lambda i: True)
    assert len(connects) == 1  # 40 messages, 3 distinct ids: one query
    assert got == {"claude-aaaaaa": ntfy_owner.AgentFeed(items[0], 10),
                   "devin-bbbbbb": ntfy_owner.AgentFeed(items[1], 10)}
    recs = ntfy_owner.title_records(s, (i.title for i in items))
    assert {i.id: ntfy_owner.owner_for(s, [], i.title, i.time, recs) for i in items} == before
    assert before["0"].label == "claude-aaaaaa-label" and before["0"].live and not before["1"].live


def test_kind_owner_matches_the_one_session_covering_the_message():
    def R(i, k, first, end=None, mtime=2000.0):
        return type("R", (), {"id": i, "kind": k, "first_seen": first, "ended_at": end})(), mtime

    recs = [R("devin-aaaaaa", "devin", 100.0, 500.0), R("devin-bbbbbb", "devin", 600.0), R("codex-cccccc", "codex", 0.0)]
    assert ntfy_owner.kind_owner("devin - Done", 300.0, recs) == "devin-aaaaaa"
    assert ntfy_owner.kind_owner("devin - Done", 900.0, recs) == "devin-bbbbbb"
    assert ntfy_owner.kind_owner("devin - Done", 550.0, recs) is None  # between the sessions (beyond slack)
    assert ntfy_owner.kind_owner("codex - Done", 900.0, recs) == "codex-cccccc"
    assert ntfy_owner.kind_owner("devin - Done", 1000.0, recs + [R("devin-dddddd", "devin", 50.0)]) is None  # two live
    idle = R("devin-ffffff", "devin", 50.0, mtime=800.0)  # wrote nothing since before the message: not the sender
    assert ntfy_owner.kind_owner("devin - Done", 1000.0, recs + [idle]) == "devin-bbbbbb"
    assert ntfy_owner.kind_owner("devin-aaaaaa - Done", 300.0, recs) is None  # id titles go through title_owner
    assert ntfy_owner.kind_owner("plain", 300.0, recs) is None


def test_agent_feed_attributes_kind_only_title_and_any_project_tag(tmp_path, monkeypatch):
    from pm.agents import registry
    from pm.core.config import Settings
    from pm.notify import feed as ntfy_feed

    s = Settings(state_dir=tmp_path / "state")
    con = registry._connect(s)
    live, stale = tmp_path / "live.json", tmp_path / "stale.json"
    live.write_text("{}")
    stale.write_text("{}")
    os.utime(stale, (1.0, 1.0))
    con.execute("INSERT INTO agent_records VALUES ('devin-44fd82','devin',NULL,1,1.0,2.0,'%1','pm','/x','l',?,NULL,'')",
                (str(live),))
    con.commit()
    con.close()
    it = NtfyItem(id="1", time=50.0, title="devin - Plan limits", message="m", tags=("/other/project",),
                  project="/other/project")
    monkeypatch.setattr(ntfy_feed, "recent", lambda settings: [it])
    monkeypatch.setattr(ntfy_owner, "build_index", lambda settings, since: [])
    assert ntfy_owner.agent_feed(s, {"devin-44fd82"}) == {"devin-44fd82": ntfy_owner.AgentFeed(it, 0)}
    con = registry._connect(s)  # an idle devin elsewhere does not make the title ambiguous
    con.execute("INSERT INTO agent_records VALUES ('devin-eeeeee','devin',NULL,2,1.0,2.0,'%2','other','/y','l2',?,NULL,'')",
                (str(stale),))
    con.execute("INSERT INTO agent_records VALUES ('devin-gggggg','devin',NULL,3,1.0,2.0,'%3','other','/z','l3',?,NULL,'')",
                (str(live),))  # a second devin written after the message: ambiguous, nobody gets it
    con.commit()
    con.close()
    assert ntfy_owner.agent_feed(s, {"devin-44fd82"}) == {}
