"""Static asset cache busting.

Browsers heuristically cache /static files (Last-Modified, no Cache-Control), so a
page rendered by a new release could run an old theme.js. Rendered pages get
``?v=<mtime hash>`` on every local /static src/href, and /static answers
``Cache-Control: no-cache`` (revalidate by ETag; a 304 is cheap). Theme and plugin static dirs
(``/static/t/<theme>/``, ``/static/p/<plugin>/``, registered with ``add_static`` where they are mounted) are
versioned the same way.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from starlette.staticfiles import StaticFiles

from pm.core.errors import Denied, Invalid
from pm.core.fsutil import safe_join

_REF = re.compile(r'''((?:src|href)=["'])(/static/[^"'?#]+)(["'])''')
_cache: dict[str, tuple[float, str]] = {}  # URL path -> (mtime, hash); bounded by the refs templates carry
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


def _ver(static_dir: Path, url: str) -> str:
    f = _file(static_dir, url)
    if f is None:
        return ""
    try:
        m = f.stat().st_mtime_ns
        hit = _cache.get(url)
        if hit and hit[0] == m:
            return hit[1]
        v = hashlib.sha1(f.read_bytes()).hexdigest()[:10]
    except OSError:
        return ""
    _cache[url] = (m, v)
    return v


def version_static(html: str, static_dir: Path) -> str:
    def sub(m: re.Match) -> str:
        v = _ver(static_dir, m.group(2))
        return m.group(1) + m.group(2) + ("?v=" + v if v else "") + m.group(3)
    return _REF.sub(sub, html)


class NoCacheStatic(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp
