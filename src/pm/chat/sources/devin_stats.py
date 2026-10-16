"""Token statistics of a Devin session from ``~/.local/share/devin/cli/sessions.db`` (read-only).

Only numbers are selected (``json_extract`` of counters); message text is never read, logged or kept.
The main chain is the parent walk from ``sessions.main_chain_id``; sub-agent chains and abandoned
branches are not on it. Results are cached by (db + wal mtime/size, session id).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .common import Stats

WINDOW = 262_000  # Devin TUI: "Context: 25k / 262k tokens (9%)"
_MAX_ROWS = 200_000
_MAX_CACHE = 32
_LOCK = threading.Lock()
_CACHE: dict[str, tuple[tuple, Stats | None]] = {}

_SQL = (
    "SELECT node_id, parent_node_id, json_extract(chat_message,'$.role'),"
    " json_extract(chat_message,'$.metadata.is_user_input'),"
    " json_extract(chat_message,'$.metadata.request_id'),"
    " json_extract(chat_message,'$.metadata.metrics.input_tokens'),"
    " json_extract(chat_message,'$.metadata.metrics.output_tokens'),"
    " json_extract(chat_message,'$.metadata.metrics.cache_creation_tokens'),"
    " json_extract(chat_message,'$.metadata.metrics.cache_read_tokens'),"
    " json_extract(chat_message,'$.metadata.num_tokens'),"
    " json_extract(metadata,'$.num_tokens_preceding')"
    " FROM message_nodes WHERE session_id=? LIMIT ?"
)


def db_path(home: Path) -> Path:
    return home / ".local" / "share" / "devin" / "cli" / "sessions.db"


def _n(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def _sig(db: Path) -> tuple:
    out = []
    for p in (db, db.with_name(db.name + "-wal")):
        try:
            s = p.stat()
            out.append((s.st_mtime_ns, s.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def compute(db: Path, sid: str) -> Stats | None:
    uri = "file:" + str(db).replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=0.3)
    except sqlite3.Error:
        return None
    try:
        con.execute("PRAGMA query_only=1")
        row = con.execute("SELECT model, main_chain_id FROM sessions WHERE id=?", (sid,)).fetchone()
        if row is None:
            return None
        model, head = row
        nodes = {r[0]: r for r in con.execute(_SQL, (sid, _MAX_ROWS))}
    except sqlite3.Error:
        return None
    finally:
        con.close()
    chain = []
    seen = set()
    cur = head if head in nodes else (max(nodes) if nodes else None)
    while cur is not None and cur in nodes and cur not in seen:
        seen.add(cur)
        chain.append(nodes[cur])
        cur = nodes[cur][1]
    chain.reverse()  # oldest first
    s = Stats(model=str(model or "")[:80], window_hint=WINDOW, source="devin", exact=True)
    reqs: set[str] = set()
    for _nid, _par, role, is_user, rid, i, o, cw, cr, ntok, prec in chain:
        if role == "user" and is_user:
            s.turns += 1
        elif role == "assistant":
            if rid:
                if rid in reqs:
                    continue
                reqs.add(rid)
            s.input += _n(i)
            s.output += _n(o)
            s.cache_write += _n(cw)
            s.cache_read += _n(cr)
            ctx = _n(prec) + _n(ntok) if _n(prec) else _n(i) + _n(cw) + _n(cr) + _n(o)
            if ctx:
                s.ctx = ctx
    return s


def session_stats(agent, home: Path | None = None, sid: str | None = None) -> Stats | None:
    from pm.chat import sources as chat_sources

    home = home or Path.home()
    if sid is None:
        path = chat_sources.session_file(agent.kind, pid=agent.pid, cwd=agent.cwd, age_s=agent.age_s, home=home)
        if path is None:
            return None
        sid = path.stem
    db = db_path(home)
    sig = _sig(db)
    if sig[0] is None:
        return None
    key = f"{db}:{sid}"
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] == sig:
            return _copy(hit[1])
    st = compute(db, sid)
    with _LOCK:
        _CACHE[key] = (sig, st)
        while len(_CACHE) > _MAX_CACHE:
            _CACHE.pop(next(iter(_CACHE)))
    return _copy(st)


def _copy(s: Stats | None) -> Stats | None:
    return None if s is None else Stats(**s.__dict__)
