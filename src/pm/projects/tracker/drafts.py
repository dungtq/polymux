"""Branch gating for CHANGELOG / FEATURES drafts (docs/TRACKER-PLAN.md section 11). The only T3 module that
calls git.

A change made on the project's default branch (``main`` / ``master``, or the project setting "default
branch"), on a detached HEAD or by an import is landed at once. One made in a work tree on another branch is a
**draft** of that branch: ``sha`` empty, ``base_sha`` = HEAD when it was made. The post-commit hook
(``pmctl render --post-commit``, ``stamp``) stamps the branch's drafts with the new HEAD.

A render of a work tree (``View``) shows the landed rows, the drafts whose ``sha`` is an ancestor of the tree's
HEAD (or of MERGE_HEAD while a merge runs), and, in a tree on a non-default branch, that branch's drafts. In
the main tree on the default branch ``classify`` (run under the main file's flock before every change) turns
the drafts such a render shows into landed rows, and marks the drafts of a branch that no longer exists as
abandoned, so a branch deleted unmerged never reaches main and a branch re-created under the same name does
not revive them.

Restamping: a draft whose ``sha`` is not an ancestor of HEAD any more (amend, rebase) is stamped again, but
only when its ``base_sha`` is still an ancestor of HEAD, so drafts of an older branch of the same name stay
out. Limit: a draft made before the branch's first commit has the fork point as ``base_sha``; if its branch is
deleted and re-created before any main render marks it abandoned, the new branch's first commit stamps it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pm.core.config import Settings
from pm.git import ctl as gitctl
from pm.projects import settings as psettings
from pm.projects.tracker import schema

DEFAULT_BRANCHES = ("main", "master")
# tables with draft rows: (table, WHERE clause selecting one project's rows)
_DRAFT_TABLES = (("tk_change", "project=?"),
                 ("tk_feature_rev", "feature_id IN (SELECT id FROM tk_feature WHERE project=?)"))


@dataclass
class View:
    """Which drafts a render of one work tree shows."""
    branch: str = ""          # the tree's branch ("" = detached HEAD or not a git tree)
    main: bool = True         # on the default branch (or detached): drafts land here
    head: str = ""            # HEAD sha ("" = unknown)
    _anc: Callable[[str], bool] = field(default=lambda _sha: False, repr=False)
    _seen: dict = field(default_factory=dict, repr=False)

    def merged(self, sha: str) -> bool:
        """``sha`` is an ancestor of HEAD (or MERGE_HEAD); cached per sha."""
        if not sha:
            return False
        if sha not in self._seen:
            self._seen[sha] = self._anc(sha)
        return self._seen[sha]

    def shows(self, branch: str, sha: str) -> bool:
        """A draft of ``branch`` stamped with ``sha`` is part of this tree's render."""
        return (not self.main and branch == self.branch) or self.merged(sha)


MAIN = View()  # no git: landed rows only


def default_branches(settings: Settings, project: str) -> tuple[str, ...]:
    b = psettings.load(settings, project).default_branch
    return (b,) if b else DEFAULT_BRANCHES


def view_for(settings: Settings, project: str, root: str | Path | None) -> View:
    """The view of the work tree ``root`` (a git error or no root: ``MAIN``)."""
    if root is None:
        return View()
    try:
        branch = gitctl.current_branch(root) or ""
        head = gitctl.head_sha(root) or ""
        mh = gitctl.merge_head(root)
    except gitctl.GitError:
        return View()

    def anc(sha: str) -> bool:
        try:
            return gitctl.is_ancestor(root, sha, "HEAD") or (mh is not None and gitctl.is_ancestor(root, sha, "MERGE_HEAD"))
        except gitctl.GitError:
            return False
    main = not branch or branch in default_branches(settings, project)
    return View(branch=branch, main=main, head=head, _anc=anc)


def head_file(root: str | Path | None, name: str) -> str:
    """``git show HEAD:<name>`` of the work tree ``root``; "" when there is none."""
    if root is None:
        return ""
    try:
        return gitctl.head_file(root, name)
    except gitctl.GitError:
        return ""


def classify(view: View, root: str | Path | None, drafts: Sequence[sqlite3.Row]) -> tuple[list[int], list[int]]:
    """(ids to land, ids to abandon) of ``drafts`` (rows with ``id``, ``branch``, ``sha``) for the main
    tree's ``view``; nothing unless ``view.main`` in a git tree with a HEAD. Git calls only: run it before
    the write transaction."""
    if not view.main or root is None or not view.head:
        return [], []
    land, gone = [], []
    exists: dict[str, bool] = {}
    for d in drafts:
        if view.merged(d["sha"]):
            land.append(d["id"])
            continue
        b = d["branch"]
        if b not in exists:
            try:
                exists[b] = gitctl.branch_exists(root, b)
            except gitctl.GitError:
                exists[b] = True  # unknown: keep it
        if not exists[b]:
            gone.append(d["id"])
    return land, gone


def stamp(settings: Settings, project: str, root: str | Path, *,
          audit: Callable[[str, dict], None] | None = None) -> int:
    """Post-commit: stamp the drafts of ``root``'s branch with its new HEAD (see the module doc); ``audit``
    runs before the commit (fail closed). The number of rows stamped."""
    view = view_for(settings, project, root)
    if view.main or not view.head:
        return 0
    con = schema.connect(settings)
    try:
        todo: list[tuple[str, int]] = []
        for table, where in _DRAFT_TABLES:
            for r in con.execute(f"SELECT id, sha, base_sha FROM {table} WHERE state='draft' AND branch=? AND "
                                 + where, (view.branch, project)):
                if r["sha"] == view.head or (r["sha"] and view.merged(r["sha"])):
                    continue
                if r["base_sha"] and not view.merged(r["base_sha"]):
                    continue  # made on an older branch of the same name
                todo.append((table, r["id"]))
        if todo:
            with schema.tx(con):
                for table, rid in todo:
                    con.execute(f"UPDATE {table} SET sha=? WHERE id=?", (view.head, rid))
                if audit is not None:
                    audit("tracker_stamp", {"project": project, "file": "drafts", "rows": len(todo),
                                            "sha": view.head})
    finally:
        con.close()
    return len(todo)
