import subprocess
from pathlib import Path

import pytest

from pm.git import ctl as gitctl
from pm.git import remote as gitremote

R = "gitdev"  # the repo fixture's push remote (pm.toml push_remote in the app)


def sh(*a, cwd):
    subprocess.run(a, cwd=cwd, check=True, capture_output=True,
                   env={"PATH": "/usr/bin:/bin", "HOME": str(cwd), "GIT_AUTHOR_NAME": "t",
                        "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture
def repo(tmp_path):
    bare = tmp_path / "bare.git"
    bare.mkdir()
    sh("git", "init", "--bare", "-b", "main", cwd=bare)
    r = tmp_path / "r"
    r.mkdir()
    sh("git", "init", "-b", "main", cwd=r)
    sh("git", "remote", "add", "gitdev", str(bare), cwd=r)
    (r / "a").write_text("1")
    sh("git", "add", "a", cwd=r)
    sh("git", "commit", "-m", "first", cwd=r)
    return r


def test_status_and_push(repo):
    s = gitctl.status(repo, remote=R)
    assert s.branch == "main" and s.has_remote and s.dirty == 0 and s.last_commit == "first"
    assert s.ahead == 0
    assert not gitctl.status(repo, remote="origin").has_remote  # another name: the repo does not have it
    gitremote.push(repo, remote=R)
    gitremote.fetch(repo, remote=R)
    s = gitctl.status(repo, remote=R)
    assert (s.ahead, s.behind) == (0, 0)
    (repo / "b").write_text("x")
    sh("git", "add", "b", cwd=repo)
    sh("git", "commit", "-m", "second", cwd=repo)
    (repo / "c").write_text("x")
    s = gitctl.status(repo, remote=R)
    assert s.ahead == 1 and s.dirty == 1
    assert [c["subject"] for c in gitctl.recent_commits(repo, 5)] == ["second", "first"]
    assert gitctl.worktrees(repo)[0]["branch"] == "main"


def test_pull_ff_only(repo, tmp_path):
    gitremote.push(repo, remote=R)
    other = tmp_path / "o"
    sh("git", "clone", str(tmp_path / "bare.git"), str(other), cwd=tmp_path)
    (other / "z").write_text("z")
    sh("git", "add", "z", cwd=other)
    sh("git", "commit", "-m", "z", cwd=other)
    sh("git", "push", "origin", "main", cwd=other)
    gitremote.fetch(repo, remote=R)
    assert gitctl.status(repo, remote=R).behind == 1
    gitremote.pull_ff_only(repo, remote=R)
    assert gitctl.status(repo, remote=R).behind == 0


@pytest.mark.parametrize("args", [
    ["push", "--force", "gitdev", "main"], ["push", "-f", "gitdev"],
    ["push", "gitdev", "+main"], ["push", "--force-with-lease", "gitdev"],
    ["commit", "--no-verify", "-m", "x"], ["push", "origin", "main"],
    ["reset", "--hard"], ["config", "x.y", "z"], ["push"],
])
def test_denied(repo, args):
    with pytest.raises(gitctl.GitDenied):
        gitctl.run_git(repo, args, push_remote=R)


def test_push_only_to_the_given_remote(repo, tmp_path):
    gitctl._check_args(["push", R, "main"], push_remote=R)
    with pytest.raises(gitctl.GitDenied):  # no push remote given: every push is refused
        gitctl._check_args(["push", R, "main"])
    with pytest.raises(gitctl.GitDenied):
        gitctl.run_git(repo, ["push", R, "main"])
    with pytest.raises(gitctl.GitDenied):  # a push to any other remote is refused
        gitctl._check_args(["push", "origin", "main"], push_remote=R)
    with pytest.raises(gitctl.GitDenied):  # a bad configured name is refused too
        gitctl._check_args(["push", "-x", "main"], push_remote="-x")
    other = tmp_path / "other.git"
    other.mkdir()
    sh("git", "init", "--bare", "-b", "main", cwd=other)
    sh("git", "remote", "add", "mirror", str(other), cwd=repo)
    gitremote.push(repo, remote="mirror")  # another configured name works the same way
    assert gitctl.status(repo, remote="mirror").has_remote and gitctl.status(repo, remote="mirror").ahead == 0
    for bad in ("", "-f", "a b", "x..y"):
        with pytest.raises(gitctl.GitDenied):
            gitremote.push(repo, remote=bad)
        with pytest.raises(gitctl.GitDenied):
            gitctl.status(repo, remote=bad)


def test_forbidden_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".cfg").mkdir()
    with pytest.raises(gitctl.GitDenied):
        gitctl.run_git(tmp_path / ".cfg", ["status"])
    assert gitctl.status(tmp_path / ".agents-git", remote=R) is None


def test_gitdir_in_cfg_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    sh("git", "init", "-b", "main", "--bare", str(tmp_path / ".cfg"), cwd=tmp_path)
    work = tmp_path / "w"
    work.mkdir()
    (work / ".git").write_text(f"gitdir: {tmp_path / '.cfg'}\n")
    with pytest.raises(gitctl.GitDenied):
        gitctl.run_git(work, ["status"])


def test_not_repo(tmp_path):
    assert gitctl.status(tmp_path, remote=R) is None


def test_last_commit_ts(repo):
    import time
    s = gitctl.status(repo, remote=R)
    assert abs(s.last_commit_ts - time.time()) < 120


def test_last_commit_ts_empty_repo(tmp_path):
    r = tmp_path / "e"
    r.mkdir()
    sh("git", "init", "-b", "main", cwd=r)
    s = gitctl.status(r, remote=R)
    assert s is not None and s.last_commit_ts == 0.0


def test_commit_log_and_urls(repo):
    log = gitctl.commit_log(repo, 5)
    assert log[0]["subject"] == "first" and len(log[0]["sha"]) == 40 and log[0]["ts"] > 0
    assert gitremote.commit_url_base(repo, ("git.example.test",), remote=R) is None  # local path remote: no link
    for url, want in (
        ("git@git.example.test:me/app.git", "https://git.example.test/me/app/commit/"),
        ("ssh://git@git.example.test/me/web", "https://git.example.test/me/web/commit/"),
        ("https://git.example.test/me/x.git", "https://git.example.test/me/x/commit/"),
        ("git@evil.example:me/app.git", None),
    ):
        sh("git", "remote", "set-url", "gitdev", url, cwd=repo)
        assert gitremote.commit_url_base(repo, ("git.example.test",), remote=R) == want
        assert gitremote.commit_url_base(repo, ("git.example.test",), remote="origin") is None  # not the push remote
    assert gitremote.commit_url_base(repo, remote=R) is None  # no allowed hosts: no links
    sh("git", "remote", "remove", "gitdev", cwd=repo)
    assert gitremote.commit_url_base(repo, ("git.example.test",), remote=R) is None


def test_dirty_files(repo):
    (repo / "n").write_text("x")
    (repo / "a").write_text("2")
    rows, total = gitctl.dirty_files(repo, limit=1)
    assert total == 2 and len(rows) == 1


@pytest.mark.parametrize("exc", [subprocess.TimeoutExpired(["git"], 1), OSError("no git")])
def test_check_gitdir_wraps_subprocess_errors(repo, monkeypatch, exc):
    gitctl._gitdir_cache.clear()

    def boom(*a, **kw):
        raise exc

    monkeypatch.setattr("pm.core.proc.subprocess.run", boom)
    with pytest.raises(gitctl.GitError) as ei:
        gitctl.check_gitdir(repo, {})
    assert not isinstance(ei.value, gitctl.GitDenied)
    assert gitctl.status(repo, remote=R) is None
