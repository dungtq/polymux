"""Permanent agent ids.

The URL id is minted from the agent's own identity (Claude ``sessionId``, the Devin / Codex /
Cursor session file), not from the tmux pane, and persisted in ``pm.db`` (table
``agent_records``). The pane is only an attribute of the record, so a restart of pm, a restart of
tmux or a different program in the same pane never changes what ``/a/<id>`` means.

Pure sqlite + hashing; no tmux, no pane text. Every failure degrades to "ids stay as detected".
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from pm.agents import sorting
from pm.core import accounts, db
from pm.core.config import ACCOUNT_KINDS, Settings
from pm.core.errors import Invalid
from pm.core.models import Agent, AgentKind

log = logging.getLogger(__name__)

LEGACY_RE = re.compile(r"^([a-z]+(?:-[a-z]+)*)-(\d+)$")  # pane based: claude-140, cursor-agent-7
TOUCH_S = 30.0  # last_seen is rewritten at most this often when nothing else changed
DEFAULT_RETENTION_DAYS = 90

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS agent_records (id TEXT PRIMARY KEY, kind TEXT NOT NULL, ident TEXT, "
    "pid INTEGER, first_seen REAL NOT NULL, last_seen REAL NOT NULL, last_pane TEXT, project TEXT, "
    "cwd TEXT, label TEXT, transcript TEXT, ended_at REAL)",
    "CREATE INDEX IF NOT EXISTS agent_records_ident ON agent_records (kind, ident)",
    "CREATE INDEX IF NOT EXISTS agent_records_pane ON agent_records (kind, last_pane, last_seen)",
    "CREATE TABLE IF NOT EXISTS agent_account_hint (kind TEXT NOT NULL, pane TEXT NOT NULL, "
    "account TEXT NOT NULL, created REAL NOT NULL)",
)
ACCOUNT_HINT_S = 300.0  # how long after a start the new agent may first be seen
_pruned: set[str] = set()
_lock = threading.Lock()


@dataclass(frozen=True)
class Record:
    id: str
    kind: str
    ident: str | None
    pid: int
    first_seen: float
    last_seen: float
    last_pane: str
    project: str | None
    cwd: str
    label: str
    transcript: str
    ended_at: float | None
    account: str = ""  # named login account the agent was started with ('' = the default account)

    @property
    def ended(self) -> bool:
        return self.ended_at is not None

    @property
    def resume_id(self) -> str | None:
        """Validated id to resume this session with (per kind), or None (no Resume button)."""
        return resume_ident(self.kind, self.ident)


_COLS = ("id, kind, ident, pid, first_seen, last_seen, last_pane, project, cwd, label, transcript, "
         "ended_at, account")


def _row(r: tuple) -> Record:
    return Record(r[0], r[1], r[2], r[3] or 0, r[4], r[5], r[6] or "", r[7], r[8] or "", r[9] or "",
                  r[10] or "", r[11], r[12] or "")


def _migrate(con: sqlite3.Connection) -> None:
    """Add ``agent_records.account`` to a table created before it."""
    if "account" not in {r[1] for r in con.execute("PRAGMA table_info(agent_records)")}:
        try:
            con.execute("ALTER TABLE agent_records ADD COLUMN account TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # another process added it first


def expect_account(settings: Settings, kind: str, pane_id: str, account: str) -> None:
    """Remember that the agent about to appear in ``pane_id`` runs under the named ``account``; the record
    minted for it takes it on first sight. Nothing to store for the default account. Best effort."""
    if not account or not pane_id:
        return
    try:
        con = _connect(settings)
        try:
            con.execute("INSERT INTO agent_account_hint VALUES (?,?,?,?)", (kind, pane_id, account, time.time()))
            con.execute("DELETE FROM agent_account_hint WHERE created < ?", (time.time() - 86400,))
            con.commit()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return


def account_of(settings: Settings, agent_id: str) -> str:
    """Account stored for an agent id ('' = default or unknown)."""
    rec = get(settings, agent_id)
    return rec.account if rec else ""


def config_dir_of(settings: Settings, agent_id: str, kind) -> Path | None:
    """Login folder of the agent's stored named account (claude / codex), None for the CLI's own folder, an
    unknown account or another kind: pass it as ``config_dir`` to the ``pm.chat`` session readers."""
    k = getattr(kind, "value", kind)
    if k not in ACCOUNT_KINDS:
        return None
    acc = account_of(settings, agent_id)
    if not acc:
        return None
    try:
        return accounts.config_dir(settings, k, acc)
    except Invalid:
        return None


def config_kw(settings: Settings, agent_id: str, kind) -> dict:
    """``{"config_dir": folder}`` for an agent under a named account, ``{}`` otherwise: splat it into a call to
    the ``pm.chat`` readers (``chat_view``, ``store_file``, ``stats_for``, ...) so the default call stays as it was."""
    d = config_dir_of(settings, agent_id, kind)
    return {"config_dir": d} if d else {}


def _hint(con: sqlite3.Connection, a: Agent, now: float) -> str:
    r = con.execute("SELECT account FROM agent_account_hint WHERE kind=? AND pane=? AND created >= ? "
                    "ORDER BY created DESC LIMIT 1", (a.kind.value, a.pane_id, now - ACCOUNT_HINT_S)).fetchone()
    return r[0] if r else ""


def _connect(settings: Settings) -> sqlite3.Connection:
    """pm.db with the schema (run once per process and DB file, ``pm.core.db.connect``)."""
    return db.connect(settings, _SCHEMA, _migrate)


def mint_id(kind: str, ident: str | None) -> str:
    """``<kind>-<6 hex>``: derived from the session identity when there is one, else random.
    Always contains a letter after the dash, so it can never be mistaken for a pane id."""
    if ident:
        h = hashlib.sha256(f"{kind}\0{ident}".encode()).hexdigest()[:6]
    else:
        h = secrets.token_hex(3)
    if h.isdigit():
        h = "a" + h[1:]
    return f"{kind}-{h}"


def prune(settings: Settings, now: float | None = None) -> int:
    """Delete ended records older than ``agent_retention_days``. Returns the number removed."""
    days = int(getattr(settings, "agent_retention_days", DEFAULT_RETENTION_DAYS) or 0)
    if days <= 0:
        return 0
    cutoff = (time.time() if now is None else now) - days * 86400
    con = _connect(settings)
    try:
        n = con.execute("DELETE FROM agent_records WHERE ended_at IS NOT NULL AND ended_at < ?",
                        (cutoff,)).rowcount
        con.commit()
        return n
    finally:
        con.close()


def _find(con: sqlite3.Connection, a: Agent, taken: set[str]) -> Record | None:
    """The record this live agent continues, or None (mint a new one)."""
    kind = a.kind.value
    by_pid = None
    if a.pid > 1:
        r = con.execute(f"SELECT {_COLS} FROM agent_records WHERE kind=? AND last_pane=? AND pid=? "
                        "ORDER BY ended_at IS NULL DESC, last_seen DESC LIMIT 1",
                        (kind, a.pane_id, a.pid)).fetchone()  # same process: also after a missed poll
        by_pid = _row(r) if r and r[0] not in taken else None
    by_ident = None
    if a.ident:
        r = con.execute(f"SELECT {_COLS} FROM agent_records WHERE kind=? AND ident=? "
                        "ORDER BY ended_at IS NULL DESC, last_seen DESC LIMIT 1", (kind, a.ident)).fetchone()
        by_ident = _row(r) if r and r[0] not in taken else None
    if a.kind is AgentKind.CLAUDE:
        # the session id is authoritative (/clear starts a new conversation = new id)
        if by_ident:
            return by_ident
        if by_pid and (by_pid.ident is None or not a.ident):
            return by_pid  # first sight had no session json yet (claim it), or it is unreadable now
        return None
    # other kinds: locating the session file is heuristic, so the process keeps its record
    return by_pid or by_ident


def assign(settings: Settings, found: list[Agent], *, complete: bool = True,
           now: float | None = None) -> list[Agent]:
    """Give each detected agent its permanent id and persist the records.

    ``complete`` is False when detection failed this round: nothing is marked ended then.
    On any database error the agents are returned unchanged (pane based ids)."""
    now = time.time() if now is None else now
    try:
        with _lock:
            return _assign(settings, found, complete, now)
    except (sqlite3.Error, OSError) as e:
        log.warning("agent registry unavailable: %s", type(e).__name__)
        return found


def _assign(settings: Settings, found: list[Agent], complete: bool, now: float) -> list[Agent]:
    key = str(settings.db_file)
    con = _connect(settings)
    try:
        if key not in _pruned:
            _pruned.add(key)
            try:
                prune(settings, now)
            except sqlite3.Error:
                pass
        out: list[Agent] = []
        taken: set[str] = set()
        for a in found:
            rec = _find(con, a, taken)
            if rec is None:
                aid = mint_id(a.kind.value, a.ident)
                if aid in taken or con.execute("SELECT 1 FROM agent_records WHERE id=?", (aid,)).fetchone():
                    aid = mint_id(a.kind.value, None)
                con.execute(f"INSERT INTO agent_records ({_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,?)",
                            (aid, a.kind.value, a.ident, a.pid, now, now, a.pane_id, a.project,
                             a.cwd, a.label, a.transcript, _hint(con, a, now)))
            else:
                aid = rec.id
                ident = a.ident or rec.ident
                changed = (rec.ended or rec.ident != ident or rec.last_pane != a.pane_id
                           or rec.pid != a.pid or rec.project != a.project or rec.cwd != a.cwd
                           or rec.label != a.label or (a.transcript and rec.transcript != a.transcript)
                           or now - rec.last_seen >= TOUCH_S)
                if changed:
                    con.execute("UPDATE agent_records SET ident=?, pid=?, last_seen=?, last_pane=?, "
                                "project=?, cwd=?, label=?, transcript=?, ended_at=NULL WHERE id=?",
                                (ident, a.pid, now, a.pane_id, a.project, a.cwd, a.label,
                                 a.transcript or rec.transcript, aid))
            taken.add(aid)
            out.append(replace(a, id=aid))
        if complete:
            marks = ",".join("?" * len(taken))
            con.execute("UPDATE agent_records SET ended_at=? WHERE ended_at IS NULL"
                        + (f" AND id NOT IN ({marks})" if taken else ""), (now, *sorted(taken)))
        con.commit()
        return out
    finally:
        con.close()


def get(settings: Settings, agent_id: str) -> Record | None:
    try:
        con = _connect(settings)
        try:
            r = con.execute(f"SELECT {_COLS} FROM agent_records WHERE id=?", (agent_id,)).fetchone()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return None
    return _row(r) if r else None


_IN_CHUNK = 500  # ids per ``IN (...)`` (well under SQLite's bound-parameter limit)


def get_many(settings: Settings, agent_ids: Iterable[str]) -> dict[str, Record]:
    """{id: Record} for the known ids among ``agent_ids``, one connection for all; {} on a DB error."""
    ids = sorted(set(agent_ids))
    out: dict[str, Record] = {}
    if not ids:
        return out
    try:
        con = _connect(settings)
        try:
            for i in range(0, len(ids), _IN_CHUNK):
                part = ids[i:i + _IN_CHUNK]
                marks = ",".join("?" * len(part))
                for r in con.execute(f"SELECT {_COLS} FROM agent_records WHERE id IN ({marks})", part):
                    out[r[0]] = _row(r)
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return {}
    return out


def first_in_pane(settings: Settings, kind: str, pane: str, since: float) -> Record | None:
    """The earliest record of ``kind`` whose pane is ``pane`` and first seen at or after ``since``."""
    try:
        con = _connect(settings)
        try:
            r = con.execute(f"SELECT {_COLS} FROM agent_records WHERE kind=? AND last_pane=? "
                            "AND first_seen >= ? ORDER BY first_seen ASC, id ASC LIMIT 1",
                            (kind, pane, since)).fetchone()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return None
    return _row(r) if r else None


def by_legacy(settings: Settings, agent_id: str) -> Record | None:
    """Newest record that used pane ``%n`` for kind ``kind`` (old ``claude-140`` bookmarks)."""
    m = LEGACY_RE.fullmatch(agent_id)
    if not m:
        return None
    try:
        con = _connect(settings)
        try:
            r = con.execute(f"SELECT {_COLS} FROM agent_records WHERE kind=? AND last_pane=? "
                            "ORDER BY last_seen DESC LIMIT 1", (m.group(1), f"%{m.group(2)}")).fetchone()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return None
    return _row(r) if r else None


@dataclass(frozen=True)
class Resolved:
    """Outcome of looking an ``/a/<id>`` up: exactly one of agent / record / redirect is set."""

    agent: Agent | None = None  # running now
    ended: Record | None = None  # known, not running
    redirect: str | None = None  # canonical id to use instead (legacy pane id)
    now_in_pane: Agent | None = None  # ended record: the agent that reuses its pane


def resolve(settings: Settings, snap, agent_id: str) -> Resolved | None:
    a = snap.agent(agent_id)
    if a is not None:
        return Resolved(agent=a)
    rec = get(settings, agent_id)
    if rec is not None:
        other = next((x for x in snap.agents if x.pane_id == rec.last_pane), None) if rec.last_pane else None
        return Resolved(ended=rec, now_in_pane=other)
    m = LEGACY_RE.fullmatch(agent_id)
    if m:
        legacy = by_legacy(settings, agent_id)
        if legacy is not None:
            return Resolved(redirect=legacy.id)
        other = next((x for x in snap.agents if x.pane_id == f"%{m.group(2)}"), None)
        if other is not None:
            return Resolved(redirect=other.id)
    return None


def ended_agent(rec: Record) -> Agent:
    """Read-only stand-in for chat rendering (pid 0: no live process is ever consulted)."""
    from pm.core.models import AgentState

    try:
        kind = AgentKind(rec.kind)
    except ValueError:
        kind = AgentKind.OTHER
    return Agent(id=rec.id, kind=kind, pane_id=rec.last_pane or "%0", session_id="", pid=0,
                 cwd=rec.cwd, project=rec.project, state=AgentState.UNKNOWN,
                 state_since=rec.ended_at or rec.last_seen, confidence=0.0,
                 session_uuid=rec.ident if kind is AgentKind.CLAUDE else None,
                 label=rec.label, last_active=rec.last_seen, ident=rec.ident,
                 transcript=rec.transcript)


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


_CODEX_RE = re.compile(r"^(?:rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-)?"
                       r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
_DEVIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,79}$")  # session name, e.g. married-scribe


def resume_ident(kind: str, ident: str | None) -> str | None:
    """The strictly validated id ``<cli> resume`` takes for this kind, or None. Never starts
    with ``-`` (so it can not be read as an option)."""
    ident = ident or ""
    if kind in ("claude", "cursor-agent"):  # Claude sessionId / Cursor chat dir name: a UUID
        return ident if UUID_RE.fullmatch(ident) else None
    if kind == "codex":  # rollout file stem ``rollout-<time>-<uuid>`` or the bare uuid
        m = _CODEX_RE.fullmatch(ident)
        return m.group(1) if m else None
    if kind == "devin":  # session name = transcript file stem
        return ident if _DEVIN_RE.fullmatch(ident) else None
    return None


def mark_ended(settings: Settings, agent_id: str, ident: str | None = None,
               now: float | None = None) -> bool:
    """Record an explicit exit: ``ended_at`` now and keep the session id (``ident``) so the
    session can be resumed. A later sighting of the same session clears it again (_assign)."""
    now = time.time() if now is None else now
    try:
        with _lock:
            con = _connect(settings)
            try:
                n = con.execute("UPDATE agent_records SET ended_at=?, ident=COALESCE(ident, ?) "
                                "WHERE id=?", (now, ident, agent_id)).rowcount
                con.commit()
                return bool(n)
            finally:
                con.close()
    except (sqlite3.Error, OSError):
        return False


def import_past(settings: Settings, rows, *, project: str | None = None) -> int:
    """Add past sessions found on disk (``pm.chat.sources.past.PastSession``: kind, ident, path,
    cwd, mtime) as ended records: pid 0, no pane, ended_at = last_seen = the file mtime. Only
    resumable idents; a (kind, ident) pm already knows (live or ended) is left alone, so a session
    seen live keeps its id. Returns the number added (0: nothing new); -1 on a database error."""
    rows = [r for r in rows if resume_ident(r.kind, r.ident)]
    if not rows:
        return 0
    try:
        with _lock:
            con = _connect(settings)
            try:
                n = 0
                for r in rows:
                    alias = resume_ident(r.kind, r.ident)
                    if con.execute("SELECT 1 FROM agent_records WHERE kind=? AND ident IN (?, ?) LIMIT 1",
                                   (r.kind, r.ident, alias)).fetchone():
                        continue
                    aid = mint_id(r.kind, r.ident)
                    if con.execute("SELECT 1 FROM agent_records WHERE id=?", (aid,)).fetchone():
                        aid = mint_id(r.kind, None)
                    con.execute(f"INSERT INTO agent_records ({_COLS}) VALUES (?,?,?,0,?,?,'',?,?,'',?,?,'')",
                                (aid, r.kind, r.ident, r.mtime, r.mtime, project, r.cwd, str(r.path), r.mtime))
                    n += 1
                con.commit()
                return n
            finally:
                con.close()
    except (sqlite3.Error, OSError) as e:
        log.warning("past session import failed: %s", type(e).__name__)
        return -1


def with_transcripts(settings: Settings, since: float) -> list[Record]:
    """Records (live and ended) with a session file that was active since `since`."""
    try:
        con = _connect(settings)
        try:
            rows = con.execute(
                f"SELECT {_COLS} FROM agent_records WHERE transcript != '' AND transcript IS NOT NULL "
                "AND last_seen >= ? ORDER BY last_seen DESC LIMIT 500", (since,)).fetchall()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return []
    return [_row(r) for r in rows]


def of_kind_since(settings: Settings, kind: str, since: float) -> list[Record]:
    """Records (live and ended, any project) of ``kind`` whose session was still going at ``since`` or later."""
    try:
        con = _connect(settings)
        try:
            rows = con.execute(f"SELECT {_COLS} FROM agent_records WHERE kind=? AND COALESCE(ended_at, last_seen) >= ? "
                               "LIMIT 500", (kind, since)).fetchall()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return []
    return [_row(r) for r in rows]


def ended_sessions(settings: Settings, *, project: str | None = None, live_idents=(),
                   limit: int = 8, q: str = "", model_of=None) -> tuple[list[Record], int]:
    """Ended sessions of every kind, newest first: (rows, total). Claude rows need a UUID session
    id (they are resumable); other kinds may have no ident and are deduped by agent id.
    `q` keeps only rows whose sorting.agent_q text contains it, before the `limit` cut, so total counts
    the matches (`model_of(record)` gives the model name)."""
    try:
        con = _connect(settings)
        try:
            sql = (f"SELECT {_COLS} FROM agent_records WHERE ended_at IS NOT NULL "
                   + ("AND project=? " if project else "")
                   + " ORDER BY ended_at DESC LIMIT 200")
            rows = con.execute(sql, (project,) if project else ()).fetchall()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return [], 0
    live = set(live_idents)
    needle = (q or "").strip().lower()
    seen: set[tuple[str, str]] = set()
    out: list[Record] = []
    for r in map(_row, rows):
        if r.kind == "claude" and not UUID_RE.fullmatch(r.ident or ""):
            continue
        key = (r.kind, r.ident or r.id)
        if (r.ident and r.ident in live) or key in seen:
            continue
        seen.add(key)
        out.append(r)
    if needle:
        out = [r for r in out if needle in sorting.agent_q(r, model_of(r) if model_of else "")]
    return out[:limit], len(out)


def ended_history(settings: Settings, *, project: str | None, cwd: str, exclude_id: str = "",
                  limit: int = 10) -> tuple[list[Record], int]:
    """Ended agents of the same project or working directory, newest first: (rows, total)."""
    if not project and not cwd:
        return [], 0
    try:
        con = _connect(settings)
        try:
            rows = con.execute(
                f"SELECT {_COLS} FROM agent_records WHERE ended_at IS NOT NULL AND id<>? "
                "AND ((? IS NOT NULL AND project=?) OR (cwd<>'' AND cwd=?)) "
                "ORDER BY ended_at DESC LIMIT 200", (exclude_id, project, project, cwd)).fetchall()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return [], 0
    out = list(map(_row, rows))
    return out[:limit], len(out)
