"""Plans: markdown files in the plan directories of the coding agents on this host plus
``<project>/plans/*.md`` (flat, no subdirectories) plus the project-root ``PLAN.md`` (read-only).
Sources (``SOURCES``): ``root`` (``<project>/PLAN.md``, read-only: create/save/rename/delete are
refused), ``claude``
(``~/.claude/plans``), ``cursor`` (``~/.cursor/plans``), ``claude-project`` (the project's
``plansDirectory`` from ``.claude/settings*.json``, else ``<project>/.claude/plans`` when it
exists) and ``project`` (``<project>/plans``). Agent plans are matched to a project by content
(the project's path appears in the file); ``all_agent=True`` lists them unfiltered.

Pure logic, no web code: the routes
(web/routes/plans.py) validate the project NAME against the registry and pass the server-derived
project path in. Safety:

* file names are ``[A-Za-z0-9][A-Za-z0-9._-]{0,63}`` + ``.md`` (no slash, no dot-dot, no hidden
  files); the user gives the stem, the ``.md`` suffix is added here;
* ``<project>/plans`` must be a real directory whose realpath is directly under the project's
  realpath (a symlinked ``plans`` is refused); a plan file must be a regular file, never a
  symlink (opened with ``O_NOFOLLOW`` and checked with ``fstat``);
* bytes are capped at ``MAX_BYTES``; writes are temp file + fsync + ``os.replace`` in the plans
  dir; every change runs under the cross-process flock of ``filelock`` (the plans dir lock, then
  the lock of the file, i.e. the lock agents take with ``flock(1)`` on the file path);
* full-file saves carry a content token (sha256 prefix) and are refused with 409 (carrying the
  current bytes) when the file is not the one the page showed.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from pm.core import filelock
from pm.core.fsutil import atomic_write
from pm.projects import backlog as rb

DIRNAME = "plans"
SUFFIX = ".md"
MAX_BYTES = 256 * 1024
MAX_FILES = 500
_TOKEN = re.compile(r"^[0-9a-f]{16}$")
_STEM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
NEW_MAXLEN = 64  # names the user types for create/rename; existing agent files may be longer
SOURCES = {
    "root": "PLAN.md (project root)",
    "project": "Project plans/",
    "claude-project": "Claude Code (project)",
    "claude": "Claude Code",
    "cursor": "Cursor",
}
READONLY = frozenset({"root"})  # listed and shown, never written by pm
ROOT_FILE = "PLAN" + SUFFIX
AGENT_SOURCES = ("claude", "cursor")  # filtered to the project by content
_HOME_DIRS = {"claude": ".claude/plans", "cursor": ".cursor/plans"}
_FORBIDDEN_HOME = (".cfg", ".agents-git", ".ssh", ".gnupg")


def token_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


class PlanError(Exception):
    def __init__(self, status: int, msg: str, current: bytes | None = None):
        super().__init__(msg)
        self.status, self.msg, self.current = status, msg, current


@dataclass(frozen=True)
class Plan:
    name: str  # stem, without ".md"
    size: int
    mtime: float
    src: str = "project"

    @property
    def label(self) -> str:
        return SOURCES.get(self.src, self.src)

    @property
    def filename(self) -> str:
        return self.name + SUFFIX


def stem_of(name: str, maxlen: int = 96) -> str:
    """Validate a user-supplied name (``foo`` or ``foo.md``) and return the stem."""
    n = (name or "").strip()
    if n.lower().endswith(SUFFIX):
        n = n[: -len(SUFFIX)]
    if not _STEM.match(n) or len(n) > maxlen or n.endswith(".") or ".." in n:
        raise PlanError(400, "Bad name: use letters, digits, '.', '_' or '-' (max 64, "
                             "must start with a letter or digit).")
    return n


def _home() -> str:
    return os.path.realpath(os.path.expanduser("~"))


def check_src(src: str) -> str:
    if src not in SOURCES:
        raise PlanError(400, "Unknown plan location.")
    return src


def _local_rel(proj: str) -> str | None:
    """Relative dir for the project-local Claude plans: ``plansDirectory`` from
    ``.claude/settings.local.json`` / ``settings.json``, else ``.claude/plans`` if it exists."""
    for fn in ("settings.local.json", "settings.json"):
        try:
            with open(os.path.join(proj, ".claude", fn), "rb") as fh:
                v = json.loads(fh.read(65536)).get("plansDirectory")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(v, str) and v.strip():
            return v.strip()
    if os.path.isdir(os.path.join(proj, ".claude", "plans")):
        return ".claude/plans"
    return None


def plans_dir(project_path: str, create: bool = False, src: str = "project") -> Path | None:
    """The plan directory of ``src`` for the project; None when absent (and not ``create``).
    Refuses symlinks, escapes from the allowed root (the project, or the agent's home dir) and
    the protected git dirs."""
    check_src(src)
    proj = os.path.realpath(project_path)
    if src in _HOME_DIRS:
        home = _home()
        d = Path(home) / _HOME_DIRS[src]
        root, want = home, os.path.join(home, _HOME_DIRS[src])
        if not d.parent.is_dir() and not create:
            return None
    else:
        rel = DIRNAME if src == "project" else _local_rel(proj)
        if rel is None:
            if create:
                raise PlanError(400, "No project-local Claude plans directory configured.")
            return None
        parts = os.path.normpath(rel).split(os.sep)
        if os.path.isabs(rel) or ".." in parts or parts[0] in ("", "."):
            raise PlanError(400, "plan directory must be inside the project; refused")
        d = Path(proj, *parts)
        root, want = proj, os.path.join(proj, *parts)
    if d.is_symlink():
        raise PlanError(400, "plans directory is a symlink; refused")
    if not d.exists():
        if not create:
            return None
        try:
            d.mkdir(mode=0o755, parents=(src != "project"))
        except FileExistsError:
            pass
    real = os.path.realpath(d)
    if not d.is_dir() or real != want or not real.startswith(root + os.sep):
        raise PlanError(400, "plans directory is not a plain directory in its root; refused")
    h = _home()
    if any(real == os.path.join(h, f) or real.startswith(os.path.join(h, f) + os.sep)
           for f in _FORBIDDEN_HOME):
        raise PlanError(400, "protected directory; refused")
    return d


def _root_file(project_path: str) -> Path | None:
    """``<realpath(project)>/PLAN.md`` when it is a plain file directly in the project (a
    symlink, or anything resolving outside the project, gives None)."""
    proj = os.path.realpath(project_path)
    f = Path(proj, ROOT_FILE)
    if os.path.realpath(f) != str(f):
        return None
    return f


def _refuse_ro(src: str) -> None:
    if src in READONLY:
        raise PlanError(403, f"{SOURCES[src]} is read-only here; edit the file directly.")


def _file(d: Path, stem: str) -> Path:
    f = d / (stem + SUFFIX)
    if os.path.dirname(os.path.realpath(f)) != os.path.realpath(d):
        raise PlanError(400, "path escapes plans/; refused")
    return f


def _read(f: Path) -> tuple[bytes, float]:
    """Read a plan without following symlinks. Missing -> 404."""
    try:
        fd = os.open(f, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise PlanError(404, "No such plan.") from None
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise PlanError(400, "plan is a symlink; refused") from None
        raise
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise PlanError(400, "plan is not a regular file; refused")
        return fh.read(MAX_BYTES * 2), st.st_mtime


_MATCH: dict[tuple, bool] = {}


def _mentions(f: Path, st, proj: str) -> bool:
    """True when the plan text contains the project path (cached by mtime/size)."""
    key = (str(f), st.st_mtime_ns, st.st_size, proj)
    hit = _MATCH.get(key)
    if hit is None:
        try:
            fd = os.open(f, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as fh:
                data = fh.read(MAX_BYTES * 2).decode("utf-8", "replace")
        except OSError:
            return False
        hit = re.search(re.escape(proj) + r"(?![\w.-])", data) is not None
        if len(_MATCH) > 5000:
            _MATCH.clear()
        _MATCH[key] = hit
    return hit


def _scan(project_path: str, src: str, all_agent: bool) -> list[Plan]:
    if src == "root":
        f = _root_file(project_path)
        try:
            st = os.lstat(f) if f else None
        except OSError:
            return []
        if st is None or not stat.S_ISREG(st.st_mode):
            return []
        return [Plan(ROOT_FILE[: -len(SUFFIX)], st.st_size, st.st_mtime, "root")]
    try:
        d = plans_dir(project_path, src=src)
    except PlanError:
        return []
    if d is None:
        return []
    proj = os.path.realpath(project_path)
    out: list[Plan] = []
    try:
        with os.scandir(d) as it:
            for e in it:
                if not e.name.endswith(SUFFIX) or e.is_symlink() or not e.is_file(follow_symlinks=False):
                    continue
                stem = e.name[: -len(SUFFIX)]
                if not _STEM.match(stem):
                    continue
                st = e.stat(follow_symlinks=False)
                if src in AGENT_SOURCES and not all_agent and not _mentions(Path(e.path), st, proj):
                    continue
                out.append(Plan(stem, st.st_size, st.st_mtime, src))
                if len(out) >= MAX_FILES:
                    break
    except OSError:
        return []
    return out


def list_plans(project_path: str, all_agent: bool = False, src: str | None = None) -> list[Plan]:
    """Regular ``*.md`` files with valid names from every source (or just ``src``), newest first
    (symlinks and junk skipped). Agent plans only when they mention the project unless
    ``all_agent``."""
    out: list[Plan] = []
    for k in ([check_src(src)] if src else SOURCES):
        out += _scan(project_path, k, all_agent)
    out.sort(key=lambda p: (-p.mtime, p.src, p.name))
    return out


def counts(project_path: str) -> dict[str, int]:
    """Plans per source, project-filtered (for the page header)."""
    return {k: n for k in SOURCES if (n := len(_scan(project_path, k, False)))}


def read_plan(project_path: str, name: str, src: str = "project") -> tuple[str, str, float]:
    """(text, token, mtime). Raises PlanError (404 when absent)."""
    stem = stem_of(name)
    if src == "root":
        f = _root_file(project_path)
        if f is None and os.path.lexists(os.path.join(os.path.realpath(project_path), ROOT_FILE)):
            raise PlanError(400, "PLAN.md is a symlink or outside the project; refused")
        if f is None or stem + SUFFIX != ROOT_FILE:
            raise PlanError(404, "No such plan.")
        data, mtime = _read(f)
        return data.decode("utf-8", "replace"), token_of(data), mtime
    d = plans_dir(project_path, src=src)
    if d is None:
        raise PlanError(404, "No such plan.")
    data, mtime = _read(_file(d, stem))
    return data.decode("utf-8", "replace"), token_of(data), mtime


def _write_atomic(f: Path, data: bytes, expect: bytes | None) -> None:
    if len(data) > MAX_BYTES:
        raise PlanError(413, f"Too large ({len(data)} bytes; max {MAX_BYTES}).")

    def check() -> None:
        if expect is not None and _read(f)[0] != expect:
            raise PlanError(409, "The plan was changed by someone else while saving; "
                                 "nothing was saved.", current=_read(f)[0])

    atomic_write(f, data, mode=0o644, fsync=True, before_replace=check)


def _locked(settings, d: Path, *files: Path):
    stack = ExitStack()
    try:
        stack.enter_context(filelock.locked(filelock.lock_path(settings.state_dir, d), 5.0))
        for f in files:
            stack.enter_context(filelock.locked(filelock.lock_path(settings.state_dir, f), 5.0))
    except filelock.LockTimeout:
        stack.close()
        raise PlanError(503, "Plans are busy (locked by another writer); try again.") from None
    return stack


def _text_bytes(text: str) -> bytes:
    if "\x00" in text:
        raise PlanError(400, "Plan text must not contain NUL bytes.")
    return text.replace("\r\n", "\n").encode()


def create_plan(settings, project_path: str, name: str, text: str,
                src: str = "project") -> tuple[str, int]:
    """New plan; 409 when the name exists. Returns (stem, bytes). In an agent directory the
    project path is appended as an invisible markdown comment (unless the text already names it)
    so the plan is found again for this project."""
    _refuse_ro(check_src(src))
    stem = stem_of(name, NEW_MAXLEN)
    if src in AGENT_SOURCES:
        proj = os.path.realpath(project_path)
        if proj not in text:
            text = text.rstrip("\n") + f"\n\n[//]: # (pm project: {proj})\n"
    data = _text_bytes(text)
    d = plans_dir(project_path, create=True, src=src)
    f = _file(d, stem)
    with _locked(settings, d, f):
        if os.path.lexists(f):
            raise PlanError(409, f"A plan named {stem} already exists.")
        if len(_scan(project_path, src, True)) >= MAX_FILES:
            raise PlanError(413, f"Too many plans (max {MAX_FILES}).")
        _write_atomic(f, data, expect=None)
    return stem, len(data)


def save_plan(settings, project_path: str, name: str, text: str, token: str,
              src: str = "project") -> tuple[int, int]:
    """Replace a plan's text if it is still the version shown (``token``). (old, new) sizes."""
    _refuse_ro(check_src(src))
    stem = stem_of(name)
    if not _TOKEN.match(token or ""):
        raise PlanError(400, "Missing page token; reload the plan.")
    data = _text_bytes(text)
    d = plans_dir(project_path, src=src)
    if d is None:
        raise PlanError(404, "No such plan.")
    f = _file(d, stem)
    with _locked(settings, d, f):
        cur, _ = _read(f)
        if token_of(cur) != token:
            raise PlanError(409, "The plan changed since this page loaded; nothing was saved.",
                            current=cur)
        _write_atomic(f, data, expect=cur)
    return len(cur), len(data)


def rename_plan(settings, project_path: str, old: str, new: str, src: str = "project") -> str:
    _refuse_ro(check_src(src))
    so, sn = stem_of(old), stem_of(new, NEW_MAXLEN)
    d = plans_dir(project_path, src=src)
    if d is None:
        raise PlanError(404, "No such plan.")
    fo, fn = _file(d, so), _file(d, sn)
    if so == sn:
        raise PlanError(400, "The new name is the same.")
    with _locked(settings, d, *sorted((fo, fn), key=str)):
        _read(fo)  # 404 / symlink / not-regular checks
        if os.path.lexists(fn):
            raise PlanError(409, f"A plan named {sn} already exists.")
        os.rename(fo, fn)
    return sn


def delete_plan(settings, project_path: str, name: str, src: str = "project") -> int:
    _refuse_ro(check_src(src))
    stem = stem_of(name)
    d = plans_dir(project_path, src=src)
    if d is None:
        raise PlanError(404, "No such plan.")
    f = _file(d, stem)
    with _locked(settings, d, f):
        data, _ = _read(f)
        os.unlink(f)
    return len(data)


def summary(settings, name: str, limit: int = 5) -> dict:
    """For the project page: newest plans (names only) and the total."""
    path = rb.registry(settings).get(name)
    items = list_plans(path) if path else []
    return {"total": len(items), "shown": items[:limit]}
