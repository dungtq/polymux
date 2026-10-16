"""Shared pieces of the Agents page mutations: plan types, refusals, path guards, backups."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
from dataclasses import dataclass, field
from pathlib import Path

from pm.core.models import AgentInfo, Inventory

from .read import clear_cache, display_path, get_inventory, is_blocked

MAX_BODY = 256 * 1024
MAX_DESC = 300
MAX_DELETE_FILES = 50
MAX_DELETE_DEPTH = 2
MAX_READ = 1024 * 1024
MAX_TARGETS = 20
SKILL_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
RULE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.(md|mdc)")
SCRIPT_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
AGENT_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
TOP_RULES = ("AGENTS.md", "CLAUDE.md", "agents-index.md")
BACKUP_REL = ".local/state/pm/agents-backups"
BACKUP_DISP = "~/" + BACKUP_REL + "/"
PROTECTED = "~/.agents-git and ~/.cfg are never touched."
STALE = "State changed since the review; review the plan again."


class MutError(Exception):
    """Refusal whose message is safe to show to the user."""


@dataclass(frozen=True)
class Op:
    kind: str  # symlink | unlink | write | delete | mkdir | skip
    path: str  # display form (~/...)
    detail: str = ""


@dataclass(frozen=True)
class Plan:
    action: str
    ops: tuple[Op, ...]
    token: str


@dataclass(frozen=True)
class _Step:
    kind: str  # skip | mkdir | write | link | relink | unlink | rmfile | rmdir
    path: Path
    root: Path
    detail: str = ""
    target: str = ""
    data: bytes = b""
    mode: int = 0o640
    backup: bool = False


@dataclass
class _Built:
    steps: list[_Step] = field(default_factory=list)
    extra: list[Path] = field(default_factory=list)


_OP_KIND = {
    "skip": "skip",
    "mkdir": "mkdir",
    "write": "write",
    "link": "symlink",
    "relink": "write",
    "unlink": "unlink",
    "rmfile": "delete",
    "rmdir": "delete",
}


# ---------------------------------------------------------------- helpers


def _home(home: Path | None) -> Path:
    return Path(home) if home is not None else Path.home()


def _resolve(p: Path) -> Path:
    try:
        return p.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as e:
        raise MutError("A path could not be resolved.") from e


def _under(p: Path, base: Path) -> bool:
    return p == base or base in p.parents


def _disp(p: Path, home: Path) -> str:
    return display_path(p, home)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _str(params: dict, key: str, *, empty: bool = False) -> str:
    v = params.get(key)
    if not isinstance(v, str):
        raise MutError(f"Parameter '{key}' must be text.")
    if "\x00" in v:
        raise MutError(f"Parameter '{key}' contains a NUL byte.")
    if not empty and not v.strip():
        raise MutError(f"Parameter '{key}' is required.")
    return v


def _named(params: dict, key: str, rx: re.Pattern[str], what: str) -> str:
    v = _str(params, key)
    if not rx.fullmatch(v):
        raise MutError(f"Invalid {what} name.")
    return v


def _body(params: dict) -> bytes:
    text = _str(params, "body", empty=True)
    try:
        data = text.encode("utf-8")
    except UnicodeEncodeError as e:
        raise MutError("Body is not valid text.") from e
    if len(data) > MAX_BODY:
        raise MutError(f"Body is larger than {MAX_BODY // 1024} KB.")
    return data


def _confirm(params: dict, name: str) -> None:
    if params.get("confirm") != name:
        raise MutError("Type the exact name to confirm the delete.")


def _lstat(p: Path) -> os.stat_result | None:
    try:
        return os.lstat(p)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise MutError("A path could not be inspected.") from e


def _guard(path: Path, root: Path, home: Path) -> None:
    """Refuse unless path's parent resolves inside root, which resolves inside home."""
    if is_blocked(path, home) or is_blocked(path.parent, home):
        raise MutError(PROTECTED)
    rroot = _resolve(root)
    if not _under(rroot, _resolve(home)):
        raise MutError("The directory is outside the home directory.")
    if not _under(_resolve(path.parent), rroot):
        raise MutError(f"{_disp(path, home)} is outside the allowed directories.")


def _read_file(path: Path, home: Path, cap: int = MAX_READ) -> bytes:
    """Read a regular, non-symlink file under the guard."""
    if is_blocked(path, home):
        raise MutError(PROTECTED)
    st = _lstat(path)
    if st is None or not stat.S_ISREG(st.st_mode):
        raise MutError(f"{_disp(path, home)} is not a regular file.")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as f:
            data = f.read(cap + 1)
    except OSError as e:
        raise MutError(f"{_disp(path, home)} could not be read.") from e
    if len(data) > cap:
        raise MutError(f"{_disp(path, home)} is too large.")
    return data


def _state(p: Path, home: Path) -> str:
    st = _lstat(p)
    if st is None:
        return "missing"
    if stat.S_ISLNK(st.st_mode):
        try:
            return "link:" + os.readlink(p)
        except OSError:
            return "link:?"
    if stat.S_ISDIR(st.st_mode):
        return f"dir:{stat.S_IMODE(st.st_mode):o}"
    if stat.S_ISREG(st.st_mode):
        if is_blocked(p, home):
            return "blocked"
        try:
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as f:
                h = hashlib.sha256()
                while chunk := f.read(65536):
                    h.update(chunk)
        except OSError:
            return "unreadable"
        return f"file:{h.hexdigest()}:{stat.S_IMODE(st.st_mode):o}"
    return "other"


def _inventory(home: Path) -> Inventory:
    clear_cache()
    return get_inventory(home=home, run_version=False)


def _from_display(s: str, home: Path) -> Path:
    if not s.startswith("~/") or "*" in s:
        raise MutError("The inventory reports no usable path here.")
    return home / s[2:]


def _agent(inv: Inventory, name: str) -> AgentInfo:
    if not AGENT_RE.fullmatch(name):
        raise MutError("Unknown agent.")
    for a in inv.agents:
        if a.name == name:
            return a
    raise MutError("Unknown agent.")


def _lines(data: bytes) -> int:
    return len(data.decode("utf-8", "replace").splitlines())


def _link_points_to(link: Path, target: Path) -> bool:
    return _resolve(link) == _resolve(target)


# ---------------------------------------------------------------- writing


def _make_link(path: Path, target: str, replace: bool) -> None:
    if not replace:
        os.symlink(target, path)
        return
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    os.symlink(target, tmp)
    try:
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _backup(path: Path, home: Path, stamp: str) -> str:
    base = home / BACKUP_REL / stamp
    if is_blocked(base, home) or not _under(_resolve(base), _resolve(home)):
        raise MutError("The backup directory is not usable.")
    st = _lstat(path)
    if st is None:
        raise MutError("Nothing to back up.")
    if stat.S_ISLNK(st.st_mode):
        data = f"symlink -> {os.readlink(path)}\n".encode()
    else:
        data = _read_file(path, home, 8 * MAX_READ)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    flat = _disp(path, home).removeprefix("~/").replace("/", "__")
    dest = base / flat
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return _disp(dest, home)
