"""Git tab JSON routes (web/routes/gitops.py): status, plan, guarded mutations, typed confirms, audit, errors."""

import json
import re
import subprocess
from dataclasses import replace

import pytest
from helpers import login
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.core.models import Tier

H = {"origin": "https://pm.example.test"}


def g(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(env, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    s, c = env
    p = s.dev_root / "alpha"
    g(p, "init", "-q", "-b", "main")
    g(p, "config", "user.name", "Ann")
    g(p, "config", "user.email", "ann@example.test")
    (p / "a").write_text("1\n")
    g(p, "add", "-A")
    g(p, "commit", "-q", "-m", "first")
    login(s, c)
    tok = re.search(r'name="csrf" value="([^"]+)"', c.get("/p/alpha").text).group(1)
    return s, c, p, tok


def post(c, tok, act, project="alpha", **f):
    return c.post(f"/p/{project}/git/{act}", data={"csrf": tok, **f}, headers=H)


def audit(s, prefix="git_"):
    rows = [json.loads(x) for x in s.audit_file.read_text().splitlines()]
    return [(a["action"], a["result"]) for a in rows if a["action"].startswith(prefix) and a["result"] != "attempt"]


def test_status_view_model(repo):
    s, c, p, _ = repo
    (p / "a").write_text("2\n")
    (p / "u").write_text("u")
    g(p, "branch", "feat")
    r = c.get("/p/alpha/git/status")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    d = r.json()
    assert set(d) == {"status", "branches", "stashes", "commits", "remote", "has_remote"}
    st = d["status"]
    assert st["branch"] == "main" and st["counts"] == {"staged": 0, "unstaged": 1, "untracked": 1, "conflicts": 0}
    assert {f["path"]: f["xy"] for f in st["files"]} == {"a": ".M", "u": "??"}
    assert [b["name"] for b in d["branches"]] == ["feat", "main"] and d["commits"][0]["subject"] == "first"
    assert d["remote"] == "gitdev" and d["has_remote"] is False and d["stashes"] == []
    assert c.get("/p/nope/git/status").status_code == 404


def test_status_not_a_repo(env):
    s, c = env
    login(s, c)
    r = c.get("/p/alpha/git/status")
    assert r.status_code == 404 and r.json() == {"ok": False, "error": "not a git repository"}


def test_plan_route(repo):
    s, c, p, _ = repo
    (p / "a").write_text("2\n")
    (p / "u").write_text("u")
    r = c.get("/p/alpha/git/plan", params={"action": "discard", "path": "a"})
    d = r.json()
    assert r.status_code == 200 and d["risk"] == "typed" and d["confirm"] == "a" and "cannot be undone" in d["text"]
    d = c.get("/p/alpha/git/plan", params=[("action", "stage"), ("paths", "a"), ("paths", "u")]).json()
    assert d == {"action": "stage", "text": "Stage 2 files: a, u.", "risk": "safe", "confirm": "", "blocked": "",
                 "guard": "", "requires": {}}
    assert c.get("/p/alpha/git/plan", params={"action": "discard", "path": "../x"}).json()["blocked"]
    assert c.get("/p/alpha/git/plan", params={"action": "rebase"}).status_code == 400
    assert (p / "a").read_text() == "2\n" and g(p, "status", "--porcelain").stdout == " M a\n?? u\n"  # no change


def test_stage_commit_flow_and_audit(repo):
    s, c, p, tok = repo
    (p / "a").write_text("2\n")
    (p / "u").write_text("u")
    r = post(c, tok, "stage", paths=["a", "u"])
    assert r.status_code == 200 and r.json() == {"ok": True, "text": "Staged 2 files.", "clash": []}
    assert post(c, tok, "unstage", paths="u").json()["ok"]
    r = post(c, tok, "commit", message="")
    assert r.status_code == 400 and r.json()["error"] == "empty commit message"
    r = post(c, tok, "commit", message="change a", amend="1")  # no amend field: ignored, never amends
    assert r.status_code == 200 and r.json()["text"].startswith("Committed ")
    assert g(p, "log", "-1", "--format=%an|%s").stdout.strip() == "Ann|change a"
    assert g(p, "rev-list", "--count", "HEAD").stdout.strip() == "2"
    r = post(c, tok, "commit", message="again")
    assert r.status_code == 400 and r.json()["error"] == "nothing staged to commit"
    r = post(c, tok, "stage", paths="nope")
    assert r.status_code == 404 and "not a changed file" in r.json()["error"]
    rows = audit(s)
    assert ("git_stage", "ok") in rows and ("git_unstage", "ok") in rows and ("git_commit", "ok") in rows
    assert ("git_commit", "denied") in rows and ("git_stage", "denied") in rows
    assert "change a" not in s.audit_file.read_text()  # the commit message is not audited


def test_discard_needs_typed_confirm(repo):
    s, c, p, tok = repo
    (p / "a").write_text("2\n")
    (p / "u").write_text("u")
    r = post(c, tok, "discard", path="a")
    assert r.status_code == 400 and (p / "a").read_text() == "2\n"
    assert post(c, tok, "discard", path="a", confirm="b").status_code == 400
    r = post(c, tok, "discard", path="a", confirm="a")
    assert r.status_code == 200 and (p / "a").read_text() == "1\n"
    r = post(c, tok, "discard", path="u", confirm="u")
    assert r.status_code == 400 and "new file" in r.json()["error"] and (p / "u").exists()
    assert post(c, tok, "discard", path="u", confirm="u", untracked="1").status_code == 200
    assert not (p / "u").exists()
    assert ("git_discard", "denied") in audit(s) and ("git_discard", "ok") in audit(s)


def test_stash_routes(repo):
    s, c, p, tok = repo
    assert post(c, tok, "stash").status_code == 400
    (p / "a").write_text("w\n")
    assert post(c, tok, "stash", message="mine").json()["ok"]
    assert (p / "a").read_text() == "1\n"
    assert post(c, tok, "stash-pop", index="x").status_code == 400
    assert post(c, tok, "stash-pop", index="3").status_code == 404
    assert post(c, tok, "stash-pop", index="0").json()["text"] == "Applied and dropped stash@{0}."
    (p / "a").write_text("w2\n")
    post(c, tok, "stash")
    assert post(c, tok, "stash-drop", index="0").status_code == 400
    assert post(c, tok, "stash-drop", index="0", confirm="stash@{0}").json()["ok"]
    assert c.get("/p/alpha/git/status").json()["stashes"] == []
    assert ("git_stash_drop", "denied") in audit(s) and ("git_stash_pop", "denied") in audit(s)


def test_checkout_merge_delete(repo):
    s, c, p, tok = repo
    g(p, "checkout", "-q", "-b", "feat")
    (p / "f").write_text("f")
    g(p, "add", "f")
    g(p, "commit", "-q", "-m", "feat")
    g(p, "checkout", "-q", "main")
    (p / "m").write_text("m")
    g(p, "add", "m")
    g(p, "commit", "-q", "-m", "main")
    (p / "a").write_text("dirty\n")
    r = post(c, tok, "checkout", branch="feat")
    assert r.status_code == 409 and "stash" in r.json()["error"]
    assert post(c, tok, "checkout", branch="feat", stash="1").json()["ok"]
    assert g(p, "branch", "--show-current").stdout.strip() == "feat" and (p / "a").read_text() == "dirty\n"
    post(c, tok, "checkout", branch="main", stash="1")
    g(p, "checkout", "--", "a")
    r = post(c, tok, "merge", branch="feat")
    assert r.status_code == 409 and "diverged" in r.json()["error"]
    assert post(c, tok, "merge", branch="feat", ff_only="0").status_code == 400  # no typed confirm
    r = post(c, tok, "merge", branch="feat", ff_only="0", confirm="feat")
    assert r.status_code == 200 and r.json()["text"] == "Merged feat into main."
    assert post(c, tok, "branch-delete", branch="main", confirm="main").status_code == 403
    assert post(c, tok, "branch-delete", branch="feat").status_code == 400
    assert post(c, tok, "branch-delete", branch="feat", confirm="feat").json()["ok"]
    assert post(c, tok, "checkout", branch="--orphan").status_code == 404
    rows = audit(s)
    assert ("git_merge", "ok") in rows and ("git_branch_delete", "ok") in rows and ("git_checkout", "denied") in rows


def test_merge_conflict_reports_files(repo):
    s, c, p, tok = repo
    g(p, "checkout", "-q", "-b", "feat")
    (p / "a").write_text("feat\n")
    g(p, "commit", "-q", "-am", "feat")
    g(p, "checkout", "-q", "main")
    (p / "a").write_text("main\n")
    g(p, "commit", "-q", "-am", "main")
    r = post(c, tok, "merge", branch="feat", ff_only="0", confirm="feat")
    assert r.status_code == 409 and r.json()["files"] == ["a"] and "aborted" in r.json()["error"]
    assert g(p, "status", "--porcelain").stdout == ""


def test_git_errors_are_short_and_masked(repo, monkeypatch):
    s, c, p, tok = repo
    from pm.git import ops
    secret = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"

    def boom(*a, **k):
        raise ops.GitError(f"fatal: token {secret} broke\nmore lines")

    monkeypatch.setattr(ops, "stash_push", boom)
    (p / "a").write_text("w\n")
    r = post(c, tok, "stash")
    assert r.status_code == 409 and secret not in r.text and "more lines" not in r.text and "-C" not in r.text
    assert secret not in s.audit_file.read_text()


def test_tier_and_guards(repo):
    s, c, p, tok = repo
    (p / "a").write_text("2\n")
    for act in ("stage", "discard", "commit", "stash", "stash-pop", "stash-drop", "checkout", "merge",
                "branch-delete", "unstage", "pull-stash"):
        assert c.post(f"/p/alpha/git/{act}", data={"csrf": tok}, headers={"origin": "https://evil.example"}
                      ).status_code == 403
        assert c.post(f"/p/alpha/git/{act}", data={"csrf": "bad"}, headers=H).status_code == 403
    c.app.state.settings = replace(s, tier_ceiling=Tier.SAFE)
    assert post(c, tok, "discard", path="a", confirm="a").status_code == 403
    assert post(c, tok, "branch-delete", branch="x", confirm="x").status_code == 403
    assert post(c, tok, "merge", branch="x", ff_only="0", confirm="x").status_code == 403
    rows = [json.loads(x) for x in s.audit_file.read_text().splitlines()]
    assert [(a["target"], a["detail"]) for a in rows if a["action"] == "git_merge" and a["result"] == "denied"
            and a["detail"]["reason"] == "tier"] == [("alpha", {"reason": "tier"})]  # require_tier, like the guard
    assert post(c, tok, "stage", paths="a").status_code == 200
    c.app.state.settings = replace(s, tier_ceiling=Tier.VIEW)
    assert post(c, tok, "stage", paths="a").status_code == 403
    c.app.state.settings = s
    assert post(c, tok, "stage", project="nope", paths="a").status_code == 404
    c.cookies.clear()
    assert post(c, tok, "stage", paths="a").status_code == 401
    r = c.get("/p/alpha/git/status", follow_redirects=False)
    assert r.status_code in (401, 303, 307) and "files" not in r.text


def test_audit_failure_fails_closed(repo, monkeypatch):
    s, c, p, tok = repo
    from pm.security import audit as audit_mod

    def boom(self, entry):
        raise audit_mod.AuditError("full")

    (p / "a").write_text("2\n")
    monkeypatch.setattr(audit_mod.AuditLog, "write", boom)
    assert post(c, tok, "stage", paths="a").status_code == 503
    assert g(p, "status", "--porcelain").stdout == " M a\n"
