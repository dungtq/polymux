"""Project discovery, TODO.md progress (read-only), cwd mapping, session start directories."""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

from pm.core import filelock
from pm.core.config import Settings
from pm.core.errors import Denied, Invalid
from pm.core.fsutil import JsonStore, atomic_write, safe_join
from pm.core.models import Project, TodoProgress

SKIP_DIRS = {"node_modules"}
ITEM = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.*\S)\s*$")


def discover(settings: Settings) -> list[Project]:
    out: dict[str, Project] = {}
    root = Path(settings.dev_root)
    if root.is_dir():
        for c in sorted(root.iterdir()):
            if c.name.startswith(".") or c.name in SKIP_DIRS or not c.is_dir():
                continue
            out[str(c)] = Project(name=c.name, path=str(c))
    for e in settings.extra_projects:
        e = Path(e)
        if e.is_dir() and str(e) not in out:
            out[str(e)] = Project(name=e.name, path=str(e))
    return list(out.values())


def parse_todo(path: str | Path) -> TodoProgress:
    f = Path(path) / "TODO.md"
    try:
        text = f.read_text(errors="replace")
    except OSError:
        return TodoProgress()
    done, open_items = 0, []
    for ln in text.splitlines():
        m = ITEM.match(ln)
        if not m:
            continue
        if m.group(1) == " ":
            open_items.append(m.group(2))
        else:
            done += 1
    return TodoProgress(done, done + len(open_items), tuple(open_items))


HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def todo_items(text: str) -> list[dict]:
    """Checklist items of a TODO.md: line index, done flag, raw text and nearest heading.

    Same item grammar as ``parse_todo`` (fenced code blocks are not special-cased there either,
    so numbers agree).
    """
    out: list[dict] = []
    head = ""
    for i, ln in enumerate(text.splitlines()):
        h = HEADING.match(ln)
        if h:
            head = h.group(1)
            continue
        m = ITEM.match(ln)
        if m:
            out.append({"line": i, "done": m.group(1) != " ", "text": m.group(2), "heading": head})
    return out


def is_linked_worktree(path: str | Path) -> bool:
    """True when ``path/.git`` is a file with a ``gitdir:`` pointer into ``<main>/.git/worktrees/<n>``.

    Cheap file check, no git call. Submodules (gitdir under ``modules``) are not worktrees."""
    g = Path(path) / ".git"
    try:
        if not g.is_file():
            return False
        line = g.read_text().strip()
    except OSError:
        return False
    if not line.startswith("gitdir:"):
        return False
    return Path(line[7:].strip()).parent.name == "worktrees"


def _worktree_main(cwd: Path) -> Path | None:
    """If cwd is inside a linked git worktree, return the main work tree path."""
    d = cwd
    for cand in (d, *d.parents):
        g = cand / ".git"
        if g.is_file():
            try:
                line = g.read_text().strip()
            except OSError:
                return None
            if line.startswith("gitdir:"):
                gd = Path(line[7:].strip())
                if not gd.is_absolute():
                    gd = cand / gd
                gd = gd.resolve()
                # <main>/.git/worktrees/<name>
                if gd.parent.name == "worktrees":
                    return gd.parent.parent.parent
            return None
        if g.is_dir():
            return None
    return None


@lru_cache(maxsize=8)
def _resolved(paths: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    """Resolve project paths once per distinct project set (resolve() is slow)."""
    return tuple((str(Path(x).resolve()), i) for i, x in enumerate(paths))


def _longest(cwd: Path, projects: list[Project]) -> Project | None:
    c = str(cwd)
    best, blen = None, -1
    for pp, i in _resolved(tuple(p.path for p in projects)):
        if c == pp or c.startswith(pp.rstrip(os.sep) + os.sep):
            n = pp.count(os.sep)
            if n > blen:
                best, blen = projects[i], n
    return best


def project_for_cwd(cwd: str | Path, projects: list[Project]) -> Project | None:
    c = Path(cwd).resolve()
    hit = _longest(c, projects)
    wt = _worktree_main(c)
    if wt is not None:
        main = _longest(wt.resolve(), projects)
        if main is not None and (hit is None or hit.path != main.path):
            return main
    return hit


MAX_CWD = 4096


def start_dir(settings: Settings, raw: str) -> str | None:
    """The optional start directory of a new tmux session: None when empty, else the real path of an existing
    directory inside ``dev_root`` or an extra project (``safe_join`` per root: no symlink escape). Invalid /
    Denied otherwise."""
    raw = raw.strip()
    if not raw:
        return None
    path = os.path.normpath(os.path.expanduser(raw))
    if len(raw) > MAX_CWD or "\0" in raw or not os.path.isabs(path):
        raise Invalid("cwd must be an absolute path")
    for root in (settings.dev_root, *settings.extra_projects):
        root = os.path.normpath(os.fspath(root))
        if path == root or path.startswith(root.rstrip("/") + "/"):
            joined = safe_join(root, os.path.relpath(path, root), allow_symlink_leaf=True)
            if joined.is_dir():
                return os.path.realpath(joined)
    raise Denied("cwd must be an existing directory under the projects root")


# ---------------------------------------------------------------- pinned favourites
def _pins_file(settings: Settings) -> Path:
    return Path(settings.state_dir) / "pins.json"


def load_pins(settings: Settings) -> set[str]:
    data = JsonStore(_pins_file(settings), list, validate=lambda d: isinstance(d, list)).load()
    return {x for x in data if isinstance(x, str)}


def set_pin(settings: Settings, name: str, pinned: bool) -> set[str]:
    """Persist the pin set (atomic replace, under the same flock family as the other state)."""
    f = _pins_file(settings)
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        with filelock.locked(f.parent / "pins.lock"):
            pins = load_pins(settings)
            pins.add(name) if pinned else pins.discard(name)
            atomic_write(f, json.dumps(sorted(pins)), mode=0o600)
    except filelock.LockTimeout as e:
        raise OSError("pins are busy") from e   # the route maps OSError to "could not save pins"
    return pins


# ------------------------------------------------------------------ new project
NEW_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}$")


class NewProjectError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.msg, self.status = msg, status


def create_project(settings: Settings, name: str, git_init: bool = False,
                   readme: bool = False) -> Path:
    """Create ``<dev_root>/<name>``. Strict name, never an existing path, confined to the root."""
    from pm.git import ctl as gitctl

    if not isinstance(name, str) or not NEW_NAME.fullmatch(name) or name in SKIP_DIRS:
        raise NewProjectError("name: letters, digits, . _ - only (max 64, no leading dot)")
    root = Path(settings.dev_root).resolve()
    if not root.is_dir():
        raise NewProjectError("projects root is missing", 500)
    target = root / name
    if target.parent != root or os.path.lexists(target):
        raise NewProjectError("a file or directory with that name already exists", 409)
    try:
        target.mkdir()
    except FileExistsError:
        raise NewProjectError("a file or directory with that name already exists", 409) from None
    except OSError:
        raise NewProjectError("could not create the directory", 500) from None
    try:
        if readme:
            (target / "README.md").write_text(f"# {name}\n", encoding="utf-8")
        if git_init:
            gitctl.init_repo(target)
    except (OSError, gitctl.GitError):
        raise NewProjectError("could not initialise the project", 502) from None
    return target
