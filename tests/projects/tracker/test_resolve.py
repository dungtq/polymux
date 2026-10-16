"""resolve: project from name / cwd (a worktree maps to its main project), actor id."""

import dataclasses

import pytest

from pm.core.errors import NotFound
from pm.projects.tracker import resolve

from .conftest import git


def test_project_by_name_and_cwd(tk):
    root = tk.dev_root / "proj"
    (root / "sub").mkdir()
    assert resolve.project_for(tk, name="proj") == ("proj", root)
    assert resolve.project_for(tk, cwd=root / "sub") == ("proj", root)
    with pytest.raises(NotFound):
        resolve.project_for(tk, name="nope")
    with pytest.raises(NotFound):
        resolve.project_for(tk, cwd=tk.state_dir.parent)


def test_worktree_resolves_to_main(tk, repo):
    wt = tk.dev_root / "proj-wt-feature"
    git(repo, "worktree", "add", "-q", "-b", "feature", str(wt))
    assert resolve.project_for(tk, cwd=wt) == ("proj", repo)
    assert resolve.project_for(tk, name="proj-wt-feature") == ("proj", repo)


def test_actor_env_then_actor_cmd_then_agent(tk, monkeypatch, tmp_path):
    assert resolve.actor(tk) == "claude-test01"
    monkeypatch.delenv("PM_ACTOR")
    assert resolve.actor(tk) == "agent"  # no actor_cmd: nothing is run
    assert resolve.actor(None) == "agent"
    assert resolve.actor(dataclasses.replace(tk, actor_cmd=(str(tmp_path / "missing"), "id"))) == "agent"
    script = tmp_path / "tmux-agents"
    script.write_text("#!/bin/sh\n[ \"$1\" = id ] && echo codex-1a2b3c\n")
    script.chmod(0o755)
    assert resolve.actor(dataclasses.replace(tk, actor_cmd=(str(script), "id"))) == "codex-1a2b3c"
    assert resolve.actor(dataclasses.replace(tk, actor_cmd=(str(script),))) == "agent"  # no "id": no output
    assert resolve.actor(dataclasses.replace(tk, actor_cmd=(str(script), 1))) == "agent"  # non-string argv
    script.write_text("#!/bin/sh\necho 'not an id; rm -rf'\n")
    assert resolve.actor(dataclasses.replace(tk, actor_cmd=(str(script), "id"))) == "agent"
    monkeypatch.setenv("PM_ACTOR", "bad id with spaces")
    assert resolve.actor(tk) == "agent"
