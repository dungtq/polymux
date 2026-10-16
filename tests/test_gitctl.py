import subprocess
from pathlib import Path

import pytest

from pm.git import ctl as gitctl


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
    s = gitctl.status(repo)
    assert s.branch == "main" and s.has_gitdev and s.dirty == 0 and s.last_commit == "first"
    assert s.ahead == 0
    gitctl.push_gitdev(repo)
    gitctl.fetch(repo)
    s = gitctl.status(repo)
    assert (s.ahead, s.behind) == (0, 0)
    (repo / "b").write_text("x")
    sh("git", "add", "b", cwd=repo)
    sh("git", "commit", "-m", "second", cwd=repo)
    (repo / "c").write_text("x")
    s = gitctl.status(repo)
    assert s.ahead == 1 and s.dirty == 1
    assert [c["subject"] for c in gitctl.recent_commits(repo, 5)] == ["second", "first"]
    assert gitctl.worktrees(repo)[0]["branch"] == "main"


def test_pull_ff_only(repo, tmp_path):
    gitctl.push_gitdev(repo)
    other = tmp_path / "o"
    sh("git", "clone", str(tmp_path / "bare.git"), str(other), cwd=tmp_path)
    (other / "z").write_text("z")
    sh("git", "add", "z", cwd=other)
    sh("git", "commit", "-m", "z", cwd=other)
    sh("git", "push", "origin", "main", cwd=other)
    gitctl.fetch(repo)
    assert gitctl.status(repo).behind == 1
    gitctl.pull_ff_only(repo)
    assert gitctl.status(repo).behind == 0


@pytest.mark.parametrize("args", [
    ["push", "--force", "gitdev", "main"], ["push", "-f", "gitdev"],
    ["push", "gitdev", "+main"], ["push", "--force-with-lease", "gitdev"],
    ["commit", "--no-verify", "-m", "x"], ["push", "origin", "main"],
    ["reset", "--hard"], ["config", "x.y", "z"], ["push"],
])
def test_denied(repo, args):
    with pytest.raises(gitctl.GitDenied):
        gitctl.run_git(repo, args)


def test_forbidden_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".cfg").mkdir()
    with pytest.raises(gitctl.GitDenied):
        gitctl.run_git(tmp_path / ".cfg", ["status"])
    assert gitctl.status(tmp_path / ".agents-git") is None


def test_gitdir_in_cfg_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    sh("git", "init", "-b", "main", "--bare", str(tmp_path / ".cfg"), cwd=tmp_path)
    work = tmp_path / "w"
    work.mkdir()
    (work / ".git").write_text(f"gitdir: {tmp_path / '.cfg'}\n")
    with pytest.raises(gitctl.GitDenied):
        gitctl.run_git(work, ["status"])


def test_not_repo(tmp_path):
    assert gitctl.status(tmp_path) is None


def test_last_commit_ts(repo):
    import time
    s = gitctl.status(repo)
    assert abs(s.last_commit_ts - time.time()) < 120


def test_last_commit_ts_empty_repo(tmp_path):
    r = tmp_path / "e"
    r.mkdir()
    sh("git", "init", "-b", "main", cwd=r)
    s = gitctl.status(r)
    assert s is not None and s.last_commit_ts == 0.0


def test_commit_log_and_urls(repo):
    log = gitctl.commit_log(repo, 5)
    assert log[0]["subject"] == "first" and len(log[0]["sha"]) == 40 and log[0]["ts"] > 0
    assert gitctl.commit_url_base(repo, ("git.example.test",)) is None  # local path remote: no link
    for url, want in (
        ("git@git.example.test:me/app.git", "https://git.example.test/me/app/commit/"),
        ("ssh://git@git.example.test/me/web", "https://git.example.test/me/web/commit/"),
        ("https://git.example.test/me/x.git", "https://git.example.test/me/x/commit/"),
        ("git@evil.example:me/app.git", None),
    ):
        sh("git", "remote", "set-url", "gitdev", url, cwd=repo)
        assert gitctl.commit_url_base(repo, ("git.example.test",)) == want
    assert gitctl.commit_url_base(repo) is None  # no allowed hosts: no links
    sh("git", "remote", "remove", "gitdev", cwd=repo)
    assert gitctl.commit_url_base(repo, ("git.example.test",)) is None


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
    assert gitctl.status(repo) is None
