"""Cross-process locking and edit leases for project BACKLOG.md / TODO.md.

Lock files live under ``<state_dir>/locks/`` (NOT beside the edited file): a sidecar
``.BACKLOG.md.lock`` would dirty ``git status`` (and the dirty counts pm shows) in every other
project, where ``.gitignore`` cannot be relied on. The name is derived from the file::

    <state_dir>/locks/<first 16 hex of sha256(realpath of the file)>.lock   (flock target)
    <state_dir>/locks/<same 16 hex>.json                                    (edit lease)

Agents that edit the files directly can take the same advisory lock with ``flock(1)``.
One lock file and one lease file per edited file, so the directory is bounded by the number of
projects; stale lease files are pruned on acquire.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path

from pm.core.fsutil import atomic_write

LEASE_SECONDS = 300
_PRUNE_AFTER = 86400
_GUARDS: dict[str, threading.Lock] = {}
_GUARDS_LOCK = threading.Lock()


class LockTimeout(Exception):
    pass


def _key(path) -> str:
    return hashlib.sha256(os.path.realpath(path).encode()).hexdigest()[:16]


def locks_dir(state_dir) -> Path:
    d = Path(state_dir) / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def lock_path(state_dir, path) -> Path:
    return locks_dir(state_dir) / f"{_key(path)}.lock"


def lease_path(state_dir, path) -> Path:
    return locks_dir(state_dir) / f"{_key(path)}.json"


@contextlib.contextmanager
def locked(lockfile, timeout: float = 5.0):
    """Hold the in-process lock and an exclusive ``flock`` on ``lockfile``.

    Polls non-blocking so a stuck holder (agent, other process) raises ``LockTimeout`` instead
    of hanging a worker thread.
    """
    lockfile = str(lockfile)
    with _GUARDS_LOCK:
        guard = _GUARDS.setdefault(lockfile, threading.Lock())
    deadline = time.monotonic() + timeout
    if not guard.acquire(timeout=max(0.0, timeout)):
        raise LockTimeout("another change is in progress")
    fd = -1
    try:
        fd = os.open(lockfile, os.O_RDWR | os.O_CREAT, 0o600)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LockTimeout("file is locked by another process") from None
                time.sleep(0.02)
        yield
    finally:
        if fd >= 0:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        guard.release()


# ------------------------------------------------------------------ edit leases
def _load(lp: Path, now: float) -> dict | None:
    try:
        d = json.loads(lp.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("id"), str):
        return None
    if float(d.get("expires", 0)) <= now:
        return None
    return d


def _store(lp: Path, lease: dict) -> None:
    atomic_write(lp, json.dumps(lease), mode=0o600, fsync=False)


def _prune(d: Path, now: float) -> None:
    for p in d.glob("*.json"):
        try:
            if json.loads(p.read_text()).get("expires", 0) < now - _PRUNE_AFTER:
                p.unlink()
        except (OSError, ValueError, AttributeError):
            pass


def lease_get(state_dir, path, now: float | None = None) -> dict | None:
    """The live lease for ``path`` or None (absent / expired)."""
    return _load(lease_path(state_dir, path), time.time() if now is None else now)


def _new(label: str, now: float) -> dict:
    return {"id": secrets.token_hex(8), "owner": label[:80], "since": now,
            "expires": now + LEASE_SECONDS}


def lease_acquire(state_dir, path, label: str, now: float | None = None,
                  takeover: bool = False) -> tuple[dict | None, dict | None]:
    """Take the lease. Returns ``(lease, None)`` or ``(None, current_holder)``.

    With ``takeover`` the lease is replaced; the second item is then the previous holder (or None).
    """
    now = time.time() if now is None else now
    lp = lease_path(state_dir, path)
    with locked(lock_path(state_dir, path), 3.0):
        cur = _load(lp, now)
        if cur is not None and not takeover:
            return None, cur
        _prune(lp.parent, now)
        lease = _new(label, now)
        _store(lp, lease)
        return lease, cur


def lease_renew(state_dir, path, lease_id: str, now: float | None = None) -> dict | None:
    now = time.time() if now is None else now
    lp = lease_path(state_dir, path)
    with locked(lock_path(state_dir, path), 3.0):
        cur = _load(lp, now)
        if cur is None:
            # Expired but nobody took it meanwhile: the owner may resume.
            if lp.exists() and _owner_file(lp) == lease_id:
                cur = _owner_file_full(lp)
            else:
                return None
        if cur is None or cur["id"] != lease_id:
            return None
        cur["expires"] = now + LEASE_SECONDS
        _store(lp, cur)
        return cur


def _owner_file_full(lp: Path) -> dict | None:
    try:
        d = json.loads(lp.read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get("id"), str) else None


def _owner_file(lp: Path) -> str | None:
    d = _owner_file_full(lp)
    return d["id"] if d else None


def lease_release(state_dir, path, lease_id: str) -> bool:
    lp = lease_path(state_dir, path)
    with locked(lock_path(state_dir, path), 3.0):
        if _owner_file(lp) != lease_id:
            return False
        with contextlib.suppress(OSError):
            lp.unlink()
        return True
