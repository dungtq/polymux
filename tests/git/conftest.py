"""Fixtures of the pm.git tests: HOME and git config isolated in tmp_path; ``repo`` = a clone-like repo on
``main`` with two commits pushed to a bare ``gitdev`` remote."""

import pytest

from git.gitrepo import commit_file, g


@pytest.fixture(autouse=True)
def iso_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_DIR",
              "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(k, raising=False)
    return home


@pytest.fixture
def repo(tmp_path):
    bare = tmp_path / "bare.git"
    g(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    r = tmp_path / "r"
    r.mkdir()
    g(r, "init", "-q", "-b", "main")
    g(r, "config", "user.name", "Ann")
    g(r, "config", "user.email", "ann@example.test")
    g(r, "remote", "add", "gitdev", str(bare))
    commit_file(r, "a", "1\n2\n3\n", "first")
    commit_file(r, "b", "b\n", "second")
    g(r, "push", "-q", "-u", "gitdev", "main")
    return r
