"""Cursor agent: ``~/.cursor/chats/<md5(cwd)>/<chat uuid>/store.db`` (SQLite).

The chat dir is md5 of the cwd; the newest ``store.db`` (by stat, including its
``-wal``) wins. The DB is opened read-only (``mode=ro``) and only JSON blobs
(``{"role": ...}`` rows, at most 256 KB in total, newest first) are read.

``read_entries`` (pm.chat.log) walks the JSON blobs by rowid: user rows (the ``<user_query>``
part; ``<user_info>`` context rows are skipped), assistant rows (``text``, ``reasoning`` as
thinking, ``tool-call`` name + one target line); ``tool`` rows are skipped.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from .common import (
    CHUNK_ROWS,
    FIRST_ROWS,
    TAIL_BYTES,
    Chunk,
    Latest,
    RawEntry,
    mtime,
    text_step,
    tool_name,
    tool_step,
)

_RECENT = 6
_MAX_ROWS = 80
_MAX_BLOB = 128 * 1024


def chat_dir(cwd: str, home: Path) -> Path:
    """``~/.cursor/chats/<md5(cwd)>``: the chat dirs of one working directory."""
    return home / ".cursor" / "chats" / hashlib.md5(cwd.encode()).hexdigest()


def locate(cwd: str, home: Path) -> Path | None:
    if not cwd:
        return None
    base = chat_dir(cwd, home)
    try:
        names = os.listdir(base)
    except OSError:
        return None
    best: tuple[float, Path] | None = None
    for n in names:
        db = base / n / "store.db"
        m = mtime(db)
        if m and (best is None or m > best[0]):
            best = (m, db)
    return best[1] if best else None


def _connect(path: Path) -> sqlite3.Connection:
    uri = "file:" + str(path).replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=0.3)


def read_latest(path: Path) -> Latest | None:
    con = _connect(path)
    try:
        con.execute("PRAGMA query_only=1")
        rows = con.execute(
            "SELECT data FROM blobs WHERE substr(data,1,1)=x'7B' AND length(data)<=? "
            "ORDER BY rowid DESC LIMIT ?", (_MAX_BLOB, _MAX_ROWS)).fetchall()
    finally:
        con.close()
    found: list[str] = []
    budget = TAIL_BYTES
    for (data,) in rows:
        budget -= len(data)
        if budget < 0 and found:
            break
        try:
            d = json.loads(data)
        except (ValueError, TypeError):
            continue
        if not isinstance(d, dict) or d.get("role") != "assistant":
            continue
        content = d.get("content")
        if isinstance(content, str):
            parts = [content]
        elif isinstance(content, list):
            parts = [b.get("text") for b in content if isinstance(b, dict) and b.get("type") == "text"
                     and isinstance(b.get("text"), str)]
        else:
            parts = []
        text = "\n".join(p for p in parts if p.strip()).strip()
        if text:
            found.append(text)
            if len(found) >= _RECENT:
                break
    if not found:
        return None
    return Latest("assistant", found[0], mtime(path), tuple(found))


_QUERY = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)
_STAMP = re.compile(r"<timestamp>([^<]{5,80})</timestamp>")
_STAMP_RE = re.compile(r"^\w+, (\w{3} \d{1,2}, \d{4}, \d{1,2}:\d{2} [AP]M) \(UTC([+-]\d{1,2})(?::?(\d{2}))?\)$")
_MAX_ENTRY_BLOB = 1024 * 1024


def _stamp(text: str) -> float:
    """Epoch s of Cursor's ``<timestamp>Thursday, Oct 8, 2026, 6:58 PM (UTC+7)</timestamp>``; 0 when absent."""
    m = _STAMP.search(text)
    m2 = _STAMP_RE.match(m.group(1).strip()) if m else None
    if not m2:
        return 0.0
    try:
        sign = -1 if m2.group(2).startswith("-") else 1
        off = f"{'+' if sign > 0 else '-'}{abs(int(m2.group(2))):02d}{m2.group(3) or '00'}"
        return datetime.strptime(f"{m2.group(1)} {off}", "%b %d, %Y, %I:%M %p %z").timestamp()
    except ValueError:
        return 0.0


def _parts(content: object, typ: str) -> str:
    if isinstance(content, str):
        return content.strip() if typ == "text" else ""
    if not isinstance(content, list):
        return ""
    parts = [b.get("text") for b in content if isinstance(b, dict) and b.get("type") == typ
             and isinstance(b.get("text"), str)]
    return "\n\n".join(p.strip() for p in parts if p.strip())


def _steps(content: object) -> tuple[tuple[str, str, str], ...]:
    if isinstance(content, str):
        st = text_step(content)
        return (st,) if st else ()
    out: list[tuple[str, str, str]] = []
    for b in content if isinstance(content, list) else []:
        if not isinstance(b, dict):
            continue
        typ = b.get("type")
        st = None
        if typ == "text":
            st = text_step(b.get("text"))
        elif typ == "reasoning":
            st = text_step(b.get("text"), "thinking")
        elif typ == "tool-call":
            st = tool_step(b.get("toolName"), b.get("args"))
        if st:
            out.append(st)
    return tuple(out)


def read_entries(path: Path, pos: int = -1) -> Chunk:
    con = _connect(path)
    try:
        con.execute("PRAGMA query_only=1")
        top = con.execute("SELECT coalesce(max(rowid), 0) FROM blobs").fetchone()[0]
        if top < pos:
            return Chunk([], -1, reset=True)
        start = max(0, top - FIRST_ROWS) if pos < 0 else pos
        rows = con.execute(
            "SELECT rowid, id, CASE WHEN length(data) <= ? THEN data END FROM blobs "
            "WHERE rowid > ? AND substr(data,1,1)=x'7B' ORDER BY rowid LIMIT ?",
            (_MAX_ENTRY_BLOB, start, CHUNK_ROWS)).fetchall()
    finally:
        con.close()
    out: list[RawEntry] = []
    last = start
    for rowid, bid, data in rows:
        last = rowid
        if data is None:
            continue
        try:
            d = json.loads(data)
        except (ValueError, TypeError):
            continue
        if not isinstance(d, dict):
            continue
        uid = (str(bid) if bid else f"r{rowid}")[:80]
        content = d.get("content")
        if d.get("role") == "user":
            raw = _parts(content, "text")
            m = _QUERY.search(raw)
            text = m.group(1).strip() if m else ("" if raw.startswith("<") else raw)
            if text:
                out.append(RawEntry("user", uid, _stamp(raw), text))
        elif d.get("role") == "assistant":
            tools = tuple(tool_name(b.get("toolName")) for b in content
                          if isinstance(b, dict) and b.get("type") == "tool-call") if isinstance(content, list) else ()
            text, think = _parts(content, "text"), _parts(content, "reasoning")
            if text or think or tools:
                out.append(RawEntry("assistant", uid, 0.0, text, think, tools, _steps(content)))
    return Chunk(out, last, more=len(rows) >= CHUNK_ROWS)
