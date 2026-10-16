"""Two-way sync of the agent memory (``memory.py``) with Claude Code's memory dirs (file format: ``memfile.py``),
and the plain render for other agents.

Claude Code keeps one dir per working directory: ``<claude_dir>/projects/<encode_cwd(cwd)>/memory`` with one file
per memory (YAML frontmatter ``name``, ``description``, ``metadata: {type, ...}``, then the Markdown body) and an
index ``MEMORY.md`` (``- [Title](file.md) — hook``). A project syncs the dir of its main work tree and of every
linked work tree (``gitglue.worktree_paths``), main first, so the first sync imports the main tree's memories.

Per dir, under its flock (``tk_memory_sync`` = file name and sha256 of each memory file at the last sync):
import a file whose hash changed (new memory, or update when pm.db did not change it since; both changed: pm.db
wins and the file is kept as ``<stem>-conflict-<ts>.md``), never delete on import; then render every memory of
the project into the dir (a file that already says the same is left byte for byte), drop the file of a memory
removed from pm.db only when it is unchanged since the last sync, and rewrite ``MEMORY.md``. Global memories are
not written into Claude's dirs. ``<memory_dir>/<project>.md`` gets the project's and the global memories in
full. A change taken from a later work tree's dir is rendered into the dirs before it in a second pass.
Files are 0600, dirs 0700; symlinked dirs or files are refused, nothing is written inside a work tree.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from pm.core import filelock
from pm.core.config import Settings
from pm.core.errors import Conflict, Denied, NotFound, PmError
from pm.core.fsutil import atomic_write, encode_cwd, safe_join
from pm.projects import projects
from pm.projects.tracker import gitglue, memfile, memory, schema
from pm.projects.tracker.memfile import INDEX, MAX_FILE, MAX_INDEX, read_file, sha
from pm.projects.tracker.memory import Memory
from pm.projects.tracker.store import Audit

MAX_SUMMARY = 64 * 1024 * 1024


# ------------------------------------------------------------------ dirs
@dataclass
class DirReport:
    dir: str
    imported: int = 0
    updated: int = 0
    written: int = 0
    removed: int = 0
    conflicts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    kept: set[str] = field(default_factory=set)  # files whose text this sync took into pm.db or a conflict copy

    def merge(self, other: DirReport) -> None:
        for k in ("imported", "updated", "written", "removed"):
            setattr(self, k, getattr(self, k) + getattr(other, k))
        self.conflicts += other.conflicts
        self.notes += [n for n in other.notes if n not in self.notes]

    def to_dict(self) -> dict:
        return {"dir": self.dir, "imported": self.imported, "updated": self.updated, "written": self.written,
                "removed": self.removed, "conflicts": self.conflicts, "notes": self.notes}


def claude_dir_for(settings: Settings, tree: Path) -> Path:
    return Path(settings.claude_dir) / "projects" / encode_cwd(str(tree)) / "memory"


def _ready(settings: Settings, d: Path, trees: list[Path], *, create: bool) -> bool:
    """Check ``d`` (no symlink below ``claude_dir``, not inside a work tree); make it (0700) when ``create``.
    False when it does not exist (and was not made)."""
    real = Path(os.path.realpath(d))
    if any(real.is_relative_to(os.path.realpath(t)) for t in trees):
        raise Denied(f"{d} is inside the project; refused")
    if not Path(settings.claude_dir).is_dir():
        raise NotFound(f"no Claude dir {settings.claude_dir}")
    cur = Path(settings.claude_dir)
    for part in d.relative_to(settings.claude_dir).parts:
        cur = cur / part
        if cur.is_symlink() or (cur.exists() and not cur.is_dir()):
            raise Denied(f"{cur} is a symlink or not a dir; refused")
        if not cur.exists():
            if not create:
                return False
            cur.mkdir(mode=0o700)
    return True


def _conflict_copy(d: Path, fname: str, data: bytes) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%S")
    for i in range(100):
        name = f"{Path(fname).stem}-conflict-{stamp}{f'-{i}' if i else ''}.md"
        if not os.path.lexists(d / name):
            atomic_write(safe_join(d, name), data, mode=0o600, fsync=False)
            return name
    raise Conflict(f"{fname}: too many conflict copies")


def import_dir(settings: Settings, project: str, d: Path, rows: dict, rep: DirReport, *, actor: str,
               audit: Audit) -> None:
    """Take the files of ``d`` that changed since the last sync into pm.db (see the module doc)."""
    index = memfile.read_index(d)
    order = {f: i for i, f in enumerate(index)}  # the index's order first: ids follow it, so does the next render
    files = sorted(memfile.scan_dir(d, rep.notes), key=lambda f: (order.get(f[0], len(order)), f[0]))
    by_file = {r["file"]: r for r in rows.values()}
    seen: set[str] = set()
    for fname, data in files:
        row = by_file.get(fname)
        if row is not None and row["hash"] == sha(data):
            seen.add(row["name"])
            continue  # unchanged since the last sync
        try:
            p = memfile.parse(data, fname)
        except PmError as e:
            rep.notes.append(f"{fname}: skipped ({e})")
            continue
        if p.name in seen:
            rep.notes.append(f"{fname}: skipped (another file has the name {p.name!r})")
            continue
        seen.add(p.name)
        title, hook = index.get(fname, ("", ""))
        kw = {"type": p.type, "description": p.description, "body": p.body, "meta": p.meta, "actor": actor,
              "audit": audit, "action": "tracker_memory_import", "collapse": False}
        mem = memory.find(settings, project, p.name)
        try:
            if mem is None:
                memory.add(settings, project, p.name, title=title, hook=hook, source=fname, **kw)
                rep.imported += 1
                rep.kept.add(fname)
                rep.notes += [f"{fname}: edited after `memory rm`, imported again"] if row is not None else []
                continue
            if memfile.same(p, mem):
                continue
            if (r := rows.get(p.name)) is not None and mem.updated_at <= r["synced_at"]:
                memory.update(settings, project, p.name, title=title or None, hook=hook or None, **kw)
                rep.updated += 1
                rep.kept.add(fname)
                continue
        except PmError as e:
            rep.notes.append(f"{fname}: skipped ({e})")
            continue
        try:
            copy = _conflict_copy(d, fname, data)
        except OSError as e:
            rep.notes.append(f"{fname}: changed in pm.db and on disk; no copy could be kept ({e.strerror or e})")
            continue
        rep.kept.add(fname)
        rep.conflicts.append(copy)
        rep.notes.append(f"{fname}: changed in pm.db and on disk; pm.db kept, the file's copy is {copy}")


def render_dir(settings: Settings, project: str, d: Path, rows: dict, rep: DirReport) -> dict[str, tuple[str, str]]:
    """Write the project's memories and ``MEMORY.md`` into ``d``; {name: (file, hash)} for ``tk_memory_sync``."""
    mems = memory.list_memories(settings, project)
    out: dict[str, tuple[str, str]] = {}
    used: set[str] = set()
    for m in mems:
        fname = rows[m.name]["file"] if m.name in rows and rows[m.name]["file"] else m.file
        fname = f"{m.name}.md" if fname in used else fname  # a file's `name:` was changed: one file per memory
        used.add(fname)
        try:
            path = safe_join(d, fname)
            cur = read_file(path, MAX_FILE)
        except PmError as e:
            _not_written(rep, out, rows, m.name, fname, e)
            continue
        text = memfile.render(m)
        if cur is not None and (cur == text.encode() or _same_bytes(cur, fname, m)):
            out[m.name] = (fname, sha(cur))
            continue
        try:
            if cur is not None and fname not in rep.kept and (m.name not in rows or sha(cur) != rows[m.name]["hash"]):
                name = _conflict_copy(d, fname, cur)  # an edit the import could not take (e.g. broken frontmatter)
                rep.conflicts.append(name)
                rep.notes.append(f"{fname}: could not be imported; pm.db kept, the file's copy is {name}")
            atomic_write(path, text, mode=0o600, fsync=False)
        except OSError as e:  # no copy: the file is left as it is
            _not_written(rep, out, rows, m.name, fname, e)
            continue
        out[m.name] = (fname, sha(text))
        rep.written += 1
    names = {m.name for m in mems}
    for name, r in rows.items():  # removed from pm.db: drop the file unless edited since the last sync
        if name in names or not r["file"] or r["file"] in used:
            continue
        try:
            path = safe_join(d, r["file"])
            cur = read_file(path, MAX_FILE)
        except PmError:
            continue
        if cur is not None and sha(cur) == r["hash"]:
            try:
                path.unlink()
            except OSError as e:  # the row stays: the next sync tries again
                out[name] = (r["file"], r["hash"])
                rep.notes.append(f"{r['file']}: not removed ({e.strerror or e})")
                continue
            rep.removed += 1
    index = memfile.render_index(mems)
    try:
        if (read_file(d / INDEX, MAX_INDEX) or b"").decode("utf-8", "replace") != index and (mems or rows):
            atomic_write(d / INDEX, index, mode=0o600, fsync=False)
    except (PmError, OSError) as e:
        rep.notes.append(f"{INDEX}: not written ({getattr(e, 'strerror', None) or e})")
    return out


def _not_written(rep: DirReport, out: dict, rows: dict, name: str, fname: str, e: Exception) -> None:
    """A memory file that could not be written: a note, and its last synced state is kept."""
    rep.notes.append(f"{fname}: not written ({getattr(e, 'strerror', None) or e})")
    if name in rows:
        out[name] = (rows[name]["file"], rows[name]["hash"])


def _same_bytes(data: bytes, fname: str, m: Memory) -> bool:
    try:
        return memfile.same(memfile.parse(data, fname), m)
    except PmError:
        return False


def _save_rows(settings: Settings, project: str, d: Path, new: dict, rep: DirReport, audit: Audit) -> None:
    con = schema.connect(settings)
    try:
        with schema.tx(con):
            now = time.time()
            con.execute("DELETE FROM tk_memory_sync WHERE project=? AND dir=?", (project, str(d)))
            con.executemany("INSERT INTO tk_memory_sync (project, dir, name, file, hash, synced_at)"
                            " VALUES (?,?,?,?,?,?)", [(project, str(d), n, f, h, now) for n, (f, h) in new.items()])
            if audit is not None:
                audit("tracker_memory_sync", {"project": project, "file": "memory", "dir": str(d)[:300],
                                              **{k: v for k, v in rep.to_dict().items() if isinstance(v, int)},
                                              "conflicts": len(rep.conflicts)})
    finally:
        con.close()


def sync_dir(settings: Settings, project: str, d: Path, trees: list[Path], *, actor: str, audit: Audit) -> DirReport:
    rep = DirReport(str(d))
    try:
        has = bool(memory.list_memories(settings, project))
        if not _ready(settings, d, trees, create=has):
            return rep
        with filelock.locked(filelock.lock_path(settings.state_dir, d)):
            con = schema.connect(settings)
            try:
                rows = {r["name"]: r for r in con.execute(
                    "SELECT * FROM tk_memory_sync WHERE project=? AND dir=?", (project, str(d)))}
            finally:
                con.close()
            _ready(settings, d, trees, create=True)  # again under the lock: swapped for a symlink meanwhile?
            import_dir(settings, project, d, rows, rep, actor=actor, audit=audit)
            _ready(settings, d, trees, create=True)
            _save_rows(settings, project, d, render_dir(settings, project, d, rows, rep), rep, audit)
    except filelock.LockTimeout:
        raise Conflict(f"{d} is busy (another sync); try again") from None
    except (Denied, NotFound) as e:
        rep.notes.append(str(e))
    return rep


# ------------------------------------------------------------------ plain render + projects
def summary_text(project: str, mems: list[Memory]) -> str:
    head = (f"# Agent memory: {project}\n\nRendered from pm.db by `pmctl memory sync`; change it with"
            " `pmctl memory set NAME ...` (this file is overwritten).\n")
    return head + "".join(f"\n## {m.name} ({m.type}{', global' if m.scope == 'global' else ''})\n\n{m.description}\n"
                          + (f"\n{m.body_md}\n" if m.body_md else "") for m in mems)


def render_summary(settings: Settings, project: str) -> Path | None:
    """``<memory_dir>/<project>.md`` (0600, dir 0700); None when there is nothing to write."""
    mems = memory.list_memories(settings, project, include_global=True)
    path = safe_join(settings.memory_dir, f"{project}.md")
    if not mems and not os.path.lexists(path):
        return None
    Path(settings.memory_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
    text = summary_text(project, mems)
    if read_file(path, MAX_SUMMARY) != text.encode():
        atomic_write(path, text, mode=0o600, fsync=False)
    return path


def refresh_summaries(settings: Settings) -> list[Path]:
    """Re-render every project's summary (after a global memory changed)."""
    d = Path(settings.memory_dir)
    names = set(memory.projects_with_memories(settings))
    names |= {p.stem for p in d.glob("*.md")} if d.is_dir() and not d.is_symlink() else set()
    return [p for n in sorted(names) if (p := render_summary(settings, n)) is not None]


def sync_project(settings: Settings, project: str, root: Path, *, actor: str, audit: Audit = None) -> dict:
    """Sync every memory dir of the project (main work tree first), then its summary."""
    trees = gitglue.worktree_paths(root)
    reps = [sync_dir(settings, project, claude_dir_for(settings, t), trees, actor=actor, audit=audit) for t in trees]
    if any(r.imported or r.updated for r in reps[1:]):  # a work tree's change: render it into the dirs before it
        for r, t in zip(reps, trees):
            r.merge(sync_dir(settings, project, claude_dir_for(settings, t), trees, actor=actor, audit=audit))
    notes = []
    try:
        summary = render_summary(settings, project)
    except (PmError, OSError) as e:
        summary = None
        notes.append(f"summary not written ({getattr(e, 'strerror', None) or e})")
    return {"project": project, "dirs": [r.to_dict() for r in reps], "summary": str(summary) if summary else None,
            "notes": notes}


def sync_all(settings: Settings, *, actor: str, audit: Audit = None) -> list[dict]:
    """Every project that has memories in pm.db or a Claude memory dir for its main work tree."""
    have = set(memory.projects_with_memories(settings))
    out = [sync_project(settings, p.name, Path(p.path), actor=actor, audit=audit)
           for p in projects.discover(settings)
           if p.name in have or claude_dir_for(settings, Path(p.path)).is_dir()]
    refresh_summaries(settings)
    return out
