"""Shared SQLite helper for ``settings.db_file`` (pm.db): the chat stores (``log``, ``items``,
``outbox``) and the tracker (``pm.projects.tracker.store``).

Each store passes its own schema tuple (and an optional ``migrate``); the schema runs once per
process and DB file (again when the file is new). WAL when possible; the DB and its ``-wal``/``-shm``
files are kept at 0600. ``agent_lock`` serialises the writers of one agent inside this process.

``backup`` writes a daily online copy to ``state_dir/backups/pm-YYYY-MM-DD.db`` (keeps
``db_backup_keep``); ``start_backup`` / ``stop_backup`` run it from the app lifespan.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import secrets
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pm.core import executors, timefmt
from pm.core.config import Settings

log = logging.getLogger(__name__)

BACKUP_FIRST_DELAY_S = 60.0
BACKUP_INTERVAL_S = 6 * 3600.0
_BACKUP_RE = re.compile(r"pm-\d{4}-\d{2}-\d{2}\.db")

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_INIT: dict[tuple[str, ...], set[str]] = {}


def agent_lock(agent_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        if len(_LOCKS) > 1024:
            _LOCKS.clear()
        return _LOCKS.setdefault(agent_id, threading.Lock())


def inited(schema: tuple[str, ...]) -> set[str]:
    """The DB files ``schema`` already ran on in this process (discard one to run it again)."""
    return _INIT.setdefault(schema, set())


def connect(settings: Settings, schema: tuple[str, ...],
            migrate: Callable[[sqlite3.Connection], None] | None = None) -> sqlite3.Connection:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    db = Path(settings.db_file)
    fresh = not db.exists()
    con = sqlite3.connect(db, timeout=5)
    key = str(db)
    done = inited(schema)
    if key not in done or fresh:
        for stmt in schema:
            con.execute(stmt)
        if migrate is not None:
            migrate(con)
        try:
            con.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass  # another connection holds it: rollback journal is fine too
        con.commit()
        done.add(key)
    for p in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        try:
            if p.stat().st_mode & 0o077:
                os.chmod(p, 0o600)
        except OSError:
            continue
    return con


def _prune(folder: Path, keep: int) -> None:
    """Delete the oldest ``pm-YYYY-MM-DD.db`` files beyond ``keep`` (names sort by date)."""
    old = sorted(p for p in folder.iterdir() if _BACKUP_RE.fullmatch(p.name))
    for p in old[:-keep]:
        with contextlib.suppress(OSError):
            p.unlink()


def _copy(src: Path, tmp: Path) -> None:
    """Online copy of ``src`` into the new file ``tmp`` (single file: rollback journal, not WAL)."""
    s = sqlite3.connect(src, timeout=5)
    try:
        d = sqlite3.connect(tmp)
        try:
            s.backup(d)
            d.execute("PRAGMA journal_mode=DELETE")
            d.commit()
        finally:
            d.close()
    finally:
        s.close()
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())


def backup(settings: Settings, *, keep: int | None = None, now: datetime | None = None,
           force: bool = False) -> Path | None:
    """Copy pm.db to ``state_dir/backups/pm-<today>.db`` (dir 0700, file 0600) unless today's copy exists
    (``force`` overwrites it), then prune to ``keep`` (default ``settings.db_backup_keep``; 0 = off).
    Returns the path written, or None (off, no pm.db yet, or already done today)."""
    keep = settings.db_backup_keep if keep is None else keep
    src = Path(settings.db_file)
    if keep <= 0 or not src.exists():
        return None
    folder = settings.state_dir / "backups"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.stat().st_mode & 0o077:
        os.chmod(folder, 0o700)
    dest = folder / f"pm-{(now.date() if now else timefmt.today()).isoformat()}.db"
    written = None
    if force or not dest.exists():
        tmp = folder / f".{dest.name}.{secrets.token_hex(6)}.tmp"
        os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600))
        try:
            _copy(src, tmp)
            os.chmod(tmp, 0o600)
            os.replace(tmp, dest)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise
        written = dest
    _prune(folder, keep)
    return written


async def _backup_loop(settings: Settings) -> None:
    await asyncio.sleep(BACKUP_FIRST_DELAY_S)
    while True:
        try:
            path = await executors.run_collectors(backup, settings)
            if path is not None:
                log.info("pm.db backup written: %s", path.name)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("pm.db backup failed: %s", type(e).__name__)
        await asyncio.sleep(BACKUP_INTERVAL_S)


def start_backup(app, settings: Settings | None = None) -> asyncio.Task | None:
    """Lifespan: back up pm.db ~60 s after start, then every 6 h (one file per day)."""
    settings = settings or app.state.settings
    if settings.db_backup_keep <= 0:
        return None
    app.state.db_backup_task = asyncio.create_task(_backup_loop(settings), name="pm-db-backup")
    return app.state.db_backup_task


async def stop_backup(app) -> None:
    task = getattr(app.state, "db_backup_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        app.state.db_backup_task = None
