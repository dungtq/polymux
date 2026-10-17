"""git glue for tracker files: stale check, render into a work tree + stage (pre-commit hook), the
``merge=pmctl`` driver (takes the items a merged branch added or ticked, then writes the render), the
post-commit / post-merge hooks (stamp a branch's CHANGELOG / FEATURES drafts; on main land the merged ones)
and ``pmctl hooks install``.

Item files (BACKLOG.md, TODO.md) go through ``mdgen`` / ``mdimport`` / ``store``; the doc files (CHANGELOG.md,
FEATURES.md) through ``changelog`` / ``featurelist``, whose render depends on the work tree (``drafts.View``).
The hooks never block a commit when ``pmctl`` is not installed, but a failing ``pmctl render --stage`` does
fail the commit (post-commit cannot). After the render the pre-commit hook runs every executable file in
``pre-commit.d/`` next to it (sorted, also without pmctl; the first failure stops the commit), so other
checks chain behind it. ``install_hooks`` refuses to replace a hook it did not write (``force`` keeps the old
one as ``<hook>.pmctl-old``); a plain pre-commit.d chain runner (``CHAIN_MARK``) is replaced without it.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from pm.core.config import Settings
from pm.core.errors import Conflict, Invalid, PmError
from pm.core.fsutil import atomic_write, safe_join
from pm.git import ctl as gitctl
from pm.projects import backlog, projects
from pm.projects.tracker import changelog, drafts, featurelist, mdgen, mdimport, schema, store

HOOK_MARK = "# pm tracker hook"
# a pre-commit that only runs pre-commit.d (written by other tools when no pre-commit exists); pm's HOOK runs
# pre-commit.d too, so install replaces it without --force and nothing is lost
CHAIN_MARK = "# git hooks chain runner (pre-commit.d)"
# sh builtins only after the render: pre-commit.d must run with no pmctl (and no PATH) at all
HOOK = f"""#!/bin/sh
{HOOK_MARK} (written by `pmctl hooks install`): re-render the tracker files (BACKLOG.md, TODO.md,
# CHANGELOG.md, FEATURES.md) from pm.db and stage them, then run every executable file in pre-commit.d/
# next to this hook in sorted order. The render is skipped when pmctl is not installed; a failing render
# or pre-commit.d script stops the commit.
if command -v pmctl >/dev/null 2>&1; then
  pmctl render --stage || exit $?
fi
for f in "${{0%/*}}"/pre-commit.d/*; do
  [ -f "$f" ] && [ -x "$f" ] || continue
  "$f" "$@" || exit $?
done
exit 0
"""
POST_COMMIT = f"""#!/bin/sh
{HOOK_MARK} (written by `pmctl hooks install`): stamp this branch's CHANGELOG.md / FEATURES.md drafts with
# the new commit, so main shows them once it is merged. Skipped when pmctl is not installed; never fails.
command -v pmctl >/dev/null 2>&1 || exit 0
pmctl render --post-commit || true
"""
POST_MERGE = f"""#!/bin/sh
{HOOK_MARK} (written by `pmctl hooks install`): after a merge, land the CHANGELOG.md / FEATURES.md drafts
# the new HEAD has merged (in a branch's work tree: stamp its drafts). Skipped when pmctl is not installed;
# never fails.
command -v pmctl >/dev/null 2>&1 || exit 0
pmctl render --post-commit || true
"""
HOOKS = {"pre-commit": HOOK, "post-commit": POST_COMMIT, "post-merge": POST_MERGE}
# anchored to the repo root: an unanchored ``TODO.md merge=pmctl`` also matches nested TODO.md files, which the
# driver refuses (exit 1 = conflict)
ATTRS = tuple(f"/{name} merge=pmctl" for name in schema.ALL_FILES.values())
# replaced on install: unanchored lines, and the lines of the driver's old name (the CLI was ``pmt`` until
# 2026-10-13; an old hook it wrote carries HOOK_MARK and is replaced too)
_OLD_ATTRS = {f"{a}{name} merge={drv}" for name in schema.ALL_FILES.values() for a in ("", "/")
              for drv in ("pmctl", "pmt")} - set(ATTRS)
_DOCS = {"changelog": changelog, "features": featurelist}


def render_for(settings: Settings, project: str, file: str, root: str | Path | None) -> str | None:
    """The render of ``file`` for the work tree ``root`` (doc files: that tree's drafts)."""
    mod = _DOCS.get(file)
    return mod.render_text(settings, project, root) if mod else mdgen.render_text(settings, project, file)


def sync(settings: Settings, project: str, file: str) -> bool:
    """Absorb a hand edit of the main file, then render it into the main tree; True when it matches."""
    mod = _DOCS.get(file)
    return (mod.sync(settings, project) if mod else mdimport.sync(settings, project, file))[1]


def absorb_locked(settings: Settings, project: str, file: str) -> bool:
    """Take a hand edit of the main file under its flock, without writing it (``pmctl render --check``)."""
    mod = _DOCS.get(file)
    if mod is None:
        return mdimport.absorb_locked(settings, project, file)
    root = mdgen.project_root(settings, project)
    return mdimport.absorb_locked(settings, project, file, lambda: mod.absorb(
        settings, project, root, drafts.view_for(settings, project, root)))


def toplevel(cwd: str | Path) -> Path:
    """The git work tree root of ``cwd``."""
    try:
        out = gitctl.run_git(cwd, ["rev-parse", "--show-toplevel"]).stdout.strip()
    except gitctl.GitError as e:
        raise Invalid(f"not a git work tree: {e}") from None
    return Path(out)


def worktree_paths(root: str | Path) -> list[Path]:
    """``root`` first, then the repo's other work trees that exist (no bare or prunable entries); just
    ``[root]`` when ``root`` is not a git repo or git fails."""
    out = [Path(root)]
    try:
        found = gitctl.worktrees(root)
    except gitctl.GitError:
        return out
    seen = {os.path.realpath(root)}
    for wt in found:
        path = wt.get("worktree", "")
        if not path or "bare" in wt or "prunable" in wt or os.path.realpath(path) in seen or not Path(path).is_dir():
            continue
        seen.add(os.path.realpath(path))
        out.append(Path(path))
    return out


def stale(settings: Settings, project: str, root: str | Path) -> list[str]:
    """Tracker files under ``root`` whose bytes differ from the render."""
    out = []
    for file in mdgen.tracked_files(settings, project, schema.ALL_FILES):
        text = render_for(settings, project, file, root)
        path = mdgen.file_path(root, file)
        if text is not None and backlog.read(path, schema.max_bytes(file) * 4) != text.encode():
            out.append(schema.ALL_FILES[file])
    return out


def render_into(settings: Settings, project: str, root: str | Path, files=schema.ALL_FILES) -> list[str]:
    """Write the render of every tracker file (among ``files``) into ``root``; the names that changed."""
    changed = []
    for file in mdgen.tracked_files(settings, project, files):
        text = render_for(settings, project, file, root)
        path, cap = mdgen.file_path(root, file), schema.max_bytes(file)
        if text is None or backlog.read(path, cap * 4) == text.encode():
            continue
        backlog.mutate(settings, path, lambda _cur, t=text: t, name=schema.ALL_FILES[file], max_bytes=cap)
        changed.append(schema.ALL_FILES[file])
    return changed


def stage(settings: Settings, project: str, root: str | Path) -> list[str]:
    """Pre-commit: render into ``root`` and ``git add`` the tracker files (to the hook's index)."""
    changed = render_into(settings, project, root)
    names = [schema.ALL_FILES[f] for f in mdgen.tracked_files(settings, project, schema.ALL_FILES)]
    if names:
        try:
            gitctl.stage(root, names)
        except gitctl.GitError as e:
            raise Invalid(f"git add failed: {e}") from None
    return changed


def _read_side(path: str | Path) -> str:
    try:
        with open(path, "rb") as f:
            return f.read(schema.DOC_MAX_BYTES * 4).decode("utf-8", "replace")
    except FileNotFoundError:
        return ""


def branch_changes(base: str, theirs: str, file: str) -> tuple[list[tuple[str, str | None, bool]],
                                                               list[tuple[int | None, str]]]:
    """What ``theirs`` (a branch's copy) did to ``base``: (added, ticked) as ``store.merge_branch`` takes
    them. Added = items whose exact title is not in base (and whose [#N] is not either; a title repeated
    more often than in base counts as added), with the heading above them in theirs; ticked = items done in theirs but open in base (matched by [#N], else title)."""
    o, b = mdimport.parse(base, file), mdimport.parse(theirs, file)
    o_title: dict[str, list[mdimport.ParsedItem]] = {}  # a repeated title: k-th in theirs = k-th in base
    for it in o.items:
        o_title.setdefault(it.title, []).append(it)
    seen: dict[str, int] = {}
    o_ref = {it.ref: it for it in o.items if it.ref is not None}
    heads = [(pos, h.group(1).strip()) for pos, t in b.lines if (h := projects.HEADING.match(t))]
    added: list[tuple[str, str | None, bool]] = []
    ticked: list[tuple[int | None, str]] = []
    for it in b.items:
        done = it.mark != " "
        k = seen[it.title] = seen.get(it.title, -1) + 1
        same = o_title.get(it.title, [])
        old = (o_ref.get(it.ref) if it.ref is not None else None) or (same[k] if k < len(same) else None)
        if old is None:
            section = next((h for pos, h in reversed(heads) if pos < it.pos), None)
            added.append((it.title, section, done))
        elif done and old.mark == " ":
            ticked.append((it.ref, it.title))
    return added, ticked


def take_worktree(settings: Settings, project: str, root: str | Path, *, actor: str = "user",
                  audit: store.Audit = None) -> dict[str, dict]:
    """Pre-commit in a linked worktree: what the branch did by hand to its copy since HEAD (merge driver
    rules: added items and ticks only, never removals) goes into pm.db; for CHANGELOG.md / FEATURES.md the
    branch's new entries / changed features become its drafts. A copy with conflict markers is skipped.
    Returns {file name: counts} for the files that had changes."""
    out = {}
    view = None
    for file in mdgen.tracked_files(settings, project, schema.ALL_FILES):
        name = schema.ALL_FILES[file]
        theirs = backlog.read(mdgen.file_path(root, file), schema.max_bytes(file) * 4).decode("utf-8", "replace")
        if mdimport.has_conflict(theirs):
            continue
        base = gitctl.head_file(root, name)
        if file in _DOCS:
            view = view or drafts.view_for(settings, project, root)
            counts = _DOCS[file].take(settings, project, base, theirs, view, actor=actor, audit=audit)
            if any(counts.values()):
                out[name] = counts
            continue
        added, ticked = branch_changes(base, theirs, file)
        if added or ticked:
            out[name] = store.merge_branch(settings, project, file, added, ticked, actor=actor, audit=audit)
    return out


def merge(settings: Settings, project: str, base: str | Path, result: str | Path, theirs: str | Path,
          path: str, *, actor: str = "user", audit: store.Audit = None, root: str | Path | None = None) -> bool:
    """Merge driver (``%O %A %B %P``): items the merged branch (``theirs``) added or ticked against the
    merge base go into pm.db (``store.merge_branch``; ours is main, where the DB is the truth), then the
    render of ``path`` (a tracker file at the repo root) is written into ``result`` (git's %A). CHANGELOG.md /
    FEATURES.md: never written to pm.db here (git also runs the driver for ``merge-tree``, a merge that is
    aborted and a rebase); in a tree on the default branch ``result`` is the render as if the branch's
    drafts that ``theirs`` has had landed (``merged_text``); they land once the merge commit exists (the
    post-merge / post-commit hook or the next main render: ``drafts.classify``, ``absorb``); elsewhere the
    render is that work tree's (``root``, default: the cwd's). False when ``path`` is not a tracker file of
    ``project``."""
    file = next((k for k, v in schema.ALL_FILES.items() if v == path), None)
    if file is None or file not in mdgen.tracked_files(settings, project, schema.ALL_FILES):
        return False
    if file in _DOCS:
        root = toplevel(root or Path.cwd())
        view = drafts.view_for(settings, project, root)
        if view.main:  # a preview: git runs drivers for merge-tree, aborted merges and rebases too
            text = _DOCS[file].merged_text(settings, project, _read_side(base), _read_side(theirs), view, actor)
        else:
            text = render_for(settings, project, file, root)
    else:
        added, ticked = branch_changes(_read_side(base), _read_side(theirs), file)
        if added or ticked:
            store.merge_branch(settings, project, file, added, ticked, actor=actor, audit=audit)
        text = mdgen.render_text(settings, project, file)
    if text is None:
        return False
    atomic_write(Path(result), text, mode=None, fsync=False)
    return True


def _hooks_dir(root: Path) -> Path:
    """The repo's hooks dir; refused when ``core.hooksPath`` points outside the repo's git dir (a shared
    hooks dir would put the hook into every repo)."""
    try:
        out = gitctl.run_git(root, ["rev-parse", "--git-path", "hooks"]).stdout.strip()
        common = gitctl.run_git(root, ["rev-parse", "--git-common-dir"]).stdout.strip()
    except gitctl.GitError as e:
        raise Invalid(f"not a git work tree: {e}") from None
    hooks, gitdir = (root / out).resolve(), (root / common).resolve()
    if not hooks.is_relative_to(gitdir):
        raise Conflict(f"core.hooksPath ({hooks}) is outside this repo; install the hook there by hand")
    return hooks


def merge_plain(base: str | Path, result: str | Path, theirs: str | Path) -> bool:
    """The driver's fallback for a tracker file name that is not in the tracker: git's own 3-way merge
    into ``result`` (conflict markers on a conflict). True when clean."""
    try:
        return gitctl.merge_file(Path.cwd(), result, base, theirs)
    except gitctl.GitError:
        return False


def _ours(name: str, text: str) -> bool:
    """A hook install may replace without --force: pm's own, or a plain pre-commit.d chain runner (HOOK runs
    pre-commit.d too, so nothing is lost and no ``.pmctl-old`` copy is kept)."""
    return HOOK_MARK in text or (name == "pre-commit" and CHAIN_MARK in text)


def _install_hook(root: Path, name: str, script: str, force: bool) -> str:
    d = _hooks_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    hook = d / name
    if hook.is_symlink():
        raise Conflict(f"{hook} is a symlink; refused")
    try:
        cur = hook.read_text(errors="replace")
    except FileNotFoundError:
        cur = None
    if cur == script:
        return f"{hook}: already installed"
    if cur is not None and not _ours(name, cur):
        if not force:
            raise Conflict(f"{hook} exists and is not pm's; refused (--force replaces it, keeping a copy)")
        atomic_write(d / f"{name}.pmctl-old", cur, mode=stat.S_IMODE(hook.stat().st_mode), fsync=False)
    atomic_write(hook, script, mode=0o755)
    return f"{hook}: installed"


def _install_attrs(root: Path) -> str:
    try:
        path = safe_join(root, ".gitattributes")
    except PmError:
        raise Conflict(".gitattributes is a symlink or escapes the repo; refused") from None
    try:
        cur = path.read_text()
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        cur, mode = "", 0o644
    lines = cur.splitlines()
    kept = [ln for ln in lines if " ".join(ln.split()) not in _OLD_ATTRS]  # old lines are replaced
    have = {" ".join(ln.split()) for ln in kept}
    missing = [a for a in ATTRS if a not in have]
    if not missing and len(kept) == len(lines):
        return f"{path}: merge=pmctl already set"
    if len(kept) != len(lines):
        cur = "".join(ln + "\n" for ln in kept)
    sep = "" if not cur or cur.endswith("\n") else "\n"
    atomic_write(path, cur + sep + "".join(a + "\n" for a in missing), mode=mode)
    return f"{path}: added {', '.join(missing)}" if missing else f"{path}: merge=pmctl lines updated"


def install_hooks(root: str | Path, *, force: bool = False) -> list[str]:
    """pre-commit + post-commit + post-merge hooks, ``.gitattributes`` merge lines and the ``merge.pmctl`` driver for the
    repo at ``root``. A foreign hook is checked before anything is written."""
    root = Path(root)
    if not force:
        d = _hooks_dir(root)
        for name in HOOKS:
            hook = d / name
            if hook.is_file() and not hook.is_symlink() and not _ours(name, hook.read_text(errors="replace")):
                raise Conflict(f"{hook} exists and is not pm's; refused (--force replaces it, keeping a copy)")
    msgs = [_install_hook(root, name, script, force) for name, script in HOOKS.items()] + [_install_attrs(root)]
    try:
        gitctl.set_merge_driver(root)
    except gitctl.GitError as e:
        raise Invalid(f"git config failed: {e}") from None
    msgs.append("merge.pmctl driver: set")
    return msgs
