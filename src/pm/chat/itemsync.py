"""Ingest of the agent's store file into append-only chat items (``pm.chat.items``).

Blocking; ``pm.agents.chatfeed`` calls ``sync`` on an executor. Never the pane: only the store file
(``pm.chat.log.store_file``). Text is masked like ``pm.chat.log`` (``clean_text`` = ``render_pane``
+ clip); a tool keeps only its name, its one masked target line (``log.step_line``), ok/err and the
note "exit N" / "background" / "stopped" / "stopped · session ended" and a background task id; never
other inputs or result content (a task's output file is read only for its final exit-code line).

Per Claude entry: a real prompt is a ``user`` item (it closes an open turn with an ``end`` item;
a prompt Claude took in mid-turn, ``RawEntry.mid_turn``, joins the open turn and closes nothing);
each assistant text block is a ``text`` item (name = model id); each tool call a ``tool`` item
(status "run", parent = newest text item of the turn); a tool result sets ok/err + ended; an
assistant piece with stop reason "end_turn" writes the ``end`` item. ``sync(idle=True)`` also ends
an open turn whose last item is older than ``IDLE_END_S``. Other kinds have no tool ids or
results: tool uid ``<entry uid>:<idx>``, status "".

Background tasks (Claude): the launch result keeps the tool "run" with note "background" and its
task id in ``ref``; a task notification ends it ok/err, TaskStop / KillShell ends it "err" with the
note "stopped". Turn ends never finish a running tool. Every sync (also when the transcript is
unchanged) reconciles up to 10 still-running background tools from the task's output file
(``claude.task_exit``): "[exited with code N]" ends it ok (0) or err "exit N" at the file's mtime; no
such line and the agent gone (pid <= 1, as ``chat.log.store_file``) ends it err "stopped · session
ended" at the transcript's mtime; a live agent's task without the line keeps running. ``finish_run``
leaves a finished tool alone, so a notification and the output file never overwrite each other.

A new conversation is read from byte 0 (append-only kinds; row stores from their own first-read
window) in bounded chunks, ``MAX_LOOPS`` per call; the cursor continues on the next call. A
rewritten file (new inode / shorter) drops the conversation's items and re-reads it.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from pm.chat import items
from pm.chat import log as chatlog
from pm.chat import sources as chat_sources
from pm.chat.sources.claude import task_exit
from pm.chat.sources.common import Block, RawEntry
from pm.chat.text import clean_text
from pm.core import db
from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, ChatMsg
from pm.core.timefmt import duration

log = logging.getLogger("pm.chatitems")

MAX_LOOPS = chatlog.MAX_LOOPS
IDLE_END_S = 5.0
BG_RECONCILE = 10  # running background tools checked per sync
SESSION_ENDED = "stopped · session ended"
_SEEN: set[str] = set()  # agents whose legacy conversion ran in this process


def _clean(text: str) -> str:
    return clean_text(text, items.MAX_TEXT) if text else ""


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def end_note(replies: int, tools: int, dur: float | None) -> str:
    """"N replies · N tool calls · <dur>" (no duration when unknown)."""
    parts = [_plural(replies, "reply", "replies"), _plural(tools, "tool call", "tool calls")]
    if dur is not None:
        parts.append(duration(dur))
    return " · ".join(parts)


@dataclass
class _Cur:
    """Cursor of one (agent, store file): read position and the open turn ("" = none open)."""

    agent_id: str
    conv: str
    pos: int
    turn: str = ""
    last_text: int = 0


# --------------------------------------------------------------------------
# sync
# --------------------------------------------------------------------------


def sync(settings: Settings, agent: Agent, *, idle: bool = False) -> int:
    """Ingest new entries of the agent's store into items; the agent's max rev. Never raises."""
    try:
        return _sync(settings, agent, idle)
    except Exception as exc:  # noqa: BLE001 - never raise; type only, no content
        log.warning("chat items sync failed: %s", type(exc).__name__)
        return items.latest(settings, agent.id)[1]


def _sync(settings: Settings, agent: Agent, idle: bool) -> int:
    kind = agent.kind if isinstance(agent.kind, AgentKind) else AgentKind(agent.kind)
    path = chatlog.store_file(agent)
    conv = chatlog.conv_key(kind, path) if path is not None else ""
    if agent.id not in _SEEN:
        _convert(settings, agent.id, skip=conv)
        if len(_SEEN) > 4096:
            _SEEN.clear()
        _SEEN.add(agent.id)
    if path is None:
        return items.latest(settings, agent.id)[1]
    try:
        sig = chatlog.file_sig(kind, path)
    except OSError:
        return items.latest(settings, agent.id)[1]
    con = items.connect(settings)
    try:
        row = con.execute("SELECT conv, inode, size, mtime, turn, fmt FROM chat_item_ingest "
                          "WHERE agent_id=? AND path=?", (agent.id, str(path))).fetchone()
        unchanged = (row is not None and row[0] == conv and (row[1], row[2], row[3]) == sig
                     and row[5] >= items.FORMAT)
        if not unchanged or (idle and row[4]):
            with db.agent_lock(agent.id):
                con.execute("BEGIN IMMEDIATE")
                try:
                    _ingest(con, agent.id, kind, path, conv, sig, read=not unchanged, idle=idle)
                    con.commit()
                except BaseException:
                    con.rollback()
                    raise
        if kind == AgentKind.CLAUDE:
            _bg_reconcile(con, agent.id, conv, path, agent.pid <= 1)
        return items.max_rev(con, agent.id)
    finally:
        con.close()


def _start(kind: AgentKind) -> int:
    """First cursor of a conversation: byte 0 for append-only files, else the reader's first window."""
    return 0 if kind in chat_sources.BYTE_CURSOR else -1


def _ingest(con: sqlite3.Connection, agent_id: str, kind: AgentKind, path: Path, conv: str,
            sig: tuple[int, int, float], *, read: bool, idle: bool) -> None:
    row = con.execute("SELECT inode, size, pos, turn, last_text_seq, fmt FROM chat_item_ingest "
                      "WHERE agent_id=? AND path=?", (agent_id, str(path))).fetchone()
    cur = _Cur(agent_id, conv, _start(kind))
    if row is not None:
        cur.pos, cur.turn, cur.last_text = row[2], row[3], row[4]
    if (row is None or row[5] < items.FORMAT
            or (kind in chat_sources.BYTE_CURSOR and (row[0] != sig[0] or sig[1] < cur.pos))):
        # new conv (legacy items are replaced), rewritten file, or read by an older format: read it again
        items.drop_conv(con, agent_id, conv)
        cur = _Cur(agent_id, conv, _start(kind))
    size = sig[1]
    if read:
        more = False
        for _ in range(MAX_LOOPS):
            chunk = chat_sources.read_entries(kind, path, cur.pos)
            if chunk.reset:
                items.drop_conv(con, agent_id, conv)
                cur = _Cur(agent_id, conv, _start(kind))
                chunk = chat_sources.read_entries(kind, path, cur.pos)
            _apply(con, cur, chunk.entries)
            cur.pos, more = chunk.pos, chunk.more
            if not more:
                break
        size = -1 if more else size  # backlog left: the next sync continues
    if idle and cur.turn:
        _idle_end(con, cur)
    con.execute(
        "INSERT INTO chat_item_ingest (agent_id, path, kind, conv, inode, size, mtime, pos, turn, last_text_seq,"
        " updated, fmt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (agent_id, path) DO UPDATE SET "
        "kind=excluded.kind, conv=excluded.conv, inode=excluded.inode, size=excluded.size, mtime=excluded.mtime, "
        "pos=excluded.pos, turn=excluded.turn, last_text_seq=excluded.last_text_seq, updated=excluded.updated, "
        "fmt=excluded.fmt",
        (agent_id, str(path), kind.value, conv, sig[0], size, sig[2], cur.pos, cur.turn, cur.last_text,
         time.time(), items.FORMAT))
    items.cap(con, agent_id)


def _apply(con: sqlite3.Connection, cur: _Cur, entries: list[RawEntry]) -> None:
    aid, conv = cur.agent_id, cur.conv
    for e in entries:
        if e.role == "user" and e.mid_turn and cur.turn:
            # taken in while working: same turn, no end item; tools after it group under its bubble
            cur.last_text = items.add(con, aid, conv, e.uid, "user", turn=cur.turn, ts=e.ts, text=_clean(e.text))
        elif e.role == "user":
            _close(con, cur)
            items.add(con, aid, conv, e.uid, "user", turn=e.uid, ts=e.ts, text=_clean(e.text))
            cur.turn, cur.last_text = e.uid, 0
        elif e.role == "result":
            items.set_result(con, aid, conv, e.uid, "err" if e.text == "err" else "ok", e.ts, e.thinking)
        elif e.role == "bg":
            _bg_start(con, cur, e)
        elif e.role == "bgend":
            _bg_end(con, cur, e)
        elif e.role == "bgstop":
            _bg_stop(con, cur, e)
        elif e.role == "assistant":
            if not cur.turn:
                cur.turn, cur.last_text = e.uid, 0  # a turn without a prompt in the store
            _blocks(con, cur, e)
            if e.end_turn:
                _close(con, cur, e.ts)


def _bg_start(con: sqlite3.Connection, cur: _Cur, e: RawEntry) -> None:
    """A background launch: the tool keeps running (note "background") under its task id."""
    items.set_result(con, cur.agent_id, cur.conv, e.uid, "run", 0.0, "background")
    items.set_ref(con, cur.agent_id, cur.conv, e.uid, e.note)


def _bg_end(con: sqlite3.Connection, cur: _Cur, e: RawEntry) -> None:
    """A task notification: the running tool (by tool id, else task id) ends ok/err."""
    status = "err" if e.text == "err" else "ok"
    if e.uid and items.finish_run(con, cur.agent_id, cur.conv, e.uid, status, e.ts):
        return
    it = items.by_ref(con, cur.agent_id, cur.conv, e.note)
    if it is not None:
        items.finish_run(con, cur.agent_id, cur.conv, it.uid, status, e.ts)


def _bg_stop(con: sqlite3.Connection, cur: _Cur, e: RawEntry) -> None:
    """TaskStop / KillShell: the running background tool ends as "err" with the note "stopped"."""
    it = items.by_ref(con, cur.agent_id, cur.conv, e.note)
    if it is not None:
        items.finish_run(con, cur.agent_id, cur.conv, it.uid, "err", e.ts, "stopped")


def _bg_reconcile(con: sqlite3.Connection, agent_id: str, conv: str, transcript: Path, ended: bool) -> None:
    """Finish running background tools from their output file's exit line; with none and the agent
    ended (pid <= 1), as err "stopped · session ended" at the transcript's mtime. Files are read
    before the write lock; nothing to finish (the usual case while a task runs) writes nothing. A tool
    closed as session ended is set to its real outcome when its exit line turns up later."""
    todo: list[tuple[str, str, float, str]] = []
    for it in items.running_background(con, agent_id, conv, limit=BG_RECONCILE):
        ref = _ref(con, it.seq)
        done = task_exit(transcript, ref) if ref else None
        if done is not None:
            code, mtime = done
            todo.append((it.uid, "ok" if code == 0 else "err", max(mtime, it.ts), "" if code == 0 else f"exit {code}"))
        elif ended:
            todo.append((it.uid, "err", max(_mtime(transcript), it.ts), SESSION_ENDED))
    fix: list[tuple[str, str, float, str]] = []  # closed as session ended, but the outcome turned up later
    for uid, ref, ts in (items.stopped_background(con, agent_id, conv, SESSION_ENDED, BG_RECONCILE) if ended else []):
        done = task_exit(transcript, ref)
        if done is not None:
            fix.append((uid, "ok" if done[0] == 0 else "err", max(done[1], ts),
                        "background" if done[0] == 0 else f"exit {done[0]}"))
    if not todo and not fix:
        return
    with db.agent_lock(agent_id):
        con.execute("BEGIN IMMEDIATE")
        try:
            for uid, status, end, note in todo:
                items.finish_run(con, agent_id, conv, uid, status, end, note)  # no-op once finished
            for uid, status, end, note in fix:
                items.set_result(con, agent_id, conv, uid, status, end, note)
            con.commit()
        except BaseException:
            con.rollback()
            raise


def _ref(con: sqlite3.Connection, seq: int) -> str:
    """A tool item's background task id (``ChatItem`` does not carry ``ref``)."""
    row = con.execute("SELECT ref FROM chat_items WHERE seq=?", (seq,)).fetchone()
    return str(row[0] or "") if row else ""


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return time.time()


def _fallback_blocks(e: RawEntry) -> tuple[Block, ...]:
    """Kinds without ``blocks``: the piece's text, then one tool per call step (no ids)."""
    out = [Block("text", text=e.text)] if e.text else []
    out += [Block("tool", name=name, line=line) for kind, name, line in e.steps if kind == "tool"]
    return tuple(out)


def _blocks(con: sqlite3.Connection, cur: _Cur, e: RawEntry) -> None:
    for idx, b in enumerate(e.blocks or _fallback_blocks(e)):
        if b.kind == "text":
            text = _clean(b.text)
            if text:
                cur.last_text = items.add(con, cur.agent_id, cur.conv, f"{e.uid}:{idx}", "text", turn=cur.turn,
                                          ts=e.ts, text=text, name=e.model)
        elif b.kind == "tool":
            items.add(con, cur.agent_id, cur.conv, b.id or f"{e.uid}:{idx}", "tool", turn=cur.turn, ts=e.ts,
                      name=" ".join(b.name.split()), line=chatlog.step_line(b.line),
                      status="run" if b.id else "", note="background" if b.background else "",
                      parent=cur.last_text)


def _close(con: sqlite3.Connection, cur: _Cur, end_ts: float = 0.0) -> None:
    """End the open turn: an ``end`` item when it has a reply or a tool call."""
    if not cur.turn:
        return
    replies, tools, start, last = items.turn_stats(con, cur.agent_id, cur.conv, cur.turn)
    if replies or tools:
        end = end_ts or last
        items.add(con, cur.agent_id, cur.conv, f"{cur.turn}:end", "end", turn=cur.turn, ts=end,
                  note=end_note(replies, tools, end - start if start and end else None))
    cur.turn, cur.last_text = "", 0


def _idle_end(con: sqlite3.Connection, cur: _Cur) -> None:
    replies, tools, _start_ts, last = items.turn_stats(con, cur.agent_id, cur.conv, cur.turn)
    if (replies or tools) and time.time() - last > IDLE_END_S:
        _close(con, cur, last)


# --------------------------------------------------------------------------
# legacy conversion: conversations only in chat_messages (pm.chat.log)
# --------------------------------------------------------------------------


def convert_legacy(settings: Settings, agent_id: str) -> int:
    """Write items for the agent's ``chat_messages`` conversations that have no items and no item
    cursor yet, once per (agent, conv). Number of items written; never raises."""
    try:
        return _convert(settings, agent_id, skip="")
    except Exception as exc:  # noqa: BLE001 - never raise; type only
        log.warning("chat items legacy conversion failed: %s", type(exc).__name__)
        return 0


def _todo(con: sqlite3.Connection, agent_id: str, skip: str) -> list[str]:
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_messages'").fetchone() is None:
        return []
    convs = [r[0] for r in con.execute("SELECT DISTINCT conv FROM chat_messages WHERE agent_id=?", (agent_id,))]
    done = {r[0] for r in con.execute("SELECT conv FROM chat_item_ingest WHERE agent_id=?", (agent_id,))}
    done |= {r[0] for r in con.execute("SELECT DISTINCT conv FROM chat_items WHERE agent_id=?", (agent_id,))}
    return [c for c in convs if c and c != skip and c not in done]


def _legacy_rows(settings: Settings, agent_id: str, convs: set[str]) -> dict[str, list[ChatMsg]]:
    out: dict[str, list[ChatMsg]] = {c: [] for c in convs}
    before: int | None = None
    while True:
        page = chatlog.messages(settings, agent_id, before_seq=before, limit=chatlog.MAX_LIMIT)
        for m in page:
            if m.conv in out:
                out[m.conv].append(m)
        if len(page) < chatlog.MAX_LIMIT:
            break
        before = page[-1].seq
    for rows in out.values():
        rows.sort(key=lambda m: m.seq)
    return out


def _convert(settings: Settings, agent_id: str, skip: str) -> int:
    con = items.connect(settings)
    try:
        if not _todo(con, agent_id, skip):
            return 0
        n = 0
        with db.agent_lock(agent_id):
            con.execute("BEGIN IMMEDIATE")
            try:
                todo = _todo(con, agent_id, skip)  # again under the lock
                rows = _legacy_rows(settings, agent_id, set(todo))
                for conv in todo:
                    n += _convert_conv(con, agent_id, conv, rows[conv])
                    con.execute(
                        "INSERT OR REPLACE INTO chat_item_ingest (agent_id, path, kind, conv, legacy_done, updated) "
                        "VALUES (?,?,?,?,1,?)", (agent_id, f"legacy:{conv}"[:400], "legacy", conv, time.time()))
                items.cap(con, agent_id)
                con.commit()
            except BaseException:
                con.rollback()
                raise
        return n
    finally:
        con.close()


def _convert_conv(con: sqlite3.Connection, agent_id: str, conv: str, rows: list[ChatMsg]) -> int:
    """user row -> user item; assistant row -> one text item + one ok tool per stored step + end."""
    n = 0
    turn = ""
    for m in rows:
        if m.role == "user":
            items.add(con, agent_id, conv, m.uid, "user", turn=m.uid, ts=m.ts, text=_clean(m.text))
            turn = m.uid
            n += 1
            continue
        turn = m.uid[:-2] if m.uid.endswith(":a") else m.uid  # "<user uid>:a" (or "<first piece>:a")
        parent = 0
        text = _clean(m.text)
        if text:
            parent = items.add(con, agent_id, conv, f"{m.uid}:0", "text", turn=turn, ts=m.ts, text=text)
            n += 1
        tools = [s for s in m.steps if s.kind == "tool"]
        for i, s in enumerate(tools):
            items.add(con, agent_id, conv, f"{m.uid}:t{i}", "tool", turn=turn, ts=m.ts, name=s.name,
                      line=chatlog.step_line(s.line), status="ok", parent=parent)
        n += len(tools)
        if text or tools:
            items.add(con, agent_id, conv, f"{turn}:end", "end", turn=turn, ts=m.ts,
                      note=end_note(1 if text else 0, max(len(tools), m.tool_total), None))
            n += 1
        turn = ""
    return n
