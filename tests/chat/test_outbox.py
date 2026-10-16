"""pm.chat.outbox: the composer outbox table and its state machine (pure DB, no tmux)."""

import pytest

from pm.chat import outbox
from pm.core.config import Settings
from pm.core.errors import Conflict, Invalid, NotFound


@pytest.fixture
def s(tmp_path):
    return Settings(state_dir=tmp_path / "state", tmux_socket="pmtest-unused")


def test_add_get_pending(s):
    m = outbox.add(s, "claude-1", "hello  world", now=100.0)
    assert m.state == "saved" and m.id > 0 and m.created == m.updated == 100.0
    assert outbox.get(s, m.id) == m
    assert outbox.get(s, 9999) is None
    other = outbox.add(s, "codex-2", "x", now=100.0)
    assert [x.id for x in outbox.pending(s, "claude-1", now=100.0)] == [m.id]
    assert [x.id for x in outbox.pending(s, "codex-2", now=100.0)] == [other.id]


def test_add_refuses_empty_too_long_and_too_many(s, monkeypatch):
    with pytest.raises(Invalid):
        outbox.add(s, "claude-1", "   ")
    with pytest.raises(Invalid):
        outbox.add(s, "claude-1", "x" * (outbox.MAX_TEXT + 1))
    monkeypatch.setattr(outbox, "MAX_OPEN", 2)
    outbox.add(s, "claude-1", "a")
    outbox.add(s, "claude-1", "b")
    with pytest.raises(Conflict):
        outbox.add(s, "claude-1", "c")
    outbox.add(s, "claude-2", "other agent is not limited")


def test_happy_path_transitions(s):
    m = outbox.add(s, "claude-1", "hi")
    assert outbox.set_state(s, m.id, "wait").state == "wait"
    assert outbox.set_state(s, m.id, "sending").state == "sending"
    assert outbox.set_state(s, m.id, "inpane", error="not confirmed").error == "not confirmed"
    done = outbox.set_state(s, m.id, "done", item=42)
    assert done.state == "done" and done.item == 42 and done.error == ""


@pytest.mark.parametrize("path,bad", [
    (["sending", "inpane", "done"], "saved"),
    (["cancelled"], "sending"),
    ([], "inpane"),
    ([], "done"),
    (["sending", "queued"], "cancelled"),
    (["sending", "inpane"], "wait"),
])
def test_bad_transitions_conflict(s, path, bad):
    m = outbox.add(s, "claude-1", "hi")
    for st in path:
        outbox.set_state(s, m.id, st)
    with pytest.raises(Conflict):
        outbox.set_state(s, m.id, bad)


def test_same_state_is_noop_unknown_state_and_missing_row(s):
    m = outbox.add(s, "claude-1", "hi")
    outbox.set_state(s, m.id, "wait")
    assert outbox.set_state(s, m.id, "wait").state == "wait"
    with pytest.raises(Invalid):
        outbox.set_state(s, m.id, "bogus")
    with pytest.raises(NotFound):
        outbox.set_state(s, 9999, "wait")


def test_failed_counts_tries_and_retry(s):
    m = outbox.add(s, "claude-1", "hi")
    outbox.set_state(s, m.id, "sending")
    f = outbox.set_state(s, m.id, "failed", error="pane gone\nwith\x1b junk " + "x" * 200)
    assert f.state == "failed" and f.tries == 1 and "\n" not in f.error and len(f.error) <= 80
    again = outbox.set_state(s, m.id, "saved")
    assert again.state == "saved" and again.tries == 1 and again.error == ""
    assert outbox.set_state(s, m.id, "cancelled").state == "cancelled"


def test_next_due_and_due_agents(s):
    a = outbox.add(s, "claude-1", "one")
    b = outbox.add(s, "claude-1", "two")
    outbox.add(s, "claude-2", "x")
    assert outbox.next_due(s, "claude-1").id == a.id
    outbox.set_state(s, a.id, "wait")
    assert outbox.next_due(s, "claude-1").id == a.id
    outbox.set_state(s, a.id, "sending")
    assert outbox.next_due(s, "claude-1").id == b.id
    outbox.set_state(s, b.id, "cancelled")
    assert outbox.next_due(s, "claude-1") is None
    assert outbox.due_agents(s) == ["claude-2"]


def test_pending_keeps_done_for_a_minute(s):
    m = outbox.add(s, "claude-1", "hi", now=1000.0)
    c = outbox.add(s, "claude-1", "drop", now=1000.0)
    outbox.set_state(s, m.id, "sending", now=1000.0)
    outbox.set_state(s, m.id, "done", item=1, now=1001.0)
    outbox.set_state(s, c.id, "cancelled", now=1001.0)
    assert [x.id for x in outbox.pending(s, "claude-1", now=1030.0)] == [m.id]
    assert outbox.pending(s, "claude-1", now=1100.0) == []


def test_match_normalises_and_picks_oldest_sent(s):
    a = outbox.add(s, "claude-1", "fix  the\n tests", now=1000.0)
    b = outbox.add(s, "claude-1", "fix the tests", now=1000.0)
    saved = outbox.add(s, "claude-1", "fix the tests", now=1000.0)  # never sent: not a candidate
    outbox.set_state(s, a.id, "sending", now=1001.0)
    outbox.set_state(s, a.id, "inpane", now=1002.0)
    outbox.set_state(s, b.id, "sending", now=1003.0)
    outbox.set_state(s, b.id, "queued", now=1004.0)
    assert outbox.match(s, "claude-1", "fix the tests", 1010.0).id == a.id
    outbox.set_state(s, a.id, "done", item=5)
    assert outbox.match(s, "claude-1", " fix the  tests ", 1010.0).id == b.id
    assert outbox.match(s, "claude-2", "fix the tests", 1010.0) is None
    assert outbox.match(s, "claude-1", "something else", 1010.0) is None
    assert outbox.match(s, "claude-1", "", 1010.0) is None
    assert outbox.get(s, saved.id).state == "saved"


def test_match_window(s):
    m = outbox.add(s, "claude-1", "hi there", now=1000.0)
    outbox.set_state(s, m.id, "sending", now=1000.0)
    outbox.set_state(s, m.id, "inpane", now=1000.0)
    assert outbox.match(s, "claude-1", "hi there", 1000.0 + outbox.MATCH_WINDOW_S - 1).id == m.id
    assert outbox.match(s, "claude-1", "hi there", 1000.0 + outbox.MATCH_WINDOW_S + 5) is None
    assert outbox.match(s, "claude-1", "hi there", 1000.0 - outbox.MATCH_SLACK_S - 5) is None


def test_match_against_masked_item_text(s):
    secret = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
    text = f"use token {secret} please"
    m = outbox.add(s, "claude-1", text, now=1000.0)
    outbox.set_state(s, m.id, "sending", now=1000.0)
    from pm.chat.text import clean_text

    masked = clean_text(text, 20000)
    assert secret not in masked  # the item stores the masked form
    assert outbox.match(s, "claude-1", masked, 1001.0).id == m.id
    wrapped = outbox.add(s, "claude-1", "<pasted_content>big paste</pasted_content>", now=1000.0)
    outbox.set_state(s, wrapped.id, "sending", now=1000.0)
    assert outbox.match(s, "claude-1", "big paste", 1001.0).id == wrapped.id


def test_sent_agents(s):
    m = outbox.add(s, "claude-1", "x", now=1000.0)
    outbox.add(s, "claude-2", "y", now=1000.0)
    outbox.set_state(s, m.id, "sending", now=1000.0)
    assert outbox.sent_agents(s, now=1010.0) == ["claude-1"]
    assert outbox.sent_agents(s, now=1000.0 + outbox.MATCH_WINDOW_S + 1) == []


def test_old_finished_rows_are_pruned(s):
    m = outbox.add(s, "claude-1", "x", now=1000.0)
    outbox.set_state(s, m.id, "cancelled", now=1000.0)
    outbox.add(s, "claude-1", "y", now=1000.0 + outbox.KEEP_S + 1)
    assert outbox.get(s, m.id) is None


def test_db_file_is_private(s):
    outbox.add(s, "claude-1", "x")
    assert (s.db_file.stat().st_mode & 0o077) == 0


def _queued(s, text, aid="claude-1", now=1000.0, state="queued"):
    m = outbox.add(s, aid, text, now=now)
    outbox.set_state(s, m.id, "sending", now=now)
    return outbox.set_state(s, m.id, state, now=now)


def test_match_queued_row_absorbed_after_a_long_tool_call(s):
    m = _queued(s, "hi there")
    late = 1000.0 + outbox.MATCH_WINDOW_S + 300  # Claude ran one tool for 15 min first
    assert outbox.match(s, "claude-1", "hi there", late).id == m.id
    assert outbox.match(s, "claude-1", "hi there", 1000.0 + outbox.QUEUED_WINDOW_S + 5) is None
    assert outbox.sent_agents(s, now=late) == ["claude-1"]


def test_match_queued_pane_block_wrapped_with_upload_paths(s):
    up = "/home/me/.local/state/pm/uploads/83666f669a429d0993333c84.png"
    text = f"move the Edit button to the top right\n\n- for every file\n- in the tree\n{up}"
    m = _queued(s, text)
    # Claude's pane: wrapped lines joined with spaces (even inside the path), or the path as a marker
    pane = "move the Edit button to the top right - for every file - in the tree /home/me/.local/st ate/pm/uploads/83666f669a429d0993333c84.png"
    assert outbox.match_queued(s, "claude-1", pane, 1001.0).id == m.id
    assert outbox.match_queued(s, "claude-1", "move the Edit button to the top right - for every file - "
                                              "in the tree [Image #1]", 1001.0).id == m.id
    # cut by the pane (clip "…"): a prefix of >= 20 chars is enough, a short one is not
    assert outbox.match_queued(s, "claude-1", "move the Edit button to the…", 1001.0).id == m.id
    assert outbox.match_queued(s, "claude-1", "move the", 1001.0) is None
    assert outbox.match_queued(s, "claude-1", "something else entirely here", 1001.0) is None
    assert outbox.match_queued(s, "claude-2", pane, 1001.0) is None
    assert outbox.match_queued(s, "claude-1", "", 1001.0) is None


def test_match_queued_states_and_masking(s):
    secret = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
    sending = _queued(s, "a message still sending", state="sending")
    assert outbox.match_queued(s, "claude-1", "a message still sending", 1001.0) is None
    outbox.set_state(s, sending.id, "inpane", now=1000.0)
    assert outbox.match_queued(s, "claude-1", "a message still sending", 1001.0).id == sending.id
    assert outbox.match_queued(s, "claude-1", "a message still sending", 1000.0 + outbox.INPANE_QUEUED_S + 1) is None
    from pm.chat.text import clean_text

    m = _queued(s, f"use token {secret} for the deploy please")
    masked = clean_text(f"use token {secret} for the deploy please", 20000)
    assert secret not in masked
    assert outbox.match_queued(s, "claude-1", masked, 5000.0).id == m.id  # queued: no age limit


def test_builder_hinted_row_matches_its_transcript_item(s):
    # Builder mode appends the hint before outbox.add: the row is exactly what was pasted and what claude
    # records, so the delivered (green) dot still comes
    from pm.agents.message import with_builder_hint
    text = with_builder_hint("make the header blue", "/home/me/dev/site", "https://mock.example.test")
    r = outbox.add(s, "claude-1", text, now=1000.0)
    outbox.set_state(s, r.id, "sending", now=1001.0)
    assert outbox.match(s, "claude-1", text, 1005.0).id == r.id
    assert outbox.match(s, "claude-1", "make the header blue", 1005.0) is None  # the hint is part of the text
