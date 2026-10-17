"""Git tab operations: detailed status, stage / unstage / discard, commit, stash, branches, checkout, merge and
branch delete. ``pm.git.plans`` says what each of them will do before it runs.

Every call goes through ``run`` = ``ctl.run_git`` with ``ctl.OPS_ALLOWED`` (``ctl.check_listed``: ``reset`` only
as ``reset -q -- <paths>``, no force / amend / hard / no-verify flags before ``--``), user values after ``--``
and read as literal paths (``GIT_LITERAL_PATHSPECS``). Paths must be changed files that ``status_detail`` reports
and stay inside the repo. Pushing stays ``pm.git.remote.push`` (the configured push remote only, never forced);
nothing here pushes, amends, rebases or resets a branch. ``remote`` (pm.toml ``push_remote``) is passed by the
caller: a branch without an upstream is compared with ``<remote>/<branch>``.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pm.core.errors import Conflict, Denied, Invalid, NotFound
from pm.core.fsutil import safe_join
from pm.git import ctl
from pm.git import remote as gitremote
from pm.git.ctl import GitError

MAX_PATHS = 500
MAX_MESSAGE = 20000
LIST_CAP = 2000


class MergeConflict(Conflict):
    """A merge or stash pop stopped on conflicting files (``files``)."""

    def __init__(self, msg: str, files: tuple[str, ...] = ()) -> None:
        super().__init__(msg)
        self.files = files


@dataclass(frozen=True)
class FileChange:
    path: str
    orig: str  # rename/copy source ("" otherwise)
    xy: str  # porcelain XY: "M.", ".M", "MM", "A.", "R.", "??", "UU", ...
    staged: bool
    unstaged: bool
    untracked: bool
    conflict: bool


@dataclass(frozen=True)
class StatusDetail:
    branch: str | None  # None when detached
    detached: bool
    head: str  # short sha; "" on an unborn branch
    upstream: str  # "" when none (<remote>/<branch> is used when it exists)
    ahead: int
    behind: int
    merging: bool
    stashes: int
    files: tuple[FileChange, ...]
    total: int  # all changed files (``files`` is capped)

    def counts(self) -> dict[str, int]:
        f = self.files
        return {"staged": sum(x.staged for x in f), "unstaged": sum(x.unstaged for x in f),
                "untracked": sum(x.untracked for x in f), "conflicts": sum(x.conflict for x in f)}

    @property
    def dirty(self) -> bool:
        """Tracked changes (staged, unstaged or conflicted); untracked files do not count."""
        return any(not x.untracked for x in self.files)

    def view(self) -> dict:
        return {**asdict(self), "counts": self.counts()}


@dataclass(frozen=True)
class Branch:
    name: str
    current: bool
    upstream: str
    ahead: int
    behind: int
    gone: bool  # the upstream branch was deleted
    sha: str
    ts: float
    subject: str


@dataclass(frozen=True)
class Stash:
    index: int
    ref: str  # stash@{n}
    ts: float
    subject: str


@dataclass(frozen=True)
class Outcome:
    text: str
    clash: tuple[str, ...] = field(default=())  # conflicted files after a stash pop (the stash is kept)
    sha: str = ""


# ------------------------------------------------------------------ runner
def run(top: Path, args: list[str], *, check: bool = True, timeout: float = ctl.TIMEOUT_S
        ) -> subprocess.CompletedProcess[str]:
    """One ``ctl.OPS_ALLOWED`` git call in ``top`` (a ``top_of`` result); no editor, literal pathspecs after ``--``
    (not for stash: it breaks its clean step). A failure raises GitError with ``short`` of git's stderr."""
    env = {"GIT_EDITOR": "true", "GIT_MERGE_AUTOEDIT": "no", **({"GIT_LITERAL_PATHSPECS": "1"} if "--" in args else {})}
    cp = ctl.run_git(top, args, check=False, timeout=timeout, allowed=ctl.OPS_ALLOWED, extra_env=env)
    if check and cp.returncode != 0:
        raise GitError(short(cp.stderr) or f"git {args[0]} failed")
    return cp


def short(text: str, n: int = 160) -> str:
    """First meaningful line of git's stderr, capped (``hint:`` lines dropped)."""
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("hint:"):
            return ln.removeprefix("fatal: ").removeprefix("error: ")[:n]
    return ""


def top_of(path: str | Path) -> Path:
    """The work-tree root of ``path``'s repo, after the ~/.cfg / ~/.agents-git checks; NotFound off-repo."""
    repo = ctl.check_repo_path(path)
    ctl.check_gitdir(repo)
    cp = run(repo, ["rev-parse", "--show-toplevel"], check=False)
    if cp.returncode != 0 or not cp.stdout.strip():
        raise NotFound("not a git repository")
    top = ctl.check_repo_path(cp.stdout.strip())
    if top != repo:
        ctl.check_gitdir(top)
    return top


# ------------------------------------------------------------------ status
def _change(path: str, orig: str, xy: str, conflict: bool = False) -> FileChange:
    if xy == "??":
        return FileChange(path, "", xy, False, False, True, False)
    if conflict:
        return FileChange(path, "", xy, False, False, False, True)
    return FileChange(path, orig, xy, xy[0] != ".", xy[1] != ".", False, False)


def parse_status(out: str) -> tuple[dict[str, str], list[FileChange]]:
    """``status --porcelain=v2 --branch -z`` output -> (headers, files)."""
    recs = out.split("\0")
    hdr: dict[str, str] = {}
    files: list[FileChange] = []
    i = 0
    while i < len(recs):
        r = recs[i]
        i += 1
        if len(r) < 2:
            continue
        if r.startswith("# "):
            k, _, v = r[2:].partition(" ")
            hdr[k] = v
        elif r[0] == "?":
            files.append(_change(r[2:], "", "??"))
        elif r[0] == "1":
            p = r.split(" ", 8)
            files.append(_change(p[8], "", p[1]))
        elif r[0] == "2":
            p = r.split(" ", 9)
            orig = recs[i] if i < len(recs) else ""
            i += 1
            files.append(_change(p[9], orig, p[1]))
        elif r[0] == "u":
            p = r.split(" ", 10)
            files.append(_change(p[10], "", p[1], conflict=True))
    return hdr, files


def ahead_behind(top: Path, ref: str) -> tuple[int, int]:
    """(ahead, behind) of HEAD against ``ref``; (0, 0) when either side is missing."""
    cp = run(top, ["rev-list", "--left-right", "--count", f"HEAD...{ref}"], check=False)
    parts = cp.stdout.split()
    return (int(parts[0]), int(parts[1])) if cp.returncode == 0 and len(parts) == 2 else (0, 0)


def has_ref(top: Path, ref: str) -> bool:
    return run(top, ["rev-parse", "--verify", "--quiet", ref], check=False).returncode == 0


def check_base(name: str) -> str:
    """``name`` when it is a branch-like ref usable in a revision range (``ctl.branch_name_ok``); Invalid otherwise."""
    if not ctl.branch_name_ok(name):
        raise Invalid("bad base branch name")
    return name


def files_since(top: Path, base: str) -> list[str]:
    """Files HEAD changed since it forked from ``base`` (``diff --name-only base...HEAD``, renames as delete +
    add), at most ``LIST_CAP``. GitError when ``base`` is unknown."""
    out = run(top, ["diff", "--name-only", "-z", "--no-renames", f"{check_base(base)}...HEAD", "--"]).stdout
    return [p for p in out.split("\0") if p][:LIST_CAP]


def status_at(top: Path, limit: int = LIST_CAP, *, remote: str | None) -> StatusDetail:
    """``remote``: compare a branch without an upstream with ``<remote>/<branch>``; None = no such fallback (callers
    that only read the files, branch or merge state)."""
    if remote is not None:
        ctl.check_remote_name(remote)
    out = run(top, ["status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all"]).stdout
    hdr, files = parse_status(out)
    head = hdr.get("branch.head", "")
    detached = head == "(detached)"
    branch = None if detached or not head else head
    oid = hdr.get("branch.oid", "")
    upstream = hdr.get("branch.upstream", "")
    ahead = behind = 0
    m = re.fullmatch(r"\+(\d+) -(\d+)", hdr.get("branch.ab", ""))
    if m:
        ahead, behind = int(m.group(1)), int(m.group(2))
    elif remote and branch and not upstream and oid != "(initial)" and has_ref(top, f"refs/remotes/{remote}/{branch}"):
        upstream = f"{remote}/{branch}"
        ahead, behind = ahead_behind(top, f"refs/remotes/{upstream}")
    return StatusDetail(
        branch=branch, detached=detached, head="" if oid in ("", "(initial)") else oid[:12], upstream=upstream,
        ahead=ahead, behind=behind, merging=has_ref(top, "MERGE_HEAD"), stashes=len(stashes_at(top)),
        files=tuple(files[:limit]), total=len(files))


def status_detail(path: str | Path, limit: int = LIST_CAP, *, remote: str) -> StatusDetail:
    """Branch, upstream ahead/behind and every changed file (staged / unstaged / untracked / conflict)."""
    return status_at(top_of(path), limit, remote=remote)


# ------------------------------------------------------------------ paths
def check_path(top: Path, rel: str) -> Path:
    """``top/rel`` after checking it stays inside the repo: relative, no ``..``, NUL or symlinked directory on
    the way. The leaf itself may be a symlink (git tracks the link, never its target)."""
    if not isinstance(rel, str) or not rel or len(rel) > 4096 or "\0" in rel or rel.startswith("/"):
        raise Invalid("bad path")
    parent, _, leaf = rel.rpartition("/")
    if leaf in ("", ".", ".."):
        raise Invalid("bad path")
    if parent:
        safe_join(top, parent)
    return top / rel


def changed(top: Path, st: StatusDetail, paths: list[str] | tuple[str, ...]) -> list[FileChange]:
    """The status entries of ``paths`` (each inside the repo and reported as changed), in request order."""
    if isinstance(paths, str) or not paths:
        raise Invalid("no paths")
    if len(paths) > MAX_PATHS:
        raise Invalid(f"more than {MAX_PATHS} paths")
    if st.total > len(st.files):
        st = status_at(top, limit=st.total, remote=None)
    known = {f.path: f for f in st.files}
    out: dict[str, FileChange] = {}
    for p in paths:
        check_path(top, p)
        if p not in known:
            raise NotFound(f"not a changed file: {p[:120]}")
        out[p] = known[p]
    return list(out.values())


def _names(changes: list[FileChange]) -> list[str]:
    """Paths of ``changes`` plus the source path of each rename."""
    names: list[str] = []
    for f in changes:
        names += [f.path] + ([f.orig] if f.orig else [])
    return names


def stage(path: str | Path, paths: list[str]) -> Outcome:
    """``git add -- <paths>``: changed files only (a deletion is staged as one)."""
    top = top_of(path)
    changes = changed(top, status_at(top, remote=None), paths)
    run(top, ["add", "--", *_names(changes)])
    return Outcome(f"Staged {len(changes)} file{'s' if len(changes) != 1 else ''}.")


def unstage(path: str | Path, paths: list[str]) -> Outcome:
    """``git reset -q -- <paths>`` (works on an unborn branch too): the files keep their worktree content."""
    top = top_of(path)
    changes = [f for f in changed(top, status_at(top, remote=None), paths) if f.staged or f.conflict]
    if not changes:
        raise Invalid("nothing staged in these paths")
    run(top, ["reset", "-q", "--", *_names(changes)])
    return Outcome(f"Unstaged {len(changes)} file{'s' if len(changes) != 1 else ''}.")


def is_new(f: FileChange) -> bool:
    """The file is not in HEAD: untracked, or added / copied / renamed in the index."""
    return f.untracked or f.xy[0] in "ACR"


def discard(path: str | Path, rel: str, *, untracked: bool = False) -> Outcome:
    """Throw away all changes to ``rel`` (staged and unstaged): restore it from HEAD. A file that is not in HEAD
    (untracked or newly added) is deleted, and only when ``untracked`` is set."""
    top = top_of(path)
    f = changed(top, status_at(top, remote=None), [rel])[0]
    if f.conflict:
        raise Conflict("the file has merge conflicts: resolve or abort the merge first")
    if not is_new(f):
        run(top, ["restore", "--source=HEAD", "--staged", "--worktree", "--", rel])
        return Outcome(f"Discarded changes to {rel}.")
    if not untracked:
        raise Invalid(f"{rel} is a new file: discarding deletes it (needs untracked)")
    target = check_path(top, rel)
    if not target.is_symlink() and not target.is_file() and target.exists():
        raise Invalid("not a file")
    if f.orig:
        run(top, ["restore", "--source=HEAD", "--staged", "--worktree", "--", f.orig])
    if f.staged:
        run(top, ["reset", "-q", "--", rel])
    target.unlink(missing_ok=True)
    return Outcome(f"Deleted new file {rel}.")


# ------------------------------------------------------------------ commit
def author(top: Path) -> str:
    """``Name <email>`` git will commit with (repo / user config); Invalid when it has none."""
    cp = run(top, ["var", "GIT_AUTHOR_IDENT"], check=False)
    m = re.match(r"(.*<[^>]*>)", cp.stdout.strip())
    if cp.returncode != 0 or not m:
        raise Invalid("no commit author: set user.name and user.email in the repo's git config")
    return m.group(1)


def check_message(message: str) -> str:
    msg = (message or "").strip()
    if not msg:
        raise Invalid("empty commit message")
    if len(msg) > MAX_MESSAGE or "\0" in msg:
        raise Invalid("commit message too long or not text")
    return msg


def commit(path: str | Path, message: str, *, amend: bool = False) -> Outcome:
    """Commit the staged changes with the author from the repo config. No amend, no hook bypass."""
    if amend:
        raise Denied("amend is not supported")
    msg = check_message(message)
    top = top_of(path)
    st = status_at(top, remote=None)
    if st.counts()["conflicts"]:
        raise Conflict("resolve the merge conflicts first")
    if not st.counts()["staged"] and not st.merging:
        raise Invalid("nothing staged to commit")
    author(top)
    run(top, ["commit", "--quiet", f"--message={msg}"], timeout=ctl.NET_TIMEOUT_S)
    sha = run(top, ["rev-parse", "--short", "HEAD"]).stdout.strip()
    return Outcome(f"Committed {sha}: {msg.splitlines()[0][:120]}", sha=sha)


# ------------------------------------------------------------------ stash
def stashes_at(top: Path) -> list[Stash]:
    cp = run(top, ["stash", "list", "--format=%gd%x1f%ct%x1f%gs"], check=False)
    out = []
    for ln in cp.stdout.splitlines():
        p = ln.split("\x1f", 2)
        m = re.fullmatch(r"stash@\{(\d+)\}", p[0])
        if len(p) == 3 and m:
            out.append(Stash(int(m.group(1)), p[0], float(p[1] or 0), p[2]))
    return out


def stash_list(path: str | Path) -> list[Stash]:
    return stashes_at(top_of(path))


def stash_ref(top: Path, index: int) -> Stash:
    """The stash ``index`` (an int, never a user string in argv); NotFound when there is none."""
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise Invalid("bad stash index")
    for s in stashes_at(top):
        if s.index == index:
            return s
    raise NotFound(f"no stash@{{{index}}}")


def stash_push(path: str | Path, message: str = "", *, untracked: bool = False) -> Outcome:
    """Stash the tracked changes (``untracked``: the untracked files too) with an optional one-line message."""
    msg = " ".join((message or "").split())[:200]
    top = top_of(path)
    st = status_at(top, remote=None)
    if st.counts()["conflicts"] or st.merging:
        raise Conflict("a merge is in progress")
    if not st.dirty and not (untracked and st.counts()["untracked"]):
        raise Invalid("nothing to stash")
    args = ["stash", "push", "--quiet"] + (["--include-untracked"] if untracked else [])
    run(top, args + ([f"--message={msg}"] if msg else []))
    return Outcome("Stashed the changes as stash@{0}.")


def _pop(top: Path, index: int) -> tuple[str, ...]:
    """``stash pop stash@{index}``: () when clean; the conflicted files when it clashed (the stash is kept)."""
    cp = run(top, ["stash", "pop", "--quiet", f"stash@{{{index}}}"], check=False)
    if cp.returncode == 0:
        return ()
    files = tuple(f.path for f in status_at(top, remote=None).files if f.conflict)
    if files:
        return files
    raise Conflict(short(cp.stderr) or "stash pop failed; the stash is kept")


def stash_pop(path: str | Path, index: int = 0) -> Outcome:
    top = top_of(path)
    s = stash_ref(top, index)
    clash = _pop(top, s.index)
    if clash:
        return Outcome(f"Applied {s.ref} with conflicts in {len(clash)} files; the stash is kept.", clash)
    return Outcome(f"Applied and dropped {s.ref}.")


def stash_drop(path: str | Path, index: int) -> Outcome:
    top = top_of(path)
    s = stash_ref(top, index)
    run(top, ["stash", "drop", "--quiet", s.ref])
    return Outcome(f"Dropped {s.ref}.")


# ------------------------------------------------------------------ branches
def _track(text: str) -> tuple[int, int, bool]:
    if text == "gone":
        return 0, 0, True
    a = re.search(r"ahead (\d+)", text)
    b = re.search(r"behind (\d+)", text)
    return int(a.group(1)) if a else 0, int(b.group(1)) if b else 0, False


def branches(path: str | Path, *, remote: str) -> list[Branch]:
    """Local branches: upstream (or <remote>/<name> when there is none), ahead/behind, last commit."""
    return branches_at(top_of(path), remote=remote)


def branches_at(top: Path, *, remote: str) -> list[Branch]:
    ctl.check_remote_name(remote)
    fmt = "%(refname:short)%1f%(HEAD)%1f%(upstream:short)%1f%(upstream:track,nobracket)%1f%(objectname:short)" \
          "%1f%(committerdate:unix)%1f%(subject)"
    cp = run(top, ["branch", "--list", f"--format={fmt}"], check=False)
    remote_refs = set(run(top, ["branch", "-r", "--format=%(refname:short)"], check=False).stdout.split())
    out: list[Branch] = []
    for ln in cp.stdout.splitlines()[:LIST_CAP]:
        p = ln.split("\x1f", 6)
        if len(p) != 7 or p[0].startswith("("):
            continue
        name, cur, up, track, sha, ts, subject = p
        ahead, behind, gone = _track(track)
        if not up and f"{remote}/{name}" in remote_refs:
            up = f"{remote}/{name}"
            cnt = run(top, ["rev-list", "--left-right", "--count", f"refs/heads/{name}...refs/remotes/{up}"],
                       check=False).stdout.split()
            ahead, behind = (int(cnt[0]), int(cnt[1])) if len(cnt) == 2 else (0, 0)
        out.append(Branch(name, cur == "*", up, ahead, behind, gone, sha, float(ts or 0), subject))
    return out


def local_branch(top: Path, name: str) -> str:
    """``name`` when it is an existing local branch (checked like a ref name), else NotFound."""
    if not isinstance(name, str) or not ctl.branch_exists(top, name):
        raise NotFound("no such local branch")
    return name


def checkout(path: str | Path, branch: str, *, stash: bool = False) -> Outcome:
    """Switch to a local branch. Tracked changes refuse it unless ``stash``: then stash, switch and pop; a pop
    that clashes leaves the conflicts in place and keeps the stash (``Outcome.clash``)."""
    top = top_of(path)
    local_branch(top, branch)
    st = status_at(top, remote=None)
    if st.branch == branch:
        raise Invalid(f"already on {branch}")
    if st.merging or st.counts()["conflicts"]:
        raise Conflict("a merge is in progress")
    if st.dirty and not stash:
        raise Conflict("uncommitted changes: stash them first (checkout with stash)")
    stashed = st.dirty
    if stashed:
        run(top, ["stash", "push", "--quiet", f"--message=pm: checkout {branch}"])
    cp = run(top, ["switch", "--quiet", "--", branch], check=False)
    if cp.returncode != 0:
        if stashed:
            _pop(top, 0)
        raise Conflict(short(cp.stderr) or "checkout failed")
    if not stashed:
        return Outcome(f"On {branch}.")
    try:
        clash = _pop(top, 0)
    except Conflict as e:
        return Outcome(f"On {branch}; the stashed changes did not apply ({e}); they are kept as stash@{{0}}.")
    if clash:
        return Outcome(f"On {branch}; popping the stash clashed in {len(clash)} files; it is kept.", clash)
    return Outcome(f"On {branch}, changes carried over.")


def merge(path: str | Path, branch: str, *, ff_only: bool = True) -> Outcome:
    """Merge a local branch into the current one. Refused with tracked changes. ``ff_only`` refuses a merge that
    needs a merge commit; a merge that conflicts is aborted (nothing changes) and raises MergeConflict."""
    top = top_of(path)
    local_branch(top, branch)
    st = status_at(top, remote=None)
    if st.detached or not st.branch:
        raise Conflict("detached HEAD: check out a branch first")
    if branch == st.branch:
        raise Invalid("cannot merge a branch into itself")
    if st.merging or st.dirty:
        raise Conflict("uncommitted changes: commit or stash them first")
    ahead, behind = ahead_behind(top, f"refs/heads/{branch}")
    if behind == 0:
        return Outcome(f"{st.branch} already has every commit of {branch}.")
    if ahead == 0:
        run(top, ["merge", "--ff-only", "--quiet", "--", branch], timeout=ctl.NET_TIMEOUT_S)
        return Outcome(f"Fast-forwarded {st.branch} to {branch} (+{behind}).")
    if ff_only:
        raise Conflict(f"{branch} and {st.branch} have diverged: not a fast-forward")
    author(top)
    cp = run(top, ["merge", "--no-edit", "--quiet", "--", branch], check=False, timeout=ctl.NET_TIMEOUT_S)
    if cp.returncode != 0:
        files = tuple(f.path for f in status_at(top, remote=None).files if f.conflict)
        run(top, ["merge", "--abort"], check=False)
        if files:
            raise MergeConflict(f"merge conflicts in {len(files)} files; merge aborted, nothing changed", files)
        raise Conflict(short(cp.stderr) or "merge failed")
    return Outcome(f"Merged {branch} into {st.branch}.")


def delete_branch(path: str | Path, name: str, *, unmerged_ok: bool = False) -> Outcome:
    """Delete a local branch (never the current one). Unmerged commits refuse it unless ``unmerged_ok``."""
    top = top_of(path)
    local_branch(top, name)
    if status_at(top, remote=None).branch == name:
        raise Denied("cannot delete the current branch")
    cp = run(top, ["branch", "-D" if unmerged_ok else "-d", "--", name], check=False)
    if cp.returncode != 0:
        if "not fully merged" in cp.stderr:
            raise Conflict(f"{name} is not fully merged: deleting it loses its commits")
        raise Conflict(short(cp.stderr) or "branch delete failed")
    return Outcome(f"Deleted branch {name}.")


def pull_stash(path: str | Path, *, remote: str) -> Outcome:
    """Stash the tracked changes, pull --ff-only from ``remote``, pop them back (a clash keeps the stash)."""
    top = top_of(path)
    st = status_at(top, remote=remote)
    if st.merging or st.counts()["conflicts"]:
        raise Conflict("a merge is in progress")
    if st.dirty:
        run(top, ["stash", "push", "--quiet", "--message=pm: pull"])
    try:
        gitremote.pull_ff_only(top, remote=remote)
    except GitError:
        if st.dirty:
            _pop(top, 0)
        raise
    clash = _pop(top, 0) if st.dirty else ()
    if clash:
        return Outcome(f"Pulled; popping the stash clashed in {len(clash)} files; it is kept.", clash)
    return Outcome("Pulled" + (" and popped the stashed changes." if st.dirty else "."))


def view(path: str | Path, commits: int = 20, *, remote: str, hosts: tuple[str, ...] = ()) -> dict:
    """The Git tab view model: status (files + counts), branches, stashes, recent commits, ``remote`` and whether
    the repo has it, and ``commit_url_base`` (``https://<host>/<owner>/<repo>/commit/`` when ``remote`` points at one
    of ``hosts``, else None: a commit's link is that + its full sha)."""
    top = top_of(path)
    return {"status": status_at(top, remote=remote).view(),
            "branches": [asdict(b) for b in branches_at(top, remote=remote)],
            "stashes": [asdict(s) for s in stashes_at(top)], "commits": ctl.commit_log(top, commits),
            "remote": remote, "has_remote": gitremote.has_remote(top, remote),
            "commit_url_base": gitremote.commit_url_base(top, hosts, remote=remote)}
