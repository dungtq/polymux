"""pm.git.plans: what a Git tab action will do, on temp repos (fixtures: tests/git/conftest.py)."""

import pytest

from git.gitrepo import diverge, g, head, remote_commit
from pm.core.errors import Invalid
from pm.git import ctl, ops, plans


def _snap(r):
    return g(r, "status", "--porcelain=v2", "--branch").stdout + g(r, "stash", "list").stdout + head(r)


def test_plans_risks_and_no_side_effects(repo):
    diverge(repo)
    (repo / "a").write_text("dirty")
    (repo / "u").write_text("u")
    before = _snap(repo)
    p = {a: plans.plan(repo, a, args) for a, args in [
        ("stage", {"paths": ["a", "u"]}), ("unstage", {"paths": ["a"]}), ("discard", {"path": "a"}),
        ("commit", {"message": "x"}), ("stash", {"message": "m", "untracked": "1"}), ("checkout", {"branch": "feat"}),
        ("merge", {"branch": "feat"}), ("branch-delete", {"branch": "feat"}), ("push", {}), ("pull", {}),
        ("stash-pop", {"index": "0"}),
    ]}
    assert _snap(repo) == before
    assert p["stage"].risk == "safe" and "2 files: a, u" in p["stage"].text and not p["stage"].blocked
    assert p["unstage"].blocked and p["commit"].blocked == "nothing staged to commit"
    assert (p["discard"].risk, p["discard"].confirm, p["discard"].requires) == ("typed", "a", {})
    assert "2 changed files (untracked files too)" in p["stash"].text
    assert (p["checkout"].guard, p["checkout"].requires, p["checkout"].risk) == ("dirty", {"stash": "1"}, "confirm")
    assert p["merge"].guard == "dirty" and p["merge"].blocked
    assert (p["branch-delete"].risk, p["branch-delete"].confirm, p["branch-delete"].requires) == \
        ("typed", "feat", {"unmerged": "1"})
    assert "Push 1 commit on main to gitdev/main. Never forced." == p["push"].text
    assert p["pull"].blocked and p["stash-pop"].blocked.startswith("no stash")
    assert plans.plan(repo, "discard", {"path": "u"}).requires == {"untracked": "1"}
    g(repo, "checkout", "--", "a")
    m = plans.plan(repo, "merge", {"branch": "feat"})
    assert (m.risk, m.confirm, m.guard, m.requires) == ("typed", "feat", "diverged", {"ff_only": "0"})
    (repo / "a").write_text("dirty")
    ops.stash_push(repo, "keep")
    d = plans.plan(repo, "stash-drop", {"index": "0"})
    assert (d.risk, d.confirm) == ("typed", "stash@{0}") and "keep" in d.text
    assert plans.plan(repo, "stash-pop", {"index": "0"}).risk == "confirm"
    assert plans.plan(repo, "checkout", {"branch": "feat"}).text == "Check out feat (now on main)."
    assert plans.plan(repo, "stash", {}).blocked == "nothing to stash"
    with pytest.raises(Invalid):
        plans.plan(repo, "rebase", {})
    assert plans.plan(repo, "stash-drop", {"index": "x"}).blocked == "bad stash index"


def test_plan_commit_and_ff_merge(repo):
    (repo / "a").write_text("x")
    ops.stage(repo, ["a"])
    p = plans.plan(repo, "commit", {"message": "Fix it\n\nmore"})
    assert p.text == 'Commit 1 staged file on main as Ann <ann@example.test>: "Fix it".' and not p.blocked
    assert plans.plan(repo, "commit", {"message": ""}).blocked == "empty commit message"
    ops.commit(repo, "c")
    g(repo, "branch", "old", "HEAD~1")
    bd = plans.plan(repo, "branch-delete", {"branch": "old"})
    assert bd.requires == {} and "every commit" in bd.text
    g(repo, "checkout", "-q", "old")
    assert plans.plan(repo, "merge", {"branch": "main"}).text.startswith("Fast-forward old to main (+1 commit)")


def test_plan_pull_and_push_after_a_remote_commit(repo, tmp_path):
    remote_commit(tmp_path, "a", "1\n2\n3\nremote\n")
    ctl.fetch(repo)
    (repo / "a").write_text("local\n1\n2\n3\n")
    p = plans.plan(repo, "pull", {})
    assert p.guard == "clash" and p.requires == {"stash": "1"} and p.risk == "confirm" and "a" in p.text
    assert plans.plan(repo, "push", {}).guard == "behind"


def test_plan_commit_blocked_without_author(repo):
    g(repo, "config", "--unset", "user.name")
    g(repo, "config", "--unset", "user.email")
    g(repo, "config", "user.useConfigOnly", "true")
    (repo / "a").write_text("new")
    ops.stage(repo, ["a"])
    assert "author" in plans.plan(repo, "commit", {"message": "m"}).blocked
