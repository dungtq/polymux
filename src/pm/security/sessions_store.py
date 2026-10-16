"""On-disk persistence for login sessions (hashes only, never raw ids)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from pathlib import Path

from pm.core import fsutil

log = logging.getLogger("pm.sessions")

MAX_SESSIONS = 20
FORMAT = 1


def sid_hash(sid: str) -> str:
    return hashlib.sha256(sid.encode()).hexdigest()


def _ensure_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def load_secret(state_dir: Path) -> bytes:
    """Cookie-signing secret, persisted so cookies survive restarts. Never logged."""
    path = Path(state_dir) / "session_secret"
    try:
        data = path.read_text().strip()
        if len(data) >= 48:
            return data.encode()
    except OSError:
        pass
    _ensure_dir(Path(state_dir))
    secret = secrets.token_urlsafe(48)
    fsutil.atomic_write(path, secret.encode(), mode=0o600, fsync=True, fsync_dir=True)
    return secret.encode()


def read_entries(path: Path) -> list[dict]:
    """Return raw valid-shaped entries; any problem -> []."""
    try:
        doc = json.loads(path.read_text())
        out = []
        for e in doc["sessions"]:
            out.append({
                "h": str(e["h"]), "created": float(e["created"]),
                "last_seen": float(e["last_seen"]), "client": str(e.get("client", "")),
                "csrf": str(e["csrf"]),
            })
        return out
    except FileNotFoundError:
        return []
    except Exception as exc:  # corrupt/unreadable: start empty, type only
        log.warning("sessions file unreadable (%s); starting empty", type(exc).__name__)
        return []


def write_entries(path: Path, entries: list[dict]) -> None:
    _ensure_dir(path.parent)
    fsutil.atomic_write(path, json.dumps({"v": FORMAT, "sessions": entries}).encode(),
                        mode=0o600, fsync=True, fsync_dir=True)
