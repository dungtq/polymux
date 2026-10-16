"""Import past CLI sessions found on disk for each project folder (backlog #156).

Every ``past_sessions_interval_s`` (0 = off) a background loop takes the projects of the already
published snapshot (it never builds one), finds the Claude / Codex / Cursor sessions whose cwd is a
project folder (``pm.chat.sources.past``) and adds the ones pm does not know yet as ended records
(``registry.import_past``). From then on they are ordinary ended rows: project HISTORY, the read-only
``/a/<id>`` chat, Resume and Fork need nothing else. A session that later runs live is found by its
ident and keeps its id. Scans run on the collectors pool, never per request; files changed in the last
``FRESH_S`` are left to the live detection. Nothing here reads pane text.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from pm.agents import registry
from pm.chat.sources import past
from pm.core import executors
from pm.core.cache import LRU
from pm.core.config import Settings
from pm.core.models import Project

log = logging.getLogger(__name__)

FIRST_DELAY_S = 45.0
MIN_INTERVAL_S = 60
FRESH_S = 120.0  # a store file written this recently may belong to a running agent
_heads = LRU(max_bytes=4 * 1024 * 1024, max_entries=8192)  # (kind, path) -> (cwd, interactive) of the file head
_seen = LRU(max_entries=1024)  # (db file, project path) -> ((kind, ident) set imported, when)
RECHECK_S = 3600.0  # an unchanged set is offered to the registry again after this long


def _head(kind: str, path: Path) -> tuple[str, bool]:
    key = (kind, str(path))
    hit = _heads.get(key)
    if hit is None:
        hit = past.head_of(kind, path)
        _heads.put(key, hit)
    return hit


def scan(settings: Settings, projects: Iterable[Project], *, home: Path | None = None,
         now: float | None = None) -> int:
    """One synchronous pass over the project folders. Returns the number of records added."""
    now = time.time() if now is None else now
    days = int(settings.agent_retention_days or 0)
    since = now - days * 86400 if days > 0 else 0.0
    names = {str(p.path): p.name for p in projects if p.path}
    found = past.discover_many(names, home or Path.home(), since=since, until=now - FRESH_S, head=_head)
    added = 0
    for cwd, rows in found.items():
        key = (str(settings.db_file), cwd)
        idents = frozenset((r.kind, r.ident) for r in rows)
        memo = _seen.get(key)
        if not idents or (memo and memo[0] == idents and now - memo[1] < RECHECK_S):
            continue
        n = registry.import_past(settings, rows, project=names[cwd])
        if n < 0:
            continue  # database error: try again next scan
        added += n
        _seen.put(key, (idents, now))
    return added


def interval_s(settings: Settings) -> int:
    v = int(settings.past_sessions_interval_s or 0)
    return 0 if v <= 0 else max(MIN_INTERVAL_S, v)


async def loop(settings: Settings, published: Callable[[Settings], object]) -> None:
    """``published(settings)``: the latest published snapshot or None (never builds one)."""
    await asyncio.sleep(FIRST_DELAY_S)
    while (every := interval_s(settings)) > 0:
        try:
            snap = published(settings)
            projects = tuple(getattr(snap, "projects", ()) or ())
            if projects:
                n = await executors.run_collectors(scan, settings, projects)
                if n:
                    log.info("past sessions: %d imported", n)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("past session scan failed: %s", type(e).__name__)
        await asyncio.sleep(every)


def start(app, published: Callable[[Settings], object], settings: Settings | None = None) -> asyncio.Task:
    settings = settings or app.state.settings
    app.state.pastsessions_task = asyncio.create_task(loop(settings, published), name="pm-pastsessions")
    return app.state.pastsessions_task


async def stop(app) -> None:
    task = getattr(app.state, "pastsessions_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        app.state.pastsessions_task = None
