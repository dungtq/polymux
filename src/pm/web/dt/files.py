"""The dt project view's Files tab listing (PRD.md §6.4, LAYOUT.md §4 step 5): the rows of a directory in the
shared grid table (Name · Git · Size · Modified · Created), built from the core's ``projfiles.file_view`` dict that
``GET /p/{name}/files`` hands the theme's ``_project_files.html`` (``tree``, ``base``, ``project_name``).

``dt_files(tree, base, name)`` is a Jinja global (``template_globals()``); it reads only that dict (no IO). The
project tree on the left and the file view (Markdown, code, diff) stay classic markup in their own ``.x-cl`` boxes
(README A5); only the listing and the breadcrumb are dt parts.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import quote

from pm.web.dt.shell import age

# git status letter (core status_map; "C" = committed and clean) -> (mark, title, tone)
GIT = {"C": ("✓", "committed, clean", "ok"), "M": ("M", "modified, not committed", "warn"),
       "A": ("A", "added, not committed", "warn"), "D": ("D", "deleted, not committed", "bad"),
       "?": ("U", "untracked", "warn"), "!": ("·", "ignored", "")}
IMG = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico")
KIND_WORD = {"link": "symlink, not followed", "gone": "deleted on disk", "special": "special file"}


@dataclass(frozen=True)
class Crumb:
    label: str
    href: str  # "" = the current file (no link)


@dataclass(frozen=True)
class FileRow:
    id: str  # path relative to the project (data-row-id)
    name: str
    kind: str  # dir | file | link | gone | special | up (the ".." row)
    href: str  # in-place link (data-pj-in); "" = not openable
    icon: str  # sprite id
    git: str  # mark ("" = not in a repo)
    git_title: str
    git_cls: str
    size: str
    modified: str
    mtime: int | None
    created: str
    title: str  # the row's tooltip


@dataclass(frozen=True)
class Node:
    name: str
    href: str
    icon: str
    dir: bool
    cur: bool  # the directory or file shown (aria-current)
    children: list[Node]  # only the directories on the path to the current one are open


@dataclass(frozen=True)
class Listing:
    crumbs: list[Crumb]
    rows: list[FileRow]
    more: int  # entries not listed (the core's cap)
    path: str  # current directory, "" = the project root
    tree: list[Node]  # the project tree (left column; hidden on phones)
    root_cur: bool  # the project root is the one shown
    size: str = ""  # a file's size ("28 B")


def hsize(n: int) -> str:
    """Bytes as the classic listing shows them: 28 B, 1.2 KB, 3.4 MB, 1.0 GB."""
    for unit, k in (("B", 1), ("KB", 1024), ("MB", 1024 ** 2)):
        if n < k * 1024:
            return f"{n} B" if unit == "B" else f"{n / k:.1f} {unit}"
    return f"{n / 1024 ** 3:.1f} GB"


def _when(ts: float | None, now: float) -> str:
    return age(ts, now) if ts else "–"


def _icon(name: str, kind: str) -> str:
    return "folder" if kind == "dir" else "img" if name.lower().endswith(IMG) else "link" if kind == "link" else "file"


def nodes(items, fb: str) -> list[Node]:
    return [Node(str(n.get("name") or ""), fb + quote(str(n.get("rel") or ""), safe=""),
                 _icon(str(n.get("name") or ""), str(n.get("kind") or "")), n.get("kind") == "dir", bool(n.get("cur")),
                 nodes(n.get("children") or (), fb)) for n in items or ()]


def _row(e: dict, parts: list[str], gs: dict, fb: str, now: float) -> FileRow:
    name, kind = str(e.get("name") or ""), str(e.get("kind") or "file")
    rel = "/".join([*parts, name])
    g = gs.get(name) if gs else None
    mark, gtitle, gcls = GIT.get(g, (g or "", "changed", "warn")) if g else ("", "", "")
    icon = _icon(name, kind)
    mt, bt = e.get("mtime") or 0.0, e.get("btime") or 0.0
    word = KIND_WORD.get(kind, "")
    return FileRow(
        id=rel, name=name + ("/" if kind == "dir" else ""), kind=kind,
        href=fb + quote(rel, safe="") if kind in ("dir", "file") else "", icon=icon, git=mark, git_title=gtitle,
        git_cls=gcls, size=hsize(int(e.get("size") or 0)) if kind == "file" else "", modified=_when(mt, now),
        mtime=int(mt) if mt else None, created=_when(bt, now),
        title=rel + (f" · {word}" if word else "") + (f" · {gtitle}" if gtitle else ""))


def listing(tree: dict, base: str, project_name: str, now: float | None = None) -> Listing:
    """The breadcrumb (project / dir / ... ; the current file has no link) and, for a directory, its rows: ".." first
    below the root, then the core's entries in its order."""
    now = time.time() if now is None else now
    parts = [str(p) for p in tree.get("parts") or ()]
    fb = base + "/files?path="
    crumbs = [Crumb(project_name, base + "/files")]
    for i, p in enumerate(parts):
        last = i == len(parts) - 1 and tree.get("kind") == "file"
        crumbs.append(Crumb(p, "" if last else fb + quote("/".join(parts[: i + 1]), safe="")))
    rows: list[FileRow] = []
    if tree.get("kind") == "dir":
        if parts:
            up = "/".join(parts[:-1])
            rows.append(FileRow(id="..", name="..", kind="up", href=fb + quote(up, safe=""), icon="up", git="",
                                git_title="", git_cls="", size="", modified="", mtime=None, created="",
                                title="Up to " + (up or project_name)))
        gs = tree.get("gs") or {}
        rows += [_row(e, parts, gs, fb, now) for e in tree.get("entries") or ()]
    entries = len(tree.get("entries") or ())
    return Listing(crumbs, rows, max(0, int(tree.get("total") or entries) - entries), "/".join(parts),
                   nodes(tree.get("ftree"), fb), not parts,
                   hsize(int(tree.get("size") or 0)) if tree.get("kind") == "file" else "")


def dt_files(tree: dict, base: str, project_name: str) -> Listing:
    return listing(tree or {}, base or "", project_name or "")
