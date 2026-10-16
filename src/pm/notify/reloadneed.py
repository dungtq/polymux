"""Does pm need a service restart, or only a browser refresh, after its own code changed?

Backend files (``*.py``, ``templates/``) are loaded once at start, so a newer mtime than the process
start means a restart. Static files (``static/``) are served from disk and versioned per request,
so a newer mtime than the page's serve time means a browser refresh. Read-only; mtimes only.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

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
