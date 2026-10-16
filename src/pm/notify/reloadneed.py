"""Does pm need a service restart, or only a browser refresh, after its own code changed?

Backend files (``*.py``, ``templates/``) are loaded once at start, so a newer mtime than the process
start means a restart. Static files (``static/``) are served from disk and versioned per request,
so a newer mtime than the page's serve time means a browser refresh. Read-only; mtimes only.

The watcher (``start`` / ``stop``, from the app lifespan) runs ``need`` every ``WATCH_S`` on the collectors pool and,
the first time it says restart, keeps that verdict (``code()``, read by the page templates without IO) and publishes
it once as a ``pm_code`` event; a restart is final for this process, so the loop ends there.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

from pm.core import executors
from pm.notify.events import PM_CODE

log = logging.getLogger(__name__)

PROCESS_START = time.time()
PKG = Path(__file__).resolve().parents[1]  # src/pm
TTL_S = 2.0
_cache: tuple[float, float, float] = (0.0, 0.0, 0.0)  # checked-at, backend mtime, static mtime


def _walk(root: Path, exts: tuple[str, ...]) -> float:
    newest = 0.0
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in ("__pycache__", "vendor") and not d.startswith(".")]
        for f in fn:
            if f.endswith(exts):
                try:
                    newest = max(newest, os.stat(os.path.join(dp, f)).st_mtime)
                except OSError:
                    pass
    return newest


def mtimes(pkg: Path | None = None, now: float | None = None) -> tuple[float, float]:
    """(newest backend mtime, newest static mtime), cached for TTL_S for the default package."""
    global _cache
    now = time.time() if now is None else now
    if pkg is None and now - _cache[0] < TTL_S:
        return _cache[1], _cache[2]
    root = pkg or PKG
    static = root / "static"
    back = 0.0
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in ("__pycache__", "static") and not d.startswith(".")]
        for f in fn:
            if f.endswith((".py", ".html", ".toml")):
                try:
                    back = max(back, os.stat(os.path.join(dp, f)).st_mtime)
                except OSError:
                    pass
    st = _walk(static, (".css", ".js", ".svg", ".webmanifest", ".png")) if static.is_dir() else 0.0
    if pkg is None:
        _cache = (now, back, st)
    return back, st


def need(served: float, start: float | None = None, pkg: Path | None = None) -> dict:
    """{need: restart|refresh|none, msg}. ``served`` is the epoch the page was rendered at."""
    start = PROCESS_START if start is None else start
    back, st = mtimes(pkg)
    if back > start + 1.0:
        return {"need": "restart", "msg": "pm code changed since the service started: restart pm (a browser refresh alone is not enough)."}
    if start > served + 1.0:
        return {"need": "refresh", "msg": "pm was restarted since this page loaded: refresh the page (no restart needed)."}
    if st > served + 1.0:
        return {"need": "refresh", "msg": "Only CSS/JS changed: a browser refresh is enough, no restart needed."}
    return {"need": "none", "msg": ""}


# ---------------------------------------------------------------- watcher: "new code, restart pm" bar
WATCH_S = 30.0
CODE_MSG = "New code: restart pm to load it."
HEAD_MAX = 120  # characters of the commit subject shown with it
_last: dict | None = None  # the watcher's restart verdict for this process; set once, never grows


def code() -> dict | None:
    """``{need: "restart", msg, head?}`` once the watcher saw new code, else None. No IO (a Jinja global)."""
    return _last


def source_root(pkg: Path | None = None) -> Path | None:
    """The checkout pm runs from (src layout: ``<root>/src/pm`` with ``<root>/.git``), else None (an installed wheel:
    a git repo above site-packages is not pm's)."""
    root = (pkg or PKG).parents[1]
    return root if (root / ".git").exists() else None


def check(head_fn: Callable[[Path], str | None] | None = None, *, start: float | None = None,
          pkg: Path | None = None) -> dict | None:
    """One watcher step (blocking: the mtime walk, then ``head_fn(root)`` for the commit subject): the restart verdict
    or None. ``head_fn`` is injected by the app (notify never imports git); its failure only drops ``head``."""
    if need(time.time(), start, pkg)["need"] != "restart":
        return None
    verdict = {"need": "restart", "msg": CODE_MSG}
    root = source_root(pkg)
    if head_fn is not None and root is not None:
        try:
            head = head_fn(root)
        except Exception as e:  # noqa: BLE001 - the subject is optional; never stop the watcher for it
            log.debug("reloadneed head failed: %s", type(e).__name__)
            head = None
        head = " ".join(str(head or "").split())[:HEAD_MAX]
        if head:
            verdict["head"] = head
    return verdict


async def watch(publish: Callable[[str, dict], object], head_fn=None, every: float = WATCH_S) -> None:
    """Until the first restart verdict: sleep ``every``, ``check`` on the collectors pool; then keep it and
    ``publish(PM_CODE, verdict)`` once. Errors are logged by type and the loop goes on."""
    global _last
    while _last is None:
        await asyncio.sleep(every)
        try:
            verdict = await executors.run_collectors(check, head_fn)
        except Exception as e:  # noqa: BLE001 - a failed walk must not end the watcher
            log.warning("reloadneed watch failed: %s", type(e).__name__)
            continue
        if verdict is not None:
            _last = verdict
            publish(PM_CODE, dict(verdict))


def start(app, publish: Callable[[str, dict], object], head_fn=None) -> asyncio.Task:
    """Call from the app lifespan. Sets app.state.reloadneed_task."""
    app.state.reloadneed_task = asyncio.create_task(watch(publish, head_fn), name="pm-reloadneed")
    return app.state.reloadneed_task


async def stop(app) -> None:
    task = getattr(app.state, "reloadneed_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        app.state.reloadneed_task = None
