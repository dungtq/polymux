"""pm.chat.itemsync: Claude JSONL -> append-only items (blocks, tools, results, turn ends), other
kinds, rewritten files, legacy conversion from chat_messages, masking."""

import json
import os
import sqlite3
from pathlib import Path

import pytest

from pm.chat import items, itemsync
from pm.chat import log as chatlog
from pm.chat.sources import claude
from pm.chat.sources.common import parse_ts
from pm.core.models import Agent, AgentKind

TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
MODEL = "claude-opus-5-5"


def ts(n: int) -> str:
    return f"2026-10-09T10:{n // 60:02d}:{n % 60:02d}.000Z"


def user(uid, text, n=0):
    return {"type": "user", "uuid": uid, "timestamp": ts(n), "message": {"role": "user", "content": text}}


def result(uid, tid, n=0, *, err=False, out="SECRET OUTPUT"):
    return {"type": "user", "uuid": uid, "timestamp": ts(n),
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                      "is_error": err, "content": out}]}}


def asst(uid, *blocks, n=0, stop=None, model=MODEL):
    return {"type": "assistant", "uuid": uid, "timestamp": ts(n),
            "message": {"role": "assistant", "model": model, "content": list(blocks), "stop_reason": stop}}


def text(t):
    return {"type": "text", "text": t}


def think(t):
    return {"type": "thinking", "thinking": t, "signature": "sig"}


def tool(tid, name="Bash", **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp or {"command": "ls -la"}}


@pytest.fixture
def jsonl(tmp_path):
    p = tmp_path / "proj" / "11111111-2222-3333-4444-555555555555.jsonl"
    p.parent.mkdir()
    p.write_text("")

    def add(*entries):
        with open(p, "a") as f:
            f.writelines(json.dumps(e) + "\n" for e in entries)
        st = p.stat()
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        return p

    add.path = p
    return add


@pytest.fixture(autouse=True)
def _fresh_seen():
    itemsync._SEEN.clear()
    yield
    itemsync._SEEN.clear()


LIVE_PID = 4242  # a Claude agent that still runs (background tasks stay "run" without an exit line)


@pytest.fixture(autouse=True)
def _no_real_claude(monkeypatch, tmp_path):
    """Never read the real ~/.claude/sessions or /tmp/claude-<uid> task files."""
    monkeypatch.setattr(chatlog, "live_session_id", lambda pid: None)
    monkeypatch.setattr(itemsync, "task_exit",
                        lambda t, task, tmp_root=None: claude.task_exit(t, task, tmp_root=tmp_path / "claude-tmp"))


def agent(path: Path, aid="claude-abc123", kind=AgentKind.CLAUDE, pid: int | None = None) -> Agent:
    pid = (LIVE_PID if kind == AgentKind.CLAUDE else 0) if pid is None else pid
    return Agent(id=aid, kind=kind, pane_id="%1", session_id="$1", pid=pid, cwd="/nowhere", transcript=str(path))


def got(settings, aid="claude-abc123"):
    return items.list_items(settings, aid, limit=1000)


def brief(its):
    return [(i.kind, i.uid) for i in its]


def test_claude_read_entries_blocks_results_model(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in [
        asst("a1", think("hmm"), text("one"), tool("t1", command="ls", run_in_background=True), text("two"),
             stop="end_turn"),
        result("r1", "t1", out="Exit code 2\nboom"),
        result("r2", "t2", err=True, out=[{"type": "text", "text": "SECRET OUTPUT"}]),
    ]))
    es = claude.read_entries(p, 0).entries
    a = es[0]
    assert a.model == MODEL and a.end_turn and a.text == "one\n\ntwo" and a.tools == ("Bash",)
    assert [(b.kind, b.id, b.name, b.line, b.text, b.background) for b in a.blocks] == [
        ("text", "", "", "", "one", False), ("tool", "t1", "Bash", "ls", "", True), ("text", "", "", "", "two", False)]
    assert [(e.role, e.uid, e.text, e.thinking) for e in es[1:]] == [
        ("result", "t1", "ok", "exit 2"), ("result", "t2", "err", "")]
    assert "SECRET" not in repr(es)


def test_text_per_block_tools_parent_end_turn(settings, jsonl):
    p = jsonl(
        user("u1", "fix it", 1),
        asst("a1", think("secret thoughts"), text("Looking."), tool("t1"), n=2),
        result("r1", "t1", 4, out="Exit code 1\nfail"),
        asst("a2", tool("t2", "Read", file_path="/a/b.py"), n=5),
        result("r2", "t2", 6, err=True),
        asst("a3", text("Fixed."), text("Bye."), n=8, stop="end_turn"),
    )
    rev = itemsync.sync(settings, agent(p))
    its = got(settings)
    assert rev == items.latest(settings, "claude-abc123")[1] > 0
    assert brief(its) == [("user", "u1"), ("text", "a1:0"), ("tool", "t1"), ("tool", "t2"), ("text", "a3:0"),
                          ("text", "a3:1"), ("end", "u1:end")]
    u, t1, tool1, tool2, f1, _f2, end = its
    assert t1.name == MODEL and t1.text == "Looking." and all(i.turn == "u1" for i in its)
    assert (tool1.status, tool1.note, tool1.ended, tool1.line, tool1.parent) == (
        "ok", "exit 1", parse_ts(ts(4)), "ls -la", t1.seq)
    assert (tool2.status, tool2.note, tool2.name, tool2.line, tool2.parent) == ("err", "", "Read", "/a/b.py", t1.seq)
    assert end.note == "3 replies · 2 tool calls · 7s"
    raw = json.dumps([i.__dict__ for i in its])
    assert "secret thoughts" not in raw and "SECRET OUTPUT" not in raw and "fail" not in raw


def test_result_in_later_sync_bumps_rev_only_for_changes(settings, jsonl):
    p = jsonl(user("u1", "go", 1), asst("a1", text("run"), tool("t1", command="sleep 9", run_in_background=True), n=2))
    a = agent(p)
    rev = itemsync.sync(settings, a)
    assert itemsync.sync(settings, a) == rev  # unchanged file: nothing read, nothing bumped
    tool1 = got(settings)[2]
    assert (tool1.status, tool1.note) == ("run", "background")
    jsonl(result("r1", "t1", 3, out="Command running in background"))
    rev2 = itemsync.sync(settings, a)
    changed = items.list_items(settings, a.id, after_rev=rev)
    assert [(i.uid, i.status, i.note) for i in changed] == [("t1", "ok", "background")] and rev2 > rev
    # re-reading the same result again changes nothing
    jsonl(result("r1b", "t1", 3, out="again"))
    assert itemsync.sync(settings, a) == rev2


def test_next_prompt_closes_turn(settings, jsonl):
    p = jsonl(user("u1", "a", 1), asst("a1", text("x"), tool("t1"), n=3), user("u2", "b", 10),
              user("u3", "c", 11))
    itemsync.sync(settings, agent(p))
    its = got(settings)
    assert brief(its) == [("user", "u1"), ("text", "a1:0"), ("tool", "t1"), ("end", "u1:end"), ("user", "u2"),
                          ("user", "u3")]  # u2 had no reply: no end item
    assert its[3].note == "1 reply · 1 tool call · 2s" and its[2].status == "run"


def test_idle_end_after_5s(settings, jsonl, monkeypatch):
    p = jsonl(user("u1", "a", 1), asst("a1", text("x"), n=2))
    a = agent(p)
    last = parse_ts(ts(2))
    monkeypatch.setattr(itemsync.time, "time", lambda: last + 3)
    itemsync.sync(settings, a, idle=True)
    assert brief(got(settings))[-1] == ("text", "a1:0")  # too recent
    rev = itemsync.sync(settings, a)  # not idle: unchanged file, no end
    monkeypatch.setattr(itemsync.time, "time", lambda: last + 6)
    assert itemsync.sync(settings, a, idle=True) > rev
    end = got(settings)[-1]
    assert (end.kind, end.uid, end.note) == ("end", "u1:end", "1 reply · 0 tool calls · 1s")
    # later pieces without a prompt open a new turn keyed by their entry uid
    jsonl(asst("a2", text("more"), n=20, stop="end_turn"))
    itemsync.sync(settings, a)
    assert brief(got(settings))[-2:] == [("text", "a2:0"), ("end", "a2:end")]


def test_rewritten_file_drops_and_rereads(settings, jsonl):
    p = jsonl(user("u1", "first", 1), asst("a1", text("one"), n=2), asst("a2", text("two"), n=3))
    a = agent(p)
    itemsync.sync(settings, a)
    assert len(got(settings)) == 3
    p.write_text(json.dumps(user("u9", "new", 5)) + "\n")  # shorter: rewritten
    itemsync.sync(settings, a)
    assert brief(got(settings)) == [("user", "u9")]


def test_whole_file_in_bounded_chunks(settings, jsonl, monkeypatch):
    from pm.chat.sources import common

    p = jsonl(*[user(f"u{i}", f"prompt {i} " + "x" * 200, i) for i in range(20)])
    monkeypatch.setattr(common, "CHUNK_BYTES", 1024)
    monkeypatch.setattr(itemsync, "MAX_LOOPS", 2)
    a = agent(p)
    itemsync.sync(settings, a)
    n1 = len(got(settings))
    assert 0 < n1 < 20
    for _ in range(20):
        itemsync.sync(settings, a)
    assert [i.uid for i in got(settings)] == [f"u{i}" for i in range(20)]  # from byte 0, in order


def test_masking(settings, jsonl):
    p = jsonl(user("u1", f"use {TOKEN}", 1),
              asst("a1", text(f"token is {TOKEN}"), tool("t1", command=f"curl -H 'Authorization: {TOKEN}'"), n=2))
    itemsync.sync(settings, agent(p))
    raw = sqlite3.connect(settings.db_file).execute(
        "SELECT group_concat(text || line || note) FROM chat_items").fetchone()[0]
    assert TOKEN not in raw and "token is" in raw


def test_other_kind_fallback(settings, tmp_path, monkeypatch):
    rp = tmp_path / "rollout-s.jsonl"
    lines = [
        {"timestamp": ts(1), "type": "response_item",
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "do it"}]}},
        {"timestamp": ts(2), "type": "response_item",
         "payload": {"type": "function_call", "name": "shell", "arguments": json.dumps({"command": ["ls", "-l"]})}},
        {"timestamp": ts(3), "type": "response_item",
         "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]}},
    ]
    rp.write_text("".join(json.dumps(x) + "\n" for x in lines))
    a = agent(rp, aid="codex-1", kind=AgentKind.CODEX)
    itemsync.sync(settings, a)
    its = got(settings, "codex-1")
    assert [i.kind for i in its] == ["user", "tool", "text"]
    tool1 = its[1]
    assert tool1.status == "" and tool1.uid.endswith(":0") and tool1.name == "shell"
    future = max(i.ts for i in its) + 60
    monkeypatch.setattr(itemsync.time, "time", lambda: future)
    itemsync.sync(settings, a, idle=True)
    assert got(settings, "codex-1")[-1].kind == "end"


def test_sync_never_raises(settings, jsonl, monkeypatch):
    p = jsonl(user("u1", "x", 1))

    def boom(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr(itemsync.chat_sources, "read_entries", boom)
    assert itemsync.sync(settings, agent(p)) == 0


def _seed_legacy(settings, aid, conv, rows):
    chatlog._connect(settings).close()
    con = sqlite3.connect(settings.db_file)
    for uid, role, t, txt, steps in rows:
        con.execute("INSERT INTO chat_messages (agent_id, conv, uid, role, ts, text, tools, steps) "
                    "VALUES (?,?,?,?,?,?,?,?)", (aid, conv, uid, role, t, txt,
                                                 json.dumps([["Bash", len(steps)]] if steps else []),
                                                 json.dumps([["tool", "Bash", s] for s in steps])))
    con.commit()
    con.close()


def test_convert_legacy_once(settings):
    _seed_legacy(settings, "claude-old", "oldconv", [
        ("u1", "user", 100, f"hi {TOKEN}", []),
        ("u1:a", "assistant", 101, "answer", ["ls", "pwd"]),
        ("u2", "user", 200, "next", []),
        ("u2:a", "assistant", 201, "", ["make"]),
    ])
    assert itemsync.convert_legacy(settings, "claude-old") == 8
    its = got(settings, "claude-old")
    assert brief(its) == [("user", "u1"), ("text", "u1:a:0"), ("tool", "u1:a:t0"), ("tool", "u1:a:t1"),
                          ("end", "u1:end"), ("user", "u2"), ("tool", "u2:a:t0"), ("end", "u2:end")]
    assert its[2].status == "ok" and its[2].ended == 0 and its[2].parent == its[1].seq and its[6].parent == 0
    assert its[4].note == "1 reply · 2 tool calls" and its[7].note == "0 replies · 1 tool call"
    assert TOKEN not in its[0].text
    rev = items.latest(settings, "claude-old")[1]
    assert itemsync.convert_legacy(settings, "claude-old") == 0  # once
    assert items.latest(settings, "claude-old")[1] == rev


def test_sync_converts_legacy_lazily_but_not_current_conv(settings, jsonl):
    p = jsonl(user("u1", "live", 1), asst("a1", text("real"), n=2))
    a = agent(p)
    _seed_legacy(settings, a.id, "oldconv", [("o1", "user", 50, "old prompt", []),
                                             ("o1:a", "assistant", 51, "old answer", [])])
    _seed_legacy(settings, a.id, p.stem, [("u1", "user", 1, "live", []), ("u1:a", "assistant", 2, "merged", [])])
    itemsync.sync(settings, a)
    its = got(settings)
    assert [(i.conv, i.uid) for i in its] == [("oldconv", "o1"), ("oldconv", "o1:a:0"), ("oldconv", "o1:end"),
                                              (p.stem, "u1"), (p.stem, "a1:0")]


def test_file_ingest_replaces_legacy_items_of_its_conv(settings, jsonl):
    p = jsonl(user("u1", "live", 1), asst("a1", text("real"), n=2))
    a = agent(p)
    _seed_legacy(settings, a.id, p.stem, [("u1", "user", 1, "live", []), ("u1:a", "assistant", 2, "merged", [])])
    assert itemsync.convert_legacy(settings, a.id) == 3
    itemsync.sync(settings, a)
    assert [i.uid for i in got(settings)] == ["u1", "a1:0"]


def test_chatlog_ignores_results_and_end_markers(settings, jsonl):
    p = jsonl(user("u1", "go", 1), asst("a1", tool("t1"), n=2), result("r1", "t1", 3),
              asst("a2", n=4, stop="end_turn"))
    chatlog.sync(settings, agent(p))
    ms = list(reversed(chatlog.messages(settings, "claude-abc123")))
    assert [(m.role, m.uid) for m in ms] == [("user", "u1"), ("assistant", "u1:a")]
    assert ms[1].tool_total == 1


def test_end_note_format():
    assert itemsync.end_note(2, 1, 65) == "2 replies · 1 tool call · 1m"
    assert itemsync.end_note(1, 0, None) == "1 reply · 0 tool calls"


def queued(uid, prompt, n=0, *, mode="prompt", origin="human", side=False):
    """A prompt Claude took in mid-turn (typed while it worked, or pm's Send now)."""
    return {"type": "attachment", "uuid": uid, "timestamp": ts(n), "isSidechain": side,
            "attachment": {"type": "queued_command", "prompt": prompt, "commandMode": mode,
                           "origin": {"kind": origin}, "humanTurn": True}}


def test_claude_read_entries_mid_turn_prompts_only_human(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in [
        queued("q1", "also add a test", 1),
        queued("q2", "<task-notification>done</task-notification>", 2, mode="task-notification",
               origin="task-notification"),
        queued("q3", "from a peer", 3, origin="peer"),
        queued("q4", "sidechain", 4, side=True),
        {"type": "queue-operation", "operation": "enqueue", "content": "also add a test", "timestamp": ts(1)},
        {"type": "attachment", "uuid": "x1", "timestamp": ts(5), "attachment": {"type": "edited_text_file"}},
    ]))
    es = claude.read_entries(p, 0).entries
    assert [(e.role, e.uid, e.text, e.mid_turn, e.ts) for e in es] == [
        ("user", "q1", "also add a test", True, parse_ts(ts(1)))]


def test_mid_turn_prompt_joins_open_turn(settings, jsonl):
    p = jsonl(
        user("u1", "fix it", 1),
        asst("a1", text("Looking."), tool("t1"), n=2),
        queued("q1", f"also check {TOKEN}", 3),
        result("r1", "t1", 4),
        asst("a2", tool("t2"), n=5),
        result("r2", "t2", 6),
        asst("a3", text("Done, both."), n=8, stop="end_turn"),
    )
    itemsync.sync(settings, agent(p))
    its = got(settings)
    assert brief(its) == [("user", "u1"), ("text", "a1:0"), ("tool", "t1"), ("user", "q1"), ("tool", "t2"),
                          ("text", "a3:0"), ("end", "u1:end")]  # one turn: the queued prompt closes nothing
    assert all(i.turn == "u1" for i in its)
    q, t2 = its[3], its[4]
    assert TOKEN not in q.text and q.text.startswith("also check")
    assert t2.parent == q.seq  # tools after the bubble group under it, not above it
    assert its[-1].note == "2 replies · 2 tool calls · 7s"


def test_mid_turn_prompt_without_open_turn_starts_one(settings, jsonl):
    p = jsonl(queued("q1", "hello", 1), asst("a1", text("hi"), n=2, stop="end_turn"))
    itemsync.sync(settings, agent(p))
    assert [(i.kind, i.uid, i.turn) for i in got(settings)] == [
        ("user", "q1", "q1"), ("text", "a1:0", "q1"), ("end", "q1:end", "q1")]


def test_chatlog_skips_mid_turn_prompts(settings, jsonl):
    p = jsonl(user("u1", "go", 1), asst("a1", text("one"), n=2), queued("q1", "and more", 3),
              asst("a2", text("two"), n=4, stop="end_turn"))
    chatlog.sync(settings, agent(p))
    ms = list(reversed(chatlog.messages(settings, "claude-abc123")))
    assert [(m.role, m.uid) for m in ms] == [("user", "u1"), ("assistant", "u1:a")]
    assert ms[1].text == "one\n\ntwo"


def test_old_format_cursor_rereads_conversation_once(settings, jsonl):
    """A conversation read before mid-turn prompts were items (fmt < FORMAT) is read again once,
    so a message stuck as "Queued in Claude" gets its user item."""
    p = jsonl(user("u1", "fix it", 1), asst("a1", text("Looking."), n=2),
              queued("q1", "also check the tests", 3), asst("a2", text("Done."), n=4, stop="end_turn"))
    itemsync.sync(settings, agent(p))
    assert ("user", "q1") in brief(got(settings))
    con = items.connect(settings)
    try:  # simulate a cursor written by the previous format, with the queued prompt missing
        con.execute("UPDATE chat_item_ingest SET fmt = 0")
        con.execute("DELETE FROM chat_items WHERE uid = 'q1'")
        con.commit()
    finally:
        con.close()
    assert ("user", "q1") not in brief(got(settings))
    itemsync.sync(settings, agent(p))
    assert ("user", "q1") in brief(got(settings))
    con = items.connect(settings)
    try:
        assert {r[0] for r in con.execute("SELECT fmt FROM chat_item_ingest")} == {items.FORMAT}
    finally:
        con.close()
    rev = items.latest(settings, agent(p).id)[1]
    assert itemsync.sync(settings, agent(p)) == rev  # once: an unchanged file is not read again


# ---------------------------------------------------------------- background tasks, narration notes
def bgtool(tid, name="Bash", **inp):
    return tool(tid, name, **(inp or {"command": "sleep 60", "run_in_background": True}))


def stop(tid, task_id, name="TaskStop", key="task_id"):
    return {"type": "tool_use", "id": tid, "name": name, "input": {key: task_id}}


def by_uid(settings, uid, aid="claude-abc123"):
    return next(i for i in got(settings, aid) if i.uid == uid)


def test_bg_launch_stays_running_across_turn_end(settings, jsonl):
    from helpers import bg_result

    p = jsonl(user("u1", "start it", 1), asst("a1", text("Starting."), bgtool("t1"), n=2),
              bg_result("r1", "t1", "bk1", ts(3)), asst("a2", text("Started."), n=4, stop="end_turn"),
              user("u2", "next", 10), asst("a3", text("ok"), n=11, stop="end_turn"))
    itemsync.sync(settings, agent(p))
    t1 = by_uid(settings, "t1")
    assert (t1.status, t1.note, t1.ended) == ("run", "background", 0)
    assert ("end", "u1:end") in brief(got(settings)) and ("end", "u2:end") in brief(got(settings))


def test_bg_agent_launch_without_input_flag_is_background(settings, jsonl):
    from helpers import bg_result

    p = jsonl(user("u1", "go", 1), asst("a1", tool("t1", "Agent", description="look", prompt="SECRET"), n=2),
              bg_result("r1", "t1", "a7d71838", ts(3), shape="agent"), asst("a2", n=4, stop="end_turn"))
    itemsync.sync(settings, agent(p))
    t1 = by_uid(settings, "t1")
    assert (t1.status, t1.note) == ("run", "background")


def test_bg_idle_end_does_not_finish_it(settings, jsonl, monkeypatch):
    from helpers import bg_result

    p = jsonl(user("u1", "go", 1), asst("a1", text("x"), bgtool("t1"), n=2), bg_result("r1", "t1", "bk1", ts(3)))
    last = parse_ts(ts(3))
    monkeypatch.setattr(itemsync.time, "time", lambda: last + 600)
    itemsync.sync(settings, agent(p), idle=True)
    assert got(settings)[-1].kind == "end"
    assert by_uid(settings, "t1").status == "run"


@pytest.mark.parametrize(("status", "want"), [("completed", "ok"), ("failed", "err"), ("killed", "err")])
@pytest.mark.parametrize("shape", ["user", "attachment"])
def test_bg_notification_same_sync(settings, jsonl, status, want, shape):
    from helpers import bg_result, task_note

    p = jsonl(user("u1", "go", 1), asst("a1", bgtool("t1"), n=2), bg_result("r1", "t1", "bk1", ts(3)),
              asst("a2", text("waiting"), n=4, stop="end_turn"),
              task_note("n1", "bk1", ts(30), status=status, tool_id="t1", shape=shape))
    itemsync.sync(settings, agent(p))
    t1 = by_uid(settings, "t1")
    assert (t1.status, t1.ended, t1.note) == (want, parse_ts(ts(30)), "background")


def test_bg_notification_later_sync_bumps_rev(settings, jsonl):
    from helpers import bg_result, task_note

    p = jsonl(user("u1", "go", 1), asst("a1", bgtool("t1"), n=2), bg_result("r1", "t1", "bk1", ts(3)),
              asst("a2", text("waiting"), n=4, stop="end_turn"))
    a = agent(p)
    rev = itemsync.sync(settings, a)
    assert by_uid(settings, "t1").status == "run"
    jsonl(task_note("n1", "bk1", ts(40), tool_id="t1"))
    rev2 = itemsync.sync(settings, a)
    changed = items.list_items(settings, a.id, after_rev=rev)
    assert rev2 > rev and [(i.uid, i.status, i.ended) for i in changed] == [("t1", "ok", parse_ts(ts(40)))]
    # a second notification for the same task (resumed agent) changes nothing
    jsonl(task_note("n2", "bk1", ts(50), status="failed", tool_id="t1", shape="attachment"))
    assert itemsync.sync(settings, a) == rev2
    assert by_uid(settings, "t1").status == "ok"


def test_bg_notification_by_task_id_only(settings, jsonl):
    from helpers import bg_result, task_note

    p = jsonl(user("u1", "go", 1), asst("a1", bgtool("t1"), bgtool("t2"), n=2),
              bg_result("r1", "t1", "bk1", ts(3)), bg_result("r2", "t2", "bk2", ts(3)),
              task_note("n1", "bk2", ts(9), status="failed"))
    itemsync.sync(settings, agent(p))
    assert [(by_uid(settings, u).status) for u in ("t1", "t2")] == ["run", "err"]


def test_bg_notification_for_unknown_task_is_ignored(settings, jsonl):
    from helpers import bg_result, task_note

    p = jsonl(user("u1", "go", 1), asst("a1", bgtool("t1"), n=2), bg_result("r1", "t1", "bk1", ts(3)),
              task_note("n1", "nope", ts(9)), task_note("n2", "nope2", ts(9), tool_id="t_missing"))
    itemsync.sync(settings, agent(p))
    assert by_uid(settings, "t1").status == "run"
    assert brief(got(settings)) == [("user", "u1"), ("tool", "t1")]  # notifications are not user items


@pytest.mark.parametrize(("name", "key"), [("TaskStop", "task_id"), ("KillShell", "shell_id")])
def test_bg_stop_marks_err_stopped(settings, jsonl, name, key):
    from helpers import bg_result, task_note

    p = jsonl(user("u1", "go", 1), asst("a1", bgtool("t1"), n=2), bg_result("r1", "t1", "bk1", ts(3)),
              asst("a2", stop("s1", "bk1", name, key), n=5), result("rs", "s1", 6, out="SECRET stopped"))
    a = agent(p)
    itemsync.sync(settings, a)
    t1 = by_uid(settings, "t1")
    assert (t1.status, t1.note, t1.ended) == ("err", "stopped", parse_ts(ts(5)))
    assert by_uid(settings, "s1").status == "ok"  # the stop tool itself
    # the "killed"/"stopped" notification that follows changes nothing
    rev = items.latest(settings, a.id)[1]
    jsonl(task_note("n1", "bk1", ts(7), status="killed", tool_id="t1"))
    assert itemsync.sync(settings, a) == rev
    assert (by_uid(settings, "t1").status, by_uid(settings, "t1").note) == ("err", "stopped")


def test_bg_stop_of_finished_task_is_noop(settings, jsonl):
    from helpers import bg_result, task_note

    p = jsonl(user("u1", "go", 1), asst("a1", bgtool("t1"), n=2), bg_result("r1", "t1", "bk1", ts(3)),
              task_note("n1", "bk1", ts(4), tool_id="t1"), asst("a2", stop("s1", "bk1"), n=5))
    itemsync.sync(settings, agent(p))
    t1 = by_uid(settings, "t1")
    assert (t1.status, t1.ended) == ("ok", parse_ts(ts(4)))


def test_bg_old_format_cursor_rereads(settings, jsonl):
    """A conversation read before background tasks (fmt < FORMAT) marked the launch "ok" at once;
    it is read again so the tool shows as running."""
    from helpers import bg_result

    assert items.FORMAT >= 3
    p = jsonl(user("u1", "go", 1), asst("a1", bgtool("t1"), n=2), bg_result("r1", "t1", "bk1", ts(3)))
    a = agent(p)
    itemsync.sync(settings, a)
    con = items.connect(settings)
    try:
        con.execute("UPDATE chat_item_ingest SET fmt = ?", (items.FORMAT - 1,))
        con.execute("UPDATE chat_items SET status = 'ok', ended = 1 WHERE uid = 't1'")
        con.commit()
    finally:
        con.close()
    itemsync.sync(settings, a)
    assert by_uid(settings, "t1").status == "run"


def test_bg_secret_never_reaches_db(settings, jsonl):
    from helpers import bg_result, task_note

    p = jsonl(user("u1", "go", 1),
              asst("a1", bgtool("t1"), tool("t2", "Agent", description="d", prompt="p"), n=2),
              bg_result("r1", "t1", "bk1", ts(3)), bg_result("r2", "t2", "ag1", ts(3), shape="agent"),
              task_note("n1", "bk1", ts(9), tool_id="t1"),
              task_note("n2", "ag1", ts(9), status="failed", shape="attachment"),
              asst("a2", text("done"), n=10, stop="end_turn"))
    itemsync.sync(settings, agent(p))
    chatlog.sync(settings, agent(p))
    con = sqlite3.connect(settings.db_file)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        dump = repr([con.execute(f"SELECT * FROM {t}").fetchall() for t in tables])
    finally:
        con.close()
    assert "SECRET" not in dump and "/tmp/x" not in dump and "task-notification" not in dump
    assert [by_uid(settings, u).status for u in ("t1", "t2")] == ["ok", "err"]


def test_narration_thinking_is_a_text_item(settings, jsonl):
    from helpers import thinking_sig

    p = jsonl(user("u1", "go", 1),
              asst("a1", {"type": "thinking", "thinking": "Reading the parser now.", "signature": thinking_sig(True)},
                   {"type": "thinking", "thinking": "private plan", "signature": thinking_sig(False)},
                   tool("t1"), n=2),
              asst("a2", text("Done."), n=3, stop="end_turn"))
    itemsync.sync(settings, agent(p))
    its = got(settings)
    assert [(i.kind, i.uid, i.text) for i in its if i.kind == "text"] == [
        ("text", "a1:0", "Reading the parser now."), ("text", "a2:0", "Done.")]
    assert by_uid(settings, "t1").parent == by_uid(settings, "a1:0").seq
    assert "private plan" not in repr(its)
