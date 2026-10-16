"""Tracker test fixtures: settings rooted in tmp (dev_root, state; never ~/dev or ~/.local/state/pm),
a project ``proj`` under the tmp dev root, git isolated from the user's global/system config."""

import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from pm.core import timefmt
from pm.core.config import Settings
from pm.projects.tracker import cli

FIX = Path(__file__).parents[2] / "fixtures" / "tracker"
ALL_SAMPLES = sorted(FIX.glob("*.md"))
SAMPLES = [p for p in ALL_SAMPLES if p.stem.rsplit("-", 1)[1] in ("BACKLOG", "TODO")]  # item files
DOC_SAMPLES = [p for p in ALL_SAMPLES if p not in SAMPLES]  # CHANGELOG.md / FEATURES.md (T3)


def sample_kind(path: Path) -> tuple[str, str]:
    """(project, file) of a fixture name like ``alpha-BACKLOG.md``."""
    proj, kind = path.stem.rsplit("-", 1)
    return proj, kind.lower()


def display_today() -> str:
    """Today in the Settings default display offset (what pmctl configures; the run's site default)."""
    return datetime.now(timezone(timedelta(hours=Settings().display_utc_offset_h))).date().isoformat()


@pytest.fixture(autouse=True)
def _display_offset():
    """In-process tracker calls stamp dates in the same offset as pmctl (the Settings default)."""
    old = timefmt.offset_hours()
    timefmt.configure(Settings())
    yield
    timefmt.configure(SimpleNamespace(display_utc_offset_h=old))


@pytest.fixture
def git_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k, v in (("NAME", "pm test"), ("EMAIL", "pm@test.invalid")):
        monkeypatch.setenv(f"GIT_AUTHOR_{k}", v)
        monkeypatch.setenv(f"GIT_COMMITTER_{k}", v)
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n")


@pytest.fixture
def tk(tmp_path, monkeypatch):
    """Settings for the tracker: dev root ``tmp/dev`` with an empty project ``proj``, state in ``tmp/state``,
    Claude's home ``tmp/claude`` (not created)."""
    dev = tmp_path / "dev"
    (dev / "proj").mkdir(parents=True)
    monkeypatch.setenv("PM_ACTOR", "claude-test01")
    return Settings(dev_root=dev, extra_projects=(), state_dir=tmp_path / "state", tmux_socket="pmtest-unused",
                    token_file=tmp_path / "cfg/token", totp_file=tmp_path / "cfg/totp",
                    claude_dir=tmp_path / "claude")


def git(cwd, *args) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          timeout=20).stdout


@pytest.fixture
def repo(tk, git_env):
    """``proj`` as a git repo with one commit (BACKLOG.md + TODO.md from the demo samples)."""
    root = Path(tk.dev_root) / "proj"
    shutil.copy(FIX / "demo-BACKLOG.md", root / "BACKLOG.md")
    shutil.copy(FIX / "demo-TODO.md", root / "TODO.md")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def cfg(tk, tmp_path):
    p = tmp_path / "pm.toml"
    p.write_text(f'dev_root = "{tk.dev_root}"\nextra_projects = []\nstate_dir = "{tk.state_dir}"\n'
                 f'claude_dir = "{tk.claude_dir}"\n')
    return p


@pytest.fixture
def pmctl(cfg, capsys, monkeypatch, repo):
    """Run pmctl in-process (from the repo dir; ``cwd=`` another dir): (exit code, stdout, stderr)."""
    monkeypatch.chdir(repo)

    def run(*argv, cwd=None):
        if cwd is not None:
            monkeypatch.chdir(cwd)
        try:
            code = cli.main(["--config", str(cfg), *argv])
        finally:
            monkeypatch.chdir(repo)
        o = capsys.readouterr()
        return code, o.out, o.err
    return run


@pytest.fixture
def on_path(cfg, tmp_path, monkeypatch):
    """A ``pmctl`` on PATH that uses the tmp config (the hooks and the merge driver call ``pmctl``)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    w = bindir / "pmctl"
    w.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m pm.projects.tracker.cli --config "{cfg}" "$@"\n')
    w.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
