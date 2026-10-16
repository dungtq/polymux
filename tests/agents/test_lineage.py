"""Fork / continue lineage: record at launch, resolve by pane + time window, pin, prune."""
import dataclasses
import sqlite3

from pm.agents import lineage, registry
from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, AgentState

REAL_REGISTRY = True


def ag(pane="%9", ident="new-1", pid=100, kind=AgentKind.CLAUDE):
    return Agent(id=f"{kind.value}-{pane[1:]}", kind=kind, pane_id=pane, session_id="$1", pid=pid,
                 cwd="/x", state=AgentState.IDLE, state_since=1.0, ident=ident)


def st(tmp_path):
    return Settings(state_dir=tmp_path / "state")


def seen(s, a, now):
    return registry.assign(s, [a], now=now)[0].id


def rows(s):
    con = sqlite3.connect(s.db_file)
    try:
        return con.execute("SELECT src_id, how, new_pane, kind, created, resolved_id "
                           "FROM agent_lineage ORDER BY created").fetchall()
    finally:
        con.close()


def test_record_then_of_by_pane_and_window(tmp_path):
    s = st(tmp_path)
    src = seen(s, ag(pane="%1", ident="src"), 900.0)
    lineage.record(s, src, "fork", "%9", "claude", now=1000.0)
    new = seen(s, ag(), 1001.0)
    o = lineage.of(s, new)
    assert o == lineage.Origin(src, "fork") and o.label == "Fork from"
    assert lineage.of(s, src) is None


def test_continue_label(tmp_path):
    s = st(tmp_path)
    src = seen(s, ag(pane="%1", ident="src"), 900.0)
    lineage.record(s, src, "continue", "%9", "claude", now=1000.0)
    o = lineage.of(s, seen(s, ag(), 1030.0))
    assert o.how == "continue" and o.label == "Continued from"


def test_record_ignores_bad_input(tmp_path):
    s = st(tmp_path)
    lineage.record(s, "claude-aaaaaa", "fork", "", "claude")  # no pane
    lineage.record(s, "claude-aaaaaa", "bogus", "%9", "claude")
    lineage.record(s, "", "fork", "%9", "claude")
    assert not s.db_file.exists() or rows(s) == []


def test_pane_reused_after_the_window_does_not_resolve(tmp_path):
    s = st(tmp_path)
    lineage.record(s, "claude-aaaaaa", "fork", "%9", "claude", now=1000.0)
    late = seen(s, ag(ident="late"), 1000.0 + lineage._WINDOW_S + 60)
    assert lineage.of(s, late) is None
    assert rows(s)[0][5] is None


def test_other_pane_or_kind_does_not_resolve(tmp_path):
    s = st(tmp_path)
    lineage.record(s, "claude-aaaaaa", "fork", "%9", "claude", now=1000.0)
    assert lineage.of(s, seen(s, ag(pane="%8", ident="x"), 1001.0)) is None
    assert lineage.of(s, seen(s, ag(kind=AgentKind.DEVIN, ident="d"), 1002.0)) is None


def test_source_itself_is_never_its_own_origin(tmp_path):
    s = st(tmp_path)
    src = seen(s, ag(pane="%9", ident="src"), 1001.0)
    lineage.record(s, src, "fork", "%9", "claude", now=1000.0)
    assert lineage.of(s, src) is None


def test_pinned_stays_stable(tmp_path):
    s = st(tmp_path)
    lineage.record(s, "claude-aaaaaa", "fork", "%9", "claude", now=1000.0)
    new = seen(s, ag(), 1001.0)
    assert lineage.of(s, new).agent_id == "claude-aaaaaa"
    assert rows(s)[0][5] == new
    # another agent later in the same pane never takes it over
    other = seen(s, ag(ident="other"), 1500.0)
    assert lineage.of(s, other) is None
    assert lineage.of(s, new).agent_id == "claude-aaaaaa"


def test_later_clear_in_the_same_pane_does_not_claim_it(tmp_path):
    s = st(tmp_path)
    lineage.record(s, "claude-aaaaaa", "continue", "%9", "claude", now=1000.0)
    first = seen(s, ag(ident="first"), 1002.0)
    cleared = seen(s, ag(ident="after-clear"), 1100.0)  # /clear: new conversation, same pane
    assert first != cleared
    assert lineage.of(s, cleared) is None  # asked first, still not the earliest
    assert lineage.of(s, first).agent_id == "claude-aaaaaa"
    assert lineage.of(s, cleared) is None


def test_prune_old_rows(tmp_path):
    s = dataclasses.replace(st(tmp_path), agent_retention_days=1)
    lineage.record(s, "claude-aaaaaa", "fork", "%1", "claude", now=1000.0)
    lineage.record(s, "claude-bbbbbb", "fork", "%2", "claude", now=1000.0 + 3 * 86400)
    assert [r[0] for r in rows(s)] == ["claude-bbbbbb"]


def test_unknown_agent_and_invalid_source_id(tmp_path):
    s = st(tmp_path)
    assert lineage.of(s, "claude-ffffff") is None
    lineage.record(s, "../etc/passwd", "fork", "%9", "claude", now=1000.0)
    new = seen(s, ag(), 1001.0)
    assert lineage.of(s, new) is None  # source id is validated before it is returned


def test_continue_prompt():
    p = lineage.continue_prompt("claude-aaaaaa", "the reply")
    assert "claude-aaaaaa" in p and p.endswith("---\nthe reply")
    assert p.startswith("Here is the previous work from claude-aaaaaa. Do not continue it yet: "
                        "first ask me what to do next.")
