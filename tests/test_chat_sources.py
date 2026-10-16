"""Readers for devin / codex / cursor stores (synthetic fixtures only)."""

import builtins
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from pm.chat import live as chat
from pm.chat import sources as chat_sources
from pm.chat.sources import codex, cursor, devin
from pm.core.models import Agent, AgentKind, AgentState

TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
CWD = "/home/me/dev/proj_x"


def mkproc(root: Path, pid: int, ppid: int, comm="devin"):
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "stat").write_text(f"{pid} ({comm}) S {ppid} " + " ".join(["0"] * 30))


def atif(steps):
    return json.dumps({"schema_version": "ATIF-v1.7", "session_id": "x", "agent": {"tool_definitions": [{"a": 1}] * 50},
                       "steps": steps, "final_metrics": {"total_steps": len(steps)}}, indent=2)


def step(i, source, message="", tools=0, ts="2026-10-06T15:50:48.245475578+00:00"):
    s = {"step_id": i, "timestamp": ts, "source": source, "message": message}
    if tools:
        s["tool_calls"] = [{"tool_call_id": "t", "function_name": "run", "arguments": {}}] * tools
    return s


@pytest.fixture
def dhome(tmp_path):
    root = tmp_path / ".local" / "share" / "devin" / "cli"
    (root / "session_locks").mkdir(parents=True)
    (root / "transcripts").mkdir()
    return tmp_path


def devin_session(home, name, lock_pid, steps):
    root = home / ".local" / "share" / "devin" / "cli"
    (root / "session_locks" / f"{name}.lock").write_text(str(lock_pid))
    (root / "transcripts" / f"{name}.json").write_text(atif(steps))
    return root / "transcripts" / f"{name}.json"


# ---------------------------------------------------------------- devin
def test_devin_locate_by_descendant_lock_pid(dhome, tmp_path):
    proc = tmp_path / "proc"
    mkproc(proc, 100, 1)
    mkproc(proc, 101, 100)  # acp child holds the lock
    mkproc(proc, 200, 1)
    mkproc(proc, 201, 200)
    mine = devin_session(dhome, "zippy-ocarina", 101, [step(0, "user", "hi")])
    devin_session(dhome, "other-one", 201, [step(0, "user", "other")])
    devin_session(dhome, "dead-one", 999, [step(0, "user", "dead")])  # stale lock pid
    assert devin.locate(100, dhome, proc) == mine
    assert devin.locate(300, dhome, proc) is None


def test_devin_latest_skips_tool_steps(dhome):
    p = devin_session(dhome, "a-b", 5, [
        step(0, "system", "sys prompt"),
        step(1, "agent", "first answer"),
        step(2, "user", "next"),
        step(3, "agent", "", tools=1),
        step(4, "agent", "Ran Command", tools=1) if False else step(4, "agent", "", tools=2),
        step(5, "agent", "final prose " + TOKEN, ts="2026-10-06T15:51:00Z"),
        step(6, "agent", "", tools=1),
    ])
    lat = devin.read_latest(p)
    assert lat.role == "assistant" and lat.text.startswith("final prose")
    assert lat.ts > 0 and lat.recent[1] == "first answer"


def test_devin_tail_bounded_with_partial_head(dhome):
    steps = [step(i, "agent", "x" * 3000) for i in range(200)] + [step(200, "agent", "newest")]
    p = devin_session(dhome, "big", 5, steps)
    assert p.stat().st_size > chat_sources.TAIL_BYTES
    assert devin.read_latest(p).text == "newest"


def test_devin_no_prose(dhome):
    p = devin_session(dhome, "n", 5, [step(0, "user", "hi"), step(1, "agent", "", tools=1)])
    assert devin.read_latest(p) is None


# ---------------------------------------------------------------- codex
def rollout(home, name, cwd, entries, day="2026/10/06"):
    d = home / ".codex" / "sessions" / day
    d.mkdir(parents=True, exist_ok=True)
    meta = {"type": "session_meta", "timestamp": "2026-10-06T07:34:15.145Z",
            "payload": {"id": "i", "cwd": cwd, "base_instructions": {"text": "b" * 20000}}}
    p = d / f"rollout-{name}.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in [meta, *entries]) + "\n")
    return p


def amsg(text, phase="final_answer", ts="2026-10-06T07:35:00.000Z"):
    return {"type": "response_item", "timestamp": ts, "payload": {
        "type": "message", "role": "assistant", "phase": phase, "content": [{"type": "output_text", "text": text}]}}


def test_codex_locate_and_read(tmp_path, monkeypatch):
    codex._CACHE.clear()
    now = time.time()
    mine = rollout(tmp_path, "mine", CWD, [amsg("older reply", "commentary"),
                                           {"type": "response_item", "payload": {"type": "function_call", "name": "x"}},
                                           {"type": "response_item", "payload": {"type": "message", "role": "user",
                                            "content": [{"type": "input_text", "text": "u"}]}},
                                           amsg("latest reply")])
    other = rollout(tmp_path, "other", "/somewhere/else", [amsg("not mine")])
    os.utime(mine, (now, now))
    os.utime(other, (now + 5, now + 5))  # newer, different cwd
    day = time.strftime("%Y/%m/%d", time.localtime(now))
    if day != "2026/10/06":  # the helper scans dates relative to now
        for p in (mine, other):
            nd = tmp_path / ".codex" / "sessions" / day
            nd.mkdir(parents=True, exist_ok=True)
            p.rename(nd / p.name)
        mine = nd / mine.name
    got = codex.locate(1, CWD, now - 60, tmp_path)
    assert got == mine
    lat = codex.read_latest(got)
    assert (lat.role, lat.text) == ("assistant", "latest reply") and lat.recent == ("latest reply", "older reply")
    assert codex.locate(1, "/nowhere", now - 60, tmp_path) is None


def test_codex_ignores_files_older_than_process(tmp_path):
    codex._CACHE.clear()
    now = time.time()
    day = time.strftime("%Y/%m/%d", time.localtime(now))
    p = rollout(tmp_path, "old", CWD, [amsg("x")], day=day)
    os.utime(p, (now - 5000, now - 5000))
    assert codex.locate(2, CWD, now - 100, tmp_path) is None


# ---------------------------------------------------------------- cursor
def mkchat(home, cwd, uuid, msgs, mtime=None):
    import hashlib

    d = home / ".cursor" / "chats" / hashlib.md5(cwd.encode()).hexdigest() / uuid
    d.mkdir(parents=True)
    con = sqlite3.connect(d / "store.db")
    con.execute("CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB)")
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO blobs VALUES ('p', ?)", (b"\x0a\x20" + b"\x00" * 40,))  # protobuf node
    for i, m in enumerate(msgs):
        con.execute("INSERT INTO blobs VALUES (?, ?)", (f"b{i}", json.dumps(m).encode()))
    con.commit()
    con.close()
    if mtime:
        os.utime(d / "store.db", (mtime, mtime))
    return d / "store.db"


def test_cursor_locate_newest_and_read(tmp_path):
    old = mkchat(tmp_path, CWD, "old", [{"role": "assistant", "content": [{"type": "text", "text": "stale"}]}],
                 mtime=time.time() - 9000)
    new = mkchat(tmp_path, CWD, "new", [
        {"role": "user", "content": [{"type": "text", "text": "q"}]},
        {"role": "assistant", "content": [{"type": "reasoning", "text": "think"}, {"type": "text", "text": "answer one"}]},
        {"role": "tool", "content": [{"type": "tool-result", "result": "Ran Command"}]},
        {"role": "assistant", "content": [{"type": "reasoning", "text": "t"}, {"type": "tool-call", "toolName": "x"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "final " + TOKEN}]},
        {"role": "assistant", "content": [{"type": "tool-call", "toolName": "y"}]},
    ])
    assert cursor.locate(CWD, tmp_path) == new != old
    lat = cursor.read_latest(new)
    assert lat.text.startswith("final") and lat.recent[1] == "answer one"
    assert cursor.locate("/no/such", tmp_path) is None


def _hold(proc: Path, pid: int, db: Path, name: str = "4") -> None:
    fd = proc / str(pid) / "fd"
    fd.mkdir(parents=True, exist_ok=True)
    os.symlink(db, fd / name)


def test_cursor_locate_uses_the_open_store_not_the_newest(tmp_path):
    """A live cursor-agent is the chat it has open, even when a newer store exists in the same cwd."""
    old = mkchat(tmp_path, CWD, "11111111-1111-4111-8111-111111111111",
                 [{"role": "assistant", "content": [{"type": "text", "text": "mine"}]}],
                 mtime=time.time() - 9000)
    mkchat(tmp_path, CWD, "22222222-2222-4222-8222-222222222222",
           [{"role": "assistant", "content": [{"type": "text", "text": "other"}]}],
           mtime=time.time())
    proc = tmp_path / "proc"
    _hold(proc, 77, old)
    assert cursor.locate(CWD, tmp_path, 77, proc) == old
    assert chat_sources.session_file(AgentKind.CURSOR, pid=77, cwd=CWD, home=tmp_path, proc_root=proc) == old
    wal = old.with_name("store.db-wal")
    wal.write_bytes(b"")
    _hold(proc, 78, wal)
    assert cursor.locate(CWD, tmp_path, 78, proc) == old
    assert cursor.locate(CWD, tmp_path, 79, proc) is None  # live, nothing open: not the newest file
    _hold(proc, 80, tmp_path / "elsewhere" / "store.db")
    assert cursor.locate(CWD, tmp_path, 80, proc) is None
    _hold(proc, 81, old.parent.parent / ".." / "store.db")
    assert cursor.locate(CWD, tmp_path, 81, proc) is None


def test_cursor_read_is_readonly(tmp_path):
    db = mkchat(tmp_path, CWD, "c", [{"role": "assistant", "content": "plain string"}])
    before = db.read_bytes()
    assert cursor.read_latest(db).text == "plain string"
    assert db.read_bytes() == before


# ---------------------------------------------------------------- chat_view integration
def agent(kind, pid=100, cwd=CWD, state=AgentState.IDLE, age=60.0):
    return Agent(id=f"{kind.value}-1", kind=kind, pane_id="%1", session_id="$1", pid=pid, cwd=cwd,
                 state=state, age_s=age)


def test_chat_view_devin_prose_not_ran_command(dhome, tmp_path, monkeypatch, settings):
    monkeypatch.setenv("HOME", str(dhome))
    proc = tmp_path / "proc"
    mkproc(proc, 100, 1)
    mkproc(proc, 101, 100)
    devin_session(dhome, "s-1", 101, [step(0, "agent", f"All done. token {TOKEN}")])
    monkeypatch.setattr(chat_sources, "PROC", proc)
    monkeypatch.setattr(devin, "_root", lambda home: Path(home) / ".local/share/devin/cli")
    orig = chat_sources.session_file

    def sf(kind, **kw):
        kw["proc_root"] = proc
        return orig(kind, **kw)

    monkeypatch.setattr(chat_sources, "session_file", sf)
    pane = " ● Ran Command\n │ $ ls\n └ exit 0\n\n❭\n"
    v = chat.chat_view(settings, agent(AgentKind.DEVIN), pane)
    assert v.source == "transcript" and v.last_text.startswith("All done") and TOKEN not in v.last_text
    assert "Ran Command" not in v.last_text


def _bind_cursor(monkeypatch, tmp_path, db, pid=100):
    """Point chat_view's session lookup at the store this pid has open."""
    proc = tmp_path / "proc-cursor"
    _hold(proc, pid, db)
    real = chat_sources.session_file

    def sf(kind, **kw):
        if kind == AgentKind.CURSOR:
            kw["proc_root"] = proc
        return real(kind, **kw)

    monkeypatch.setattr(chat_sources, "session_file", sf)


def test_chat_view_cursor_and_codex(tmp_path, monkeypatch, settings):
    monkeypatch.setenv("HOME", str(tmp_path))
    codex._CACHE.clear()
    db = mkchat(tmp_path, CWD, "33333333-3333-4333-8333-333333333333",
                [{"role": "assistant", "content": [{"type": "text", "text": "cursor says hi"}]}])
    _bind_cursor(monkeypatch, tmp_path, db)
    v = chat.chat_view(settings, agent(AgentKind.CURSOR), "  $ ls\n\n→ x\n")
    assert (v.source, v.last_text) == ("transcript", "cursor says hi")
    now = time.time()
    day = time.strftime("%Y/%m/%d", time.localtime(now))
    p = rollout(tmp_path, "r", CWD, [amsg("codex says hi")], day=day)
    os.utime(p, (now, now))
    v = chat.chat_view(settings, agent(AgentKind.CODEX), "› ask\n")
    assert (v.source, v.last_text) == ("transcript", "codex says hi")


def test_pane_newer_than_store_while_running(tmp_path, monkeypatch, settings):
    monkeypatch.setenv("HOME", str(tmp_path))
    db = mkchat(tmp_path, CWD, "44444444-4444-4444-8444-444444444444",
                [{"role": "assistant", "content": [{"type": "text", "text": "old reply"}]}])
    _bind_cursor(monkeypatch, tmp_path, db)
    pane = "  old reply\n\n  $ ls\n    out\n\n  brand new streaming prose here\n\n→ next\n"
    v = chat.chat_view(settings, agent(AgentKind.CURSOR, state=AgentState.RUNNING), pane)
    assert v.source == "pane" and v.last_text == "brand new streaming prose here"
    v = chat.chat_view(settings, agent(AgentKind.CURSOR, state=AgentState.IDLE), pane)
    assert v.last_text == "old reply"  # idle: the store is authoritative


def test_no_credential_or_foreign_files_opened(dhome, tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    mkproc(proc, 100, 1)
    mkproc(proc, 101, 100)
    devin_session(dhome, "mine", 101, [step(0, "agent", "hi")])
    devin_session(dhome, "foreign", 777, [step(0, "agent", "foreign")])
    cred = dhome / ".local" / "share" / "devin" / "credentials.toml"
    cred.write_text("secret")
    (dhome / ".codex").mkdir()
    (dhome / ".codex" / "auth.json").write_text("{}")
    (dhome / ".cursor").mkdir()
    (dhome / ".cursor" / "mcp.json").write_text("{}")
    opened: list[str] = []
    real_open, real_read = builtins.open, Path.read_text

    def spy_open(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)

    def spy_read(self, *a, **k):
        opened.append(str(self))
        return real_read(self, *a, **k)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(Path, "read_text", spy_read)
    p = chat_sources.session_file(AgentKind.DEVIN, pid=100, cwd=CWD, home=dhome, proc_root=proc)
    assert devin.read_latest(p).text == "hi"
    chat_sources.session_file(AgentKind.CODEX, pid=1, cwd=CWD, age_s=10, home=dhome)
    chat_sources.session_file(AgentKind.CURSOR, pid=1, cwd=CWD, home=dhome)
    bad = ("credentials", "auth.json", "mcp.json", ".key", ".env", "foreign.json")
    assert not [o for o in opened if any(b in o for b in bad)]


# ---------------------------------------------------------------- read_entries (pm.chat.log)
def test_read_new_lines_bounds(tmp_path):
    from pm.chat.sources.common import read_new_lines

    p = tmp_path / "f.jsonl"
    p.write_bytes(b"aaaa\nbbbb\ncccc\ndd")  # 17 bytes; lines start at 0, 5, 10
    lines, pos, more = read_new_lines(p, -1, first=10)  # starts at byte 7, mid "bbbb": partial dropped
    assert [t for _, t in lines] == ["cccc"] and pos == 15 and not more
    lines, _, _ = read_new_lines(p, -1, first=12)  # starts at byte 5, exactly a line start: kept
    assert [(o, t) for o, t in lines] == [(5, "bbbb"), (10, "cccc")]
    lines, pos4, more = read_new_lines(p, 0, chunk=7)
    assert [t for _, t in lines] == ["aaaa"] and pos4 == 5 and more
    assert read_new_lines(p, 15) == ([], 15, False)  # "dd" has no newline yet


def test_codex_read_entries_skips_injected_and_counts_tools(tmp_path):
    p = tmp_path / "rollout-y.jsonl"
    items = [
        {"type": "response_item", "payload": {"type": "message", "role": "user", "id": "a",
                                              "content": [{"type": "input_text", "text": "# AGENTS.md instructions for /x"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "developer", "id": "d",
                                              "content": [{"type": "input_text", "text": "dev"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "id": "b",
                                              "content": [{"type": "input_text", "text": "real"}]}},
        {"type": "response_item", "payload": {"type": "function_call", "id": "f", "name": "shell", "arguments": "x"}},
        {"type": "response_item", "payload": {"type": "local_shell_call", "id": "g"}},
        {"type": "response_item", "payload": {"type": "function_call_output", "id": "h", "output": TOKEN}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "dup"}},
    ]
    p.write_text("".join(json.dumps(e) + "\n" for e in items))
    ch = codex.read_entries(p, -1)
    assert [(e.role, e.text, e.tools) for e in ch.entries] == [
        ("user", "real", ()), ("assistant", "", ("shell",)), ("assistant", "", ("local_shell",))]
    assert ch.pos == p.stat().st_size and not ch.more
    assert codex.read_entries(p, ch.pos).entries == []


def test_devin_read_entries_cursor_by_step_id(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(atif([step(0, "user", "hi"), step(1, "agent", "", tools=1)]))
    ch = devin.read_entries(p, -1)
    assert [(e.role, e.uid, e.tools) for e in ch.entries] == [("user", "s0", ()), ("assistant", "s1", ("run",))]
    assert ch.pos == 1
    assert devin.read_entries(p, 1).entries == []
    p.write_text(atif([step(0, "user", "hi")]))  # mid-rewrite / shorter: cursor kept, no reset
    ch2 = devin.read_entries(p, 1)
    assert ch2.entries == [] and ch2.pos == 1 and not ch2.reset


def test_cursor_read_entries_reset_when_rows_shrink(tmp_path):
    db = tmp_path / "c" / "store.db"
    db.parent.mkdir()
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB)")
    con.execute("INSERT INTO blobs VALUES ('a', ?)", (json.dumps({"role": "user", "content": "plain prompt"}).encode(),))
    con.commit()
    con.close()
    ch = cursor.read_entries(db, -1)
    assert [(e.role, e.text) for e in ch.entries] == [("user", "plain prompt")] and ch.pos == 1
    assert cursor.read_entries(db, 5).reset


def test_claude_read_entries_uid_and_blocks(tmp_path):
    from pm.chat.sources import claude

    p = tmp_path / "s.jsonl"
    entries = [
        {"type": "user", "uuid": "u1", "message": {"content": [{"type": "text", "text": "go"}]}},
        {"type": "assistant", "uuid": "a1", "message": {"content": [
            {"type": "redacted_thinking", "data": "x"}, {"type": "thinking", "thinking": "", "signature": "s"},
            {"type": "server_tool_use", "name": "web_search"}, {"type": "tool_use", "name": "m" * 100}]}},
        {"type": "user", "message": {"content": "no uuid"}},
        {"type": "summary", "summary": "x"},
    ]
    p.write_text("".join(json.dumps(e) + "\n" for e in entries))
    ch = claude.read_entries(p, -1)
    assert [(e.role, e.uid) for e in ch.entries][:2] == [("user", "u1"), ("assistant", "a1")]
    assert ch.entries[1].thinking == "" and ch.entries[1].tools == ("web_search", "m" * 64)
    assert ch.entries[2].uid.startswith("o") and ch.entries[2].text == "no uuid"


# ---------------------------------------------------------------- claude: narration notes, background tasks
def _cts(n: int) -> str:
    return f"2026-10-12T16:{n // 60:02d}:{n % 60:02d}.000Z"


def _claude(tmp_path, *entries):
    from pm.chat.sources import claude

    p = tmp_path / "bg.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return claude.read_entries(p, 0).entries


def _casst(uid, *blocks, n=0, stop=None):
    return {"type": "assistant", "uuid": uid, "timestamp": _cts(n),
            "message": {"role": "assistant", "model": "claude-opus-5-5", "content": list(blocks),
                        "stop_reason": stop}}


def _thinking(t, sig):
    return {"type": "thinking", "thinking": t, "signature": sig}


def test_claude_narration_thinking_is_text(tmp_path):
    from helpers import thinking_sig

    es = _claude(tmp_path, _casst("a1", _thinking("Checking the parser first.", thinking_sig(True)), n=1))
    assert len(es) == 1
    e = es[0]
    assert (e.role, e.text, e.thinking) == ("assistant", "Checking the parser first.", "")
    assert [(b.kind, b.text) for b in e.blocks] == [("text", "Checking the parser first.")]
    assert [s[0] for s in e.steps] == ["text"]


def test_claude_plain_signed_thinking_stays_thinking(tmp_path):
    from helpers import thinking_sig

    es = _claude(tmp_path, _casst("a1", _thinking("private reasoning", thinking_sig(False)),
                                  {"type": "text", "text": "Done."}, n=1))
    e = es[0]
    assert (e.text, e.thinking) == ("Done.", "private reasoning")
    assert [(b.kind, b.text) for b in e.blocks] == [("text", "Done.")]
    assert [s[0] for s in e.steps] == ["thinking", "text"]


def test_claude_empty_narration_thinking_ignored(tmp_path):
    from helpers import thinking_sig

    assert _claude(tmp_path, _casst("a1", _thinking("", thinking_sig(True)), n=1),
                   _casst("a2", _thinking("   ", thinking_sig(True)), n=2)) == []


@pytest.mark.parametrize("sig", ["!!!not base64 ***", "", "A", "=" * 70, 12345, None, ["x"], {"a": 1}])
def test_claude_garbage_signature_never_raises(tmp_path, sig):
    es = _claude(tmp_path, _casst("a1", _thinking("some reasoning", sig), n=1))
    assert [(e.text, e.thinking) for e in es] == [("", "some reasoning")]


def test_claude_bg_launch_structured_bash_and_agent(tmp_path):
    from helpers import bg_result

    es = _claude(tmp_path,
                 _casst("a1", {"type": "tool_use", "id": "toolu_B1", "name": "Bash",
                               "input": {"command": "sleep 60", "run_in_background": True}}, n=1),
                 bg_result("r1", "toolu_B1", "bk4spq3l", _cts(2)),
                 _casst("a2", {"type": "tool_use", "id": "toolu_A1", "name": "Agent",
                               "input": {"description": "look", "prompt": "SECRET", "run_in_background": True}},
                        n=3),
                 bg_result("r2", "toolu_A1", "a7d71838c618", _cts(4), shape="agent"))
    bg = [(e.role, e.uid, e.note, e.ts) for e in es if e.role != "assistant"]
    assert bg == [("bg", "toolu_B1", "bk4spq3l", common_ts(_cts(2))),
                  ("bg", "toolu_A1", "a7d71838c618", common_ts(_cts(4)))]
    assert "SECRET" not in repr([e for e in es if e.role == "bg"])


def common_ts(s):
    from pm.chat.sources.common import parse_ts

    return parse_ts(s)


def test_claude_bg_launch_text_fallback(tmp_path):
    from helpers import bg_result

    es = _claude(tmp_path, bg_result("r1", "toolu_B1", "bcgx0zfji", _cts(1), shape="bash-text"),
                 bg_result("r2", "toolu_A1", "a1b2c3d4", _cts(2), shape="agent-text"))
    assert [(e.role, e.uid, e.note) for e in es] == [("bg", "toolu_B1", "bcgx0zfji"), ("bg", "toolu_A1", "a1b2c3d4")]


@pytest.mark.parametrize("bad", ["../../etc/passwd", "a b", "x" * 65, "", "id;rm", 123, None])
def test_claude_bg_launch_bad_task_id_is_plain_result(tmp_path, bad):
    from helpers import bg_result

    e = bg_result("r1", "toolu_B1", "placeholder", _cts(1))
    e["toolUseResult"]["backgroundTaskId"] = bad
    e["message"]["content"][0]["content"] = "Started."
    es = _claude(tmp_path, e)
    assert [(x.role, x.uid, x.text) for x in es] == [("result", "toolu_B1", "ok")]


def test_claude_async_not_true_is_plain_result(tmp_path):
    from helpers import bg_result

    e = bg_result("r1", "toolu_A1", "a1b2c3", _cts(1), shape="agent")
    e["toolUseResult"]["isAsync"] = "true"  # not the bool True
    e["message"]["content"][0]["content"] = [{"type": "text", "text": "Agent finished."}]
    assert [(x.role, x.uid) for x in _claude(tmp_path, e)] == [("result", "toolu_A1")]


@pytest.mark.parametrize("shape", ["user", "attachment"])
@pytest.mark.parametrize(("status", "want"), [("completed", "ok"), ("failed", "err"), ("killed", "err"),
                                               ("stopped", "err"), ("weird", "err")])
def test_claude_task_notification_shapes(tmp_path, shape, status, want):
    from helpers import task_note

    es = _claude(tmp_path, task_note("n1", "bk4spq3l", _cts(5), status=status, tool_id="toolu_B1", shape=shape))
    assert [(e.role, e.uid, e.text, e.note, e.ts) for e in es] == [
        ("bgend", "toolu_B1", want, "bk4spq3l", common_ts(_cts(5)))]
    assert "SECRET" not in repr(es) and "/tmp/x" not in repr(es)


def test_claude_task_notification_without_tool_id(tmp_path):
    from helpers import task_note

    es = _claude(tmp_path, task_note("n1", "bk4spq3l", _cts(5)), task_note("n2", "a9z", _cts(6), shape="attachment"))
    assert [(e.role, e.uid, e.text, e.note) for e in es] == [("bgend", "", "ok", "bk4spq3l"), ("bgend", "", "ok", "a9z")]


def test_claude_task_notification_is_not_a_user_prompt(tmp_path):
    from helpers import task_note

    es = _claude(tmp_path, task_note("n1", "bk1", _cts(5), tool_id="toolu_B1"),
                 task_note("n2", "bk1", _cts(6), tool_id="toolu_B1", shape="attachment"))
    assert {e.role for e in es} == {"bgend"}
    assert all(not e.mid_turn for e in es)


def test_claude_task_stop_and_kill_shell(tmp_path):
    es = _claude(tmp_path,
                 _casst("a1", {"type": "tool_use", "id": "toolu_S1", "name": "TaskStop",
                               "input": {"task_id": "bk4spq3l"}}, n=1),
                 _casst("a2", {"type": "tool_use", "id": "toolu_S2", "name": "KillShell",
                               "input": {"shell_id": "bcgx0zfji"}}, n=2),
                 _casst("a3", {"type": "tool_use", "id": "toolu_S3", "name": "TaskStop",
                               "input": {"task_id": "../bad id"}}, n=3))
    stops = [(e.role, e.uid, e.note, e.ts) for e in es if e.role == "bgstop"]
    assert stops == [("bgstop", "", "bk4spq3l", common_ts(_cts(1))), ("bgstop", "", "bcgx0zfji", common_ts(_cts(2)))]
    tools = [b.id for e in es if e.role == "assistant" for b in e.blocks if b.kind == "tool"]
    assert tools == ["toolu_S1", "toolu_S2", "toolu_S3"]  # the stop tool itself is still an item


# --- background task output file: claude.task_exit ---

SESS = "84b545db-d973-468a-9d7c-b3891ed253f2"


@pytest.fixture
def task_out(tmp_path):
    """(transcript path, tmp root, writer of <root>/<slug>/<sess>/tasks/<id>.output)."""
    from pm.chat.sources import claude

    transcript = tmp_path / "projects" / "-home-me-dev-pm" / f"{SESS}.jsonl"
    root = tmp_path / "claude-1000"
    tasks = root / "-home-me-dev-pm" / SESS / "tasks"
    tasks.mkdir(parents=True)

    def write(task_id: str, body: str) -> Path:
        p = tasks / f"{task_id}.output"
        p.write_text(body)
        return p

    return claude, transcript, root, write


@pytest.mark.parametrize(("body", "want"), [
    ("SECRET line\n[exited with code 0]\n", 0), ("x\n[exited with code 2]", 2), ("[exited with code 137]  \n", 137)])
def test_claude_task_exit_marker(task_out, body, want):
    claude, transcript, root, write = task_out
    p = write("b2h863lyu", "SECRET " * 50 + body)
    assert claude.task_exit(transcript, "b2h863lyu", tmp_root=root) == (want, p.stat().st_mtime)


@pytest.mark.parametrize("body", ["", "still running\n", "[exited with code 0]\nmore output\n",
                                  "[exited with code 1234]\n", "[exited with code -1]\n"])
def test_claude_task_exit_no_marker(task_out, body):
    claude, transcript, root, write = task_out
    write("bk1", body)
    assert claude.task_exit(transcript, "bk1", tmp_root=root) is None


@pytest.mark.parametrize("bad", ["", "..", "../bk1", "a/b", "bk 1", "x" * 65, "bk1\0"])
def test_claude_task_exit_bad_ref(task_out, bad):
    claude, transcript, root, write = task_out
    write("bk1", "[exited with code 0]\n")
    assert claude.task_exit(transcript, bad, tmp_root=root) is None


def test_claude_task_exit_missing_file_or_root(task_out, tmp_path):
    claude, transcript, root, _write = task_out
    assert claude.task_exit(transcript, "nope", tmp_root=root) is None
    assert claude.task_exit(transcript, "nope", tmp_root=tmp_path / "absent") is None


def test_claude_task_exit_non_uuid_transcript(task_out):
    claude, transcript, root, write = task_out
    write("bk1", "[exited with code 0]\n")
    assert claude.task_exit(transcript.with_name("notes.jsonl"), "bk1", tmp_root=root) is None
    assert claude.task_exit(transcript.parent.parent / ".." / f"{SESS}.jsonl", "bk1", tmp_root=root) is None


def test_claude_task_exit_symlink_leaf_and_escape(task_out, tmp_path):
    claude, transcript, root, write = task_out
    outside = tmp_path / "outside.output"
    outside.write_text("[exited with code 0]\n")
    real = write("real", "[exited with code 0]\n")
    tasks = real.parent
    (tasks / "leaf.output").symlink_to(real)  # symlink leaf, even inside the root
    (tasks / "esc.output").symlink_to(outside)
    assert claude.task_exit(transcript, "leaf", tmp_root=root) is None
    assert claude.task_exit(transcript, "esc", tmp_root=root) is None
    assert claude.task_exit(transcript, "real", tmp_root=root) is not None


def test_claude_task_exit_symlinked_root(task_out, tmp_path):
    claude, transcript, root, write = task_out
    write("bk1", "[exited with code 0]\n")
    link = tmp_path / "claude-link"
    link.symlink_to(root)
    assert claude.task_exit(transcript, "bk1", tmp_root=link) is None


def test_claude_task_exit_root_of_other_owner(task_out, monkeypatch):
    claude, transcript, root, write = task_out
    write("bk1", "[exited with code 0]\n")
    other = os.getuid() + 1
    monkeypatch.setattr(claude.os, "getuid", lambda: other)
    assert claude.task_exit(transcript, "bk1", tmp_root=root) is None


def test_claude_task_exit_fifo_and_dir(task_out):
    claude, transcript, root, write = task_out
    tasks = write("x", "").parent
    os.mkfifo(tasks / "fifo.output")
    (tasks / "dir.output").mkdir()
    assert claude.task_exit(transcript, "fifo", tmp_root=root) is None  # never opened: would block
    assert claude.task_exit(transcript, "dir", tmp_root=root) is None


def test_task_exit_other_session_folder_no_symlinks(tmp_path):
    from pm.chat.sources.claude import task_exit
    root = tmp_path / "root"
    t = tmp_path / "proj" / "11111111-1111-4111-8111-111111111111.jsonl"
    other = root / "proj" / "22222222-2222-4222-8222-222222222222" / "tasks"
    other.mkdir(parents=True)
    (other / "bk1.output").write_text("[exited with code 0]\n")
    assert task_exit(t, "bk1", tmp_root=root)[0] == 0
    (root / "proj" / "notauuid" / "tasks").mkdir(parents=True)
    (root / "proj" / "notauuid" / "tasks" / "bk2.output").write_text("[exited with code 0]\n")
    assert task_exit(t, "bk2", tmp_root=root) is None  # only session (uuid) folders
    elsewhere = tmp_path / "elsewhere" / "tasks"
    elsewhere.mkdir(parents=True)
    (elsewhere / "bk3.output").write_text("[exited with code 0]\n")
    (root / "proj" / "33333333-3333-4333-8333-333333333333").symlink_to(tmp_path / "elsewhere")
    assert task_exit(t, "bk3", tmp_root=root) is None  # a symlinked session folder is not followed


# ---------------------------------------------------------------- alternate config dirs (named accounts)
def test_claude_transcript_and_live_session_from_config_dir(tmp_path):
    from pm.chat.sources import claude
    cdir, uuid = tmp_path / "acct", "11111111-2222-3333-4444-555555555555"
    p = claude.transcript_path(CWD, uuid, tmp_path / "home", config_dir=cdir)
    assert p == cdir / "projects" / claude.encode_cwd(CWD) / f"{uuid}.jsonl"
    assert claude.transcript_path(CWD, uuid, tmp_path / "home") == (
        tmp_path / "home" / ".claude" / "projects" / claude.encode_cwd(CWD) / f"{uuid}.jsonl")
    proc = tmp_path / "proc"
    (proc / "11").mkdir(parents=True)
    f = ["S", "1"] + ["0"] * 9 + ["0", "0", "0", "0", "20", "0", "1", "0", "777", "0", "256"]
    (proc / "11" / "stat").write_text("11 (claude) " + " ".join(f))
    sd = cdir / "sessions"
    sd.mkdir(parents=True)
    (sd / "11.json").write_text(json.dumps({"pid": 11, "procStart": "777", "sessionId": uuid}))
    assert claude.live_session_id(11, proc_root=proc, config_dir=cdir) == uuid
    assert claude.live_session_id(11, proc_root=proc, config_dir=tmp_path / "other") is None


def test_codex_config_dir_candidates_and_cache_not_shared(tmp_path):
    codex._CACHE.clear()
    now = time.time()
    day = time.strftime("%Y/%m/%d", time.localtime(now))
    pa = rollout(tmp_path / "acc", "x", CWD, [amsg("a")], day=day)  # <acc>/.codex/sessions
    cdir = tmp_path / "acc" / ".codex"
    assert codex.locate(1, CWD, now - 60, tmp_path, cdir) == pa
    assert codex.locate(1, CWD, now - 60, tmp_path) is None  # default dir has none; cache not shared
    assert codex.locate(1, CWD, now - 60, tmp_path, tmp_path / "accB") is None
    assert [p for _, p in codex._candidates(tmp_path, now - 60, cdir)] == [pa]
    assert chat_sources.session_file(AgentKind.CODEX, pid=1, cwd=CWD, age_s=60, home=tmp_path, config_dir=cdir) == pa
