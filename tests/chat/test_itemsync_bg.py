"""pm.chat.itemsync._bg_reconcile: a background task whose notification never came is finished from
its output file's "[exited with code N]" line, or as "stopped · session ended" once the agent is gone."""

import os

import pytest
from helpers import task_note

from pm.chat import items, itemsync
from pm.chat.sources.common import parse_ts

from .test_itemsync import (  # noqa: F401 - fixtures used by name
    _fresh_seen,
    _no_real_claude,
    agent,
    asst,
    bgtool,
    by_uid,
    jsonl,
    ts,
    user,
)


@pytest.fixture
def out(tmp_path, jsonl):  # noqa: F811
    """Writer of the task output file for the ``jsonl`` transcript under the patched tmp root."""
    tasks = tmp_path / "claude-tmp" / jsonl.path.parent.name / jsonl.path.stem / "tasks"
    tasks.mkdir(parents=True)

    def write(task_id: str, body: str, mtime: float | None = None):
        p = tasks / f"{task_id}.output"
        p.write_text(body)
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    write.tasks = tasks
    return write


def launch(jsonl, *ids):  # noqa: F811
    from helpers import bg_result

    tools = [bgtool(f"t{i}") for i in range(len(ids))]
    return jsonl(user("u1", "go", 1), asst("a1", *tools, n=2),
                 *[bg_result(f"r{i}", f"t{i}", tid, ts(3)) for i, tid in enumerate(ids)],
                 asst("a2", n=4, stop="end_turn"))


def ended(p):
    return agent(p, pid=0)


@pytest.mark.parametrize(("body", "want"), [("SECRET\n[exited with code 0]\n", ("ok", "background")),
                                            ("SECRET\n[exited with code 3]\n", ("err", "exit 3"))])
def test_marker_finishes_live_agent_task(settings, jsonl, out, body, want):
    p = launch(jsonl, "bk1")
    end = parse_ts(ts(20))
    out("bk1", body, end)
    itemsync.sync(settings, agent(p))
    t0 = by_uid(settings, "t0")
    assert (t0.status, t0.note, t0.ended) == (*want, end)


def test_no_marker_live_agent_stays_running(settings, jsonl, out):
    p = launch(jsonl, "bk1")
    out("bk1", "still going\n")
    itemsync.sync(settings, agent(p))
    assert (by_uid(settings, "t0").status, by_uid(settings, "t0").note) == ("run", "background")


def test_no_marker_ended_agent_is_stopped(settings, jsonl, out):
    p = launch(jsonl, "bk1", "ag1")
    out("bk1", "partial\n")  # "ag1" (an async agent) has no output file at all
    itemsync.sync(settings, ended(p))
    mtime = p.stat().st_mtime
    for uid in ("t0", "t1"):
        t = by_uid(settings, uid)
        assert (t.status, t.note, t.ended) == ("err", itemsync.SESSION_ENDED, mtime)


def test_marker_wins_for_ended_agent(settings, jsonl, out):
    p = launch(jsonl, "bk1")
    out("bk1", "[exited with code 0]\n", parse_ts(ts(9)))
    itemsync.sync(settings, ended(p))
    assert (by_uid(settings, "t0").status, by_uid(settings, "t0").ended) == ("ok", parse_ts(ts(9)))


def test_unchanged_transcript_still_reconciles(settings, jsonl, out):
    p = launch(jsonl, "bk1")
    a = agent(p)
    rev = itemsync.sync(settings, a)
    assert itemsync.sync(settings, a) == rev  # unchanged, nothing to reconcile yet
    out("bk1", "[exited with code 1]\n")
    rev2 = itemsync.sync(settings, a)
    changed = items.list_items(settings, a.id, after_rev=rev)
    assert rev2 > rev and [(i.uid, i.status, i.note) for i in changed] == [("t0", "err", "exit 1")]
    assert itemsync.sync(settings, a) == rev2  # finished: no new rev


def test_unchanged_transcript_agent_ends(settings, jsonl, out):
    p = launch(jsonl, "bk1")
    itemsync.sync(settings, agent(p))
    assert by_uid(settings, "t0").status == "run"
    itemsync.sync(settings, ended(p))  # same file, the agent's pid is gone now
    assert (by_uid(settings, "t0").status, by_uid(settings, "t0").note) == ("err", itemsync.SESSION_ENDED)


def test_later_notification_does_not_overwrite(settings, jsonl, out):
    p = launch(jsonl, "bk1")
    a = agent(p)
    out("bk1", "[exited with code 0]\n", parse_ts(ts(9)))
    rev = itemsync.sync(settings, a)
    jsonl(task_note("n1", "bk1", ts(30), status="failed", tool_id="t0"))
    itemsync.sync(settings, a)
    t0 = by_uid(settings, "t0")
    assert (t0.status, t0.ended) == ("ok", parse_ts(ts(9))) and items.latest(settings, a.id)[1] == rev


def test_notification_first_then_marker_is_noop(settings, jsonl, out):
    p = launch(jsonl, "bk1")
    jsonl(task_note("n1", "bk1", ts(30), status="failed", tool_id="t0"))
    out("bk1", "[exited with code 0]\n")
    itemsync.sync(settings, ended(p))
    t0 = by_uid(settings, "t0")
    assert (t0.status, t0.ended, t0.note) == ("err", parse_ts(ts(30)), "background")


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_unsafe_output_file_is_ignored(settings, jsonl, out, tmp_path, kind):
    p = launch(jsonl, "bk1")
    if kind == "symlink":
        real = tmp_path / "real.output"
        real.write_text("[exited with code 0]\n")
        (out.tasks / "bk1.output").symlink_to(real)
    else:
        os.mkfifo(out.tasks / "bk1.output")
    itemsync.sync(settings, agent(p))
    assert by_uid(settings, "t0").status == "run"


def test_more_than_ten_running(settings, jsonl, out):
    ids = [f"bk{i}" for i in range(12)]
    p = launch(jsonl, *ids)
    for tid in ids:
        out(tid, "[exited with code 0]\n")
    a = agent(p)
    itemsync.sync(settings, a)
    assert sum(by_uid(settings, f"t{i}").status == "run" for i in range(12)) == 2  # 10 per sync, newest first
    assert by_uid(settings, "t0").status == "run" and by_uid(settings, "t11").status == "ok"
    itemsync.sync(settings, a)  # unchanged file: the next sync takes the rest
    assert all(by_uid(settings, f"t{i}").status == "ok" for i in range(12))


def test_other_kinds_are_not_reconciled(settings, jsonl, out, monkeypatch):
    calls = []
    monkeypatch.setattr(itemsync, "_bg_reconcile", lambda *a: calls.append(a))
    p = launch(jsonl, "bk1")
    itemsync.sync(settings, agent(p))
    assert len(calls) == 1
    from pm.core.models import AgentKind

    itemsync.sync(settings, agent(p, aid="devin-x", kind=AgentKind.DEVIN))
    assert len(calls) == 1


def test_output_bytes_never_stored(settings, jsonl, out):
    import sqlite3

    p = launch(jsonl, "bk1")
    out("bk1", "SECRET OUTPUT " + "x" * 40 + "\n[exited with code 0]\n")
    itemsync.sync(settings, ended(p))
    con = sqlite3.connect(settings.db_file)
    try:
        dump = repr(con.execute("SELECT * FROM chat_items").fetchall())
    finally:
        con.close()
    assert "SECRET" not in dump and "exited" not in dump


def test_output_in_the_session_the_process_started_with(settings, jsonl, out, tmp_path):
    # a resumed / cleared chat writes a new transcript, but claude keeps its first session's tmp folder
    p = launch(jsonl, "bk1")
    first = tmp_path / "claude-tmp" / p.parent.name / "84b545db-d973-468a-9d7c-b3891ed253f2" / "tasks"
    first.mkdir(parents=True)
    (first / "bk1.output").write_text("[exited with code 0]\n")
    itemsync.sync(settings, ended(p))
    assert (by_uid(settings, "t0").status, by_uid(settings, "t0").note) == ("ok", "background")


def test_session_ended_row_gets_its_real_outcome_later(settings, jsonl, out, tmp_path):
    p = launch(jsonl, "bk1")
    itemsync.sync(settings, ended(p))  # no file anywhere yet: closed as session ended
    assert by_uid(settings, "t0").note == itemsync.SESSION_ENDED
    out("bk1", "[exited with code 2]\n", parse_ts(ts(30)))
    itemsync.sync(settings, ended(p))
    t0 = by_uid(settings, "t0")
    assert (t0.status, t0.note, t0.ended) == ("err", "exit 2", parse_ts(ts(30)))
