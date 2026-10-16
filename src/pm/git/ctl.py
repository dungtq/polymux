"""Allow-listed, argv-only git runner and read/write API. Never touches ~/.cfg or ~/.agents-git."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pm.core.cache import SWR
from pm.core.models import GitStatus
from pm.core.proc import ProcError, ProcTimeout, run_argv

GIT_BIN = "git"
REMOTE = "gitdev"
TIMEOUT_S = 20.0
NET_TIMEOUT_S = 60.0
ALLOWED = frozenset(
    "status rev-parse rev-list log diff branch worktree fetch pull add commit push remote".split()
)
FORBIDDEN_ARGS = frozenset({"--force", "-f", "--force-with-lease", "--no-verify", "--mirror", "--delete"})
# the Git tab's subcommands (pm.git.ops), checked by ``check_listed`` with the flags refused before ``--``
OPS_ALLOWED = frozenset("status rev-parse rev-list log diff branch add reset restore commit stash switch merge var".split())
LISTED_BAD_FLAG = re.compile(r"^(--force|--no-verify|--hard|--amend|--mirror|--delete|-[A-Za-z]*f[A-Za-z]*$)")


class GitError(Exception):
    pass


class GitDenied(GitError):
    pass


def _forbidden_dirs() -> tuple[Path, ...]:
    home = Path.home()
    return tuple((home / n).resolve() for n in (".cfg", ".agents-git"))


def _under(p: Path, base: Path) -> bool:
    return p == base or base in p.parents


def check_repo_path(path: str | Path) -> Path:
    p = Path(path).expanduser().resolve()
    for bad in _forbidden_dirs():
        if _under(p, bad):
            raise GitDenied(f"refused repo path {p}")
    return p


DEFAULT_GIT_HOSTS: tuple[str, ...] = ()
REMOTE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", re.ASCII)
_HOST = r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?"
_PATH = r"[A-Za-z0-9_][A-Za-z0-9._-]{0,99}(?:/[A-Za-z0-9_][A-Za-z0-9._-]{0,99}){0,3}"
_URL_SSH = re.compile(rf"ssh://git@({_HOST})(?::([0-9]{{1,5}}))?/({_PATH})", re.ASCII)
_URL_SCP = re.compile(rf"git@({_HOST}):({_PATH})", re.ASCII)
REMOTE_READ_ARGS = frozenset({"-v", "get-url"})
REMOTE_WRITE_ARGS = frozenset({"add", "set-url", "remove"})


def check_remote_name(name: str) -> str:
    if not isinstance(name, str) or not REMOTE_NAME_RE.fullmatch(name) or ".." in name or name.endswith((".lock", ".")):
        raise GitDenied("bad remote name")
    return name


def remote_url_host(url: str) -> str:
    """Host of a syntactically valid ``ssh://git@HOST[:PORT]/PATH`` or ``git@HOST:PATH`` url, else GitDenied.

    Only these two shapes are accepted: no http(s), file:, ext::, option-like or multi-line values.
    """
    if not isinstance(url, str) or len(url) > 400:
        raise GitDenied("bad remote url")
    m = _URL_SSH.fullmatch(url)
    if m:
        port = m.group(2)
        if port is not None and not 1 <= int(port) <= 65535:
            raise GitDenied("bad remote url")
        path = m.group(3)
    else:
        m = _URL_SCP.fullmatch(url)
        if not m:
            raise GitDenied("remote url must be ssh://git@host/path or git@host:path")
        path = m.group(2)
    if any(seg in {".", ".."} or seg.endswith(".lock") for seg in path.split("/")):
        raise GitDenied("bad remote url")
    return m.group(1).lower()


def check_remote_url(url: str, allowed_hosts: tuple[str, ...] | list[str] = DEFAULT_GIT_HOSTS) -> str:
    host = remote_url_host(url)
    if host not in {h.lower() for h in allowed_hosts}:
        raise GitDenied("remote host is not in the allowed git hosts")
    return url


def _check_remote_args(rest: list[str], allowed_hosts) -> None:
    """``git remote`` forms pm may run: list, -v, get-url NAME, and add/set-url/remove with ``--``."""
    if not rest:
        return
    sub, tail = rest[0], rest[1:]
    if sub == "-v" and not tail:
        return
    if sub == "get-url" and len(tail) == 1:
        check_remote_name(tail[0])
        return
    if sub in {"add", "set-url"} and len(tail) == 3 and tail[0] == "--":
        check_remote_name(tail[1])
        check_remote_url(tail[2], allowed_hosts)
        return
    if sub == "remove" and len(tail) == 2 and tail[0] == "--":
        check_remote_name(tail[1])
        return
    raise GitDenied(f"git remote form not allowed: {rest[:1]}")


def _check_args(args: list[str], allowed_hosts=DEFAULT_GIT_HOSTS) -> None:
    if not args or args[0] not in ALLOWED:
        raise GitDenied(f"git subcommand not allowed: {args[:1]}")
    if args[0] == "remote":
        _check_remote_args(args[1:], allowed_hosts)
        return
    for a in args:
        if a in FORBIDDEN_ARGS or (a.startswith("-") and not a.startswith("--") and "f" in a[1:] and a[1:].isalpha() and args[0] in {"push", "pull", "fetch"}):
            raise GitDenied(f"git flag not allowed: {a}")
        if a.startswith("--force") or a.startswith("--no-verify"):
            raise GitDenied(f"git flag not allowed: {a}")
        if a.startswith("+") or ":+" in a:
            raise GitDenied(f"forced refspec not allowed: {a}")
    if args[0] == "push":
        rest = [a for a in args[1:] if not a.startswith("-")]
        if not rest or rest[0] != REMOTE:
            raise GitDenied("push only to gitdev")


def check_listed(args: list[str], allowed: frozenset[str]) -> None:
    """GitDenied unless ``args`` is a subcommand of ``allowed`` without a ``LISTED_BAD_FLAG`` flag or ``+`` refspec
    before ``--`` (user values follow ``--``); ``reset`` only as ``reset -q -- <paths>``."""
    if not args or args[0] not in allowed:
        raise GitDenied(f"git subcommand not allowed: {args[:1]}")
    head = args[: args.index("--")] if "--" in args else args
    for a in head[1:]:
        if LISTED_BAD_FLAG.match(a) or a.startswith("+"):
            raise GitDenied(f"git flag not allowed: {a}")
    if args[0] == "reset" and args[1:3] != ["-q", "--"]:
        raise GitDenied("git reset only as reset -q -- <paths>")


_LOCATION_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


def git_env(*, clean: bool = True, index: str | None = None) -> dict[str, str]:
    """The env of a git call: C locale, no terminal prompt. ``clean`` drops ``GIT_DIR`` / ``GIT_WORK_TREE`` /
    ``GIT_INDEX_FILE`` (a hook's env must not redirect the call); ``index`` then sets ``GIT_INDEX_FILE``
    (made absolute against the cwd)."""
    env = {**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"}
    if clean:
        for k in _LOCATION_VARS:
            env.pop(k, None)
    if index:
        env["GIT_INDEX_FILE"] = os.path.abspath(index)
    return env


def run_git(
    path: str | Path, args: list[str], *, timeout: float = TIMEOUT_S, check: bool = True,
    allowed_hosts: tuple[str, ...] | list[str] = DEFAULT_GIT_HOSTS,
    allowed: frozenset[str] | None = None, extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """One git call in ``path``: argv list, fixed binary, timeout, ``git_env`` (+ ``extra_env``), the ~/.cfg /
    ~/.agents-git checks. ``allowed`` None: the ``ALLOWED`` rules (remote forms, push to gitdev only, no force);
    a set (``OPS_ALLOWED``, ...): ``check_listed``. Failures raise GitError (``check``: a non-zero exit too)."""
    if allowed is None:
        _check_args(list(args), allowed_hosts)
    else:
        check_listed(list(args), allowed)
    repo = check_repo_path(path)
    env = {**git_env(), **(extra_env or {})}
    argv = [GIT_BIN, "-C", str(repo), *args]
    try:
        cp = run_argv(argv, timeout=timeout, env=env, text=True)
    except ProcTimeout as e:
        raise GitError(f"git {args[0]} timed out") from e
    except ProcError as e:
        raise GitError(str(e)) from e
    if args[0] != "rev-parse":
        check_gitdir(repo, env)
    if check and cp.returncode != 0:
        raise GitError(cp.stderr.strip() or f"git {args[0]} failed")
    return cp


def init_repo(path: str | Path) -> None:
    """``git init -q`` in ``path`` (a new project folder). ``init`` is in no allow-list: this is its only use."""
    run_git(path, ["init", "-q"], allowed=frozenset({"init"}))


_gitdir_cache: dict[Path, bool] = {}


def check_gitdir(repo: Path, env: dict | None = None) -> None:
    """Refuse when the repo's git-dir is ~/.cfg or ~/.agents-git (work-tree = $HOME case). ``env``: ``git_env()``
    when None."""
    try:
        cp = run_argv([GIT_BIN, "-C", str(repo), "rev-parse", "--absolute-git-dir"],
                      timeout=TIMEOUT_S, env=git_env() if env is None else env, text=True)
    except ProcTimeout as e:
        raise GitError("git rev-parse timed out") from e
    except ProcError as e:
        raise GitError(str(e)) from e
    if cp.returncode != 0:
        return
    gd = Path(cp.stdout.strip()).resolve()
    for bad in _forbidden_dirs():
        if _under(gd, bad):
            raise GitDenied(f"refused git-dir {gd}")


def is_repo(path: str | Path) -> bool:
    try:
        check_repo_path(path)
        cp = run_git(path, ["rev-parse", "--git-dir"], check=False)
        return cp.returncode == 0
    except GitError:
        return False


def _guard(path: str | Path) -> None:
    """Pre-flight: a deny check before any real command (rev-parse is exempt from the post-check)."""
    repo = check_repo_path(path)
    check_gitdir(repo, git_env(clean=False))


def current_branch(path: str | Path) -> str | None:
    cp = run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"], check=False)
    b = cp.stdout.strip()
    return b if cp.returncode == 0 and b and b != "HEAD" else None


def status(path: str | Path) -> GitStatus | None:
    try:
        _guard(path)
        if not is_repo(path):
            return None
        branch = current_branch(path)
        porcelain = run_git(path, ["status", "--porcelain"], check=False).stdout
        dirty = len([ln for ln in porcelain.splitlines() if ln.strip()])
        last = run_git(path, ["log", "-1", "--format=%s"], check=False).stdout.strip()
        ts_out = run_git(path, ["log", "-1", "--format=%ct"], check=False).stdout.strip()
        try:
            last_ts = float(ts_out) if ts_out else 0.0
        except ValueError:
            last_ts = 0.0
        remotes = run_git(path, ["remote"], check=False).stdout.split()
        has = REMOTE in remotes
        ahead = behind = 0
        if has and branch:
            cp = run_git(
                path,
                ["rev-list", "--left-right", "--count", f"HEAD...refs/remotes/{REMOTE}/{branch}"],
                check=False,
            )
            if cp.returncode == 0:
                parts = cp.stdout.split()
                if len(parts) == 2:
                    ahead, behind = int(parts[0]), int(parts[1])
        return GitStatus(branch, dirty, ahead, behind, last, has, last_commit_ts=last_ts)
    except GitError:
        return None


def recent_commits(path: str | Path, n: int = 10) -> list[dict[str, str]]:
    n = max(1, min(int(n), 200))
    cp = run_git(path, ["log", f"-{n}", "--format=%h%x1f%an%x1f%ct%x1f%s"], check=False)
    out = []
    for ln in cp.stdout.splitlines():
        p = ln.split("\x1f", 3)
        if len(p) == 4:
            out.append({"hash": p[0], "author": p[1], "ts": p[2], "subject": p[3]})
    return out


def commit_log(path: str | Path, n: int = 5) -> list[dict[str, str]]:
    """Last ``n`` commits with full sha (for links); [] on any git error."""
    n = max(1, min(int(n), 50))
    try:
        cp = run_git(path, ["log", f"-{n}", "--format=%H%x1f%h%x1f%an%x1f%ct%x1f%s"], check=False)
    except GitError:
        return []
    out = []
    for ln in cp.stdout.splitlines():
        p = ln.split("\x1f", 4)
        if len(p) == 5 and re.fullmatch(r"[0-9a-f]{40,64}", p[0]):
            try:
                ts = float(p[3])
            except ValueError:
                ts = 0.0
            out.append({"sha": p[0], "hash": p[1], "author": p[2], "ts": ts, "subject": p[4]})
    return out


def commit_url_base(path: str | Path, hosts: tuple[str, ...] | list[str] = DEFAULT_GIT_HOSTS) -> str | None:
    """``https://<host>/<owner>/<repo>/commit/`` from the gitdev remote when its host is in ``hosts``, else None."""
    if not hosts:
        return None
    try:
        cp = run_git(path, ["remote", "get-url", REMOTE], check=False)
    except GitError:
        return None
    url = cp.stdout.strip() if cp.returncode == 0 else ""
    for host in hosts:
        h = re.escape(host)
        m = re.match(rf"^(?:(?:ssh://)?git@{h}(?::\d+)?[:/]|https://{h}/)([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url)
        if m:
            return f"https://{host}/{m.group(1)}/commit/"
    return None


def dirty_files(path: str | Path, limit: int = 60) -> tuple[list[tuple[str, str]], int]:
    """(status code, path) of changed files, capped at ``limit``; plus the total count."""
    try:
        cp = run_git(path, ["status", "--porcelain"], check=False)
    except GitError:
        return [], 0
    rows = [(ln[:2].strip() or "?", ln[3:]) for ln in cp.stdout.splitlines() if len(ln) > 3]
    return rows[:limit], len(rows)


DIFF_MAX_BYTES = 512 * 1024


_STATUS_FRESH, _STATUS_STALE = 3.0, 60.0
_status_cache = SWR(max_entries=200)
_diff_cache = SWR(max_entries=200)


def _file_key(path: str | Path, rel: str) -> tuple:
    try:
        st = os.stat(os.path.join(str(path), rel))
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (None, None)  # deleted file: its diff still works, key is stable


def file_diff(path: str | Path, rel: str) -> str:
    """Cached ``_file_diff`` keyed by (repo, rel, file mtime_ns/size); a changed file is a miss. A commit or
    staging change (file untouched) is picked up by the same 3 s / 60 s stale-while-revalidate window as status."""
    if not rel:
        return ""
    key = (str(path), rel, *_file_key(path, rel))
    return _diff_cache.get(key, lambda: _file_diff(path, rel), _STATUS_FRESH, _STATUS_STALE)


def _file_diff(path: str | Path, rel: str) -> str:
    """Read-only ``git diff HEAD -- <rel>`` of one file (raw, capped); '' when clean, untracked or not a repo."""
    try:
        cp = run_git(path, ["diff", "--no-color", "--no-ext-diff", "HEAD", "--", rel], check=False)
    except GitError:
        return ""
    return cp.stdout[:DIFF_MAX_BYTES] if cp.returncode == 0 else ""


def status_map(path: str | Path, limit: int = 2000) -> tuple[dict[str, str], bool]:
    """Cached ``_status_map``: fresh 3 s, served stale up to 60 s while a background thread refreshes, then sync.
    Callers get a private copy of the dict."""
    gs, ok = _status_cache.get((str(path), limit), lambda: _status_map(path, limit), _STATUS_FRESH, _STATUS_STALE)
    return dict(gs), ok


def _status_map(path: str | Path, limit: int = 2000) -> tuple[dict[str, str], bool]:
    """({path relative to ``path``: 'M'|'A'|'D'|'?'|'!'}, in_repo): changed files, '!' = ignored; dirs get 'M' when
    something below changed. ({}, False) off-repo."""
    try:
        pre = run_git(path, ["rev-parse", "--show-prefix"], check=False)
        if pre.returncode != 0:
            return {}, False
        prefix = pre.stdout.strip()
        cp = run_git(path, ["status", "--porcelain", "--ignored"], check=False)
    except GitError:
        return {}, False
    if cp.returncode != 0:
        return {}, False
    out: dict[str, str] = {}
    for ln in cp.stdout.splitlines()[:limit]:
        if len(ln) < 4:
            continue
        code = ln[:2]
        p = ln[3:].split(" -> ")[-1].strip('"')
        if prefix:
            if not p.startswith(prefix):
                continue
            p = p[len(prefix):]
        m = "?" if code == "??" else "!" if code == "!!" else "A" if "A" in code else "D" if "D" in code else "M"
        p = p.rstrip("/")
        if not p:
            continue
        out[p] = m
        parts = p.split("/")
        if m != "!":
            for i in range(1, len(parts)):  # mark parent dirs as modified
                out.setdefault("/".join(parts[:i]), "M")
    return out, True


def worktrees(path: str | Path) -> list[dict[str, str]]:
    cp = run_git(path, ["worktree", "list", "--porcelain"], check=False)
    out: list[dict[str, str]] = []
    cur: dict[str, str] = {}
    for ln in cp.stdout.splitlines() + [""]:
        if not ln:
            if cur:
                out.append(cur)
            cur = {}
            continue
        k, _, v = ln.partition(" ")
        if k == "branch":
            v = v.removeprefix("refs/heads/")
        cur[k] = v
    return out


def fetch(path: str | Path) -> str:
    return run_git(path, ["fetch", REMOTE], timeout=NET_TIMEOUT_S).stderr.strip()


def pull_ff_only(path: str | Path) -> str:
    branch = current_branch(path)
    if not branch:
        raise GitError("detached HEAD")
    return run_git(path, ["pull", "--ff-only", REMOTE, branch], timeout=NET_TIMEOUT_S).stdout.strip()


def push_gitdev(path: str | Path) -> str:
    branch = current_branch(path)
    if not branch:
        raise GitError("detached HEAD")
    cp = run_git(path, ["push", REMOTE, branch], timeout=NET_TIMEOUT_S)
    return (cp.stderr or cp.stdout).strip()


@dataclass(frozen=True)
class RemoteInfo:
    name: str
    fetch_url: str
    push_url: str


def remotes(path: str | Path) -> list[RemoteInfo]:
    """``git remote -v`` as rows (name, fetch url, push url); [] on any git error."""
    try:
        cp = run_git(path, ["remote", "-v"], check=False)
    except GitError:
        return []
    fetch: dict[str, str] = {}
    push: dict[str, str] = {}
    for ln in cp.stdout.splitlines():
        m = re.fullmatch(r"(\S+)\t(.*) \((fetch|push)\)", ln)
        if m:
            (fetch if m.group(3) == "fetch" else push)[m.group(1)] = m.group(2)
    return [RemoteInfo(n, u, push.get(n, u)) for n, u in fetch.items()]


def _remote_names(path: str | Path) -> list[str]:
    return run_git(path, ["remote"], check=False).stdout.split()


def remote_add(path: str | Path, name: str, url: str, allowed_hosts=DEFAULT_GIT_HOSTS) -> None:
    check_remote_name(name)
    if name in _remote_names(path):
        raise GitError("remote already exists")
    run_git(path, ["remote", "add", "--", name, url], allowed_hosts=allowed_hosts)


def remote_set_url(path: str | Path, name: str, url: str, allowed_hosts=DEFAULT_GIT_HOSTS) -> None:
    check_remote_name(name)
    if name not in _remote_names(path):
        raise GitError("no such remote")
    run_git(path, ["remote", "set-url", "--", name, url], allowed_hosts=allowed_hosts)


def remote_remove(path: str | Path, name: str) -> None:
    check_remote_name(name)
    if name not in _remote_names(path):
        raise GitError("no such remote")
    run_git(path, ["remote", "remove", "--", name])


# ------------------------------------------------------------------ tracker glue (pmctl hooks / render --stage)
MERGE_DRIVER = {"merge.pmctl.name": "pm tracker: take a branch's new/ticked items and CHANGELOG / FEATURES drafts, re-render BACKLOG.md / TODO.md / CHANGELOG.md / FEATURES.md",
                "merge.pmctl.driver": "pmctl render --merge %O %A %B %P"}
OLD_MERGE_SECTION = "merge.pmt"  # the driver's name before the CLI was renamed pmt -> pmctl (2026-10-13)


_RAW_CMDS = frozenset({"add", "config", "show", "merge-base", "merge-file"})


def _raw(repo: Path, args: list[str], env: dict, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """One git call outside the ``run_git`` allow-list: only ``add`` / ``config`` / ``show`` / ``merge-base``
    / ``merge-file``, fixed subcommand and options from this module, file names only after ``--`` (``show``:
    ``HEAD:<tracker file>``; ``merge-base``: a checked sha and ref; ``merge-file``: absolute paths)."""
    if not args or args[0] not in _RAW_CMDS:
        raise GitDenied(f"git subcommand not allowed: {args[:1]}")
    check_gitdir(repo, env)
    try:
        cp = run_argv([GIT_BIN, "-C", str(repo), *args], timeout=TIMEOUT_S, env=env, text=True)
    except ProcTimeout as e:
        raise GitError(f"git {args[0]} timed out") from e
    except ProcError as e:
        raise GitError(str(e)) from e
    if check and cp.returncode != 0:
        raise GitError(cp.stderr.strip() or f"git {args[0]} failed")
    return cp


def set_merge_driver(repo_path: str | Path) -> None:
    """Set the repo-local ``merge.pmctl.*`` keys (only these) for the ``merge=pmctl`` attribute and drop the
    old ``merge.pmt`` section when there is one."""
    repo = check_repo_path(repo_path)
    env = git_env()
    for key, val in MERGE_DRIVER.items():
        _raw(repo, ["config", "--local", key, val], env)
    try:
        _raw(repo, ["config", "--local", "--remove-section", OLD_MERGE_SECTION], env)
    except GitError:
        pass  # no old section


TRACKER_FILES = ("BACKLOG.md", "TODO.md", "CHANGELOG.md", "FEATURES.md")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")


def head_file(repo_path: str | Path, name: str) -> str:
    """``git show HEAD:<name>`` (a root-level tracker file name); "" when HEAD or the file is missing."""
    if name not in TRACKER_FILES:
        raise GitDenied(f"not a tracker file: {name!r}")
    repo = check_repo_path(repo_path)
    try:
        return _raw(repo, ["show", f"HEAD:{name}"], git_env()).stdout
    except GitError:
        return ""


def stage(repo_path: str | Path, files: list[str], *, keep_index_env: bool = True) -> None:
    """``git add -- files`` in ``repo_path``. ``keep_index_env`` keeps ``GIT_INDEX_FILE`` (made absolute
    against the cwd): inside a pre-commit hook the add must go to the index being committed."""
    repo = check_repo_path(repo_path)
    if not files:
        return
    env = git_env(index=os.environ.get("GIT_INDEX_FILE") if keep_index_env else None)
    _raw(repo, ["add", "--", *files], env)


def is_ancestor(repo_path: str | Path, sha: str, ref: str = "HEAD") -> bool:
    """``git merge-base --is-ancestor <sha> <ref>``: True on exit 0, False otherwise (not an ancestor, unknown
    commit, no MERGE_HEAD). ``sha``: 7-40 lowercase hex; ``ref``: ``HEAD`` or ``MERGE_HEAD``."""
    if not isinstance(sha, str) or not _SHA.match(sha):
        raise GitDenied("bad commit sha")
    if ref not in ("HEAD", "MERGE_HEAD"):
        raise GitDenied(f"ref not allowed: {ref!r}")
    repo = check_repo_path(repo_path)
    return _raw(repo, ["merge-base", "--is-ancestor", sha, ref], git_env(), check=False).returncode == 0


def _rev(repo_path: str | Path, ref: str) -> str | None:
    cp = run_git(repo_path, ["rev-parse", "--verify", "--quiet", ref], check=False)
    out = cp.stdout.strip()
    return out if cp.returncode == 0 and _SHA.match(out) else None


def head_sha(repo_path: str | Path) -> str | None:
    """The full sha of HEAD; None on an unborn branch or outside a repo."""
    return _rev(repo_path, "HEAD")


def merge_head(repo_path: str | Path) -> str | None:
    """The sha of MERGE_HEAD while a merge is in progress, else None."""
    return _rev(repo_path, "MERGE_HEAD")


def branch_name_ok(name: str) -> bool:
    """Branch-like ref name (``main``, ``gitdev/main``): no leading ``-``/``/``/``.``, ``..``, ``//``, ``/``/``.lock`` end."""
    return (isinstance(name, str) and bool(_BRANCH.match(name)) and not name.startswith(("-", "/", "."))
            and ".." not in name and "//" not in name and not name.endswith(("/", ".lock")))


def branch_exists(repo_path: str | Path, branch: str) -> bool:
    """``refs/heads/<branch>`` exists (``branch`` checked by ``branch_name_ok``; a bad name is False)."""
    return branch_name_ok(branch) and _rev(repo_path, f"refs/heads/{branch}") is not None


def merge_file(repo_path: str | Path, current: str | Path, base: str | Path, other: str | Path) -> bool:
    """``git merge-file`` (a plain 3-way merge into ``current``, conflict markers on a conflict): the merge
    driver's fallback for a tracker file name that is not in the tracker. True when it merged cleanly."""
    repo = check_repo_path(repo_path)
    paths = [str(Path(p).resolve()) for p in (current, base, other)]  # absolute: never read as an option
    return _raw(repo, ["merge-file", "-L", "ours", "-L", "base", "-L", "theirs", *paths], git_env(),
                check=False).returncode == 0
