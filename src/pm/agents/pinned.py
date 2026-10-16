"""Pinned agent sessions: agent ids the user pinned on an agent row (Inbox, project page).

One JSON list in ``state_dir/agent_pins.json`` (oldest first), written atomically under a flock like the
project pins (``projects.set_pin``). At most ``MAX_PINS`` ids are kept; pinning past the cap drops the oldest.
Pins of agents that ended stay until unpinned or pushed out by the cap (an ended agent has no row to show).
"""

from __future__ import annotations

import json
from pathlib import Path

from pm.agents.lineage import AGENT_ID_RE
from pm.core import filelock
from pm.core.config import Settings
from pm.core.errors import Invalid
from pm.core.fsutil import JsonStore, atomic_write

MAX_PINS = 200


def _file(settings: Settings) -> Path:
    return Path(settings.state_dir) / "agent_pins.json"


def _ids(settings: Settings) -> list[str]:
    data = JsonStore(_file(settings), list, validate=lambda d: isinstance(d, list)).load()
    out: list[str] = []
    for x in data:
        if isinstance(x, str) and len(x) <= 64 and AGENT_ID_RE.fullmatch(x) and x not in out:
            out.append(x)
    return out[-MAX_PINS:]


def load(settings: Settings) -> set[str]:
    """The pinned agent ids (empty when the file is missing or broken)."""
    return set(_ids(settings))


def set_pin(settings: Settings, agent_id: str, on: bool) -> set[str]:
    """Pin or unpin one agent id; returns the new set. Raises ``Invalid`` for a bad id, ``OSError`` when busy."""
    if len(agent_id) > 64 or not AGENT_ID_RE.fullmatch(agent_id):
        raise Invalid("bad agent id")
    f = _file(settings)
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        with filelock.locked(f.parent / "agent_pins.lock"):
            ids = [x for x in _ids(settings) if x != agent_id]
            if on:
                ids.append(agent_id)
            ids = ids[-MAX_PINS:]
            atomic_write(f, json.dumps(ids), mode=0o600)
    except filelock.LockTimeout as e:
        raise OSError("agent pins are busy") from e
    return set(ids)
