"""Readers for the on-disk session stores of the coding agents.

Two readers per kind, both bounded and defensive (callers wrap in try):

* ``read_latest`` (non-Claude; Claude lives in ``pm.chat.live``): tail-bounded (<= 256 KB), the
  latest assistant TEXT message only (no tool calls, results or thinking).
* ``read_entries`` (every kind, for ``pm.chat.log``): incremental from a cursor, first read
  bounded (last 4 MB / last 2000 rows). Real user prompts and assistant pieces: text,
  thinking (when the store keeps it in clear), tool-call NAMES and per-block ``steps`` (one
  line each: first line of a text/thinking block, or a tool call's target such as the command
  or file path, see ``common.tool_target``). Other tool inputs and all tool results are never
  returned.

Locating the file for a live process uses pid/cwd/start time, never the content of
unrelated sessions. Credential files are never touched. All text is unmasked: callers mask.
"""

from __future__ import annotations

import time
from pathlib import Path

from pm.core.models import AgentKind

from . import antigravity, claude, codex, cursor, devin
from .common import (  # noqa: F401
    TAIL_BYTES,
    Chunk,
    Latest,
    RawEntry,
    mtime,
    parse_ts,
    read_tail_lines,
)

PROC = Path("/proc")


def session_file(
    kind: AgentKind | str,
    *,
    pid: int,
    cwd: str,
    age_s: float = 0.0,
    home: Path | None = None,
    proc_root: Path = PROC,
    locks: dict[int, list[str]] | None = None,
    config_dir: Path | None = None,
) -> Path | None:
    """The store file for non-Claude kinds (stat/dir-list only). Claude is resolved by its caller.
    ``config_dir``: a named account's folder replacing ``~/.codex`` (codex only)."""
    home = home or Path.home()
    kind = AgentKind(kind) if not isinstance(kind, AgentKind) else kind
    if kind is AgentKind.DEVIN:
        return devin.locate(pid, home, proc_root, locks)
    if kind is AgentKind.CODEX:
        return codex.locate(pid, cwd, (time.time() - age_s) if age_s > 0 else 0.0, home,
                            config_dir)
    if kind is AgentKind.CURSOR:
        return cursor.locate(cwd, home, pid, proc_root)
    if kind is AgentKind.ANTIGRAVITY:
        return antigravity.locate(pid, home, proc_root)
    return None


def read_latest(kind: AgentKind | str, path: Path) -> Latest | None:
    kind = AgentKind(kind) if not isinstance(kind, AgentKind) else kind
    if kind is AgentKind.DEVIN:
        return devin.read_latest(path)
    if kind is AgentKind.CODEX:
        return codex.read_latest(path)
    if kind is AgentKind.CURSOR:
        return cursor.read_latest(path)
    if kind is AgentKind.ANTIGRAVITY:
        return antigravity.read_latest(path)
    return None


def read_entries(kind: AgentKind | str, path: Path, pos: int = -1) -> Chunk:
    """New history entries of ``path`` after cursor ``pos`` (-1 = first read, bounded)."""
    kind = AgentKind(kind) if not isinstance(kind, AgentKind) else kind
    reader = {AgentKind.CLAUDE: claude.read_entries, AgentKind.DEVIN: devin.read_entries,
              AgentKind.CODEX: codex.read_entries, AgentKind.CURSOR: cursor.read_entries,
              AgentKind.ANTIGRAVITY: antigravity.read_entries}.get(kind)
    return reader(path, pos) if reader else Chunk([], pos)


# byte-offset cursors (append-only jsonl): a shrinking file or new inode means "rewritten"
BYTE_CURSOR = frozenset({AgentKind.CLAUDE, AgentKind.CODEX})
