"""Google Antigravity CLI (``agy``): ``~/.gemini/antigravity-cli/conversations/<uuid>.db`` (SQLite).

Read-only (``mode=ro``) and bounded. The OAuth token, history and presence files are never opened
(presence locks are only stat()ed for their inode). Tables used: ``steps`` (one protobuf per step)
and ``gen_metadata`` (one protobuf per model request, with the token usage).

Protobuf layout (found empirically, no schema available; unknown fields are ignored)::

    steps.step_payload   1 = step type (14 user input, 15 planner response, others tool/system)
                         19.2  = user text            (type 14)
                         20.1  = assistant text       (type 15; 20.8 repeats it; 20.3 = thinking)
                         20.7  = tool call (repeated; 20.7.2 = tool name, 20.7.3 = JSON args: only the
                                 target key is read, e.g. AbsolutePath / CommandLine / Query)
                         5.6.1 / 5.1.1 = epoch seconds of the step
    gen_metadata.data    1.19  = model name ("gemini-3.8-flash")
                         1.4.2 = uncached prompt tokens   1.4.5 = cache-read prompt tokens
                         1.4.3 = output tokens (thinking included)   1.4.9 = thinking tokens
                         1.9.10.1 = context tokens (next request)    1.9.10.4 = context window

The live conversation of a pid is the ``conversations/<uuid>.db`` it holds open (``/proc/<pid>/fd``);
else the ``presence/<uuid>.lock`` it holds an flock on (``/proc/locks``, matched by inode).
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from pathlib import Path

from .common import (
    CHUNK_ROWS,
    FIRST_ROWS,
    Chunk,
    Latest,
    RawEntry,
    Stats,
    mtime,
    text_step,
    tool_name,
    tool_step,
)

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_DB_RE = re.compile(rf"/(?:conversations/({_UUID})\.db(?:-wal|-shm)?|presence/({_UUID})\.lock)$")
_STEP_USER, _STEP_REPLY, _STEP_TOOL = 14, 15, 132
# CORTEX_STEP_STATUS (field 4 of step_payload): 1 PENDING, 2 GENERATING, 3 DONE, 8 WAITING, 9 RUNNING
_IN_PROGRESS = (1, 2, 8, 9)
_RECENT = 6
_MAX_STEPS = 60
_MAX_BLOB = 256 * 1024
_MAX_GEN_BLOB = 4 * 1024 * 1024
_MAX_FIELDS = 4000
_MAX_DEPTH = 6
_BATCH = 500
_DEFAULT_WINDOW = 1_000_000

# --------------------------------------------------------------------------
# protobuf wire reader (varint, 64-bit, length-delimited, 32-bit); any error -> None
# --------------------------------------------------------------------------


def _varint(b: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        c = b[i]  # IndexError on truncation: caught by the caller
        i += 1
        r |= (c & 0x7F) << s
        s += 7
        if not c & 0x80:
            return r, i
        if s > 70:
            raise ValueError("varint too long")


def fields(buf: bytes) -> list[tuple[int, int, int | bytes]] | None:
    """Top-level ``(field, wire_type, value)`` of one message, or None when ``buf`` is not valid.

    Varints and fixed32/64 give ints, length-delimited gives bytes. Never raises; at most
    ``_MAX_FIELDS`` fields.
    """
    out: list[tuple[int, int, int | bytes]] = []
    i, n = 0, len(buf)
    try:
        while i < n:
            key, i = _varint(buf, i)
            f, w = key >> 3, key & 7
            if f == 0:
                return None
            if w == 0:
                v, i = _varint(buf, i)
            elif w == 2:
                ln, i = _varint(buf, i)
                if i + ln > n:
                    return None
                v, i = buf[i:i + ln], i + ln
            elif w == 1:
                if i + 8 > n:
                    return None
                v, i = int.from_bytes(buf[i:i + 8], "little"), i + 8
            elif w == 5:
                if i + 4 > n:
                    return None
                v, i = int.from_bytes(buf[i:i + 4], "little"), i + 4
            else:
                return None
            out.append((f, w, v))
            if len(out) > _MAX_FIELDS:
                return None
    except (IndexError, ValueError):
        return None
    return out


def find(buf: bytes | int | None, *path: int, _depth: int = 0) -> int | bytes | None:
    """First value at the nested field ``path`` (e.g. ``find(b, 1, 4, 2)``), or None."""
    if not isinstance(buf, bytes) or _depth > _MAX_DEPTH or not path:
        return None
    fs = fields(buf)
    if fs is None:
        return None
    for f, _w, v in fs:
        if f == path[0]:
            if len(path) == 1:
                return v
            r = find(v, *path[1:], _depth=_depth + 1)
            if r is not None:
                return r
    return None


def _text(v: object) -> str:
    if not isinstance(v, bytes) or not v:
        return ""
    try:
        return v.decode("utf-8").strip()
    except UnicodeDecodeError:
        return ""


# --------------------------------------------------------------------------
# locating the conversation of a live process
# --------------------------------------------------------------------------


def _root(home: Path) -> Path:
    return home / ".gemini" / "antigravity-cli"


def _conv_path(root: Path, uuid: str) -> Path | None:
    p = root / "conversations" / f"{uuid}.db"
    return p if p.is_file() else None


def _from_fds(pid: int, root: Path, proc_root: Path) -> Path | None:
    fd_dir = proc_root / str(pid) / "fd"
    try:
        names = os.listdir(fd_dir)
    except OSError:
        return None
    best: tuple[float, Path] | None = None
    for n in names[:512]:
        try:
            target = os.readlink(fd_dir / n)
        except OSError:
            continue
        m = _DB_RE.search(target)
        if m is None:
            continue
        p = _conv_path(root, m.group(1) or m.group(2))
        if p is None:
            continue
        t = mtime(p)
        if best is None or t > best[0]:
            best = (t, p)
    return best[1] if best else None


def _from_flock(pid: int, root: Path, proc_root: Path) -> Path | None:
    """``presence/<uuid>.lock`` flocked by ``pid`` (``/proc/locks`` lists pid and inode)."""
    try:
        with open(proc_root / "locks", encoding="ascii", errors="replace") as f:
            lines = f.read(1 << 20).splitlines()
    except OSError:
        return None
    inodes: set[int] = set()
    for ln in lines:
        parts = ln.split()
        if len(parts) >= 6 and parts[1] == "FLOCK" and parts[4] == str(pid):
            try:
                inodes.add(int(parts[5].rsplit(":", 1)[1]))
            except (ValueError, IndexError):
                continue
    if not inodes:
        return None
    try:
        names = os.listdir(root / "presence")
    except OSError:
        return None
    for n in names:
        if not n.endswith(".lock"):
            continue
        try:
            if (root / "presence" / n).stat().st_ino in inodes:
                return _conv_path(root, n[:-5])
        except OSError:
            continue
    return None


def locate(pid: int, home: Path, proc_root: Path = Path("/proc")) -> Path | None:
    """Conversation db of the live ``agy`` ``pid``; None while it has no conversation yet."""
    if pid <= 1:
        return None
    root = _root(home)
    return _from_fds(pid, root, proc_root) or _from_flock(pid, root, proc_root)


# --------------------------------------------------------------------------
# chat
# --------------------------------------------------------------------------


def _connect(path: Path) -> sqlite3.Connection:
    uri = "file:" + str(path).replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=0.3)
    con.execute("PRAGMA query_only=1")
    return con


def _step_ts(payload: bytes) -> float:
    for path in ((5, 6, 1), (5, 1, 1)):
        v = find(payload, *path)
        if isinstance(v, int) and 1_000_000_000 < v < 10_000_000_000:
            return float(v)
    return 0.0


def read_latest(path: Path) -> Latest | None:
    con = _connect(path)
    try:
        rows = con.execute(
            "SELECT step_type, step_payload FROM steps WHERE step_type IN (?, ?) "
            "AND length(step_payload) <= ? ORDER BY idx DESC LIMIT ?",
            (_STEP_USER, _STEP_REPLY, _MAX_BLOB, _MAX_STEPS)).fetchall()
    finally:
        con.close()
    found: list[tuple[str, float]] = []
    task, task_ts = "", 0.0
    for typ, payload in rows:
        if not isinstance(payload, bytes) or find(payload, 4) in _IN_PROGRESS:
            continue
        if typ == _STEP_REPLY and len(found) < _RECENT:
            t = _text(find(payload, 20, 1)) or _text(find(payload, 20, 8))
            if t:
                found.append((t, _step_ts(payload)))
        elif typ == _STEP_USER and not task:
            task = _text(find(payload, 19, 2)) or _text(find(payload, 19, 3, 1))
            task_ts = _step_ts(payload)
        if task and len(found) >= _RECENT:
            break
    if not found:
        return None
    return Latest("assistant", found[0][0], found[0][1], tuple(t for t, _ in found), task, task_ts)


def _reply(payload: bytes) -> tuple[str, str, tuple[str, ...], tuple[tuple[str, str, str], ...]]:
    """(text, thinking, tool names, steps) of a planner-response step."""
    body = find(payload, 20)
    if not isinstance(body, bytes):
        return "", "", (), ()
    text = _text(find(body, 1)) or _text(find(body, 8))
    think = _text(find(body, 3))
    calls = [v for f, _w, v in (fields(body) or []) if f == 7 and isinstance(v, bytes)]
    tools = tuple(tool_name(_text(find(v, 2))) for v in calls)
    steps = [st for st in (text_step(think, "thinking"), text_step(text)) if st]
    steps += [tool_step(_text(find(v, 2)), _text(find(v, 3))) for v in calls]
    return text, think, tools, tuple(steps)


def read_entries(path: Path, pos: int = -1) -> Chunk:
    """User-input and planner-response steps with ``idx`` above ``pos`` (-1: the last 2000 steps)."""
    con = _connect(path)
    try:
        top = con.execute("SELECT coalesce(max(idx), -1) FROM steps").fetchone()[0]
        if top < pos:
            return Chunk([], -1, reset=True)
        start = max(-1, top - FIRST_ROWS) if pos < 0 else pos
        rows = con.execute(
            "SELECT idx, step_type, CASE WHEN length(step_payload) <= ? THEN step_payload END FROM steps "
            "WHERE idx > ? ORDER BY idx LIMIT ?", (_MAX_BLOB * 4, start, CHUNK_ROWS)).fetchall()
    finally:
        con.close()
    out: list[RawEntry] = []
    last = start
    for idx, typ, payload in rows:
        if isinstance(payload, bytes) and typ in (_STEP_USER, _STEP_REPLY) and find(payload, 4) in _IN_PROGRESS:
            break  # step still streaming: wait until status becomes DONE so text/tools are complete
        last = idx
        if not isinstance(payload, bytes):
            continue
        if typ == _STEP_USER:
            text = _text(find(payload, 19, 2)) or _text(find(payload, 19, 3, 1))
            if text:
                out.append(RawEntry("user", f"i{idx}", _step_ts(payload), text))
        elif typ == _STEP_REPLY:
            text, think, tools, steps = _reply(payload)
            if text or think or tools:
                out.append(RawEntry("assistant", f"i{idx}", _step_ts(payload), text, think, tools, steps))
    return Chunk(out, last, more=len(rows) >= CHUNK_ROWS)


# --------------------------------------------------------------------------
# token statistics
# --------------------------------------------------------------------------

_STATS_CACHE: dict[str, dict] = {}
_STATS_MAX = 32
_LOCK = threading.Lock()


def scan_usage(blob: bytes) -> dict | None:
    """Token counters of one ``gen_metadata`` blob, or None when it has no usage message."""
    top = fields(blob)
    if top is None:
        return None
    gen = next((v for f, _w, v in top if f == 1 and isinstance(v, bytes)), None)
    model = _text(find(gen, 19)) or next((_text(v) for f, _w, v in top if f == 19), "")
    usage = find(gen, 4)
    if not isinstance(usage, bytes):
        return None
    u = {f: v for f, w, v in (fields(usage) or []) if w == 0}
    if 2 not in u and 3 not in u:
        return None
    ctx = find(gen, 9, 10, 1)
    window = find(gen, 9, 10, 4)
    return {
        "in": u.get(2, 0), "out": u.get(3, 0), "cache": u.get(5, 0),
        "ctx": ctx if isinstance(ctx, int) else u.get(2, 0) + u.get(5, 0),
        "window": window if isinstance(window, int) else 0,
        "model": model,
    }


def _stat_path(agent, home: Path) -> Path | None:
    p: Path | None = None
    if getattr(agent, "pid", 0) > 1:
        try:
            p = locate(agent.pid, home)
        except Exception:  # noqa: BLE001
            p = None
    if p is None and getattr(agent, "transcript", ""):
        t = Path(agent.transcript)
        p = t if t.is_file() else None
    return p


def session_stats(agent, home: Path | None = None):
    """``Stats`` of the agent's conversation, or None. Incremental per db (last idx)."""
    path = _stat_path(agent, home or Path.home())
    if path is None:
        return None
    key, mt = str(path), mtime(path)
    with _LOCK:
        c = _STATS_CACHE.get(key)
        if c is not None and c["mtime"] == mt:
            return _build(Stats, c)
        c = dict(c) if c else {"last": -1, "in": 0, "out": 0, "cache": 0, "ctx": 0, "window": 0,
                               "model": "", "turns": 0, "replies": 0, "tools": 0, "dur": 0}
    try:
        con = _connect(path)
        try:
            while True:
                rows = con.execute(
                    "SELECT idx, CASE WHEN size <= ? THEN data END FROM gen_metadata WHERE idx > ? "
                    "ORDER BY idx LIMIT ?", (_MAX_GEN_BLOB, c["last"], _BATCH)).fetchall()
                for idx, data in rows:
                    c["last"] = max(c["last"], idx)
                    u = scan_usage(data) if isinstance(data, bytes) else None
                    if u is None:
                        continue
                    c["in"] += u["in"]
                    c["out"] += u["out"]
                    c["cache"] += u["cache"]
                    c["ctx"], c["window"] = u["ctx"], u["window"] or c["window"]
                    c["model"] = u["model"] or c["model"]
                if len(rows) < _BATCH:
                    break
            c["turns"] = con.execute("SELECT count(*) FROM steps WHERE step_type=?", (_STEP_USER,)).fetchone()[0]
            c["replies"] = con.execute("SELECT count(*) FROM steps WHERE step_type=?", (_STEP_REPLY,)).fetchone()[0]
            c["tools"] = con.execute("SELECT count(*) FROM steps WHERE step_type=?", (_STEP_TOOL,)).fetchone()[0]
            first = con.execute("SELECT CASE WHEN length(step_payload) <= ? THEN step_payload END FROM steps "
                                "WHERE step_type IN (?, ?) ORDER BY idx ASC LIMIT 1",
                                (_MAX_BLOB, _STEP_USER, _STEP_REPLY)).fetchone()
            last = con.execute("SELECT CASE WHEN length(step_payload) <= ? THEN step_payload END FROM steps "
                               "WHERE step_type IN (?, ?) ORDER BY idx DESC LIMIT 1",
                               (_MAX_BLOB, _STEP_USER, _STEP_REPLY)).fetchone()
            t0 = _step_ts(first[0]) if first and isinstance(first[0], bytes) else 0.0
            t1 = _step_ts(last[0]) if last and isinstance(last[0], bytes) else 0.0
            c["dur"] = max(0, int(t1 - t0)) if t0 and t1 else 0
        finally:
            con.close()
    except sqlite3.Error:
        return None
    c["mtime"] = mt
    with _LOCK:
        if len(_STATS_CACHE) >= _STATS_MAX:
            _STATS_CACHE.clear()
        _STATS_CACHE[key] = c
    return _build(Stats, c)


def usage_extras(agent, home: Path | None = None) -> tuple[int, int, int] | None:
    """``(replies, tools, duration_sec)`` from the cached conversation stats, or None."""
    path = _stat_path(agent, home or Path.home())
    if path is None or session_stats(agent, home) is None:
        return None
    with _LOCK:
        c = _STATS_CACHE.get(str(path))
    return (c.get("replies", 0), c.get("tools", 0), c.get("dur", 0)) if c else None


def _build(stats_cls, c: dict):
    return stats_cls(
        turns=c["turns"], input=c["in"], output=c["out"], cache_write=0, cache_read=c["cache"],
        ctx=c["ctx"], model=c["model"][:80], window_hint=_DEFAULT_WINDOW,  # agy /context reports 1.0M although field 1.9.10.4 says 256000
        source="antigravity", exact=True)
