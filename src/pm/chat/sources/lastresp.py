"""Time of an agent's last response, from its own session file (stat + bounded tail read, cached).

``Agent.last_active`` also moves on tmux redraws and on any write to the file, so an agent that
finished hours ago can still look fresh. This reads the timestamp of the newest assistant entry
instead. 0.0 means "unknown"; callers fall back to ``last_active`` (``Agent.done_ts``).
"""

from __future__ import annotations

import json
from pathlib import Path

from pm.core.models import AgentKind

from . import read_latest
from .common import mtime, parse_ts, read_tail_lines

_CACHE: dict[str, tuple[tuple[int, int], float]] = {}
_MAX_CACHE = 256


def _claude(path: Path) -> float:
    for raw in reversed(read_tail_lines(path)):
        if '"assistant"' not in raw:
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("type") == "assistant" and not entry.get("isSidechain"):
            ts = parse_ts(entry.get("timestamp"))
            if ts:
                return ts
    return 0.0


def _compute(kind: AgentKind, path: Path) -> float:
    if kind is AgentKind.CLAUDE:
        return _claude(path)
    latest = read_latest(kind, path)
    return latest.ts if latest is not None else 0.0


def last_response_ts(kind: AgentKind, path: Path | None) -> float:
    """Epoch s of the newest assistant entry in ``path``; 0.0 when unknown. Never raises."""
    if path is None:
        return 0.0
    try:
        st = path.stat()
        sig = (int(mtime(path) * 1e6), st.st_size)  # mtime() also sees a SQLite -wal sibling
        hit = _CACHE.get(str(path))
        if hit and hit[0] == sig:
            return hit[1]
        ts = _compute(kind, path)
    except Exception:  # noqa: BLE001 - best effort, the caller falls back to last_active
        return 0.0
    if len(_CACHE) >= _MAX_CACHE:
        _CACHE.clear()
    _CACHE[str(path)] = (sig, ts)
    return ts
