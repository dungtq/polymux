"""Static asset cache busting.

Browsers heuristically cache /static files (Last-Modified, no Cache-Control), so a
page rendered by a new release could run an old theme.js. Rendered pages get
``?v=<content hash>`` on every local /static src/href. /static answers a request whose ``v`` is the file's current
hash with ``Cache-Control: public, max-age=31536000, immutable`` (a new release changes the URL, so the browser
never asks again: a page switch loads ~100 assets from its cache instead of revalidating each), anything else
(no ``v``, an old one) with ``no-cache`` (revalidate by ETag; a 304 is cheap). Theme and plugin static dirs
(``/static/t/<theme>/``, ``/static/p/<plugin>/``, registered with ``add_static`` where they are mounted) are
versioned the same way.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.staticfiles import StaticFiles

from pm.core.errors import Denied, Invalid
from pm.core.fsutil import safe_join

_REF = re.compile(r'''((?:src|href)=["'])(/static/[^"'?#]+)(["'])''')
_cache: dict[str, tuple[tuple[int, int], str]] = {}  # file path -> ((mtime_ns, size), hash); bounded by the static files
_dirs: dict[str, Path] = {}  # URL prefix ("/static/t/<name>/") -> its dir; one entry per mounted theme / plugin


def add_static(prefix: str, directory: Path) -> None:
    """Version URLs under ``prefix`` (a theme or plugin static mount) from the files in ``directory``."""
    _dirs[prefix.rstrip("/") + "/"] = Path(directory)


def _file(static_dir: Path, url: str) -> Path | None:
    root, rel = static_dir, url[len("/static/"):]
    for prefix, d in _dirs.items():
        if url.startswith(prefix):
            root, rel = d, url[len(prefix):]
            break
    try:
        return safe_join(root, rel, allow_symlink_leaf=True)
    except (Invalid, Denied):  # '..', absolute, empty, escapes the dir
        return None


def _hash(f: Path) -> str:
    """Short content hash of ``f``, cached per path until its mtime changes; "" when it cannot be read."""
    try:
        st = f.stat()
        m = (st.st_mtime_ns, st.st_size)  # size too: two in-place writes in one mtime tick must not keep a stale hash
        hit = _cache.get(str(f))
        if hit and hit[0] == m:
            return hit[1]
        v = hashlib.sha1(f.read_bytes()).hexdigest()[:10]
    except OSError:
        return ""
    _cache[str(f)] = (m, v)
    return v


def _ver(static_dir: Path, url: str) -> str:
    f = _file(static_dir, url)
    return _hash(f) if f is not None else ""


def version_static(html: str, static_dir: Path) -> str:
    def sub(m: re.Match) -> str:
        v = _ver(static_dir, m.group(2))
        return m.group(1) + m.group(2) + ("?v=" + v if v else "") + m.group(3)
    return _REF.sub(sub, html)


_IMMUTABLE = "public, max-age=31536000, immutable"


def _query_v(scope) -> str:
    for part in scope.get("query_string", b"").decode("latin-1").split("&"):
        k, _, v = part.partition("=")
        if k == "v":
            return v
    return ""


class NoCacheStatic(StaticFiles):
    def _current(self, path: str, v: str) -> bool:
        """True when ``v`` is the current content hash of the file ``path`` serves (blocking: stat, maybe a read)."""
        full, _ = self.lookup_path(path)
        return bool(full) and v == _hash(Path(full))

    async def get_response(self, path, scope):
        # the check runs in anyio's worker threads, like StaticFiles' own lookup_path (not a pm.core.executors pool:
        # static files must not queue behind collectors or jobs)
        resp = await super().get_response(path, scope)
        v, cc = _query_v(scope), "no-cache"
        if v and resp.status_code in (200, 304) and await run_in_threadpool(self._current, path, v):
            cc = _IMMUTABLE
        resp.headers["Cache-Control"] = cc
        return resp
