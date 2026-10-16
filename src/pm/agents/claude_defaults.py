"""The model and effort Claude Code starts with when nothing was chosen: ``model`` and ``effortLevel`` from its
settings files (read-only, best effort). Precedence per key: ``<cwd>/.claude/settings.local.json``, then
``<cwd>/.claude/settings.json``, then ``<home>/.claude/settings.json``."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from pm.core.cache import LRU

_CAP = 65_536
_CACHE = LRU(max_bytes=65_536, max_entries=256)


def _text(v, cap: int) -> str:
    """A settings value as a short printable string, '' when it is not one."""
    return "".join(ch for ch in v if ch.isprintable()).strip()[:cap] if isinstance(v, str) else ""


def _read(path: str) -> tuple[str, str]:
    """(model, effortLevel) of one settings file, '' for each that is absent; cached by (path, mtime)."""
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode):
            return "", ""
        key = (path, st.st_mtime)
        hit = _CACHE.get(key)
        if hit is not None:
            return hit
        with open(path, "rb") as fh:
            data = json.loads(fh.read(_CAP))
        hit = (_text(data.get("model"), 80), _text(data.get("effortLevel"), 16))
    except (OSError, ValueError, AttributeError):
        return "", ""
    _CACHE.put(key, hit)
    return hit


def read(cwd: str, home: Path | None = None) -> tuple[str, str]:
    """(model, effort) Claude Code defaults to for an agent working in ``cwd``: the raw settings values
    (an alias such as ``opus`` stays one), '' for each that no file sets."""
    base = [os.path.join(cwd, ".claude", fn) for fn in ("settings.local.json", "settings.json")] \
        if cwd and os.path.isabs(cwd) else []
    found = [_read(p) for p in [*base, str((home or Path.home()) / ".claude" / "settings.json")]]
    return (next((m for m, _ in found if m), ""), next((e for _, e in found if e), ""))
