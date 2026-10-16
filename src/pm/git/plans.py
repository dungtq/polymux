"""What a Git tab action will do, before it runs. Nothing here changes the repo.

``plan(path, action, args)`` -> ``Plan``: ``text`` (plain sentences for the UI), ``risk`` ('safe' | 'confirm' |
'typed'; with 'typed' the user types ``confirm``, the object's name), ``blocked`` (why it would be refused now;
'' = it can run), ``guard`` ('dirty' | 'behind' | 'diverged' | 'clash' | 'unmerged' | 'conflict' | '') and
``requires``: the extra form fields the run needs (``untracked``, ``stash``, ``ff_only``, ``unmerged``; for
``pull``, ``stash`` means the stash-pull-pop route). Push and pull compare with ``remote`` (pm.toml ``push_remote``,
passed by the caller) as of the last fetch.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pm.core.errors import Invalid, PmError
from pm.git import ctl, ops
from pm.git import remote as gitremote
from pm.git.ops import StatusDetail

Args = Mapping[str, object]


@dataclass(frozen=True)
class Plan:
    action: str
    text: str
    risk: str = "safe"
    confirm: str = ""
    blocked: str = ""
    guard: str = ""
    requires: dict[str, str] = field(default_factory=dict)

    def view(self) -> dict:
        return asdict(self)


def _n(k: int, word: str) -> str:
    return f"{k} {word}{'' if k == 1 else 's'}"


def _names(names: list[str], n: int = 5) -> str:
    more = len(names) - n
    return ", ".join(names[:n]) + (f" and {more} more" if more > 0 else "")


def _blocked(action: str, why: str, guard: str = "") -> Plan:
    return Plan(action, why[:1].upper() + why[1:] + ".", blocked=why, guard=guard)


def _busy(st: StatusDetail) -> bool:
    return st.merging or bool(st.counts()["conflicts"])


def _tracked(st: StatusDetail) -> int:
    return sum(not f.untracked for f in st.files)


def _on(st: StatusDetail) -> str:
    return st.branch or "detached HEAD"


# ------------------------------------------------------------------ files and commit
def plan_stage(top: Path, st: StatusDetail, paths: list[str]) -> Plan:
    ch = ops.changed(top, st, paths)
    return Plan("stage", f"Stage {_n(len(ch), 'file')}: {_names([f.path for f in ch])}.")


def plan_unstage(top: Path, st: StatusDetail, paths: list[str]) -> Plan:
    ch = [f for f in ops.changed(top, st, paths) if f.staged or f.conflict]
    if not ch:
        return _blocked("unstage", "nothing staged in these paths")
    return Plan("unstage", f"Unstage {_n(len(ch), 'file')}: {_names([f.path for f in ch])}. "
                           "Their changes stay in the working tree.")


def plan_discard(top: Path, st: StatusDetail, rel: str) -> Plan:
    f = ops.changed(top, st, [rel])[0]
    if f.conflict:
        return _blocked("discard", "the file has merge conflicts: resolve or abort the merge first", "conflict")
    if ops.is_new(f):
        return Plan("discard", f"Delete {rel}: it is a new file that was never committed. This cannot be undone.",
                    "typed", rel, requires={"untracked": "1"})
    return Plan("discard", f"Throw away every change to {rel}, staged and unstaged, and restore it from the last "
                           "commit. This cannot be undone.", "typed", rel)


def plan_commit(top: Path, st: StatusDetail, message: str) -> Plan:
    c = st.counts()
    if c["conflicts"]:
        return _blocked("commit", "resolve the merge conflicts first", "conflict")
    if not c["staged"] and not st.merging:
        return _blocked("commit", "nothing staged to commit")
    who = ops.author(top)
    msg = (message or "").strip()
    subject = msg.splitlines()[0][:120] if msg else ""
    text = f"Commit {_n(c['staged'], 'staged file')} on {_on(st)} as {who}" + (f': "{subject}".' if msg else ".")
    if not msg:
        return Plan("commit", text + " A commit message is needed.", blocked="empty commit message")
    ops.check_message(msg)
    return Plan("commit", text)


# ------------------------------------------------------------------ stash
def plan_stash(top: Path, st: StatusDetail, message: str, untracked: bool) -> Plan:
    if _busy(st):
        return _blocked("stash", "a merge is in progress", "conflict")
    n = _tracked(st) + (st.counts()["untracked"] if untracked else 0)
    if not n:
        return _blocked("stash", "nothing to stash")
    msg = " ".join((message or "").split())[:200]
    return Plan("stash", f"Stash {_n(n, 'changed file')}" + (" (untracked files too)" if untracked else "")
                + (f' as "{msg}"' if msg else "") + ". The files go back to the last commit; pop the stash to get "
                "the changes back.")


def plan_stash_pop(top: Path, st: StatusDetail, index: int) -> Plan:
    s = ops.stash_ref(top, index)
    return Plan("stash-pop", f"Apply {s.ref} ({s.subject}) to the working tree and drop it. If it clashes with "
                             "local changes, the conflicts stay in the files and the stash is kept.", "confirm")


def plan_stash_drop(top: Path, st: StatusDetail, index: int) -> Plan:
    s = ops.stash_ref(top, index)
    return Plan("stash-drop", f"Delete {s.ref} ({s.subject}). This cannot be undone.", "typed", s.ref)


# ------------------------------------------------------------------ branches
def plan_checkout(top: Path, st: StatusDetail, branch: str) -> Plan:
    ops.local_branch(top, branch)
    if st.branch == branch:
        return _blocked("checkout", f"already on {branch}")
    if _busy(st):
        return _blocked("checkout", "a merge is in progress", "conflict")
    if st.dirty:
        return Plan("checkout", f"Check out {branch}. You have {_n(_tracked(st), 'uncommitted change')}: pm stashes "
                                f"them, checks out {branch} and pops them back. If the pop clashes, the conflicts "
                                "stay in the files and the stash is kept.", "confirm", guard="dirty",
                    requires={"stash": "1"})
    return Plan("checkout", f"Check out {branch} (now on {_on(st)}).")


def plan_merge(top: Path, st: StatusDetail, branch: str) -> Plan:
    ops.local_branch(top, branch)
    if not st.branch:
        return _blocked("merge", "detached HEAD: check out a branch first")
    if branch == st.branch:
        return _blocked("merge", "cannot merge a branch into itself")
    if st.merging or st.dirty:
        return _blocked("merge", "uncommitted changes: commit or stash them first", "dirty")
    ahead, behind = ops.ahead_behind(top, f"refs/heads/{branch}")
    if not behind:
        return _blocked("merge", f"{st.branch} already has every commit of {branch}")
    if not ahead:
        return Plan("merge", f"Fast-forward {st.branch} to {branch} (+{_n(behind, 'commit')}). No merge commit.")
    return Plan("merge", f"Merge {branch} into {st.branch} with a merge commit: {branch} has {_n(behind, 'commit')} "
                         f"{st.branch} lacks and {st.branch} has {ahead} of its own. On conflicts pm aborts the merge "
                         "and nothing changes.", "typed", branch, guard="diverged", requires={"ff_only": "0"})


def plan_branch_delete(top: Path, st: StatusDetail, name: str, remote: str) -> Plan:
    ops.local_branch(top, name)
    if st.branch == name:
        return _blocked("branch-delete", "cannot delete the current branch")
    _, unique = ops.ahead_behind(top, f"refs/heads/{name}")
    if not unique:
        return Plan("branch-delete", f"Delete branch {name}: every commit of it is in {_on(st)}.", "typed", name)
    up = f"refs/remotes/{remote}/{name}"
    pushed = ops.has_ref(top, up) and ops.run(top, ["rev-list", "--count", f"{up}..refs/heads/{name}"],
                                              check=False).stdout.strip() == "0"
    return Plan("branch-delete", f"Delete branch {name}: {_n(unique, 'commit')} on it are not in {_on(st)}"
                + (f"; {remote} still has them." if pushed else " and are lost."), "typed", name, guard="unmerged",
                requires={"unmerged": "1"})


# ------------------------------------------------------------------ the push remote
def _remote_ref(top: Path, st: StatusDetail, action: str, remote: str) -> tuple[str, Plan | None]:
    if not st.branch:
        return "", _blocked(action, "detached HEAD: check out a branch first")
    if not gitremote.has_remote(top, remote):
        return "", _blocked(action, f"no {remote} remote")
    return f"refs/remotes/{remote}/{st.branch}", None


def plan_push(top: Path, st: StatusDetail, remote: str) -> Plan:
    ref, stop = _remote_ref(top, st, "push", remote)
    if stop:
        return stop
    b = st.branch
    if not ops.has_ref(top, ref):
        return Plan("push", f"Push {b} to {remote} as a new branch. Never forced.")
    ahead, behind = ops.ahead_behind(top, ref)
    if ahead and behind:
        return _blocked("push", f"{b} and {remote}/{b} have diverged: merge them first (pm never force-pushes)",
                        "diverged")
    if behind:
        return _blocked("push", f"{remote}/{b} has {_n(behind, 'new commit')}: pull first", "behind")
    if not ahead:
        return _blocked("push", f"nothing to push: {remote}/{b} is up to date")
    return Plan("push", f"Push {_n(ahead, 'commit')} on {b} to {remote}/{b}. Never forced.")


def plan_pull(top: Path, st: StatusDetail, remote: str) -> Plan:
    ref, stop = _remote_ref(top, st, "pull", remote)
    if stop:
        return stop
    b = st.branch
    if not ops.has_ref(top, ref):
        return _blocked("pull", f"{remote} has no branch {b} (fetch first)")
    ahead, behind = ops.ahead_behind(top, ref)
    if not behind:
        return _blocked("pull", f"{b} is up to date with {remote}/{b}")
    if ahead:
        return _blocked("pull", f"{b} and {remote}/{b} have diverged: not a fast-forward", "diverged")
    if _busy(st):
        return _blocked("pull", "a merge is in progress", "conflict")
    incoming = set(ops.run(top, ["diff", "--name-only", "-z", f"HEAD...{ref}"]).stdout.split("\0")) - {""}
    clash = sorted(incoming & {p for f in st.files if not f.untracked for p in (f.path, f.orig) if p})
    if clash:
        return Plan("pull", f"Stash, pull and pop: your changes to {_names(clash)} clash with the incoming "
                            f"{_n(behind, 'commit')}. pm stashes them, fast-forwards {b} from {remote} and pops "
                            "them back; if the pop clashes, the stash is kept.", "confirm", guard="clash",
                    requires={"stash": "1"})
    return Plan("pull", f"Fast-forward {b} by {_n(behind, 'commit')} from {remote}/{b}.")


# ------------------------------------------------------------------ dispatch
def _index(args: Args) -> int:
    try:
        return int(str(args.get("index", "0")))
    except ValueError:
        raise Invalid("bad stash index") from None


def _paths(args: Args) -> list[str]:
    v = args.get("paths") or []
    return [v] if isinstance(v, str) else [str(x) for x in v]  # type: ignore[union-attr]


def _s(args: Args, key: str) -> str:
    v = args.get(key, "")
    return v if isinstance(v, str) else ""


def _flag(args: Args, key: str) -> bool:
    return _s(args, key) in ("1", "true", "on")


PLANS: dict[str, Callable[[Path, StatusDetail, Args, str], Plan]] = {
    "stage": lambda t, st, a, r: plan_stage(t, st, _paths(a)),
    "unstage": lambda t, st, a, r: plan_unstage(t, st, _paths(a)),
    "discard": lambda t, st, a, r: plan_discard(t, st, _s(a, "path")),
    "commit": lambda t, st, a, r: plan_commit(t, st, _s(a, "message")),
    "stash": lambda t, st, a, r: plan_stash(t, st, _s(a, "message"), _flag(a, "untracked")),
    "stash-pop": lambda t, st, a, r: plan_stash_pop(t, st, _index(a)),
    "stash-drop": lambda t, st, a, r: plan_stash_drop(t, st, _index(a)),
    "checkout": lambda t, st, a, r: plan_checkout(t, st, _s(a, "branch")),
    "merge": lambda t, st, a, r: plan_merge(t, st, _s(a, "branch")),
    "branch-delete": lambda t, st, a, r: plan_branch_delete(t, st, _s(a, "branch"), r),
    "push": lambda t, st, a, r: plan_push(t, st, r),
    "pull": lambda t, st, a, r: plan_pull(t, st, r),
}


def plan(path: str | Path, action: str, args: Args, *, remote: str) -> Plan:
    """The plan of ``action`` with ``args`` (paths / path / message / untracked / index / branch); push, pull and
    branch-delete look at ``remote``. A domain refusal (unknown file, branch or stash, empty message, no author)
    comes back as a blocked plan."""
    if action not in PLANS:
        raise Invalid("unknown git action")
    ctl.check_remote_name(remote)
    top = ops.top_of(path)
    st = ops.status_at(top, remote=remote)
    try:
        return PLANS[action](top, st, args, remote)
    except PmError as e:
        return _blocked(action, str(e))
