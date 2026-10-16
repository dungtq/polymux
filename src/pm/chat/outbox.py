"""Composer outbox: messages typed in pm's composer, stored before anything reaches tmux.

Table ``chat_outbox`` in ``settings.db_file`` (docs/CHAT-SYNC.md). Pure DB, no tmux: the background
deliverer (``pm.agents.deliver``) moves rows through the state machine of
``pm.core.models.OutboxMsg``; ``pm.agents.chatfeed`` links a row to the transcript user item once
the message shows up there (``match`` -> ``done``).

The text is the user's own input. It is never logged here; callers audit the id and length only.
"""

from __future__ import annotations

import re
import sqlite3
import time
from contextlib import closing

from pm.chat.text import clean_text, strip_wrappers
from pm.core import db
from pm.core.config import Settings
from pm.core.errors import Conflict, Invalid, NotFound
from pm.core.models import OutboxMsg

MAX_TEXT = 20000
MAX_OPEN = 50  # not done/cancelled rows per agent; more is refused (the composer is not a queue dump)
DONE_SHOWN_S = 60.0  # pending() keeps a just matched row so the bubble can show "done"
MATCH_WINDOW_S = 600.0  # match(): only rows sent within this many seconds before the item
MATCH_SLACK_S = 60.0  # ...or this little after it (clock steps, a transcript ts a bit early)
QUEUED_WINDOW_S = 3600.0  # a "queued" row (Send now) waits until Claude's tool call ends: may be long
INPANE_QUEUED_S = 120.0  # match_queued(): an "inpane" row may still show as Claude's queued block this long
QUEUED_PREFIX_MIN = 20  # match_queued(): shortest common prefix accepted when one text is cut
KEEP_S = 7 * 86400  # done / cancelled rows are dropped after a week
ITEM_TEXT_MAX = 20000  # ChatItem.text bound: the masked form is compared at the same length

STATES = ("saved", "wait", "sending", "inpane", "done", "queued", "failed", "cancelled")
OPEN = ("saved", "wait", "sending", "inpane", "queued", "failed")
DUE = ("saved", "wait")
SENT = ("sending", "inpane", "queued")
# new state -> the states it may be reached from
_FROM: dict[str, tuple[str, ...]] = {
    "wait": ("saved",),
    "sending": ("saved", "wait", "failed"),
    "inpane": ("sending",),
    "queued": ("sending",),
    "done": ("sending", "inpane", "queued"),
    "failed": ("sending", "inpane", "saved", "wait"),
    "saved": ("failed",),
    "cancelled": ("saved", "wait", "failed"),
}

_SCHEMA = (
    ("CREATE TABLE IF NOT EXISTS chat_outbox ("
     " id INTEGER PRIMARY KEY AUTOINCREMENT,"
     " agent_id TEXT NOT NULL, text TEXT NOT NULL, state TEXT NOT NULL,"
     " created REAL NOT NULL, updated REAL NOT NULL, sent REAL NOT NULL DEFAULT 0,"
     " tries INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', item INTEGER NOT NULL DEFAULT 0)"),
    "CREATE INDEX IF NOT EXISTS chat_outbox_agent ON chat_outbox (agent_id, state, id)",
)
_COLS = "id, agent_id, text, state, created, updated, tries, error, item"
_ERROR_MAX = 80


def _con(settings: Settings) -> sqlite3.Connection:
    return db.connect(settings, _SCHEMA)


def _row(r: tuple) -> OutboxMsg:
    return OutboxMsg(id=int(r[0]), agent_id=r[1], text=r[2], state=r[3], created=float(r[4]),
                     updated=float(r[5]), tries=int(r[6]), error=r[7], item=int(r[8]))


def _qs(states: tuple[str, ...]) -> str:
    return ",".join("?" * len(states))


def add(settings: Settings, agent_id: str, text: str, *, now: float | None = None) -> OutboxMsg:
    """Store a new ``saved`` row. Raises Invalid (empty / too long) or Conflict (too many open)."""
    if not isinstance(text, str) or not text.strip():
        raise Invalid("empty message")
    if len(text) > MAX_TEXT:
        raise Invalid("message too long")
    now = time.time() if now is None else now
    with closing(_con(settings)) as con, con:
        con.execute("DELETE FROM chat_outbox WHERE state IN ('done','cancelled') AND updated < ?",
                    (now - KEEP_S,))
        n = con.execute(f"SELECT count(*) FROM chat_outbox WHERE agent_id=? AND state IN ({_qs(OPEN)})",
                        (agent_id, *OPEN)).fetchone()[0]
        if n >= MAX_OPEN:
            raise Conflict("too many unsent messages")
        cur = con.execute("INSERT INTO chat_outbox (agent_id, text, state, created, updated)"
                          " VALUES (?, ?, 'saved', ?, ?)", (agent_id, text, now, now))
        oid = cur.lastrowid
    return OutboxMsg(id=int(oid), agent_id=agent_id, text=text, state="saved", created=now, updated=now)


def get(settings: Settings, oid: int) -> OutboxMsg | None:
    with closing(_con(settings)) as con:
        r = con.execute(f"SELECT {_COLS} FROM chat_outbox WHERE id=?", (int(oid),)).fetchone()
    return _row(r) if r else None


def pending(settings: Settings, agent_id: str, *, now: float | None = None) -> list[OutboxMsg]:
    """Rows still to show as composer bubbles: every open row, plus ``done`` ones of the last minute."""
    now = time.time() if now is None else now
    with closing(_con(settings)) as con:
        rows = con.execute(
            f"SELECT {_COLS} FROM chat_outbox WHERE agent_id=? AND (state IN ({_qs(OPEN)})"
            " OR (state='done' AND updated >= ?)) ORDER BY id", (agent_id, *OPEN, now - DONE_SHOWN_S)).fetchall()
    return [_row(r) for r in rows]


def set_state(settings: Settings, oid: int, state: str, *, error: str = "", item: int = 0,
              now: float | None = None) -> OutboxMsg:
    """Move a row to ``state``. Atomic (one conditional UPDATE): of two writers racing for the same
    transition only one wins. Setting the state a row already has is a no-op that returns it.

    Raises NotFound (no row), Invalid (unknown state) or Conflict (transition not allowed)."""
    if state not in _FROM:
        raise Invalid("unknown state")
    now = time.time() if now is None else now
    err = " ".join(error.split())[:_ERROR_MAX]
    sets = ["state=?", "updated=?", "error=?"]
    args: list = [state, now, err]
    if state in SENT:
        sets.append("sent=?")
        args.append(now)
    if state == "failed":
        sets.append("tries=tries+1")
    if state == "done":
        sets.append("item=?")
        args.append(int(item))
    froms = _FROM[state]
    with closing(_con(settings)) as con, con:
        cur = con.execute(f"UPDATE chat_outbox SET {', '.join(sets)} WHERE id=? AND state IN ({_qs(froms)})",
                          (*args, int(oid), *froms))
        changed = cur.rowcount == 1
    m = get(settings, oid)
    if m is None:
        raise NotFound("no such message")
    if not changed and m.state != state:
        raise Conflict(f"cannot go from {m.state} to {state}")
    return m


def next_due(settings: Settings, agent_id: str) -> OutboxMsg | None:
    """The oldest row the deliverer still has to send (``saved`` or ``wait``)."""
    with closing(_con(settings)) as con:
        r = con.execute(f"SELECT {_COLS} FROM chat_outbox WHERE agent_id=? AND state IN ({_qs(DUE)})"
                        " ORDER BY id LIMIT 1", (agent_id, *DUE)).fetchone()
    return _row(r) if r else None


def due_agents(settings: Settings) -> list[str]:
    """Agents with at least one row to send."""
    with closing(_con(settings)) as con:
        rows = con.execute(f"SELECT DISTINCT agent_id FROM chat_outbox WHERE state IN ({_qs(DUE)})",
                           DUE).fetchall()
    return [r[0] for r in rows]


def sent_agents(settings: Settings, *, now: float | None = None) -> list[str]:
    """Agents with a row typed into the pane in the last ``MATCH_WINDOW_S`` but not matched yet:
    the chat feed keeps ingesting them even without a viewer, so the row can become ``done``."""
    now = time.time() if now is None else now
    with closing(_con(settings)) as con:
        rows = con.execute(f"SELECT DISTINCT agent_id FROM chat_outbox WHERE state IN ({_qs(SENT)}) AND"
                           " (sent >= ? OR (state='queued' AND sent >= ?))",
                           (*SENT, now - MATCH_WINDOW_S, now - QUEUED_WINDOW_S)).fetchall()
    return [r[0] for r in rows]


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _keys(text: str) -> set[str]:
    """Comparable forms of a message: whitespace-normalised raw text, and the cleaned/masked form the
    chat items store (``clean_text`` as ``pm.chat.items``), without paste wrapper tags."""
    raw = strip_wrappers(text or "")
    return {k for k in (_norm(text), _norm(raw), _norm(clean_text(raw, ITEM_TEXT_MAX))) if k}


def match(settings: Settings, agent_id: str, text: str, ts: float) -> OutboxMsg | None:
    """The oldest ``sending`` / ``inpane`` / ``queued`` row of ``agent_id`` with the same normalised text
    as a transcript user item at ``ts``, sent within ``MATCH_WINDOW_S`` before it (a ``queued`` row:
    ``QUEUED_WINDOW_S``, Claude takes it in only when its running tool call ends). Read only."""
    want = _keys(text)
    if not want:
        return None
    ts = ts or time.time()
    with closing(_con(settings)) as con:
        rows = con.execute(
            f"SELECT {_COLS} FROM chat_outbox WHERE agent_id=? AND state IN ({_qs(SENT)})"
            " AND (sent >= ? OR (state='queued' AND sent >= ?)) AND sent <= ? ORDER BY id",
            (agent_id, *SENT, ts - MATCH_WINDOW_S, ts - QUEUED_WINDOW_S, ts + MATCH_SLACK_S)).fetchall()
    for r in rows:
        m = _row(r)
        if _keys(m.text) & want:
            return m
    return None


# an upload path as pm's composer appends it (``pm.core.uploads``), "[Image #1]" / "[Pasted text #2 +9 lines]"
_UPLOAD_PATH = re.compile(r"(?:/[\w.-]+)*/uploads/[0-9a-f]{24}\.(?:png|jpe?g|gif|webp)", re.IGNORECASE)
_MARKER = re.compile(r"\[(?:Image|Pasted text)\s*#\d+[^\]]{0,24}\]", re.IGNORECASE)
_WS = re.compile(r"\s+")


def _loose(text: str) -> str:
    """Comparable form of a queued message: no paste tags, upload paths or image/paste markers, no
    whitespace at all (Claude's pane joins wrapped lines with spaces, even inside a path), no "…"."""
    t = _MARKER.sub("", _UPLOAD_PATH.sub("", strip_wrappers(text or "")))
    t = _WS.sub("", t)
    return _MARKER.sub("", _UPLOAD_PATH.sub("", t)).rstrip("…")  # again: a path the pane split


def _loose_eq(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= QUEUED_PREFIX_MIN and long_.startswith(short)


def match_queued(settings: Settings, agent_id: str, pane_text: str, now: float | None = None) -> OutboxMsg | None:
    """The oldest ``queued`` row (or ``inpane`` row sent in the last ``INPANE_QUEUED_S``) whose text is
    Claude's queued block ``pane_text`` (masked, wrapped lines joined): compared without whitespace,
    upload paths and image markers, equal or one a prefix of the other (>= ``QUEUED_PREFIX_MIN``
    chars). The row text is compared raw and masked. Read only."""
    pane = _loose(pane_text)
    if not pane:
        return None
    now = time.time() if now is None else now
    with closing(_con(settings)) as con:
        rows = con.execute(
            f"SELECT {_COLS} FROM chat_outbox WHERE agent_id=? AND (state='queued'"
            " OR (state='inpane' AND sent >= ?)) ORDER BY id", (agent_id, now - INPANE_QUEUED_S)).fetchall()
    for r in rows:
        m = _row(r)
        raw = strip_wrappers(m.text)
        if _loose_eq(pane, _loose(raw)) or _loose_eq(pane, _loose(clean_text(raw, ITEM_TEXT_MAX))):
            return m
    return None
