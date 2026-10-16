"""Discovery of past CLI sessions on disk per working directory (fake HOME only)."""

import hashlib
import json
import os
import time
from pathlib import Path

from pm.chat.sources import past
from pm.chat.sources.claude import encode_cwd

U1 = "11111111-2222-3333-4444-555555555551"
U2 = "11111111-2222-3333-4444-555555555552"
U3 = "11111111-2222-3333-4444-555555555553"
NOW = time.time()
WIN = {"since": NOW - 30 * 86400, "until": NOW - 60}


def _touch(p: Path, age_s: float) -> Path:
    t = NOW - age_s
    os.utime(p, (t, t))
    return p


def claude_file(home: Path, cwd: str, uuid: str, age_s: float = 3600, head_cwd: str | None = None,
                entrypoint: str = "cli") -> Path:
    d = home / ".claude" / "projects" / encode_cwd(cwd)
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{uuid}.jsonl"
    lines = [{"type": "summary", "summary": "x"},
             {"type": "user", "uuid": "u1", "cwd": head_cwd or cwd, "entrypoint": entrypoint,
              "message": {"role": "user", "content": "hi"}}]
    f.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return _touch(f, age_s)


def codex_file(home: Path, cwd: str, uuid: str, age_s: float = 3600, source: object = "cli",
               originator: str = "codex-tui") -> Path:
    t = time.localtime(NOW - age_s)
    d = home / ".codex" / "sessions" / f"{t.tm_year:04d}" / f"{t.tm_mon:02d}" / f"{t.tm_mday:02d}"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"rollout-2026-10-01T10-00-00-{uuid}.jsonl"
    meta = {"id": uuid, "cwd": cwd, "source": source, "originator": originator}
    f.write_text(json.dumps({"type": "session_meta", "payload": meta}) + "\n")
    return _touch(f, age_s)


def cursor_db(home: Path, cwd: str, chat: str, age_s: float = 3600) -> Path:
    d = home / ".cursor" / "chats" / hashlib.md5(cwd.encode()).hexdigest() / chat
    d.mkdir(parents=True, exist_ok=True)
    (d / "store.db").write_bytes(b"x")
    return _touch(d / "store.db", age_s)


def test_claude_top_level_uuid_files_only(tmp_path):
    home, cwd = tmp_path, "/home/u/dev/alpha"
    f = claude_file(home, cwd, U1)
    sub = f.with_suffix("") / "subagents"
    sub.mkdir(parents=True)
    (sub / f"{U2}.jsonl").write_text("{}\n")  # sub-agent transcript: never a session
    (f.parent / "notes.jsonl").write_text("{}\n")
    (f.parent / f"{U3}.jsonl").write_text("")  # empty
    got = past.discover(cwd, home, **WIN)
    assert [(s.kind, s.ident, s.path, s.cwd) for s in got] == [("claude", U1, f, cwd)]
    assert abs(got[0].mtime - (NOW - 3600)) < 2


def test_claude_lossy_dir_name_is_checked_against_the_head(tmp_path):
    claude_file(tmp_path, "/home/u/dev/a-b", U1, head_cwd="/home/u/dev/a.b")  # same encoded dir
    claude_file(tmp_path, "/home/u/dev/a-b", U2)
    assert [s.ident for s in past.discover("/home/u/dev/a-b", tmp_path, **WIN)] == [U2]


def test_codex_matches_cwd_from_first_line(tmp_path):
    f = codex_file(tmp_path, "/w/alpha", U1, age_s=2 * 86400)
    codex_file(tmp_path, "/w/beta", U2)
    got = past.discover_many(["/w/alpha", "/w/beta", "/w/none"], tmp_path, **WIN)
    assert [(s.kind, s.ident, s.path) for s in got["/w/alpha"]] == [("codex", f.stem, f)]
    assert [s.ident for s in got["/w/beta"]] == [f"rollout-2026-10-01T10-00-00-{U2}"]
    assert got["/w/none"] == []


def test_cursor_chat_dirs_by_md5_of_cwd(tmp_path):
    db = cursor_db(tmp_path, "/w/alpha", U1)
    cursor_db(tmp_path, "/w/alpha", "not-a-uuid")
    cursor_db(tmp_path, "/w/other", U2)
    got = past.discover("/w/alpha", tmp_path, **WIN)
    assert [(s.kind, s.ident, s.path) for s in got] == [("cursor-agent", U1, db)]


def test_window_drops_old_and_fresh_files_and_sorts_newest_first(tmp_path):
    cwd = "/w/alpha"
    claude_file(tmp_path, cwd, U1, age_s=40 * 86400)  # older than retention
    claude_file(tmp_path, cwd, U2, age_s=10)  # still being written
    claude_file(tmp_path, cwd, U3, age_s=7200)
    codex_file(tmp_path, cwd, U1, age_s=3600)
    got = past.discover(cwd, tmp_path, **WIN)
    assert [s.kind for s in got] == ["codex", "claude"] and got[1].ident == U3


def test_cap_keeps_the_newest_per_kind(tmp_path):
    cwd = "/w/alpha"
    ids = [f"11111111-2222-3333-4444-{i:012d}" for i in range(5)]
    for i, u in enumerate(ids):
        claude_file(tmp_path, cwd, u, age_s=3600 * (i + 1))
    got = past.discover(cwd, tmp_path, cap=2, **WIN)
    assert [s.ident for s in got] == ids[:2]


def test_missing_stores_and_injected_head(tmp_path):
    assert past.discover("/w/x", tmp_path, **WIN) == []
    assert past.discover_many([""], tmp_path, **WIN) == {}
    codex_file(tmp_path, "/w/x", U1)
    seen = []
    got = past.discover("/w/x", tmp_path, head=lambda k, p: seen.append(k) or ("/w/x", True), **WIN)
    assert [s.kind for s in got] == ["codex"] and seen == ["codex"]


def test_headless_claude_runs_are_skipped(tmp_path):
    cwd = "/w/alpha"
    claude_file(tmp_path, cwd, U1, entrypoint="sdk-cli")  # claude -p (pm quick chat)
    claude_file(tmp_path, cwd, U2, entrypoint="sdk-ts")
    f = claude_file(tmp_path, cwd, U3)
    old = f.parent / "11111111-2222-3333-4444-000000000000.jsonl"  # no entrypoint at all: older CLI
    old.write_text(json.dumps({"type": "user", "cwd": cwd}) + "\n")
    _touch(old, 7200)
    assert [s.ident for s in past.discover(cwd, tmp_path, **WIN)] == [U3, old.stem]
    assert past.claude_head(f) == (cwd, True) and past.claude_head(tmp_path / "nope") == ("", True)


def test_codex_exec_and_subagent_rollouts_are_skipped(tmp_path):
    cwd = "/w/alpha"
    codex_file(tmp_path, cwd, U1, source="exec", originator="codex_exec")
    codex_file(tmp_path, cwd, U2, source={"subagent": "review"})
    keep = codex_file(tmp_path, cwd, U3, source="vscode")
    assert [s.path for s in past.discover(cwd, tmp_path, **WIN)] == [keep]


def test_cursor_chat_dir_is_md5_of_cwd(tmp_path):
    from pm.chat.sources import cursor
    assert cursor.chat_dir("/w/a", tmp_path) == tmp_path / ".cursor/chats" / hashlib.md5(b"/w/a").hexdigest()
