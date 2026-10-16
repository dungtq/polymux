"""New project planner + runner (pm.projects.newproject): spec parsing, templates, check, plan order, run results."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pm.core.config import Settings
from pm.projects import newproject as npj
from pm.projects.projects import NewProjectError


@pytest.fixture
def st(tmp_path, monkeypatch):
    """Settings with dev_root in tmp_path; git isolated from the user's config, with a committer identity."""
    (tmp_path / "dev").mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@example.test"),
                 ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@example.test")):
        monkeypatch.setenv(k, v)
    return Settings(state_dir=tmp_path / "st", dev_root=tmp_path / "dev", extra_projects=(),
                    git_allowed_hosts=("git.example.test",))


def ids(steps):
    return [s.id for s in steps]


def git(path, *args) -> str:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=10).stdout.strip()


# ------------------------------------------------------------------ parse_spec
def test_parse_spec_defaults_and_options():
    s = npj.parse_spec({"name": " demo ", "stack": "web", "hostname": "demo.example", "Bad-Key": "x", "csrf": "t"})
    assert (s.name, s.template, s.readme, s.git, s.remote, s.commit, s.agent) == ("demo", "empty", True, True, True,
                                                                                  False, None)
    assert dict(s.options) == {"stack": "web", "hostname": "demo.example"}


def test_parse_spec_flags_and_agent():
    s = npj.parse_spec({"name": "x", "readme": "0", "git": "0", "remote": "1", "commit": "1", "agent": "codex",
                        "model": "gpt-6", "permission_mode": "read-only", "prompt": "hi"})
    assert not s.readme and not s.git and not s.remote and not s.commit  # remote / commit need git
    assert s.agent == npj.AgentSpec("codex", "gpt-6", None, "read-only", None, "hi")


@pytest.mark.parametrize("fields", [{"name": "x", "agent": "other"}, {"name": "x", "agent": "nope"},
                                    {"name": "x", "agent": "claude", "prompt": "p" * (npj.MAX_PROMPT + 1)},
                                    {"name": "x", **{f"k{i}": "v" for i in range(npj.MAX_OPTIONS + 1)}}])
def test_parse_spec_refuses(fields):
    with pytest.raises(NewProjectError):
        npj.parse_spec(fields)


# ------------------------------------------------------------------ templates
def test_templates_default_and_validation(st):
    assert [t["name"] for t in npj.templates(st)] == ["empty"]
    bad = [{"name": ".x"}, {"nope": 1}, "str", {"name": "ok", "files": {"a": 1}}, {"name": "ok", "files": ["a"]}]
    s = Settings(project_templates=bad)
    assert [t["name"] for t in npj.templates(s)] == ["empty"]
    s = Settings(project_templates=[{"name": "py", "description": "Python", "files": {"a.py": ""}}, {"name": "py"}])
    assert npj.templates(s) == [{"name": "py", "description": "Python", "files": {"a.py": ""}}]


# ------------------------------------------------------------------ check
@pytest.mark.parametrize("bad", ["", ".hidden", "a/b", "../x", "a b", "node_modules", "a" * 65])
def test_check_bad_names(st, bad):
    with pytest.raises(NewProjectError) as e:
        npj.check(st, npj.Spec(name=bad))
    assert e.value.status == 400


def test_check_existing_missing_root_template_remote(st, tmp_path):
    (tmp_path / "dev" / "dup").mkdir()
    with pytest.raises(NewProjectError) as e:
        npj.check(st, npj.Spec(name="dup"))
    assert e.value.status == 409
    with pytest.raises(NewProjectError) as e:
        npj.check(Settings(dev_root=tmp_path / "missing"), npj.Spec(name="x"))
    assert e.value.status == 500
    with pytest.raises(NewProjectError, match="unknown template"):
        npj.check(st, npj.Spec(name="x", template="nope"))
    s = Settings(dev_root=st.dev_root, project_templates=[{"name": "t", "files": {"../escape": "x"}}])
    with pytest.raises(NewProjectError, match="bad file path"):
        npj.check(s, npj.Spec(name="x", template="t"))
    s = Settings(dev_root=st.dev_root, git_remote_template="git@evil.example:me/{name}.git",
                 git_allowed_hosts=("git.example.test",))
    with pytest.raises(NewProjectError, match="git_remote_template"):
        npj.check(s, npj.Spec(name="x"))
    npj.check(s, npj.Spec(name="x", remote=False))  # remote off: the template is not used


# ------------------------------------------------------------------ plan
def test_plan_order_and_remote_skip(st):
    extra = [npj.Step("acme.stack", "Create stack", "plugin", lambda *a: "ok")]
    spec = npj.Spec(name="demo", commit=True, agent=npj.AgentSpec("claude", "opus", permission_mode="plan"))
    steps = npj.plan(st, spec, extra)
    assert ids(steps) == ["folder", "readme", "git", "remote", "commit", "acme.stack", "agent"]
    remote = steps[3]
    assert remote.kind == "skip" and remote.run is None and "not configured" in remote.text
    assert steps[-1].text == "Launch claude opus (plan) in demo"
    assert ids(npj.plan(st, npj.Spec(name="demo", readme=False, git=False, remote=False))) == ["folder"]


def test_plan_remote_and_template_readme(st):
    s = Settings(dev_root=st.dev_root, git_remote_template="git@git.example.test:me/{name}.git",
                 project_templates=[{"name": "web", "files": {"README.md": "# {name}", "index.html": "<p>"}}])
    steps = npj.plan(s, npj.Spec(name="demo", template="web"))
    assert ids(steps) == ["folder", "template", "git", "remote"]  # the template brings its README
    assert steps[3].text == "Add remote origin git@git.example.test:me/demo.git" and steps[3].run is not None
    s = Settings(dev_root=st.dev_root, git_remote_template="git@git.example.test:me/{name}.git", push_remote="gitdev")
    assert npj.plan(s, npj.Spec(name="demo"))[3].text == "Add remote gitdev git@git.example.test:me/demo.git"
    assert "Add remote gitdev: not configured" in npj.plan(Settings(dev_root=st.dev_root, push_remote="gitdev"),
                                                           npj.Spec(name="demo"))[3].text


# ------------------------------------------------------------------ run
def test_run_all_core_steps(st, tmp_path):
    s = Settings(dev_root=st.dev_root, git_remote_template="ssh://git@git.example.test/me/{name}.git",
                 git_allowed_hosts=("git.example.test",), push_remote="gitdev",
                 project_templates=[{"name": "web", "files": {"src/app.txt": "app {name}\n"}}])
    got = list(npj.run(s, npj.Spec(name="demo", template="web", commit=True)))
    assert [(r.id, r.ok, r.state) for r in got] == [("folder", True, "done"), ("template", True, "done"),
                                                    ("readme", True, "done"), ("git", True, "done"),
                                                    ("remote", True, "done"), ("commit", True, "done")]
    d = tmp_path / "dev" / "demo"
    assert (d / "README.md").read_text() == "# demo\n" and (d / "src/app.txt").read_text() == "app demo\n"
    assert git(d, "remote", "get-url", "gitdev") == "ssh://git@git.example.test/me/demo.git"
    assert git(d, "log", "--format=%s") == npj.COMMIT_MSG
    assert git(d, "status", "--porcelain") == ""


def test_run_remote_not_configured_is_skipped(st, tmp_path):
    got = list(npj.run(st, npj.Spec(name="demo")))
    assert [(r.id, r.state) for r in got] == [("folder", "done"), ("readme", "done"), ("git", "done"),
                                              ("remote", "skipped")]
    assert all(r.ok for r in got) and "not configured" in got[-1].text
    assert git(tmp_path / "dev" / "demo", "remote") == ""


def test_run_refuses_before_any_change(st, tmp_path):
    (tmp_path / "dev" / "dup").mkdir()
    with pytest.raises(NewProjectError):
        npj.run(st, npj.Spec(name="dup"))
    assert sorted(p.name for p in (tmp_path / "dev").iterdir()) == ["dup"]


def test_run_stops_at_first_failure(st, tmp_path):
    seen = []

    def boom(settings, spec, path):
        seen.append(path)
        raise RuntimeError("stack failed: port busy\nsecond line")

    def launch(settings, spec, path):
        seen.append("launch")
        return "launched"

    extra = [npj.Step("acme.stack", "Create stack", "plugin", boom),
             npj.Step("acme.vm", "Create VM", "plugin", lambda *a: "vm")]
    got = list(npj.run(st, npj.Spec(name="demo", git=False, remote=False, agent=npj.AgentSpec("claude")),
                       extra, launch=launch))
    assert [(r.id, r.ok, r.state) for r in got] == [
        ("folder", True, "done"), ("readme", True, "done"), ("acme.stack", False, "failed"),
        ("acme.vm", False, "not_run"), ("agent", False, "not_run")]
    assert got[2].text == "Create stack: stack failed: port busy"
    assert seen == [tmp_path.resolve() / "dev" / "demo"]  # the launcher never ran
    assert (tmp_path / "dev" / "demo" / "README.md").exists()  # nothing is undone


def test_run_agent_step_uses_injected_launcher(st, tmp_path):
    calls = []

    def launch(settings, spec, path):
        calls.append((spec.agent.kind, Path(path).name, Path(path).is_dir()))
        return "Launched claude in window agent-claude (@7)"

    got = list(npj.run(st, npj.Spec(name="demo", git=False, agent=npj.AgentSpec("claude")), launch=launch))
    assert got[-1] == npj.StepResult("agent", True, "Launched claude in window agent-claude (@7)", "done")
    assert calls == [("claude", "demo", True)]
    no = list(npj.run(st, npj.Spec(name="demo2", git=False, agent=npj.AgentSpec("claude"))))
    assert no[-1].state == "failed" and "not available" in no[-1].text


def test_run_git_failure_reports(st, tmp_path, monkeypatch):
    monkeypatch.setattr(npj.gitctl, "GIT_BIN", str(tmp_path / "no-such-git"))
    got = list(npj.run(st, npj.Spec(name="demo")))
    assert [(r.id, r.state) for r in got] == [("folder", "done"), ("readme", "done"), ("git", "failed"),
                                              ("remote", "not_run")]
    assert got[2].text.startswith("git init: git init failed")
