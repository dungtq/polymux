"""pm.chat.log: full chat history in SQLite (synthetic Claude transcripts, tmp state dir)."""

import json
import os
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pm.chat import log as chatlog
from pm.chat.sources import common
from pm.core.models import Agent, AgentKind, ChatMsg

TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def atif(steps):
    return json.dumps({"schema_version": "ATIF-v1.7", "session_id": "x", "steps": steps,
                       "final_metrics": {"total_steps": len(steps)}}, indent=2)


def step(i, source, message="", tools=0, ts="2026-10-06T15:50:48.245475578+00:00"):
    s = {"step_id": i, "timestamp": ts, "source": source, "message": message}
    if tools:
        s["tool_calls"] = [{"tool_call_id": "t", "function_name": "run", "arguments": {"cmd": "SECRET"}}] * tools
    return s


def ts(n: int) -> str:
    return f"2026-10-09T10:{n // 60:02d}:{n % 60:02d}.000Z"


def user(uid, text, n=0, **kw):
    return {"type": "user", "uuid": uid, "timestamp": ts(n), "message": {"role": "user", "content": text}, **kw}


def tool_result(uid, n=0):
    return {"type": "user", "uuid": uid, "timestamp": ts(n),
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x",
                                                      "content": "SECRET OUTPUT"}]}}


def asst(uid, *blocks, n=0, **kw):
    return {"type": "assistant", "uuid": uid, "timestamp": ts(n),
            "message": {"role": "assistant", "content": list(blocks)}, **kw}


def text(t):
    return {"type": "text", "text": t}


def think(t):
    return {"type": "thinking", "thinking": t, "signature": "sig"}


def tool(name, **inp):
    return {"type": "tool_use", "id": "t", "name": name, "input": inp or {"command": "cat /etc/secret"}}


@pytest.fixture
def jsonl(tmp_path):
    p = tmp_path / "proj" / "11111111-2222-3333-4444-555555555555.jsonl"
    p.parent.mkdir()
    p.write_text("")

    def add(*entries):
        with open(p, "a") as f:
            f.writelines(json.dumps(e) + "\n" for e in entries)
        st = p.stat()  # make sure the size/mtime signature changes even within one mtime tick
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        return p

    add.path = p
    return add


def agent(path: Path, aid="claude-abc123", kind=AgentKind.CLAUDE) -> Agent:
    # ended agent: the recorded transcript is used, no pid/session lookups
    return Agent(id=aid, kind=kind, pane_id="%1", session_id="$1", pid=0, cwd="/nowhere", transcript=str(path))


def all_msgs(settings, aid="claude-abc123"):
    return list(reversed(chatlog.messages(settings, aid, limit=500)))


def test_turn_grouping_tools_thinking(settings, jsonl):
    p = jsonl(
        user("u1", "fix the bug", 1),
        asst("a1", think("let me look"), n=2),
        asst("a2", text("Looking."), n=3),
        asst("a3", tool("Bash"), n=4),
        tool_result("r1", 5),
        asst("a4", tool("Read"), tool("Bash"), n=6),
        tool_result("r2", 7),
        asst("a5", text("Fixed it."), n=8),
        user("u2", "thanks", 9),
        asst("a6", text("You're welcome."), n=10),
    )
    conv = chatlog.sync(settings, agent(p))
    assert conv == p.stem
    ms = all_msgs(settings)
    assert [(m.role, m.uid) for m in ms] == [("user", "u1"), ("assistant", "u1:a"), ("user", "u2"),
                                              ("assistant", "u2:a")]
    turn = ms[1]
    assert isinstance(turn, ChatMsg)
    assert turn.text == "Looking.\n\nFixed it."
    assert turn.thinking == "let me look"
    assert [(t.name, t.n) for t in turn.tools] == [("Bash", 2), ("Read", 1)]
    assert turn.tool_total == 3
    assert turn.ts == common.parse_ts(ts(2))
    assert all(m.conv == p.stem for m in ms)
    assert [m.seq for m in ms] == sorted(m.seq for m in ms)
    # tool inputs and results are never stored
    raw = sqlite3.connect(settings.db_file).execute("SELECT group_concat(text || thinking || tools) "
                                                    "FROM chat_messages").fetchone()[0]
    assert "SECRET OUTPUT" not in raw and "/etc/secret" not in raw


def test_skips_meta_sidechain_systemish_and_redacted_thinking(settings, jsonl):
    p = jsonl(
        user("m1", "<command-name>/clear</command-name>", 1),
        user("m2", "caveat", 2, isMeta=True),
        user("u1", "real prompt", 3),
        asst("s1", text("subagent text"), n=4, isSidechain=True),
        user("s2", "subagent prompt", 4, isSidechain=True),
        asst("a1", think(""), text("answer"), n=5),
        user("m3", "<task-notification>done</task-notification>", 6),
        asst("a2", text("more"), n=7),
    )
    chatlog.sync(settings, agent(p))
    ms = all_msgs(settings)
    assert [(m.role, m.text) for m in ms] == [("user", "real prompt"), ("assistant", "answer\n\nmore")]
    assert ms[1].thinking == ""


def test_incremental_growing_turn_keeps_seq(settings, jsonl):
    p = jsonl(user("u1", "go", 1), asst("a1", text("step one"), tool("Bash"), n=2))
    a = agent(p)
    chatlog.sync(settings, a)
    first = all_msgs(settings)
    assert [m.role for m in first] == ["user", "assistant"]
    seq, upd = first[1].seq, first[1].updated
    assert chatlog.latest_seq(settings, a.id) == seq

    jsonl(tool_result("r1", 3), asst("a2", think("hmm"), text("step two"), tool("Bash"), tool("Edit"), n=4))
    chatlog.sync(settings, a)
    ms = all_msgs(settings)
    assert len(ms) == 2
    turn = ms[1]
    assert turn.seq == seq and turn.updated >= upd
    assert turn.text == "step one\n\nstep two"
    assert turn.thinking == "hmm"
    assert {t.name: t.n for t in turn.tools} == {"Bash": 2, "Edit": 1}
    assert turn.tools[0].name == "Bash"  # most used first
    assert chatlog.get(settings, a.id, seq) == turn

    jsonl(user("u2", "next", 5), asst("a3", text("ok"), n=6))
    chatlog.sync(settings, a)
    ms = all_msgs(settings)
    assert [m.uid for m in ms] == ["u1", "u1:a", "u2", "u2:a"]
    assert ms[2].seq > seq  # a turn stays after its user message, the next prompt after the turn


def test_unchanged_file_is_not_read(settings, jsonl, monkeypatch):
    p = jsonl(user("u1", "hi", 1), asst("a1", text("hello"), n=2))
    a = agent(p)
    chatlog.sync(settings, a)
    calls = []
    real = chatlog.chat_sources.read_entries
    monkeypatch.setattr(chatlog.chat_sources, "read_entries", lambda *x, **k: calls.append(1) or real(*x, **k))
    assert chatlog.sync(settings, a) == p.stem
    assert calls == []
    jsonl(asst("a2", text("again"), n=3))
    chatlog.sync(settings, a)
    assert calls  # changed: read
    assert len(all_msgs(settings)) == 2


def test_partial_last_line_waits(settings, jsonl):
    p = jsonl(user("u1", "hi", 1))
    with open(p, "a") as f:
        f.write(json.dumps(asst("a1", text("half"), n=2))[:20])  # writer mid-line
    a = agent(p)
    chatlog.sync(settings, a)
    assert [m.role for m in all_msgs(settings)] == ["user"]
    with open(p, "a") as f:
        f.write(json.dumps(asst("a1", text("half"), n=2))[20:] + "\n")
    chatlog.sync(settings, a)
    assert [m.text for m in all_msgs(settings)] == ["hi", "half"]


def test_masking(settings, jsonl):
    p = jsonl(user("u1", f"use token {TOKEN} please", 1),
              asst("a1", think(f"the token is {TOKEN}"), text(f"\x1b[31mexport GITHUB_TOKEN={TOKEN}\x1b[0m"), n=2))
    chatlog.sync(settings, agent(p))
    ms = all_msgs(settings)
    blob = " ".join(m.text + m.thinking for m in ms)
    assert TOKEN not in blob
    assert "••••" in ms[0].text and "••••" in ms[1].text and "••••" in ms[1].thinking
    assert "\x1b" not in blob
    raw = sqlite3.connect(settings.db_file).execute("SELECT group_concat(text || thinking) FROM chat_messages").fetchone()[0]
    assert TOKEN not in raw


def test_text_clipped_keeps_newest(settings, jsonl):
    p = jsonl(user("u1", "x" * 30000, 1), asst("a1", text("A" * 15000), n=2), asst("a2", text("B" * 15000), n=3))
    chatlog.sync(settings, agent(p))
    u, turn = all_msgs(settings)
    assert len(u.text) <= chatlog.MAX_TEXT
    assert len(turn.text) <= chatlog.MAX_TEXT
    assert turn.text.endswith("B" * 15000)


def test_pagination_order_and_multiple_convs(settings, tmp_path, jsonl):
    p = jsonl(*[e for i in range(10) for e in (user(f"u{i}", f"q{i}", 2 * i), asst(f"a{i}", text(f"r{i}"), n=2 * i + 1))])
    a = agent(p)
    chatlog.sync(settings, a)
    newest = chatlog.messages(settings, a.id, limit=5)
    assert [m.text for m in newest] == ["r9", "q9", "r8", "q8", "r7"]
    older = chatlog.messages(settings, a.id, before_seq=newest[-1].seq, limit=3)
    assert [m.text for m in older] == ["q7", "r6", "q6"]
    after = chatlog.messages(settings, a.id, after_seq=older[-1].seq, limit=2)
    assert [m.text for m in after] == ["q7", "r6"]  # the oldest 2 after it, newest first
    assert chatlog.messages(settings, a.id, after_seq=chatlog.latest_seq(settings, a.id)) == []

    # /clear: a new session file for the same agent id = a new conv; listing spans both
    p2 = tmp_path / "proj" / "99999999-2222-3333-4444-555555555555.jsonl"
    p2.write_text(json.dumps(user("n1", "fresh", 100)) + "\n")
    a2 = agent(p2)
    assert chatlog.sync(settings, a2) == p2.stem
    top = chatlog.messages(settings, a.id, limit=2)
    assert [(m.text, m.conv) for m in top] == [("fresh", p2.stem), ("r9", p.stem)]
    # another agent id sees nothing of it
    assert chatlog.messages(settings, "claude-other") == []
    assert chatlog.latest_seq(settings, "claude-other") == 0
    assert chatlog.get(settings, "claude-other", top[0].seq) is None


def test_cap_per_agent(settings, jsonl, monkeypatch):
    monkeypatch.setattr(chatlog, "MAX_PER_AGENT", 6)
    p = jsonl(*[e for i in range(5) for e in (user(f"u{i}", f"q{i}", 2 * i), asst(f"a{i}", text(f"r{i}"), n=2 * i + 1))])
    chatlog.sync(settings, agent(p))
    ms = all_msgs(settings)
    assert [m.text for m in ms] == ["q2", "r2", "q3", "r3", "q4", "r4"]


def test_first_ingest_bounded_to_tail(settings, jsonl, monkeypatch):
    monkeypatch.setattr(common, "FIRST_BYTES", 2000)
    p = jsonl(*[e for i in range(40) for e in (user(f"u{i}", f"question {i}", i), asst(f"a{i}", text(f"answer {i}"), n=i))])
    chatlog.sync(settings, agent(p))
    ms = all_msgs(settings)
    assert ms and len(ms) < 40
    assert ms[-1].text == "answer 39"
    texts = [m.text for m in ms]
    assert "question 0" not in texts


def test_truncation_resets_conv(settings, jsonl):
    p = jsonl(user("u1", "one", 1), asst("a1", text("first answer"), n=2))
    a = agent(p)
    chatlog.sync(settings, a)
    p.write_text(json.dumps(user("u9", "rewritten", 3)) + "\n")  # shorter than the cursor
    chatlog.sync(settings, a)
    assert [m.text for m in all_msgs(settings)] == ["rewritten"]


def test_never_raises_on_garbage(settings, tmp_path, jsonl):
    p = jsonl()
    with open(p, "ab") as f:
        f.write(b"\x00\xff not json\n[1,2]\n\"str\"\n{\"type\": \"assistant\", \"message\": 5}\n"
                b"{\"type\": \"user\", \"message\": {\"content\": [1, null, {\"type\": \"text\"}]}}\n")
    assert chatlog.sync(settings, agent(p)) == p.stem
    assert chatlog.messages(settings, "claude-abc123") == []
    # missing file, unknown kind, broken db path
    assert chatlog.sync(settings, agent(tmp_path / "missing.jsonl")) == ""
    assert chatlog.sync(settings, agent(p, kind=AgentKind.OTHER)) == ""
    bad = type(settings)(tmux_socket="pmtest-unused", state_dir=p)  # a file, not a dir
    assert chatlog.sync(bad, agent(p)) == ""
    assert chatlog.messages(bad, "x") == [] and chatlog.latest_seq(bad, "x") == 0 and chatlog.get(bad, "x", 1) is None


def test_db_mode_0600(settings, jsonl):
    p = jsonl(user("u1", "hi", 1))
    chatlog.sync(settings, agent(p))
    assert stat.S_IMODE(os.stat(settings.db_file).st_mode) == 0o600


def test_live_claude_agent_resolves_transcript(settings, tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    uuid = "abcdefab-1111-2222-3333-444444444444"
    from pm.chat.sources.claude import transcript_path

    tp = transcript_path("/home/me/dev/x", uuid, home)
    tp.parent.mkdir(parents=True)
    tp.write_text(json.dumps(user("u1", "hello", 1)) + "\n")
    monkeypatch.setattr("pm.chat.log.live_session_id", lambda pid, **k: None)
    a = Agent(id="claude-live01", kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=4242,
              cwd="/home/me/dev/x", session_uuid=uuid)
    assert chatlog.sync(settings, a) == uuid
    assert [m.text for m in chatlog.messages(settings, a.id)] == ["hello"]


def test_codex_ingest(settings, tmp_path):
    # codex rollout
    rp = tmp_path / "rollout-x.jsonl"
    lines = [
        {"type": "session_meta", "payload": {"cwd": "/x"}},
        {"type": "response_item", "timestamp": ts(1), "payload": {"type": "message", "role": "user", "id": "m0",
                                                                  "content": [{"type": "input_text", "text": "<environment_context>x</environment_context>"}]}},
        {"type": "response_item", "timestamp": ts(2), "payload": {"type": "message", "role": "user", "id": "m1",
                                                                  "content": [{"type": "input_text", "text": "say hi"}]}},
        {"type": "response_item", "timestamp": ts(3), "payload": {"type": "reasoning", "id": "r1",
                                                                  "summary": [{"type": "summary_text", "text": "think"}]}},
        {"type": "response_item", "timestamp": ts(4), "payload": {"type": "custom_tool_call", "id": "c1", "name": "exec",
                                                                  "input": "rm -rf /"}},
        {"type": "response_item", "timestamp": ts(5), "payload": {"type": "custom_tool_call_output", "id": "c2",
                                                                  "output": "SECRET"}},
        {"type": "response_item", "timestamp": ts(6), "payload": {"type": "message", "role": "assistant", "id": "m2",
                                                                  "content": [{"type": "output_text", "text": "hi!"}]}},
    ]
    rp.write_text("".join(json.dumps(e) + "\n" for e in lines))
    a = agent(rp, "codex-aaaaaa", AgentKind.CODEX)
    assert chatlog.sync(settings, a) == "rollout-x"
    ms = list(reversed(chatlog.messages(settings, a.id)))
    assert [(m.role, m.text, m.thinking) for m in ms] == [("user", "say hi", ""), ("assistant", "hi!", "think")]
    assert [(t.name, t.n) for t in ms[1].tools] == [("exec", 1)]
    assert ms[1].uid == "m1:a"


def test_devin_ingest_incremental(settings, tmp_path):
    p = tmp_path / "zippy.json"
    steps = [step(0, "system", "sys"), step(1, "user", "do it"), step(2, "agent", "working", tools=2)]
    p.write_text(atif(steps))
    a = agent(p, "devin-aaaaaa", AgentKind.DEVIN)
    assert chatlog.sync(settings, a) == "zippy"
    s3 = step(3, "agent", "done")
    s3["reasoning_content"] = "because"
    p.write_text(atif(steps + [s3, step(4, "user", "next")]))
    chatlog.sync(settings, a)
    ms = list(reversed(chatlog.messages(settings, a.id)))
    assert [(m.role, m.text) for m in ms] == [("user", "do it"), ("assistant", "working\n\ndone"), ("user", "next")]
    assert ms[1].thinking == "because" and ms[1].tool_total == 2


def test_cursor_ingest(settings, tmp_path):
    db = tmp_path / "chat-uuid" / "store.db"
    db.parent.mkdir()
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB)")
    rows = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": [{"type": "text", "text": "<user_info>\nOS</user_info>"}]},
        {"role": "user", "content": [{"type": "text", "text": "<timestamp>Thursday, Oct 8, 2026, 6:58 PM (UTC+7)"
                                                              "</timestamp>\n<user_query>\nexplain\n</user_query>"}]},
        {"role": "assistant", "content": [{"type": "reasoning", "text": "ponder"}, {"type": "text", "text": "Sure."},
                                          {"type": "tool-call", "toolName": "Read", "args": {"path": "/etc/x"}}]},
        {"role": "tool", "content": [{"type": "tool-result", "result": "SECRET"}]},
    ]
    for i, r in enumerate(rows):
        con.execute("INSERT INTO blobs VALUES (?, ?)", (f"id{i}", json.dumps(r).encode()))
    con.execute("INSERT INTO blobs VALUES ('bin', x'0a0b')")
    con.commit()
    con.close()
    a = agent(db, "cursor-aaaaaa", AgentKind.CURSOR)
    assert chatlog.sync(settings, a) == "chat-uuid"
    ms = list(reversed(chatlog.messages(settings, a.id)))
    assert [(m.role, m.text, m.thinking) for m in ms] == [("user", "explain", ""), ("assistant", "Sure.", "ponder")]
    assert ms[0].ts == datetime(2026, 10, 8, 11, 58, tzinfo=UTC).timestamp()
    assert ms[1].tools[0].name == "Read"


def _pb_varint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _pb(field: int, value: bytes | int) -> bytes:
    if isinstance(value, int):
        return _pb_varint(field << 3) + _pb_varint(value)
    return _pb_varint(field << 3 | 2) + _pb_varint(len(value)) + value


def test_antigravity_ingest(settings, tmp_path):
    db = tmp_path / "0c20fc1b-41cd-4c6c-b0b8-39058923ccb8.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE steps (idx integer PRIMARY KEY, step_type integer, step_payload blob)")
    tsb = _pb(5, _pb(6, _pb(1, 1791463454)))
    user_p = _pb(1, 14) + tsb + _pb(19, _pb(2, b"help me"))
    reply = _pb(1, 15) + tsb + _pb(20, _pb(1, b"Here.") + _pb(3, b"thinking it over")
                                   + _pb(7, _pb(1, b"call_1") + _pb(2, b"view_file") + _pb(3, b'{"p":"/x"}'))
                                   + _pb(7, _pb(2, b"view_file")))
    tool_step = _pb(1, 132) + tsb
    con.executemany("INSERT INTO steps VALUES (?,?,?)", [(0, 14, user_p), (1, 15, reply), (2, 132, tool_step)])
    con.commit()
    con.close()
    a = agent(db, "antigravity-aaaaaa", AgentKind.ANTIGRAVITY)
    assert chatlog.sync(settings, a) == db.stem
    ms = list(reversed(chatlog.messages(settings, a.id)))
    assert [(m.role, m.text, m.thinking) for m in ms] == [("user", "help me", ""), ("assistant", "Here.", "thinking it over")]
    assert [(t.name, t.n) for t in ms[1].tools] == [("view_file", 2)]
    assert ms[0].ts == 1791463454


def _seed(settings, rows):
    chatlog._connect(settings).close()
    con = sqlite3.connect(settings.db_file)
    for i, (aid, role, text) in enumerate(rows):
        con.execute("INSERT INTO chat_messages (agent_id, conv, uid, role, ts, text) VALUES (?,?,?,?,?,?)",
                    (aid, "c", f"u{i}", role, 100 + i, text))
    con.commit()
    con.close()


def test_search_literal_newest_first_masked(settings):  # noqa: F811
    long = "x" * 200 + " needle here " + "y" * 200
    _seed(settings, [("claude-a", "user", "100% sure_thing"), ("claude-b", "assistant", long),
                     ("claude-a", "assistant", "NEEDLE again"), ("claude-c", "user", "1005 surething")])
    hits = chatlog.search(settings, "needle", 5)
    assert [h["agent_id"] for h in hits] == ["claude-a", "claude-b"]
    assert hits[1]["snippet"].startswith("…") and hits[1]["snippet"].endswith("…") and len(hits[1]["snippet"]) < 130
    assert [h["agent_id"] for h in chatlog.search(settings, "100% sure_thing")] == ["claude-a"]  # wildcards literal
    assert len(chatlog.search(settings, "needle", 1)) == 1
    assert chatlog.search(settings, "ne") == []


def test_search_snippet_masks(settings):  # noqa: F811
    from pm.security.mask import render_pane

    secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    if render_pane(secret) == secret:
        pytest.skip("mask has no github token rule")
    _seed(settings, [("claude-a", "user", f"token {secret} marker")])
    (h,) = chatlog.search(settings, "marker")
    assert secret not in h["snippet"]


def _narr(t):
    from helpers import thinking_sig

    return {"type": "thinking", "thinking": t, "signature": thinking_sig(True)}


def test_narration_thinking_is_reply_text(settings, jsonl):
    p = jsonl(user("u1", "go", 1), asst("a1", _narr("Looking at it."), think("private"), text("Fixed."), n=2))
    chatlog.sync(settings, agent(p))
    ms = all_msgs(settings)
    assert (ms[1].text, ms[1].thinking) == ("Looking at it.\n\nFixed.", "private")


def test_old_format_cursor_rereads_once(settings, jsonl, monkeypatch):
    """A conversation read by an older parser (fmt < FORMAT: narration notes were thinking) is read again."""
    assert chatlog.FORMAT >= 1
    p = jsonl(user("u1", "go", 1), asst("a1", _narr("Looking at it."), text("Fixed."), n=2))
    a = agent(p)
    chatlog.sync(settings, a)
    con = sqlite3.connect(settings.db_file)
    con.execute("UPDATE chat_ingest SET fmt = 0")
    con.execute("UPDATE chat_messages SET text = 'Fixed.', thinking = 'Looking at it.' WHERE role = 'assistant'")
    con.commit()
    con.close()
    chatlog.sync(settings, a)  # unchanged file, old format: read again from scratch
    ms = all_msgs(settings)
    assert [(m.role, m.text, m.thinking) for m in ms] == [
        ("user", "go", ""), ("assistant", "Looking at it.\n\nFixed.", "")]
    con = sqlite3.connect(settings.db_file)
    assert {r[0] for r in con.execute("SELECT fmt FROM chat_ingest")} == {chatlog.FORMAT}
    con.close()
    calls = []
    real = chatlog.chat_sources.read_entries
    monkeypatch.setattr(chatlog.chat_sources, "read_entries", lambda *x, **k: calls.append(1) or real(*x, **k))
    chatlog.sync(settings, a)
    assert calls == []  # once


def test_migrates_chat_ingest_without_fmt(settings, jsonl):
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(settings.db_file)
    con.execute("CREATE TABLE chat_ingest (agent_id TEXT NOT NULL, path TEXT NOT NULL, kind TEXT NOT NULL,"
                " conv TEXT NOT NULL, inode INTEGER NOT NULL DEFAULT 0, size INTEGER NOT NULL DEFAULT -1,"
                " mtime REAL NOT NULL DEFAULT 0, pos INTEGER NOT NULL DEFAULT -1, last_user TEXT NOT NULL DEFAULT '',"
                " turn TEXT NOT NULL DEFAULT '', updated REAL NOT NULL DEFAULT 0, PRIMARY KEY (agent_id, path))")
    con.commit()
    con.close()
    chatlog._INIT.discard(str(settings.db_file))
    p = jsonl(user("u1", "go", 1), asst("a1", _narr("Note."), n=2))
    chatlog.sync(settings, agent(p))
    assert [m.text for m in all_msgs(settings)] == ["go", "Note."]
    cols = [r[1] for r in sqlite3.connect(settings.db_file).execute("PRAGMA table_info(chat_ingest)")]
    assert cols.count("fmt") == 1


def test_context_text_order_trim_mask_and_empty(settings, jsonl):
    assert chatlog.context_text(settings, "claude-none") == ""
    p = jsonl(user("u1", "first question", 1), asst("a1", think("hidden thought"), text("first answer"),
                                                     tool("Bash"), n=2),
              user("u2", "token " + TOKEN, 3), asst("a2", text("second answer"), n=4))
    chatlog.sync(settings, agent(p))
    out = chatlog.context_text(settings, "claude-abc123")
    assert out.startswith("User: first question\n\nAssistant: first answer\n\nUser: token ")
    assert out.endswith("\n\nAssistant: second answer") and out.count("User: ") == 2
    assert TOKEN not in out and "hidden thought" not in out and "/etc/secret" not in out
    short = chatlog.context_text(settings, "claude-abc123", max_chars=30)
    assert len(short) <= 30 and short.endswith("Assistant: second answer") and "first" not in short


def test_context_text_pages_past_max_limit(settings, jsonl, monkeypatch):
    p = jsonl(*[e for i in range(6)
                for e in (user(f"u{i}", f"q{i}", 2 * i), asst(f"a{i}", text(f"r{i}"), n=2 * i + 1))])
    chatlog.sync(settings, agent(p))
    monkeypatch.setattr(chatlog, "MAX_LIMIT", 2)
    out = chatlog.context_text(settings, "claude-abc123")
    assert out.startswith("User: q0\n\nAssistant: r0") and out.endswith("User: q5\n\nAssistant: r5")


def test_context_text_reads_a_bounded_number_of_pages(settings, monkeypatch):
    # a long tool-only stretch (no text) must not page through the whole stored history every turn
    calls = []

    def fake(st, aid, before_seq=None, limit=50, **kw):
        calls.append(before_seq)
        start = 100000 if before_seq is None else before_seq
        return [ChatMsg(seq=start - i, uid=f"u{start - i}", conv="c", role="assistant", ts=0.0, text="")
                for i in range(1, limit + 1)]
    monkeypatch.setattr(chatlog, "messages", fake)
    assert chatlog.context_text(settings, "claude-x") == ""
    assert len(calls) == chatlog.CONTEXT_PAGES
