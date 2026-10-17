"""pm.git.ops on temp repos (git init in tmp_path; HOME points into tmp_path, never a real repo)."""

import subprocess
from pathlib import Path

import pytest

from git.gitrepo import commit_file, diverge, g, head, remote_commit
from pm.core.errors import Conflict, Denied, Invalid, NotFound
from pm.git import ctl, ops
from pm.git import remote as gitremote

R = "gitdev"  # the fixture's remote (pm.toml push_remote in the app)


def by_path(st):
    return {f.path: f for f in st.files}


# ------------------------------------------------------------------ status
def test_status_detail_codes_and_counts(repo):
    (repo / "a").write_text("1\n2\n3\nx\n")
    g(repo, "add", "a")
    (repo / "a").write_text("1\n2\n3\nx\ny\n")  # MM
    (repo / "b").unlink()  # .D
    (repo / "n").write_text("new")
    g(repo, "add", "n")  # A.
    (repo / "u").write_text("u")  # ??
    (repo / "d").mkdir()
    (repo / "d" / "deep").write_text("x")  # untracked-files=all lists the file
    st = ops.status_detail(repo, remote=R)
    f = by_path(st)
    assert {k: v.xy for k, v in f.items()} == {"a": "MM", "b": ".D", "n": "A.", "u": "??", "d/deep": "??"}
    assert f["a"].staged and f["a"].unstaged and not f["b"].staged and f["b"].unstaged and f["u"].untracked
    assert st.counts() == {"staged": 2, "unstaged": 2, "untracked": 2, "conflicts": 0}
    assert st.branch == "main" and not st.detached and st.upstream == "gitdev/main" and (st.ahead, st.behind) == (0, 0)
    assert st.dirty and st.total == 5 and len(st.head) == 12 and not st.merging and st.stashes == 0
    v = st.view()
    assert v["counts"]["untracked"] == 2 and v["files"][0]["path"]


def test_status_rename_detached_ahead_and_remote_fallback(repo):
    g(repo, "mv", "b", "b2")
    f = by_path(ops.status_detail(repo, remote=R))
    assert f["b2"].xy == "R." and f["b2"].orig == "b" and f["b2"].staged
    g(repo, "commit", "-q", "-m", "mv")
    assert ops.status_detail(repo, remote=R).ahead == 1
    g(repo, "branch", "--unset-upstream")
    st = ops.status_detail(repo, remote=R)  # no upstream: <remote>/<branch> is used
    assert st.upstream == "gitdev/main" and st.ahead == 1
    st = ops.status_detail(repo, remote="origin")  # another push remote: the repo has none, so no fallback
    assert st.upstream == "" and st.ahead == 0
    g(repo, "remote", "add", "origin", str(repo.parent / "bare.git"))
    g(repo, "fetch", "-q", "origin")
    st = ops.status_detail(repo, remote="origin")
    assert st.upstream == "origin/main" and st.ahead == 1
    with pytest.raises(ctl.GitDenied):
        ops.status_detail(repo, remote="-x")
    g(repo, "checkout", "-q", "--detach")
    st = ops.status_detail(repo, remote=R)
    assert st.detached and st.branch is None


def test_status_unborn_and_not_a_repo(tmp_path):
    r = tmp_path / "x"
    r.mkdir()
    with pytest.raises(NotFound):
        ops.status_detail(r, remote=R)
    g(r, "init", "-q", "-b", "main")
    (r / "f").write_text("f")
    st = ops.status_detail(r, remote=R)
    assert st.head == "" and st.branch == "main" and by_path(st)["f"].untracked


def test_subdir_project_uses_repo_root(repo):
    (repo / "sub").mkdir()
    (repo / "sub" / "x").write_text("x")
    assert "sub/x" in by_path(ops.status_detail(repo / "sub", remote=R))


# ------------------------------------------------------------------ stage / unstage / paths
def test_stage_unstage_and_deleted(repo):
    (repo / "a").write_text("changed")
    (repo / "b").unlink()
    (repo / "u").write_text("u")
    ops.stage(repo, ["a", "b", "u"])
    f = by_path(ops.status_detail(repo, remote=R))
    assert (f["a"].xy, f["b"].xy, f["u"].xy) == ("M.", "D.", "A.")
    ops.unstage(repo, ["a", "u"])
    f = by_path(ops.status_detail(repo, remote=R))
    assert (f["a"].xy, f["u"].xy) == (".M", "??")
    with pytest.raises(Invalid, match="nothing staged"):
        ops.unstage(repo, ["a"])


def test_unstage_on_unborn_branch(tmp_path):
    r = tmp_path / "x"
    r.mkdir()
    g(r, "init", "-q", "-b", "main")
    (r / "f").write_text("1")
    g(r, "add", "f")
    (r / "f").write_text("12")  # AM: `rm --cached` would refuse this
    ops.unstage(r, ["f"])
    assert by_path(ops.status_detail(r, remote=R))["f"].untracked


@pytest.mark.parametrize("bad", ["../etc/passwd", "/etc/passwd", "", "a/../a", "x\0y", "./", ".."])
def test_paths_must_stay_inside(repo, bad):
    (repo / "a").write_text("changed")
    with pytest.raises((Invalid, Denied)):
        ops.stage(repo, [bad])


def test_paths_must_be_changed_and_are_literal(repo, tmp_path):
    (repo / "*").write_text("star")
    (repo / ":(top)a").write_text("magic")
    (repo / "a").write_text("changed")
    with pytest.raises(NotFound):
        ops.stage(repo, ["b"])  # tracked but unchanged
    with pytest.raises(NotFound):
        ops.stage(repo, ["nope"])
    with pytest.raises(Invalid):
        ops.stage(repo, [])
    with pytest.raises(Invalid):
        ops.stage(repo, "a")  # a str is not a list of paths
    ops.stage(repo, ["*"])
    ops.stage(repo, [":(top)a"])
    f = by_path(ops.status_detail(repo, remote=R))
    assert f["*"].xy == "A." and f[":(top)a"].xy == "A." and f["a"].xy == ".M"  # no glob, no pathspec magic


def test_symlinked_dir_escape_is_denied(repo, tmp_path):
    out = tmp_path / "outside"
    out.mkdir()
    (out / "x").write_text("x")
    (repo / "link").symlink_to(out)
    with pytest.raises(Denied):
        ops.check_path(repo, "link/x")
    (repo / "leaf").symlink_to(tmp_path / "outside" / "x")  # a symlink leaf is fine: git tracks the link
    ops.stage(repo, ["leaf"])
    assert by_path(ops.status_detail(repo, remote=R))["leaf"].xy == "A."


# ------------------------------------------------------------------ discard
def test_discard_tracked_staged_and_unstaged(repo):
    (repo / "a").write_text("staged")
    g(repo, "add", "a")
    (repo / "a").write_text("staged+more")
    ops.discard(repo, "a")
    assert (repo / "a").read_text() == "1\n2\n3\n" and "a" not in by_path(ops.status_detail(repo, remote=R))
    (repo / "b").unlink()
    ops.discard(repo, "b")
    assert (repo / "b").read_text() == "b\n"


def test_discard_new_files_need_untracked(repo):
    (repo / "u").write_text("u")
    with pytest.raises(Invalid, match="new file"):
        ops.discard(repo, "u")
    assert (repo / "u").exists()
    ops.discard(repo, "u", untracked=True)
    assert not (repo / "u").exists()
    (repo / "n").write_text("n")
    g(repo, "add", "n")
    (repo / "n").write_text("n2")
    ops.discard(repo, "n", untracked=True)
    assert not (repo / "n").exists() and ops.status_detail(repo, remote=R).total == 0
    with pytest.raises(NotFound):
        ops.discard(repo, "u", untracked=True)


def test_discard_rename_restores_source(repo):
    g(repo, "mv", "b", "b2")
    ops.discard(repo, "b2", untracked=True)
    assert (repo / "b").read_text() == "b\n" and not (repo / "b2").exists() and ops.status_detail(repo, remote=R).total == 0


# ------------------------------------------------------------------ commit
def test_commit_rules_and_author(repo):
    with pytest.raises(Denied):
        ops.commit(repo, "x", amend=True)
    with pytest.raises(Invalid, match="empty"):
        ops.commit(repo, "   \n")
    with pytest.raises(Invalid, match="nothing staged"):
        ops.commit(repo, "msg")
    (repo / "a").write_text("new")
    (repo / "u").write_text("u")
    ops.stage(repo, ["a"])
    out = ops.commit(repo, "--amend fix things\n\nbody")
    assert out.sha and out.text.startswith(f"Committed {out.sha}")
    log = g(repo, "log", "-1", "--format=%an <%ae>|%s").stdout.strip()
    assert log == "Ann <ann@example.test>|--amend fix things"  # a message is a value, never a flag
    assert g(repo, "rev-list", "--count", "HEAD").stdout.strip() == "3"
    assert by_path(ops.status_detail(repo, remote=R))["u"].untracked  # untracked files are not committed


def test_commit_without_author(repo):
    g(repo, "config", "--unset", "user.name")
    g(repo, "config", "--unset", "user.email")
    g(repo, "config", "user.useConfigOnly", "true")
    (repo / "a").write_text("new")
    ops.stage(repo, ["a"])
    with pytest.raises(Invalid, match="author"):
        ops.commit(repo, "msg")


# ------------------------------------------------------------------ stash
def test_stash_push_list_pop_drop(repo):
    with pytest.raises(Invalid, match="nothing to stash"):
        ops.stash_push(repo, "x")
    (repo / "a").write_text("work")
    ops.stash_push(repo, "my  work\nline")
    (repo / "b").write_text("more")
    ops.stash_push(repo)
    s = ops.stash_list(repo)
    assert [x.ref for x in s] == ["stash@{0}", "stash@{1}"] and s[1].subject.endswith("my work line")
    assert ops.status_detail(repo, remote=R).total == 0 and ops.status_detail(repo, remote=R).stashes == 2
    ops.stash_pop(repo, 1)
    assert (repo / "a").read_text() == "work" and len(ops.stash_list(repo)) == 1
    ops.stash_drop(repo, 0)
    assert ops.stash_list(repo) == []
    with pytest.raises(NotFound):
        ops.stash_pop(repo, 0)
    with pytest.raises(Invalid):
        ops.stash_drop(repo, -1)
    (repo / "u").write_text("u")
    ops.stash_push(repo, untracked=True)
    assert not (repo / "u").exists()


def test_stash_pop_clash_keeps_stash(repo):
    (repo / "a").write_text("stashed\n")
    ops.stash_push(repo)
    commit_file(repo, "a", "committed\n", "c")
    out = ops.stash_pop(repo, 0)
    assert out.clash == ("a",) and "kept" in out.text and len(ops.stash_list(repo)) == 1


# ------------------------------------------------------------------ branches / checkout
def test_branches(repo):
    g(repo, "branch", "feat")
    g(repo, "checkout", "-q", "feat")
    commit_file(repo, "f", "f", "feat work")
    g(repo, "checkout", "-q", "main")
    commit_file(repo, "m", "m", "main work")
    b = {x.name: x for x in ops.branches(repo, remote=R)}
    assert b["main"].current and b["main"].upstream == "gitdev/main" and (b["main"].ahead, b["main"].behind) == (1, 0)
    assert not b["feat"].current and b["feat"].upstream == "" and b["feat"].subject == "feat work"
    g(repo, "push", "-q", "gitdev", "feat")
    commit_file(repo, "m2", "m", "x")
    g(repo, "checkout", "-q", "feat")
    commit_file(repo, "f2", "f", "feat 2")
    g(repo, "checkout", "-q", "main")
    feat = {x.name: x for x in ops.branches(repo, remote=R)}["feat"]
    assert feat.upstream == "gitdev/feat" and (feat.ahead, feat.behind) == (1, 0) and feat.ts > 0
    feat = {x.name: x for x in ops.branches(repo, remote="origin")}["feat"]  # another name: no such remote ref
    assert feat.upstream == "" and (feat.ahead, feat.behind) == (0, 0)


def test_checkout_guards_and_stash(repo):
    g(repo, "branch", "feat")
    with pytest.raises(NotFound):
        ops.checkout(repo, "nope")
    with pytest.raises(NotFound):
        ops.checkout(repo, "--orphan")
    with pytest.raises(Invalid, match="already"):
        ops.checkout(repo, "main")
    (repo / "a").write_text("dirty")
    with pytest.raises(Conflict, match="stash"):
        ops.checkout(repo, "feat")
    out = ops.checkout(repo, "feat", stash=True)
    assert "carried" in out.text and ops.status_detail(repo, remote=R).branch == "feat"
    assert (repo / "a").read_text() == "dirty" and ops.stash_list(repo) == []
    g(repo, "checkout", "--", "a")
    (repo / "u").write_text("untracked only")
    assert ops.checkout(repo, "main").text == "On main."


def test_checkout_stash_pop_clash(repo):
    g(repo, "checkout", "-q", "-b", "feat")
    commit_file(repo, "a", "feat\n", "feat a")
    g(repo, "checkout", "-q", "main")
    (repo / "a").write_text("local\n")
    out = ops.checkout(repo, "feat", stash=True)
    assert out.clash == ("a",) and ops.status_detail(repo, remote=R).branch == "feat" and len(ops.stash_list(repo)) == 1


# ------------------------------------------------------------------ merge / delete
def test_merge_ff_and_guards(repo):
    g(repo, "checkout", "-q", "-b", "feat")
    commit_file(repo, "f", "f", "feat")
    g(repo, "checkout", "-q", "main")
    with pytest.raises(Invalid):
        ops.merge(repo, "main")
    (repo / "a").write_text("dirty")
    with pytest.raises(Conflict, match="uncommitted"):
        ops.merge(repo, "feat")
    g(repo, "checkout", "--", "a")
    out = ops.merge(repo, "feat")
    assert "Fast-forwarded" in out.text and head(repo) == g(repo, "rev-parse", "feat").stdout.strip()
    assert "already" in ops.merge(repo, "feat").text


def test_merge_non_ff(repo):
    diverge(repo)
    before = head(repo)
    with pytest.raises(Conflict, match="diverged"):
        ops.merge(repo, "feat")
    assert head(repo) == before
    ops.merge(repo, "feat", ff_only=False)
    assert g(repo, "rev-list", "--count", "--merges", "HEAD").stdout.strip() == "1"


def test_merge_conflict_aborts_cleanly(repo):
    diverge(repo, main_text="1\n2\n3\nmain\n")
    before = head(repo)
    with pytest.raises(ops.MergeConflict) as e:
        ops.merge(repo, "feat", ff_only=False)
    assert e.value.files == ("a",) and e.value.status == 409
    st = ops.status_detail(repo, remote=R)
    assert head(repo) == before and st.total == 0 and not st.merging


def test_delete_branch(repo):
    with pytest.raises(Denied):
        ops.delete_branch(repo, "main")
    diverge(repo)
    with pytest.raises(Conflict, match="not fully merged"):
        ops.delete_branch(repo, "feat")
    ops.delete_branch(repo, "feat", unmerged_ok=True)
    assert not ctl.branch_exists(repo, "feat")
    g(repo, "branch", "done")
    ops.delete_branch(repo, "done")
    with pytest.raises(NotFound):
        ops.delete_branch(repo, "done")


# ------------------------------------------------------------------ pull with stash
def test_pull_stash(repo, tmp_path):
    remote_commit(tmp_path, "a", "1\n2\n3\nremote\n")
    gitremote.fetch(repo, remote=R)
    (repo / "a").write_text("local\n1\n2\n3\n")
    out = ops.pull_stash(repo, remote=R)
    assert out.clash == () and (repo / "a").read_text() == "local\n1\n2\n3\nremote\n" and ops.stash_list(repo) == []
    assert ops.status_detail(repo, remote=R).behind == 0


def test_pull_stash_pop_clash(repo, tmp_path):
    remote_commit(tmp_path, "a", "remote\n")
    gitremote.fetch(repo, remote=R)
    (repo / "a").write_text("local\n")
    out = ops.pull_stash(repo, remote=R)
    assert out.clash == ("a",) and len(ops.stash_list(repo)) == 1


# ------------------------------------------------------------------ no force, allow-list
def test_no_force_anywhere(repo, tmp_path, monkeypatch):
    seen = []
    real = ctl.run_argv

    def rec(argv, **kw):
        seen.append(list(argv))
        return real(argv, **kw)

    monkeypatch.setattr(ctl, "run_argv", rec)
    diverge(repo)
    ops.merge(repo, "feat", ff_only=False)
    commit_file(repo, "z", "z", "z")
    gitremote.push(repo, remote=R)
    (repo / "a").write_text("w")
    ops.checkout(repo, "feat", stash=True)
    ops.checkout(repo, "main", stash=True)
    ops.pull_stash(repo, remote=R)
    pushes = [a for a in seen if "push" in a and "stash" not in a]
    assert pushes and all(a[a.index("push") + 1:] == ["gitdev", "main"] for a in pushes)
    for a in seen:
        assert not any(x in ("--force", "-f", "--force-with-lease", "--amend", "--hard", "--no-verify")
                       or x.startswith("+") for x in a), a


@pytest.mark.parametrize("args", [
    ["push", "gitdev", "main"], ["reset", "--hard"], ["reset", "HEAD~1"], ["checkout", "x"], ["clean", "-fd"],
    ["commit", "--amend", "-m", "x"], ["commit", "--no-verify", "-m", "x"], ["switch", "-f", "--", "x"],
    ["switch", "--force", "--", "x"], ["branch", "--delete", "--", "x"], ["config", "a.b", "c"], [],
    ["stash", "push", "-fq"], ["rebase", "main"],
])
def test_check_listed_denied(args):
    with pytest.raises(ctl.GitDenied):
        ctl.check_listed(args, ctl.OPS_ALLOWED)


def test_check_listed_values_after_double_dash_are_free():
    ctl.check_listed(["add", "--", "-f", "--force", "+x"], ctl.OPS_ALLOWED)  # file names, after --
    ctl.check_listed(["reset", "-q", "--", "-f"], ctl.OPS_ALLOWED)


def test_forbidden_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".cfg").mkdir()
    with pytest.raises(ctl.GitDenied):
        ops.status_detail(tmp_path / ".cfg", remote=R)


def test_view_names_the_given_remote(repo):
    v = ops.view(repo, remote=R)
    assert v["remote"] == R and v["has_remote"] and v["status"]["upstream"] == "gitdev/main"
    assert v["commit_url_base"] is None  # no allowed hosts given: no commit links
    w = ops.view(repo, remote="origin")
    assert w["remote"] == "origin" and not w["has_remote"] and w["commit_url_base"] is None
    with pytest.raises(ctl.GitDenied):
        ops.view(repo, remote="a b")
