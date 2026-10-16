"""Append-only chat items (``chat_items`` in ``settings.db_file``).

Blocking; callers use an executor. ``pm.chat.itemsync`` writes (masked text only), the read API
here returns ``pm.core.models.ChatItem`` in conversation order (seq, oldest first).

``rev`` is global and monotonic: a counter row (``chat_item_rev``) bumped inside the write
transaction, so a rev is never reused even after rows are dropped (cap, rewritten store file).
An insert takes a new rev; the only allowed changes (a tool's status/ended/note, a user item's
outbox link) take a new rev too. Text items are never updated.

``chat_item_ingest`` keeps ``itemsync``'s cursor per (agent, store file): byte offset / row cursor,
the file's inode/size/mtime, the open turn and its newest text item, and legacy conversions
(``path`` "legacy:<conv>", ``legacy_done`` 1).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from pm.chat import outbox as chat_outbox
from pm.core import db
from pm.core.config import Settings
from pm.core.models import ChatItem, OutboxMsg

log = logging.getLogger("pm.chatitems")

MAX_TEXT = 20000
MAX_NAME = 64
MAX_LINE = 160
MAX_NOTE = 80
MAX_PER_AGENT = 20000
MAX_LIMIT = 1000
PAGE = 200  # items per page: the first load is the newest PAGE, "Load older" the PAGE before it
KINDS = ("user", "text", "think", "tool", "end")

_SCHEMA = (
    ("CREATE TABLE IF NOT EXISTS chat_items ("
     " seq INTEGER PRIMARY KEY AUTOINCREMENT, rev INTEGER NOT NULL,"
     " agent_id TEXT NOT NULL, conv TEXT NOT NULL, uid TEXT NOT NULL, kind TEXT NOT NULL,"
     " turn TEXT NOT NULL DEFAULT '', ts REAL NOT NULL DEFAULT 0, text TEXT NOT NULL DEFAULT '',"
     " name TEXT NOT NULL DEFAULT '', line TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '',"
     " ended REAL NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '', parent INTEGER NOT NULL DEFAULT 0,"
     " outbox INTEGER NOT NULL DEFAULT 0, ref TEXT NOT NULL DEFAULT '',"
     " UNIQUE (agent_id, conv, uid))"),
    "CREATE INDEX IF NOT EXISTS chat_items_agent ON chat_items (agent_id, seq)",
    "CREATE INDEX IF NOT EXISTS chat_items_rev ON chat_items (agent_id, rev)",
    "CREATE INDEX IF NOT EXISTS chat_items_turn ON chat_items (agent_id, conv, turn)",
    "CREATE TABLE IF NOT EXISTS chat_item_rev (id INTEGER PRIMARY KEY CHECK (id = 0), rev INTEGER NOT NULL)",
    "INSERT OR IGNORE INTO chat_item_rev (id, rev) VALUES (0, 0)",
    ("CREATE TABLE IF NOT EXISTS chat_item_ingest ("
     " agent_id TEXT NOT NULL, path TEXT NOT NULL, kind TEXT NOT NULL, conv TEXT NOT NULL,"
     " inode INTEGER NOT NULL DEFAULT 0, size INTEGER NOT NULL DEFAULT -1, mtime REAL NOT NULL DEFAULT 0,"
     " pos INTEGER NOT NULL DEFAULT -1, turn TEXT NOT NULL DEFAULT '', last_text_seq INTEGER NOT NULL DEFAULT 0,"
     " legacy_done INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL DEFAULT 0,"
     " fmt INTEGER NOT NULL DEFAULT 0,"
     " PRIMARY KEY (agent_id, path))"),
)
# itemsync's parsing format: a cursor written by an older format re-reads its conversation once
# (2 = mid-turn queued_command prompts become user items, 2026-10-12;
#  3 = narration thinking becomes text items, background tasks stay running until notified, 2026-10-12;
#  4 = Antigravity waits for streaming steps to finish before ingesting, 2026-10-17;
#  5 = thinking of Antigravity / Devin / Cursor / Codex becomes ``think`` items, 2026-10-17)
FORMAT = 5


def connect(settings: Settings) -> sqlite3.Connection:
    return db.connect(settings, _SCHEMA, _migrate)


def _migrate(con: sqlite3.Connection) -> None:
    """Add ``chat_item_ingest.fmt`` (old rows get 0: re-read once) and ``chat_items.ref`` to tables
    created before them."""
    for table, col, ddl in (("chat_item_ingest", "fmt", "INTEGER NOT NULL DEFAULT 0"),
                            ("chat_items", "ref", "TEXT NOT NULL DEFAULT ''")):
        if col not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
            try:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass  # another process added it first


# --------------------------------------------------------------------------
# writing (pm.chat.itemsync; the caller holds db.agent_lock and a write transaction)
# --------------------------------------------------------------------------


def next_rev(con: sqlite3.Connection) -> int:
    con.execute("UPDATE chat_item_rev SET rev = rev + 1 WHERE id = 0")
    return int(con.execute("SELECT rev FROM chat_item_rev WHERE id = 0").fetchone()[0])


def add(con: sqlite3.Connection, agent_id: str, conv: str, uid: str, kind: str, *, turn: str = "",
        ts: float = 0.0, text: str = "", name: str = "", line: str = "", status: str = "",
        note: str = "", parent: int = 0) -> int:
    """Insert one item (text already masked); its seq. An existing uid is left as it is (same seq)."""
    uid = uid[:160]
    row = con.execute("SELECT seq FROM chat_items WHERE agent_id=? AND conv=? AND uid=?",
                      (agent_id, conv, uid)).fetchone()
    if row is not None:
        return int(row[0])
    cur = con.execute(
        "INSERT INTO chat_items (rev, agent_id, conv, uid, kind, turn, ts, text, name, line, status, note, parent) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (next_rev(con), agent_id, conv, uid, kind, turn[:160], ts or 0.0, text[:MAX_TEXT], name[:MAX_NAME],
         line[:MAX_LINE], status, note[:MAX_NOTE], int(parent)))
    return int(cur.lastrowid or 0)


def set_result(con: sqlite3.Connection, agent_id: str, conv: str, uid: str, status: str, ended: float,
               note: str) -> bool:
    """A tool's result: status/ended (and the note when one is given). New rev only when it changed."""
    row = con.execute("SELECT seq, status, ended, note FROM chat_items WHERE agent_id=? AND conv=? AND uid=? "
                      "AND kind='tool'", (agent_id, conv, uid[:160])).fetchone()
    if row is None:
        return False
    seq, old_status, old_ended, old_note = row
    note = note[:MAX_NOTE] or old_note
    if (status, ended or 0.0, note) == (old_status, old_ended, old_note):
        return False
    con.execute("UPDATE chat_items SET status=?, ended=?, note=?, rev=? WHERE seq=?",
                (status, ended or 0.0, note, next_rev(con), seq))
    return True


def finish_run(con: sqlite3.Connection, agent_id: str, conv: str, uid: str, status: str, ended: float,
               note: str = "") -> bool:
    """``set_result`` for a tool still running (status "run"); a finished one is left as it is."""
    row = con.execute("SELECT 1 FROM chat_items WHERE agent_id=? AND conv=? AND uid=? AND kind='tool' "
                      "AND status='run'", (agent_id, conv, uid[:160])).fetchone()
    return row is not None and set_result(con, agent_id, conv, uid, status, ended, note)


def set_ref(con: sqlite3.Connection, agent_id: str, conv: str, uid: str, ref: str) -> bool:
    """Store a tool's background task id (new rev when it changed)."""
    row = con.execute("SELECT seq, ref FROM chat_items WHERE agent_id=? AND conv=? AND uid=? AND kind='tool'",
                      (agent_id, conv, uid[:160])).fetchone()
    if row is None or row[1] == ref[:MAX_NAME]:
        return False
    con.execute("UPDATE chat_items SET ref=?, rev=? WHERE seq=?", (ref[:MAX_NAME], next_rev(con), row[0]))
    return True


def by_ref(con: sqlite3.Connection, agent_id: str, conv: str, task_id: str) -> ChatItem | None:
    """The newest tool item of ``conv`` launched as background task ``task_id``."""
    if not task_id:
        return None
    r = con.execute(f"SELECT {_COLS} FROM chat_items WHERE agent_id=? AND conv=? AND kind='tool' AND ref=? "
                    "ORDER BY seq DESC LIMIT 1", (agent_id, conv, task_id[:MAX_NAME])).fetchone()
    return _item(r) if r else None


def running_background(con: sqlite3.Connection, agent_id: str, conv: str, limit: int = 10) -> list[ChatItem]:
    """Background tool items of ``conv`` still running, newest first."""
    rows = con.execute(f"SELECT {_COLS} FROM chat_items WHERE agent_id=? AND conv=? AND kind='tool' "
                       "AND status='run' AND note='background' ORDER BY seq DESC LIMIT ?",
                       (agent_id, conv, max(1, int(limit)))).fetchall()
    return [_item(r) for r in rows]


def stopped_background(con: sqlite3.Connection, agent_id: str, conv: str, note: str,
                       limit: int = 10) -> list[tuple[str, str, float]]:
    """(uid, task id, ts) of background tools closed as err with ``note`` (e.g. "stopped · session ended"),
    newest first: their outcome may still turn up later."""
    rows = con.execute("SELECT uid, ref, ts FROM chat_items WHERE agent_id=? AND conv=? AND kind='tool' "
                       "AND status='err' AND note=? AND ref!='' ORDER BY seq DESC LIMIT ?",
                       (agent_id, conv, note[:MAX_NOTE], max(1, int(limit)))).fetchall()
    return [(str(u), str(r), float(t or 0.0)) for u, r, t in rows]


def turn_stats(con: sqlite3.Connection, agent_id: str, conv: str, turn: str) -> tuple[int, int, float, float]:
    """(text items, tool items, first ts, last ts or tool end) of one turn."""
    r = con.execute("SELECT coalesce(sum(kind='text'),0), coalesce(sum(kind='tool'),0), min(ts), "
                    "max(max(ts, ended)) FROM chat_items WHERE agent_id=? AND conv=? AND turn=? AND kind != 'end'",
                    (agent_id, conv, turn)).fetchone()
    return int(r[0]), int(r[1]), float(r[2] or 0.0), float(r[3] or 0.0)


def drop_conv(con: sqlite3.Connection, agent_id: str, conv: str) -> None:
    con.execute("DELETE FROM chat_items WHERE agent_id=? AND conv=?", (agent_id, conv))


def cap(con: sqlite3.Connection, agent_id: str) -> None:
    row = con.execute("SELECT seq FROM chat_items WHERE agent_id=? ORDER BY seq DESC LIMIT 1 OFFSET ?",
                      (agent_id, MAX_PER_AGENT - 1)).fetchone()
    if row is not None:
        con.execute("DELETE FROM chat_items WHERE agent_id=? AND seq < ?", (agent_id, row[0]))


def max_rev(con: sqlite3.Connection, agent_id: str) -> int:
    row = con.execute("SELECT max(rev) FROM chat_items WHERE agent_id=?", (agent_id,)).fetchone()
    return int(row[0] or 0) if row else 0


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

_COLS = "seq, rev, uid, conv, kind, turn, ts, text, name, line, status, ended, note, parent, outbox"


def _item(r: tuple) -> ChatItem:
    return ChatItem(seq=r[0], rev=r[1], uid=r[2], conv=r[3], kind=r[4], turn=r[5], ts=r[6] or 0.0,
                    text=r[7] or "", name=r[8] or "", line=r[9] or "", status=r[10] or "", ended=r[11] or 0.0,
                    note=r[12] or "", parent=r[13] or 0, outbox=r[14] or 0)


def list_items(settings: Settings, agent_id: str, *, after_rev: int | None = None,
               before_seq: int | None = None, limit: int = 200) -> list[ChatItem]:
    """Items of ``agent_id`` in conversation order (seq, oldest first). Never raises.

    No cursor: the newest ``limit`` items. ``before_seq``: the newest ``limit`` with seq < before_seq
    (older page). ``after_rev``: items with rev > after_rev (new AND changed); over ``limit`` the
    lowest revs come first, so polling with the max rev seen never skips one.
    """
    limit = max(1, min(int(limit), MAX_LIMIT))
    where, args = ["agent_id=?"], [agent_id]
    if before_seq is not None:
        where.append("seq < ?")
        args.append(int(before_seq))
    if after_rev is not None:
        where.append("rev > ?")
        args.append(int(after_rev))
    order = "rev ASC" if after_rev is not None else "seq DESC"
    try:
        con = connect(settings)
        try:
            rows = con.execute(f"SELECT {_COLS} FROM chat_items WHERE {' AND '.join(where)} "
                               f"ORDER BY {order} LIMIT ?", (*args, limit)).fetchall()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 - never raise; type only
        log.warning("chat items read failed: %s", type(exc).__name__)
        return []
    return sorted((_item(r) for r in rows), key=lambda i: i.seq)


def latest(settings: Settings, agent_id: str) -> tuple[int, int]:
    """(max seq, max rev) of ``agent_id``'s items; (0, 0) when none or on error."""
    try:
        con = connect(settings)
        try:
            row = con.execute("SELECT max(seq), max(rev) FROM chat_items WHERE agent_id=?",
                              (agent_id,)).fetchone()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 - never raise; type only
        log.warning("chat items read failed: %s", type(exc).__name__)
        return 0, 0
    return (int(row[0] or 0), int(row[1] or 0)) if row else (0, 0)


def link_outbox(settings: Settings, agent_id: str, seq: int, outbox_id: int) -> bool:
    """Link user item ``seq`` to the outbox row it was delivered from (new rev). False when no such
    user item or already linked to it."""
    con = connect(settings)
    try:
        with db.agent_lock(agent_id):
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute("SELECT outbox FROM chat_items WHERE agent_id=? AND seq=? AND kind='user'",
                                  (agent_id, int(seq))).fetchone()
                if row is None or row[0] == int(outbox_id):
                    con.rollback()
                    return False
                con.execute("UPDATE chat_items SET outbox=?, rev=? WHERE seq=?",
                            (int(outbox_id), next_rev(con), int(seq)))
                con.commit()
            except BaseException:
                con.rollback()
                raise
        return True
    finally:
        con.close()


# --------------------------------------------------------------------------
# one page of the items view (GET /a/<id>/items)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Page:
    """One response of the items view: items (oldest first), ``more`` (older page exists / poll was
    truncated), the oldest seq, the next cursor (``rev``), the pending outbox (None for an older page)
    and ``reset`` (the page holds items that were re-created: reload in full)."""

    items: list[ChatItem]
    more: bool
    oldest: int
    rev: int
    outbox: list[OutboxMsg] | None
    reset: bool = False


def _gone(settings: Settings, agent_id: str, seq: int | None) -> bool:
    """True when the page holds item ``seq`` but the store no longer does (a conversation was re-read
    from its file: its items were dropped and re-created with new seqs)."""
    if not seq:
        return False
    got = list_items(settings, agent_id, before_seq=seq + 1, limit=1)
    return not got or got[-1].seq != seq


def page(settings: Settings, agent_id: str, *, after_rev: int | None = None, before: int | None = None,
         lo: int | None = None, hi: int | None = None, limit: int = PAGE) -> Page:
    """Blocking. One response's items plus the new cursor (max rev) and the outbox.

    full (no cursor): newest ``limit`` items; the cursor is read first, so a change made meanwhile
    has a higher rev and comes with the next poll. poll (``after_rev``): items whose rev > after_rev
    (new or changed); ``lo``/``hi`` = the page's lowest and highest item seq: if either is gone,
    ``reset`` asks the page to reload in full. older (``before``): the ``limit`` items before that
    seq (no outbox)."""
    if after_rev is not None and any(_gone(settings, agent_id, s) for s in (lo, hi)):
        return Page([], False, 0, after_rev, None, reset=True)
    rev0 = latest(settings, agent_id)[1] if after_rev is None and before is None else 0
    if after_rev is not None:
        got = list_items(settings, agent_id, after_rev=after_rev, limit=limit)
        more = len(got) >= limit  # truncated (lowest revs first): the page asks again at once
    else:
        got = list_items(settings, agent_id, before_seq=before, limit=limit + 1)
        more, got = len(got) > limit, got[-limit:]
    rev = max([rev0, after_rev or 0, *(i.rev for i in got)])
    obs = None
    if before is None:
        linked = {i.outbox for i in got if i.kind == "user" and i.outbox}
        obs = [m for m in chat_outbox.pending(settings, agent_id) if m.id not in linked and m.state != "cancelled"]
    return Page(got, more, got[0].seq if got else 0, rev, obs)
