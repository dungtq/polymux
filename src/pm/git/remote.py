"""A project's git remotes: list / add / set-url / remove, and fetch, pull --ff-only and push with one named
remote (pm.toml ``push_remote``; callers pass it, there is no default). Every call goes through
``ctl.run_git`` (argv list, allow-list, user values after ``--``); a push is refused unless it targets the
remote it is given, and never forced. The name and url checks live in ``pm.git.ctl``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pm.git.ctl import (
    DEFAULT_GIT_HOSTS,
    NET_TIMEOUT_S,
    GitError,
    check_remote_name,
    current_branch,
    run_git,
)


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


def has_remote(path: str | Path, remote: str) -> bool:
    return remote in {r.name for r in remotes(path)}


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


def commit_url_base(path: str | Path, hosts: tuple[str, ...] | list[str] = DEFAULT_GIT_HOSTS, *,
                    remote: str) -> str | None:
    """``https://<host>/<owner>/<repo>/commit/`` from ``remote``'s url when its host is in ``hosts``, else None."""
    check_remote_name(remote)
    if not hosts:
        return None
    try:
        cp = run_git(path, ["remote", "get-url", remote], check=False)
    except GitError:
        return None
    url = cp.stdout.strip() if cp.returncode == 0 else ""
    for host in hosts:
        h = re.escape(host)
        m = re.match(rf"^(?:(?:ssh://)?git@{h}(?::\d+)?[:/]|https://{h}/)([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url)
        if m:
            return f"https://{host}/{m.group(1)}/commit/"
    return None


def fetch(path: str | Path, *, remote: str) -> str:
    return run_git(path, ["fetch", check_remote_name(remote)], timeout=NET_TIMEOUT_S).stderr.strip()


def pull_ff_only(path: str | Path, *, remote: str) -> str:
    check_remote_name(remote)
    branch = current_branch(path)
    if not branch:
        raise GitError("detached HEAD")
    return run_git(path, ["pull", "--ff-only", remote, branch], timeout=NET_TIMEOUT_S).stdout.strip()


def push(path: str | Path, *, remote: str) -> str:
    """Push the current branch to ``remote`` (``run_git`` refuses any other target; never forced)."""
    check_remote_name(remote)
    branch = current_branch(path)
    if not branch:
        raise GitError("detached HEAD")
    cp = run_git(path, ["push", remote, branch], timeout=NET_TIMEOUT_S, push_remote=remote)
    return (cp.stderr or cp.stdout).strip()
