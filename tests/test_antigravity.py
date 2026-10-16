"""Google Antigravity CLI (agy): wire reader, chat extraction, stats, pid -> conversation, detection."""

import os
import sqlite3
from pathlib import Path

from test_agents_detect import mkproc, pane

from pm.agents import detect_agents
from pm.chat import live as chat
from pm.chat import sources as chat_sources
from pm.chat.sources import antigravity as agy
from pm.core.models import Agent, AgentKind, AgentState, TmuxPane

UUID = "7f3a9803-ff1b-4c9a-b40d-053597374c4b"
UUID2 = "2a6e0683-deac-49b1-a73f-6a446ef6a805"


# ---- tiny protobuf encoder -------------------------------------------------

def vi(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def f_int(f: int, v: int) -> bytes:
    return vi(f << 3) + vi(v)


def f_msg(f: int, body: bytes | str) -> bytes:
    body = body.encode() if isinstance(body, str) else body
    return vi(f << 3 | 2) + vi(len(body)) + body


def gen_blob(inp, out, cache, ctx, window=256000, model="gemini-3.8-flash") -> bytes:
    usage = f_int(1, 1318) + f_int(2, inp) + f_int(3, out) + (f_int(5, cache) if cache else b"") + f_int(9, 7)
    ctxm = f_msg(10, f_int(1, ctx) + f_int(4, window))
    return f_msg(1, f_msg(4, usage) + f_msg(9, ctxm) + f_msg(19, model))


def user_step(text, ts=1791460720) -> bytes:
    meta = f_msg(1, f_int(1, ts))
    return f_int(1, 14) + f_msg(5, meta) + f_msg(19, f_msg(2, text) + f_msg(3, f_msg(1, text)))


def reply_step(text, ts=1791460726) -> bytes:
    meta = f_msg(6, f_int(1, ts))
    return (f_int(1, 15) + f_msg(5, meta)
            + f_msg(20, f_msg(1, text) + f_msg(3, "secret thinking") + f_msg(8, text)))


def make_db(path: Path, steps, gens):
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE trajectory_meta(trajectory_id TEXT, cascade_id TEXT, trajectory_type INT, source INT);"
        "CREATE TABLE steps(idx INTEGER PRIMARY KEY, step_type INT, status INT, has_subtrajectory INT, metadata BLOB,"
        " error_details BLOB, permissions BLOB, task_details BLOB, render_info BLOB, step_payload BLOB, step_format INT);"
        "CREATE TABLE gen_metadata(idx INTEGER PRIMARY KEY, data BLOB, size INT);"
        "CREATE TABLE executor_metadata(idx INTEGER PRIMARY KEY, data BLOB);")
    for i, (t, p) in enumerate(steps):
        con.execute("INSERT INTO steps(idx, step_type, status, step_payload) VALUES (?,?,3,?)", (i, t, p))
    for i, g in enumerate(gens):
        con.execute("INSERT INTO gen_metadata VALUES (?,?,?)", (i, g, len(g)))
    con.commit()
    con.close()


def home_with_conv(tmp_path, steps=None, gens=None, uuid=UUID):
    home = tmp_path / "home"
    root = home / ".gemini" / "antigravity-cli"
    (root / "conversations").mkdir(parents=True, exist_ok=True)
    (root / "presence").mkdir(exist_ok=True)
    steps = steps if steps is not None else [
        (14, user_step("first question")), (15, reply_step("first answer")),
        (132, f_int(1, 132)), (14, user_step("do the thing now")), (15, reply_step("second answer", 1791460800))]
    gens = gens if gens is not None else [gen_blob(1000, 50, 0, 5000), gen_blob(200, 30, 900, 6000)]
    db = root / "conversations" / f"{uuid}.db"
    make_db(db, steps, gens)
    (root / "presence" / f"{uuid}.lock").write_bytes(b"")
    return home, root, db


# ---- wire reader -----------------------------------------------------------

def test_wire_reader_and_defensive():
    buf = f_int(1, 300) + f_msg(2, f_int(3, 7) + f_msg(4, "hi")) + vi(5 << 3 | 5) + b"\x01\x00\x00\x00"
    assert agy.fields(buf)[0] == (1, 0, 300)
    assert agy.find(buf, 2, 3) == 7
    assert agy.find(buf, 2, 4) == b"hi"
    assert agy.find(buf, 9) is None
    assert agy.fields(vi(2 << 3 | 2) + vi(50) + b"x") is None  # truncated length
    assert agy.fields(b"\xff" * 20) is None  # varint never ends
    assert agy.fields(vi(3 << 3 | 3)) is None  # group wire type
    assert agy.fields(b"\x00\x01") is None  # field 0
    assert agy.find(b"garbage\xff\xff", 1) is None
    assert agy.find(None, 1) is None


def test_scan_usage():
    u = agy.scan_usage(gen_blob(1000, 50, 900, 5000, model="gemini-x"))
    assert u == {"in": 1000, "out": 50, "cache": 900, "ctx": 5000, "window": 256000, "model": "gemini-x"}
    assert agy.scan_usage(b"\xff\xff") is None
    assert agy.scan_usage(f_msg(1, f_msg(19, "gemini-x"))) is None


# ---- chat ------------------------------------------------------------------

def test_read_latest(tmp_path):
    _, _, db = home_with_conv(tmp_path)
    lt = agy.read_latest(db)
    assert lt.role == "assistant" and lt.text == "second answer" and lt.ts == 1791460800.0
    assert lt.recent == ("second answer", "first answer")
    assert lt.task == "do the thing now"
    assert "secret thinking" not in repr(lt)


def test_read_latest_empty_and_dispatch(tmp_path):
    _, _, db = home_with_conv(tmp_path, steps=[(14, user_step("only a prompt"))], gens=[])
    assert agy.read_latest(db) is None
    assert chat_sources.read_latest("antigravity", db) is None
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not sqlite")
    try:
        agy.read_latest(bad)
    except sqlite3.Error:
        pass  # callers wrap in try, like the other sources


def test_chat_view_uses_store_and_masks(tmp_path, monkeypatch):
    home, _, db = home_with_conv(tmp_path, steps=[
        (14, user_step("fix it with key sk-ant-api03-abcdefghijklmnopqrstuvwx")), (15, reply_step("done, all good"))])
    monkeypatch.setattr(chat_sources, "session_file", lambda *a, **k: db)
    ag = Agent(id="antigravity-1", kind=AgentKind.ANTIGRAVITY, pane_id="%1", session_id="$1", pid=5, cwd="/p",
               state=AgentState.IDLE, transcript=str(db))
    info, _ = chat._store_info(ag)
    assert info.text == "done, all good" and info.task.startswith("fix it with key")
    view = chat.chat_view(None, ag, "")
    assert view.last_text == "done, all good" and view.source == "transcript"
    assert "sk-ant-api03-abcdefghijklmnopqrstuvwx" not in view.task


# ---- pid -> conversation ---------------------------------------------------

def test_locate_via_open_fd(tmp_path):
    home, root, db = home_with_conv(tmp_path)
    home_with_conv(tmp_path, uuid=UUID2)
    proc = tmp_path / "proc"
    (proc / "77" / "fd").mkdir(parents=True)
    os.symlink(str(db), proc / "77" / "fd" / "5")
    os.symlink(str(root / "conversation_summaries.db"), proc / "77" / "fd" / "6")
    os.symlink("/dev/null", proc / "77" / "fd" / "7")
    assert agy.locate(77, home, proc) == db
    assert chat_sources.session_file(AgentKind.ANTIGRAVITY, pid=77, cwd="/x", home=home, proc_root=proc) == db
    assert agy.locate(78, home, proc) is None
    assert agy.locate(1, home, proc) is None


def test_locate_via_flock_inode(tmp_path):
    home, root, db = home_with_conv(tmp_path)
    ino = (root / "presence" / f"{UUID}.lock").stat().st_ino
    proc = tmp_path / "proc"
    (proc / "77").mkdir(parents=True)
    (proc / "locks").write_text(
        f"1: FLOCK  ADVISORY  WRITE 99 fc:00:{ino} 0 EOF\n"
        f"2: FLOCK  ADVISORY  WRITE 77 fc:00:{ino + 1000} 0 EOF\n"
        f"3: POSIX  ADVISORY  READ 77 fc:00:{ino} 0 EOF\n")
    assert agy.locate(77, home, proc) is None  # 77 holds a different inode
    assert agy.locate(99, home, proc) == db


# ---- stats -----------------------------------------------------------------

def test_session_stats(tmp_path):
    home, _, db = home_with_conv(tmp_path)
    ag = Agent(id="antigravity-1", kind=AgentKind.ANTIGRAVITY, pane_id="%1", session_id="$1", pid=0, cwd="/p",
               transcript=str(db))
    st = agy.session_stats(ag, home)
    assert (st.turns, st.input, st.output, st.cache_read, st.cache_write) == (2, 1200, 80, 900, 0)
    assert st.ctx == 6000 and st.window == 1_000_000 and st.model == "gemini-3.8-flash"
    assert st.source == "antigravity" and st.exact and st.pct == 1
    # incremental: a new request is added, nothing is double counted
    con = sqlite3.connect(db)
    g = gen_blob(10, 5, 0, 7000)
    con.execute("INSERT INTO gen_metadata VALUES (2, ?, ?)", (g, len(g)))
    con.commit()
    con.close()
    os.utime(db, (1, 1))  # force a different mtime on coarse filesystems
    st = agy.session_stats(ag, home)
    assert (st.input, st.output, st.ctx) == (1210, 85, 7000)


def test_session_stats_missing_or_garbage(tmp_path):
    ag = Agent(id="antigravity-1", kind=AgentKind.ANTIGRAVITY, pane_id="%1", session_id="$1", pid=0, cwd="/p",
               transcript=str(tmp_path / "nope.db"))
    assert agy.session_stats(ag, tmp_path) is None
    _, _, db = home_with_conv(tmp_path, gens=[b"\xff\xff\xff", gen_blob(5, 1, 0, 100, window=0)])
    ag = Agent(id="antigravity-1", kind=AgentKind.ANTIGRAVITY, pane_id="%1", session_id="$1", pid=0, cwd="/p",
               transcript=str(db))
    st = agy.session_stats(ag, tmp_path)
    assert st.input == 5 and st.window == 1_000_000  # no window in the data: Gemini default


# ---- detection -------------------------------------------------------------

def test_detect_agy(tmp_path):
    home, root, db = home_with_conv(tmp_path)
    proc = tmp_path / "proc"
    proc.mkdir()
    mkproc(proc, 20, 1, "fish", ["fish"])
    mkproc(proc, 21, 20, "agy", ["/home/u/.local/bin/agy"])
    (proc / "21" / "fd").mkdir()
    os.symlink(str(db), proc / "21" / "fd" / "9")
    a = detect_agents([pane(20, 4)], sessions_dir=tmp_path / "none", proc_root=proc, home=home)
    assert [(x.id, x.kind, x.pid) for x in a] == [("antigravity-4", AgentKind.ANTIGRAVITY, 21)]
    assert a[0].transcript == str(db) and a[0].ident == UUID and a[0].last_active > 0


def test_detect_agy_without_conversation(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    mkproc(proc, 21, 1, "agy", ["agy"])
    a = detect_agents([pane(21, 1)], sessions_dir=tmp_path / "none", proc_root=proc, home=tmp_path / "h")
    assert a[0].kind is AgentKind.ANTIGRAVITY and a[0].transcript == ""
