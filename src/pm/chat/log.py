"""Full chat history of an agent, stored in SQLite (``settings.db_file``).

Blocking; callers use ``asyncio.to_thread`` / an executor.

``sync`` ingests the new entries of the agent's current session store (never the pane)
into ``chat_messages``; ``messages`` / ``latest_seq`` / ``get`` read it back as
``pm.core.models.ChatMsg``. Every text and thinking string is masked (``render_pane``) and
clipped before it is stored; tool calls are stored as counts per name plus one masked
``steps`` line per call (its target: command, file path, pattern, URL, query; never other
inputs or results). Errors are logged by exception type name only, never content.

Steps (column ``steps``, JSON ``[[kind, name, line], ...]``, added by ``_migrate`` on older
DBs, whose rows keep ``[]``): one per assistant text block, thinking block and tool call, in
order, each line masked then cut to ``STEP_LINE`` chars; the newest ``MAX_STEPS`` per turn.

Grouping: a user row is one real user prompt. Every assistant piece after it (until the
next real prompt; tool results do not end a turn) is merged into one assistant row with
uid ``<user uid>:a`` (``<first piece uid>:a`` when the prompt is outside the first-read
window). The turn row grows while the agent runs: same uid and seq, ``updated`` bumped.
Text over the cap keeps its newest part (the answer is at the end of a turn).

Per (agent id, store file) a ``chat_ingest`` row keeps the cursor (byte offset, rowid,
step idx or step id), the file's inode/size/mtime (nothing is read while they are
unchanged) and the open turn. A rewritten append-only file (new inode or shorter than
the cursor) or a cursor of an older ``FORMAT`` drops that conversation and re-reads it.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import Counter
from pathlib import Path

from pm.chat import sources as chat_sources
from pm.chat.sources.claude import live_session_id, transcript_path
from pm.chat.sources.common import MAX_TOOL_NAME, STEP_RAW, RawEntry, mtime
from pm.chat.text import clean_text
from pm.core import db
from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, ChatMsg, ChatStep, ToolCount

log = logging.getLogger("pm.chatlog")

MAX_TEXT = 20000
MAX_PER_AGENT = 5000
MAX_LIMIT = 500
MAX_LOOPS = 4  # bounded chunks per sync (a large backlog finishes on later syncs)
MAX_STEPS = 200  # per assistant turn, newest kept
STEP_LINE = 160
STEP_KINDS = ("text", "tool", "thinking")
# parsing format: a cursor written by an older one re-reads its conversation once
# (1 = narration thinking is text, 2026-10-12; 2 = Antigravity waits for streaming steps, 2026-10-17)
FORMAT = 2

_SCHEMA = (
    ("CREATE TABLE IF NOT EXISTS chat_messages ("
    " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " agent_id TEXT NOT NULL, conv TEXT NOT NULL, uid TEXT NOT NULL, role TEXT NOT NULL,"
    " ts REAL NOT NULL DEFAULT 0, text TEXT NOT NULL DEFAULT '', thinking TEXT NOT NULL DEFAULT '',"
    " tools TEXT NOT NULL DEFAULT '[]', updated REAL NOT NULL DEFAULT 0,"
    " steps TEXT NOT NULL DEFAULT '[]',"
    " UNIQUE (agent_id, conv, uid))"),
    "CREATE INDEX IF NOT EXISTS chat_messages_agent ON chat_messages (agent_id, seq)",
    ("CREATE TABLE IF NOT EXISTS chat_ingest ("
    " agent_id TEXT NOT NULL, path TEXT NOT NULL, kind TEXT NOT NULL, conv TEXT NOT NULL,"
    " inode INTEGER NOT NULL DEFAULT 0, size INTEGER NOT NULL DEFAULT -1, mtime REAL NOT NULL DEFAULT 0,"
    " pos INTEGER NOT NULL DEFAULT -1, last_user TEXT NOT NULL DEFAULT '', turn TEXT NOT NULL DEFAULT '',"
    " updated REAL NOT NULL DEFAULT 0, fmt INTEGER NOT NULL DEFAULT 0,"
    " PRIMARY KEY (agent_id, path))"),
)

_INIT = db.inited(_SCHEMA)  # DB files the schema ran on (tests discard one to re-run it)


def _connect(settings: Settings) -> sqlite3.Connection:
    return db.connect(settings, _SCHEMA, _migrate)


def _migrate(con: sqlite3.Connection) -> None:
    """Add columns missing from a table created by an older pm (old rows keep the default)."""
    for table, col, ddl in (("chat_messages", "steps", "TEXT NOT NULL DEFAULT '[]'"),
                            ("chat_ingest", "fmt", "INTEGER NOT NULL DEFAULT 0")):
        if col not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
            try:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass  # another process added it first (duplicate column)


def _clean(text: str) -> str:
    return clean_text(text, MAX_TEXT) if text else ""


def _join(old: str, new: str) -> str:
    """``old`` + blank line + ``new``; over the cap the newest part is kept."""
    out = f"{old}\n\n{new}" if old and new else (old or new)
    if len(out) > MAX_TEXT:
        out = "…" + out[-(MAX_TEXT - 1):].lstrip()
    return out


def step_line(line: str) -> str:
    """Mask (like the message text), collapse to one line, cap at ``STEP_LINE`` with an ellipsis."""
    from pm.security.mask import render_pane

    if not line:
        return ""
    out = " ".join(render_pane(line).split())
    if len(out) > STEP_LINE:
        return out[:STEP_LINE - 1].rstrip() + "…"
    if len(line) >= STEP_RAW and not out.endswith("…"):  # the parser cut it already
        out = out[:STEP_LINE - 1].rstrip() + "…"
    return out


def _step(raw: object) -> tuple[str, str, str] | None:
    if not (isinstance(raw, tuple | list) and len(raw) == 3):
        return None
    kind, name, line = raw
    if kind not in STEP_KINDS or not isinstance(name, str) or not isinstance(line, str):
        return None
    name = " ".join(name.split())[:MAX_TOOL_NAME] if kind == "tool" else ""
    line = step_line(line)
    if kind != "tool" and not line:
        return None
    return kind, name, line


def _steps_json(steps: list[tuple[str, str, str]]) -> str:
    return json.dumps([list(s) for s in steps[-MAX_STEPS:]], ensure_ascii=False)


def _steps_load(raw: object) -> list[tuple[str, str, str]]:
    try:
        data = json.loads(raw) if isinstance(raw, str) else []
    except ValueError:
        return []
    out: list[tuple[str, str, str]] = []
    for item in data if isinstance(data, list) else []:
        if (isinstance(item, list) and len(item) == 3 and item[0] in STEP_KINDS
                and isinstance(item[1], str) and isinstance(item[2], str)):
            out.append((item[0], item[1][:MAX_TOOL_NAME], item[2][:STEP_LINE]))
    return out[-MAX_STEPS:]


# --------------------------------------------------------------------------
# locating the store
# --------------------------------------------------------------------------


def store_file(agent: Agent) -> Path | None:
    kind = agent.kind
    if kind == AgentKind.CLAUDE:
        uuid = agent.session_uuid or ""
        if agent.pid > 1:
            uuid = live_session_id(agent.pid) or uuid  # /clear or resume since the last detect
        path = transcript_path(agent.cwd, uuid)
        if (path is None or not path.is_file()) and agent.transcript and Path(agent.transcript).is_file():
            path = Path(agent.transcript)
        return path if path is not None and path.is_file() else None
    if kind not in (AgentKind.DEVIN, AgentKind.CODEX, AgentKind.CURSOR, AgentKind.ANTIGRAVITY):
        return None
    if agent.pid <= 1:  # ended agent: the store file recorded while it ran
        t = Path(agent.transcript) if agent.transcript else None
        return t if t is not None and t.is_file() else None
    path = chat_sources.session_file(kind, pid=agent.pid, cwd=agent.cwd, age_s=agent.age_s)
    if path is None and agent.transcript and Path(agent.transcript).is_file():
        path = Path(agent.transcript)
    return path


def conv_key(kind: AgentKind, path: Path) -> str:
    if kind == AgentKind.CURSOR:  # .../<chat uuid>/store.db
        return path.parent.name[:80]
    return path.stem[:80]


def file_sig(kind: AgentKind, path: Path) -> tuple[int, int, float]:
    st = path.stat()
    size = st.st_size
    if kind in (AgentKind.CURSOR, AgentKind.ANTIGRAVITY):  # SQLite: the -wal grows first
        try:
            size += path.with_name(path.name + "-wal").stat().st_size
        except OSError:
            pass
        return st.st_ino, size, mtime(path)
    return st.st_ino, size, st.st_mtime


# --------------------------------------------------------------------------
# ingest
# --------------------------------------------------------------------------


def sync(settings: Settings, agent: Agent) -> str:
    """Ingest new entries of the agent's current store. Returns the conv key ("" when none). Never raises."""
    try:
        return _sync(settings, agent)
    except Exception as exc:  # noqa: BLE001 - never raise; type only, no content
        log.warning("chatlog sync failed: %s", type(exc).__name__)
        return ""


def _sync(settings: Settings, agent: Agent) -> str:
    kind = agent.kind if isinstance(agent.kind, AgentKind) else AgentKind(agent.kind)
    path = store_file(agent)
    if path is None:
        return ""
    conv = conv_key(kind, path)
    try:
        sig = file_sig(kind, path)
    except OSError:
        return ""
    con = _connect(settings)
    try:
        row = con.execute("SELECT conv, inode, size, mtime, fmt FROM chat_ingest WHERE agent_id=? AND path=?",
                          (agent.id, str(path))).fetchone()
        if row is not None and row[0] == conv and (row[1], row[2], row[3]) == sig and row[4] >= FORMAT:
            return conv  # unchanged: nothing read
        with db.agent_lock(agent.id):
            con.execute("BEGIN IMMEDIATE")
            try:
                _ingest(con, agent.id, kind, path, conv, sig)
                con.commit()
            except BaseException:
                con.rollback()
                raise
        return conv
    finally:
        con.close()


def _ingest(con: sqlite3.Connection, agent_id: str, kind: AgentKind, path: Path, conv: str,
            sig: tuple[int, int, float]) -> None:
    row = con.execute("SELECT inode, pos, last_user, turn, fmt FROM chat_ingest WHERE agent_id=? AND path=?",
                      (agent_id, str(path))).fetchone()
    inode, pos, last_user, turn, fmt = row if row is not None else (sig[0], -1, "", "", FORMAT)
    rewritten = kind in chat_sources.BYTE_CURSOR and (inode != sig[0] or sig[1] < pos)
    if row is not None and (fmt < FORMAT or rewritten):
        pos, last_user, turn = -1, "", ""  # rewritten file or read by an older format
        _drop_conv(con, agent_id, conv)
    more = False
    for _ in range(MAX_LOOPS):
        chunk = chat_sources.read_entries(kind, path, pos)
        if chunk.reset:
            _drop_conv(con, agent_id, conv)
            pos, last_user, turn = -1, "", ""
            chunk = chat_sources.read_entries(kind, path, -1)
        last_user, turn = _apply(con, agent_id, conv, chunk.entries, last_user, turn)
        pos, more = chunk.pos, chunk.more
        if not more:
            break
    now = time.time()
    # bounded read with a backlog left: store size -1 so the next sync continues
    con.execute(
        "INSERT INTO chat_ingest (agent_id, path, kind, conv, inode, size, mtime, pos, last_user, turn, updated, fmt) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (agent_id, path) DO UPDATE SET "
        "kind=excluded.kind, conv=excluded.conv, inode=excluded.inode, size=excluded.size, mtime=excluded.mtime, "
        "pos=excluded.pos, last_user=excluded.last_user, turn=excluded.turn, updated=excluded.updated, fmt=excluded.fmt",
        (agent_id, str(path), kind.value, conv, sig[0], -1 if more else sig[1], sig[2], pos, last_user, turn, now,
         FORMAT))
    _cap(con, agent_id)


def _drop_conv(con: sqlite3.Connection, agent_id: str, conv: str) -> None:
    con.execute("DELETE FROM chat_messages WHERE agent_id=? AND conv=?", (agent_id, conv))


class _Turn:
    __slots__ = ("steps", "text", "thinking", "tools", "ts", "uid")

    def __init__(self, uid: str, ts: float) -> None:
        self.uid, self.ts = uid, ts
        self.text: list[str] = []
        self.thinking: list[str] = []
        self.tools: Counter[str] = Counter()
        self.steps: list[tuple[str, str, str]] = []


def _apply(con: sqlite3.Connection, agent_id: str, conv: str, entries: list[RawEntry],
           last_user: str, turn: str) -> tuple[str, str]:
    pending: _Turn | None = None
    for e in entries:
        if e.mid_turn:
            continue  # a prompt taken in mid-turn: items only (pm.chat.itemsync); turns stay whole here
        if e.role == "user":
            if pending is not None:
                _flush(con, agent_id, conv, pending)
                pending = None
            _upsert_user(con, agent_id, conv, e)
            last_user, turn = e.uid, ""
            continue
        if e.role != "assistant" or not (e.text or e.thinking or e.tools):
            continue  # tool results and empty pieces (an end_turn marker) are items only (pm.chat.itemsync)
        if not turn:
            turn = f"{last_user or e.uid}:a"[:120]
        if pending is None:
            pending = _Turn(turn, e.ts)
        if e.text:
            pending.text.append(_clean(e.text))
        if e.thinking:
            pending.thinking.append(_clean(e.thinking))
        pending.tools.update(e.tools)
        for raw in e.steps:
            st = _step(raw)
            if st is not None:
                pending.steps.append(st)
        if len(pending.steps) > 2 * MAX_STEPS:
            del pending.steps[:-MAX_STEPS]
    if pending is not None:
        _flush(con, agent_id, conv, pending)
    return last_user, turn


def _upsert_user(con: sqlite3.Connection, agent_id: str, conv: str, e: RawEntry) -> None:
    text = _clean(e.text)
    now = time.time()
    con.execute(
        "INSERT INTO chat_messages (agent_id, conv, uid, role, ts, text, thinking, tools, updated) "
        "VALUES (?,?,?,?,?,?,'','[]',?) ON CONFLICT (agent_id, conv, uid) DO UPDATE SET "
        "text=excluded.text, ts=excluded.ts, updated=excluded.updated WHERE text != excluded.text",
        (agent_id, conv, e.uid[:120], "user", e.ts, text, now))


def _flush(con: sqlite3.Connection, agent_id: str, conv: str, t: _Turn) -> None:
    text = "\n\n".join(p for p in t.text if p)
    thinking = "\n\n".join(p for p in t.thinking if p)
    if not text and not thinking and not t.tools:
        return
    now = time.time()
    row = con.execute("SELECT seq, ts, text, thinking, tools, steps FROM chat_messages "
                      "WHERE agent_id=? AND conv=? AND uid=?", (agent_id, conv, t.uid)).fetchone()
    if row is None:
        con.execute(
            "INSERT INTO chat_messages (agent_id, conv, uid, role, ts, text, thinking, tools, updated, steps) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (agent_id, conv, t.uid, "assistant", t.ts, _join("", text), _join("", thinking),
             _tools_json(t.tools), now, _steps_json(t.steps)))
        return
    seq, ts, old_text, old_think, old_tools, old_steps = row
    tools = Counter(dict(_tools_load(old_tools)))
    tools.update(t.tools)
    con.execute(
        "UPDATE chat_messages SET ts=?, text=?, thinking=?, tools=?, updated=?, steps=? WHERE seq=?",
        (ts or t.ts, _join(old_text, text), _join(old_think, thinking), _tools_json(tools), now,
         _steps_json(_steps_load(old_steps) + t.steps), seq))


def _tools_json(c: Counter[str]) -> str:
    items = sorted(((n[:64], k) for n, k in c.items() if k > 0), key=lambda t: (-t[1], t[0]))
    return json.dumps([[n, k] for n, k in items[:200]])


def _tools_load(raw: object) -> list[tuple[str, int]]:
    try:
        data = json.loads(raw) if isinstance(raw, str) else []
    except ValueError:
        return []
    out: list[tuple[str, int]] = []
    for item in data if isinstance(data, list) else []:
        if (isinstance(item, list) and len(item) == 2 and isinstance(item[0], str)
                and isinstance(item[1], int) and not isinstance(item[1], bool)):
            out.append((item[0][:64], item[1]))
    return out


def _cap(con: sqlite3.Connection, agent_id: str) -> None:
    row = con.execute("SELECT seq FROM chat_messages WHERE agent_id=? ORDER BY seq DESC LIMIT 1 OFFSET ?",
                      (agent_id, MAX_PER_AGENT - 1)).fetchone()
    if row is not None:
        con.execute("DELETE FROM chat_messages WHERE agent_id=? AND seq < ?", (agent_id, row[0]))


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

_COLS = "seq, uid, conv, role, ts, text, thinking, tools, updated, steps"


def _msg(r: tuple) -> ChatMsg:
    return ChatMsg(seq=r[0], uid=r[1], conv=r[2], role=r[3], ts=r[4] or 0.0, text=r[5] or "",
                   thinking=r[6] or "", tools=tuple(ToolCount(n, k) for n, k in _tools_load(r[7])),
                   updated=r[8] or 0.0,
                   steps=tuple(ChatStep(k, n, ln) for k, n, ln in _steps_load(r[9])))


def messages(settings: Settings, agent_id: str, *, before_seq: int | None = None,
             after_seq: int | None = None, limit: int = 50) -> list[ChatMsg]:
    """Stored messages of ``agent_id`` (every conv), newest first.

    ``before_seq``: older page (seq < before_seq, the newest ``limit`` of them). ``after_seq``:
    newer messages (seq > after_seq); when more than ``limit`` exist the OLDEST ``limit`` of
    them are returned (still newest first), so polling with the max seq seen never skips rows.
    A growing assistant turn keeps its seq: re-fetch it with ``get`` (its ``updated`` changes).
    """
    limit = max(1, min(int(limit), MAX_LIMIT))
    where, args = ["agent_id=?"], [agent_id]
    if before_seq is not None:
        where.append("seq < ?")
        args.append(int(before_seq))
    if after_seq is not None:
        where.append("seq > ?")
        args.append(int(after_seq))
    order = "ASC" if after_seq is not None else "DESC"
    try:
        con = _connect(settings)
        try:
            rows = con.execute(f"SELECT {_COLS} FROM chat_messages WHERE {' AND '.join(where)} "
                               f"ORDER BY seq {order} LIMIT ?", (*args, limit)).fetchall()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 - never raise; type only
        log.warning("chatlog read failed: %s", type(exc).__name__)
        return []
    out = [_msg(r) for r in rows]
    if order == "ASC":
        out.reverse()
    return out


CONTEXT_PAGES = 10


def context_text(settings: Settings, agent_id: str, max_chars: int = 24000) -> str:
    """Recent conversation of ``agent_id`` as plain text, oldest first ("User: …" / "Assistant: …";
    text only, no thinking, no tools), masked like ``_snip``, at most ``max_chars`` (the oldest part
    is cut). "" when nothing is stored. Never raises (``messages`` does not)."""
    from pm.security.mask import render_pane

    parts: list[str] = []  # newest first while paging
    used, before, pages = 0, None, 0
    while used < max_chars and pages < CONTEXT_PAGES:  # pages: tool-only stretches must not read the whole log
        pages += 1
        page = messages(settings, agent_id, before_seq=before, limit=MAX_LIMIT)
        if not page:
            break
        for m in page:
            body = render_pane(m.text).strip() if m.text else ""
            if body:
                parts.append(f"{'User' if m.role == 'user' else 'Assistant'}: {body}")
                used += len(parts[-1]) + 2
                if used >= max_chars:
                    break
        before = page[-1].seq
    out = "\n\n".join(reversed(parts))
    if len(out) > max_chars:
        out = "…" + out[-(max_chars - 1):]
    return out


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _snip(text: str, q: str, width: int = 100) -> str:
    """Short one-line, masked excerpt of ``text`` around the first match of ``q``."""
    from pm.security.mask import render_pane

    i = text.lower().find(q.lower())
    i = max(i, 0)
    half = max(0, (width - len(q)) // 2)
    a = max(0, i - half)
    b = i + len(q) + half
    out = render_pane(" ".join(text[a:b].split()))
    return ("…" if a else "") + out + ("…" if b < len(text) else "")


def search(settings: Settings, q: str, limit: int = 5) -> list[dict]:
    """Newest-first messages whose (already masked) text contains ``q``:
    ``[{agent_id, role, ts, seq, snippet}]``. Literal match (LIKE wildcards escaped); never raises,
    never logs the query or any hit."""
    q = " ".join(q.split())[:80]
    if len(q) < 3:
        return []
    limit = max(1, min(int(limit), 50))
    try:
        con = _connect(settings)
        try:
            con.execute("PRAGMA query_only=1")
            rows = con.execute(
                "SELECT agent_id, role, ts, seq, text FROM chat_messages WHERE text LIKE ? ESCAPE '\\' "
                "ORDER BY seq DESC LIMIT ?", (_like(q), limit)).fetchall()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 - never raise; type only
        log.warning("chatlog search failed: %s", type(exc).__name__)
        return []
    return [{"agent_id": r[0], "role": r[1], "ts": r[2] or 0.0, "seq": r[3], "snippet": _snip(r[4] or "", q)}
            for r in rows]


def latest_seq(settings: Settings, agent_id: str) -> int:
    try:
        con = _connect(settings)
        try:
            row = con.execute("SELECT max(seq) FROM chat_messages WHERE agent_id=?", (agent_id,)).fetchone()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 - never raise; type only
        log.warning("chatlog read failed: %s", type(exc).__name__)
        return 0
    return int(row[0] or 0) if row else 0


def get(settings: Settings, agent_id: str, seq: int) -> ChatMsg | None:
    try:
        con = _connect(settings)
        try:
            row = con.execute(f"SELECT {_COLS} FROM chat_messages WHERE agent_id=? AND seq=?",
                              (agent_id, int(seq))).fetchone()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 - never raise; type only
        log.warning("chatlog read failed: %s", type(exc).__name__)
        return None
    return _msg(row) if row else None
