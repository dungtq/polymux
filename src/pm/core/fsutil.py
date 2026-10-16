"""File IO helpers: atomic writes, small JSON state files, containment of user-supplied paths."""

from __future__ import annotations

import json
import logging
import os
import secrets
import stat
import threading
from collections.abc import Callable
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


def resolve_md(raw: str, cwd: str = "") -> Path | None:
    """Real path of a readable .md file anywhere on disk, else None.

    Relative paths join ``cwd``, ``~`` expands. Any dot-directory/file in the resolved real path is
    refused (``~/.ssh``, ``~/.cfg``, ``~/.agents-git``) except under ``~/.claude/plans``."""
    if not raw or len(raw) > 1024 or "\0" in raw or not raw.lower().endswith(".md"):
        return None
    p = Path(os.path.expanduser(raw)) if raw.startswith("~") else Path(raw)
    if not p.is_absolute():
        p = Path(cwd or "/") / p
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
