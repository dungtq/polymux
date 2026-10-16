"""``pmctl merge-check``: before a branch is merged into BASE, what else touches its files.

``files`` = ``git diff --name-only BASE...HEAD`` plus this work tree's uncommitted changes. Reported: whether
BASE moved on (commits not in HEAD: rebase / merge it first; a warning, never an overlap), the other agents'
active, non-stale claims covering any of ``files`` (own claims: same agent or same work tree, are left out), and
the repo's other work trees whose uncommitted changes or commits not in BASE touch any of ``files``.
The git logic of claims lives here too (``where``: the cwd's work tree and branch). Git errors come out as
``Invalid``; all git calls go through ``pm.git.ops`` / ``pm.git.ctl`` (argv, timeout, user values checked).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pm.core.config import Settings
from pm.core.errors import Invalid, NotFound
from pm.git import ctl as gitctl
from pm.git import ops
from pm.projects.tracker import claims, drafts

CLEAR, OVERLAP, ERROR = 0, 1, 2


@dataclass
class Report:
    base: str
    branch: str
    files: list[str]
    behind: int
    claims: list[claims.Claim] = field(default_factory=list)
    worktrees: list[dict] = field(default_factory=list)

    @property
    def base_moved(self) -> bool:
        return self.behind > 0

    @property
    def exit_code(self) -> int:
        return OVERLAP if self.claims or self.worktrees else CLEAR

    def to_dict(self) -> dict:
        return {"base": self.base, "branch": self.branch, "files": self.files, "base_moved": self.base_moved,
                "behind": self.behind, "claims": [c.to_dict() for c in self.claims], "worktrees": self.worktrees}


def where(cwd: str | Path) -> tuple[str, str]:
    """(branch, work tree root) of ``cwd``; ("", "") off git, branch "" when detached or unborn."""
    try:
        top = ops.top_of(cwd)
        return gitctl.current_branch(top) or "", str(top)
    except (gitctl.GitError, NotFound):
        return "", ""


def default_base(settings: Settings, project: str, top: Path) -> str:
    """The first of the project's default branches (``drafts.default_branches``) that exists here."""
    for b in drafts.default_branches(settings, project):
        try:
            if ops.has_ref(top, f"refs/heads/{ops.check_base(b)}"):
                return b
        except Invalid:
            continue
    raise Invalid("no main branch here (use --base BRANCH)")


def _dirty(top: Path) -> set[str]:
    """Uncommitted paths of the work tree ``top`` (staged, unstaged, untracked; both names of a rename)."""
    st = ops.status_at(top)
    if st.total > len(st.files):
        st = ops.status_at(top, limit=st.total)
    return {p for f in st.files for p in (f.path, f.orig) if p}


def _changed(top: Path, base: str) -> set[str]:
    return set(ops.files_since(top, base)) | _dirty(top)


def _other_trees(top: Path, base: str, files: set[str]) -> list[dict]:
    """The repo's other work trees (``git worktree list``) whose changes touch ``files``; one that cannot be
    read (gone, bare, locked away) is skipped."""
    out = []
    for wt in gitctl.worktrees(top):
        path = wt.get("worktree", "")
        if not path or "bare" in wt or "prunable" in wt or not Path(path).is_dir():
            continue
        try:
            other = ops.top_of(path)
            if other == top:
                continue
            hit = sorted(_changed(other, base) & files)
        except (gitctl.GitError, NotFound, Invalid):
            continue
        if hit:
            out.append({"path": str(other), "branch": wt.get("branch", ""), "files": hit})
    return out


def _same_tree(a: str, top: Path) -> bool:
    try:
        return bool(a) and Path(a).resolve() == top
    except OSError:
        return False


def check(settings: Settings, project: str, cwd: str | Path, *, agent: str, base: str | None = None,
          live: frozenset[str] | None = None) -> Report:
    """The merge check of the work tree at ``cwd`` against ``base`` (default: ``default_base``)."""
    try:
        top = ops.top_of(cwd)
        base = ops.check_base(base) if base else default_base(settings, project, top)
        if not ops.has_ref(top, base):
            raise NotFound(f"no such branch or ref: {base}")
        files = _changed(top, base)
        _ahead, behind = ops.ahead_behind(top, base)
        branch = gitctl.current_branch(top) or ""
        trees = _other_trees(top, base, files)
    except gitctl.GitError as e:
        raise Invalid(f"git: {e}") from None
    hits = [c for c in claims.list_claims(settings, project, live=live)
            if not c.stale and c.agent != agent and not _same_tree(c.worktree, top) and claims.touches(c, files)]
    return Report(base=base, branch=branch, files=sorted(files), behind=behind, claims=hits, worktrees=trees)
