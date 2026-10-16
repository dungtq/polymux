"""Where a new agent came from: "Fork from" / "Continued from" links.

``fork.fork_session`` and the /continue route record ``(source agent id, how, new pane, kind)`` in
``pm.db`` (table ``agent_lineage``) when they start a tmux window. The new agent only gets its
permanent id once it is detected, so ``of`` matches it later, deterministically: same kind, same
pane, first seen shortly after the row was written, and the EARLIEST record in that pane at or
after that moment (a later /clear in the pane starts another conversation and never claims it).
A match is pinned (``resolved_id``) and stays stable.

Pure sqlite; no tmux, no pane text. Every failure degrades to "no origin link".
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass

from pm.agents import registry
from pm.core.config import Settings

AGENT_ID_RE = re.compile(r"^[a-z]+(?:-[a-z]+)*-[0-9a-z]+$")  # permanent agent ids (claude-3fa9c1); the one copy in pm.agents
_HOWS = {"fork": "Fork from", "continue": "Continued from"}
_LEAD_S = 2.0  # the new agent's record may predate our row by a moment
_WINDOW_S = 300.0  # how long after the launch the new agent may first be seen

CONTINUE_PREAMBLE = ("Here is the previous work from {src_id}. Do not continue it yet: "
                     "first ask me what to do next.\n\n---\n")

_SCHEMA = ("CREATE TABLE IF NOT EXISTS agent_lineage (src_id TEXT NOT NULL, how TEXT NOT NULL, "
           "new_pane TEXT NOT NULL, kind TEXT NOT NULL, created REAL NOT NULL, resolved_id TEXT)")


@dataclass(frozen=True)
class Origin:
    agent_id: str
    how: str  # "fork" | "continue"

    @property
    def label(self) -> str:
        return _HOWS.get(self.how, "From")


def continue_prompt(src_id: str, text: str) -> str:
    """First prompt of a continued agent: the preamble naming the source, then the message."""
    return CONTINUE_PREAMBLE.format(src_id=src_id) + text


def _connect(settings: Settings) -> sqlite3.Connection:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(settings.db_file, timeout=5)
    con.execute(_SCHEMA)
    return con


def record(settings: Settings, src_id: str, how: str, pane_id: str, kind: str,
           now: float | None = None) -> None:
    """Remember that the agent about to appear in ``pane_id`` came from ``src_id``. Best effort."""
    if how not in _HOWS or not pane_id or not src_id or not kind:
        return
    ts = time.time() if now is None else now
    days = int(settings.agent_retention_days or 0)
    try:
        con = _connect(settings)
        try:
            con.execute("INSERT INTO agent_lineage (src_id, how, new_pane, kind, created) "
                        "VALUES (?,?,?,?,?)", (src_id, how, pane_id, kind, ts))
            if days > 0:
                con.execute("DELETE FROM agent_lineage WHERE created < ?", (ts - days * 86400,))
            con.commit()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return


def _origin(src_id: str, how: str) -> Origin | None:
    return Origin(src_id, how) if AGENT_ID_RE.fullmatch(src_id or "") and how in _HOWS else None


def of(settings: Settings, agent_id: str) -> Origin | None:
    """The agent this one was forked / continued from, or None."""
    try:
        con = _connect(settings)
        try:
            row = con.execute("SELECT src_id, how FROM agent_lineage WHERE resolved_id=? "
                              "ORDER BY created DESC LIMIT 1", (agent_id,)).fetchone()
            if row:
                return _origin(row[0], row[1])
            rec = registry.get(settings, agent_id)
            if rec is None or not rec.last_pane:
                return None
            rows = con.execute(
                "SELECT rowid, src_id, how, created FROM agent_lineage WHERE resolved_id IS NULL "
                "AND new_pane=? AND kind=? AND src_id != ? AND created BETWEEN ? AND ? "
                "ORDER BY created DESC", (rec.last_pane, rec.kind, agent_id,
                                          rec.first_seen - _WINDOW_S, rec.first_seen + _LEAD_S)).fetchall()
            for rowid, src_id, how, created in rows:
                first = registry.first_in_pane(settings, rec.kind, rec.last_pane, created - _LEAD_S)
                if first is None or first.id != agent_id:
                    continue
                con.execute("UPDATE agent_lineage SET resolved_id=? WHERE rowid=? "
                            "AND resolved_id IS NULL", (agent_id, rowid))
                con.commit()
                return _origin(src_id, how)
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return None
    return None
