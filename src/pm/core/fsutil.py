"""File IO helpers: atomic writes, small JSON state files, containment of user-supplied paths."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import stat
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from pm.core.errors import Denied, Invalid

log = logging.getLogger("pm.fsutil")


def _fsync_dir(d: Path) -> None:
    try:
        dfd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def atomic_write(path, data: str | bytes, *, mode: int | None = 0o600, fsync: bool = True,
                 fsync_dir: bool = False, encoding: str = "utf-8",
                 before_replace: Callable[[], None] | None = None) -> None:
    """Write ``data`` to a temp file next to ``path`` and ``os.replace`` it into place.

    ``mode=None`` keeps the mode of an existing ``path`` (0600 when new). On any error the temp file
    is removed and the error re-raised; ``path`` is then unchanged.
    """
    path = Path(path)
    if isinstance(data, str):
        data = data.encode(encoding)
    if mode is None:
        try:
            mode = stat.S_IMODE(os.stat(path).st_mode)
        except FileNotFoundError:
            mode = 0o600
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            if fsync:
                os.fsync(f.fileno())
        if before_replace is not None:
            before_replace()
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if fsync_dir:
        _fsync_dir(path.parent)


def _enter(lock):
    """``lock`` is a lock object (has __enter__) or a factory returning a context manager."""
    return lock if hasattr(lock, "__enter__") else lock()


class JsonStore:
    """One JSON file of state: tolerant load, atomic save, locked read-modify-write."""

    def __init__(self, path, default_factory: Callable[[], Any], *,
                 validate: Callable[[Any], bool] | None = None, lock=None, indent: int | None = None,
                 sort_keys: bool = False, mode: int | None = 0o600, warn_corrupt: bool = False,
                 logger: logging.Logger | None = None, fsync_dir: bool = False) -> None:
        self.path = Path(path)
        self.default_factory = default_factory
        self.validate = validate
        self.lock = lock if lock is not None else threading.Lock()
        self.indent, self.sort_keys, self.mode = indent, sort_keys, mode
        self.warn_corrupt = warn_corrupt
        self.fsync_dir = fsync_dir
        self.log = logger or log

    def _corrupt(self, why: str) -> Any:
        if self.warn_corrupt:
            self.log.warning("%s: %s, using defaults", self.path.name, why)
        return self.default_factory()

    def load(self) -> Any:
        """The stored object, or ``default_factory()`` when missing, unreadable or invalid."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self.default_factory()
        except (OSError, UnicodeDecodeError) as e:
            return self._corrupt(f"unreadable ({e.__class__.__name__})")
        try:
            obj = json.loads(text)
        except ValueError:
            return self._corrupt("bad JSON")
        if self.validate is not None:
            try:
                ok = self.validate(obj)
            except Exception:  # noqa: BLE001 - any validator failure means "invalid"
                ok = False
            if not ok:
                return self._corrupt("invalid content")
        return obj

    def save(self, obj: Any) -> None:
        atomic_write(self.path, json.dumps(obj, indent=self.indent, sort_keys=self.sort_keys), mode=self.mode,
                     fsync_dir=self.fsync_dir)

    def update(self, fn: Callable[[Any], Any]) -> Any:
        """Under the lock: load, ``obj = fn(obj)``, save, return the new object."""
        with _enter(self.lock):
            obj = fn(self.load())
            self.save(obj)
            return obj


def encode_cwd(cwd: str) -> str:
    """Claude Code's project-dir encoding (``~/.claude/projects/<this>``): every char outside [A-Za-z0-9-]
    becomes '-'."""
    return re.sub(r"[^A-Za-z0-9-]", "-", cwd)


def safe_join(root, rel, *, allow_symlink_leaf: bool = False) -> Path:
    """``Path(root) / rel`` (unresolved) after checking it stays inside ``root``.

    Invalid: empty, NUL, absolute or any ``..`` part. Denied: resolves outside root (symlink escape),
    or the leaf itself is a symlink (unless ``allow_symlink_leaf``).
    """
    s = os.fspath(rel)
    if isinstance(s, bytes):
        raise Invalid("path must be text")
    if not s or "\0" in s:
        raise Invalid("empty or NUL in path")
    if os.path.isabs(s) or ".." in PurePosixPath(s).parts:
        raise Invalid("path must be relative, without '..'")
    real_root = Path(os.path.realpath(root))
    joined = Path(root) / s
    if not Path(os.path.realpath(joined)).is_relative_to(real_root):
        raise Denied("path escapes root")
    if not allow_symlink_leaf and joined.is_symlink():
        raise Denied("path is a symlink")
    return joined


def resolve_md(raw: str, cwd: str = "", roots: Iterable[str] = (), seen: Iterable[str] = (),
               dirs: Iterable[str] = (), projects: Iterable[tuple[str, str]] = ()) -> Path | None:
    """Real path of a readable .md file anywhere on disk, else None.

    ``~`` expands. A relative path is tried, each step only when the earlier ones miss, against: ``cwd`` (``/``
    when empty) and each of ``roots`` (the agent's project root); the first of ``seen`` (absolute paths, newest
    first, e.g. the files an agent's chat named) that ends with ``/<raw>``; each of ``dirs`` (absolute dirs the
    chat worked in, newest first); the ``projects`` (``(name, root)`` pairs, ``_md_in_projects``); for a bare
    name, a bounded search below ``cwd`` and ``roots`` (``_md_find_bare``). ``seen`` and ``dirs`` may be lazy
    iterators, read only this far. Every candidate gets the same checks (``_md_real``): any dot-directory/file in
    the resolved real path is refused (``~/.ssh``, ``~/.cfg``) except under ``~/.claude/plans``; symlinks are
    resolved first. No step but the first takes a ref with ``..``."""
    if not raw or len(raw) > 1024 or "\0" in raw or not raw.lower().endswith(".md"):
        return None
    p = Path(os.path.expanduser(raw)) if raw.startswith("~") else Path(raw)
    if p.is_absolute():
        return _md_real(p)
    roots = [r for r in roots if r]
    real = _md_in_bases(p, (cwd or "/", *roots))
    rel = PurePosixPath(p)
    if real is not None or ".." in rel.parts:
        return real
    real = _md_in_seen("/" + str(rel), seen)
    if real is None:
        tried: list[str] = []
        real = _md_in_bases(p, _remember(dirs, tried))
        real = real or _md_in_projects(p, tried, projects, roots)
    if real is None and len(rel.parts) == 1:
        real = _md_find_bare(rel.name, [b for b in (cwd, *roots) if b])
    return real


def _remember(items: Iterable[str], into: list[str]):
    for it in items:
        into.append(it)
        yield it


def _md_in_bases(p: Path, bases: Iterable[str]) -> Path | None:
    """First ``<base>/<p>`` that passes ``_md_real``; ``bases`` is read lazily (a hit stops the chat read)."""
    done: set[str] = set()
    for base in bases:
        if not base or base in done or not os.path.isabs(base):
            continue
        done.add(base)
        real = _md_real(Path(base) / p)
        if real is not None:
            return real
    return None


def _md_in_seen(tail: str, seen: Iterable[str]) -> Path | None:
    for s in seen:
        if isinstance(s, str) and s.endswith(tail) and s.startswith(("/", "~/")):
            real = _md_real(Path(os.path.expanduser(s)))
            if real is not None:
                return real
    return None


def _md_in_projects(p: Path, dirs: list[str], projects: Iterable[tuple[str, str]],
                    skip: Iterable[str]) -> Path | None:
    """``<root>/<p>`` of a known project: the first project the chat's ``dirs`` point into (a deleted
    ``~/dev/polymux-wt-x`` points to ``polymux``), else the one project that has the file; several and none
    preferred: None (no guess). The roots in ``skip`` were tried already."""
    projects = [(n, r) for n, r in projects if r and os.path.isabs(r) and r not in skip]
    hits = {r: real for _n, r in projects if (real := _md_real(Path(r) / p)) is not None}
    if not hits:
        return None
    for d in dirs:
        root = _project_of(d, projects)
        if root in hits:
            return hits[root]
    found = set(hits.values())
    return found.pop() if len(found) == 1 else None


def _project_of(d: str, projects: list[tuple[str, str]]) -> str | None:
    """Root of the project ``d`` lies in or next to: the folder under the project's parent is its name or
    ``<name>-...`` (worktrees, also deleted ones); the longest name wins."""
    best, key_len = None, 0
    for name, root in projects:
        parent, base = os.path.split(root.rstrip("/"))
        prefix = parent.rstrip("/") + "/"
        if not d.startswith(prefix):
            continue
        top = d[len(prefix):].split("/", 1)[0]
        for key in (base, name):
            if key and len(key) > key_len and (top == key or top.startswith(key + "-")):
                best, key_len = root, len(key)
    return best


_FIND_SKIP = frozenset({"node_modules", "dist", "build", "__pycache__", "site-packages", "venv"})
FIND_MAX_DEPTH = 6
FIND_MAX_DIRS = 5000
FIND_MAX_SECONDS = 0.5  # an unfinished search cannot prove the match unique: None


def _md_find_bare(name: str, bases: list[str]) -> Path | None:
    """The one file called ``name`` below ``bases`` (breadth first, no hidden or build folders, no symlinked
    folders, at most ``FIND_MAX_DEPTH`` deep, ``FIND_MAX_DIRS`` folders and ``FIND_MAX_SECONDS``); two
    different ones, or out of time: None."""
    reals = [os.path.realpath(b) for b in bases if os.path.isabs(b) and b != "/"]
    reals = [b for b in dict.fromkeys(reals) if not any(o != b and Path(b).is_relative_to(o) for o in reals)]
    queue, budget, found = deque((b, 0) for b in reals), FIND_MAX_DIRS, set()
    stop = time.monotonic() + FIND_MAX_SECONDS
    while queue and budget > 0 and len(found) < 2:
        if time.monotonic() > stop:
            return None
        d, depth = queue.popleft()
        budget -= 1
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            if e.name.startswith("."):
                continue
            if e.name == name and (real := _md_real(Path(e.path))) is not None:
                found.add(real)
            elif depth < FIND_MAX_DEPTH and e.name not in _FIND_SKIP and e.is_dir(follow_symlinks=False):
                queue.append((e.path, depth + 1))
    return found.pop() if len(found) == 1 else None


def _md_real(p: Path) -> Path | None:
    try:
        real = p.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if real.suffix.lower() != ".md" or not real.is_file():
        return None
    parts = real.parts
    try:
        plans = (Path(os.path.expanduser("~")) / ".claude" / "plans").resolve()
    except (OSError, RuntimeError):
        plans = None
    if plans is not None and real.is_relative_to(plans):
        parts = real.relative_to(plans).parts
    if any(part.startswith(".") for part in parts):
        return None
    return real


_MD_MENTION = re.compile(r"(?<![\w.~/:-])(~?/[\w.@+~/-]*\.md)(?![\w-]|\.\w)", re.IGNORECASE)


def md_mentions(text: str) -> list[str]:
    """Absolute (or ``~/``) .md paths written in ``text``, in order, each once (``resolve_md``'s ``seen``)."""
    return list(dict.fromkeys(_MD_MENTION.findall(text or ""))) if ".md" in (text or "").lower() else []
