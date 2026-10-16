"""gitglue + git.ctl tracker helpers on a tmp repo: hooks install, stale, render --stage, merge driver."""

import os
import shutil
import stat
import subprocess

import pytest

from pm.core.errors import Conflict
from pm.git import ctl as gitctl
from pm.projects.tracker import gitglue, mdgen, mdimport, store

from .conftest import git

ATTRS = "/BACKLOG.md merge=pmctl\n/TODO.md merge=pmctl\n/CHANGELOG.md merge=pmctl\n/FEATURES.md merge=pmctl\n"


@pytest.fixture
def trk(tk, repo):
    mdimport.import_project(tk, "proj", repo)
    return tk


def test_install_hooks(repo):
    msgs = gitglue.install_hooks(repo)
    hook = repo / ".git" / "hooks" / "pre-commit"
    assert hook.read_text() == gitglue.HOOK and stat.S_IMODE(hook.stat().st_mode) == 0o755
    post = repo / ".git" / "hooks" / "post-commit"
    assert post.read_text() == gitglue.POST_COMMIT and stat.S_IMODE(post.stat().st_mode) == 0o755
    assert (repo / ".gitattributes").read_text() == ATTRS
    assert git(repo, "config", "--local", "merge.pmctl.driver").strip() == "pmctl render --merge %O %A %B %P"
    assert git(repo, "config", "--local", "merge.pmctl.name").strip()
    assert len(msgs) == 4
    again = gitglue.install_hooks(repo)
    assert all("already" in m for m in again[:3])
    assert (repo / ".gitattributes").read_text().count("merge=pmctl") == 4


def test_gitattributes_keeps_existing_lines(repo):
    (repo / ".gitattributes").write_text("*.png binary")
    gitglue.install_hooks(repo)
    assert (repo / ".gitattributes").read_text() == "*.png binary\n" + ATTRS


def test_gitattributes_anchored_nested_todo_not_matched(repo):
    """An old unanchored line counts as missing and is replaced; a nested TODO.md never gets merge=pmctl."""
    (repo / ".gitattributes").write_text("*.png binary\nTODO.md  merge=pmctl\nBACKLOG.md merge=pmctl\n")
    msgs = gitglue.install_hooks(repo)
    assert "already" not in msgs[2]
    assert (repo / ".gitattributes").read_text() == "*.png binary\n" + ATTRS
    (repo / "sub").mkdir()
    out = git(repo, "check-attr", "merge", "--", "TODO.md", "sub/TODO.md", "BACKLOG.md")
    assert out.splitlines() == ["TODO.md: merge: pmctl", "sub/TODO.md: merge: unspecified", "BACKLOG.md: merge: pmctl"]
    assert "already" in gitglue.install_hooks(repo)[2]


def test_install_replaces_old_pmt_setup(repo):
    """Before the rename the CLI was ``pmt``: its hook, attribute lines and merge.pmt keys are replaced."""
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(f"#!/bin/sh\n{gitglue.HOOK_MARK} (written by `pmt hooks install`)\nexec pmt render --stage\n")
    (repo / ".gitattributes").write_text("*.png binary\n/BACKLOG.md merge=pmt\nTODO.md merge=pmt\n")
    git(repo, "config", "--local", "merge.pmt.driver", "pmt render --merge %A %P")
    gitglue.install_hooks(repo)
    assert hook.read_text() == gitglue.HOOK and "command -v pmctl" in gitglue.HOOK
    assert (repo / ".gitattributes").read_text() == "*.png binary\n" + ATTRS
    assert "merge.pmt." not in git(repo, "config", "--local", "--list")
    assert not hook.with_name("pre-commit.pmctl-old").exists()


def test_hook_exits_0_without_pmctl(tmp_path):
    """The pre-commit hook never blocks a commit when ``pmctl`` is not on PATH."""
    hook = tmp_path / "pre-commit"
    hook.write_text(gitglue.HOOK)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    cp = subprocess.run(["/bin/sh", str(hook)], env={"PATH": str(empty)}, capture_output=True, text=True,
                        timeout=20)
    assert cp.returncode == 0 and cp.stderr == ""


def test_foreign_hook_refused_unless_force(repo):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\necho mine\n")
    hook.chmod(0o700)
    with pytest.raises(Conflict):
        gitglue.install_hooks(repo)
    assert hook.read_text() == "#!/bin/sh\necho mine\n"
    gitglue.install_hooks(repo, force=True)
    assert hook.read_text() == gitglue.HOOK
    old = hook.with_name("pre-commit.pmctl-old")
    assert old.read_text() == "#!/bin/sh\necho mine\n" and stat.S_IMODE(old.stat().st_mode) == 0o700


def test_shared_hooks_path_refused(repo, tmp_path):
    git(repo, "config", "core.hooksPath", str(tmp_path / "shared-hooks"))
    with pytest.raises(Conflict):
        gitglue.install_hooks(repo)
    assert not (tmp_path / "shared-hooks" / "pre-commit").exists()


def test_stale_and_render_into(trk, repo):
    assert gitglue.stale(trk, "proj", repo) == []
    (repo / "TODO.md").write_text("hand edit\n")
    assert gitglue.stale(trk, "proj", repo) == ["TODO.md"]
    assert gitglue.render_into(trk, "proj", repo) == ["TODO.md"]
    assert gitglue.stale(trk, "proj", repo) == []


def test_stage_uses_the_hooks_index(trk, repo, monkeypatch, tmp_path):
    store.add_item(trk, "proj", "backlog", "staged")
    index = tmp_path / "next-index"
    index.write_bytes((repo / ".git" / "index").read_bytes())
    monkeypatch.setenv("GIT_INDEX_FILE", str(index))
    gitglue.stage(trk, "proj", repo)
    monkeypatch.delenv("GIT_INDEX_FILE")
    assert git(repo, "diff", "--cached", "--name-only") == ""  # real index untouched
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    out = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--name-only"], env=env, check=True,
                         capture_output=True, text=True, timeout=20).stdout
    assert out.split() == ["BACKLOG.md"]


def test_ctl_stage_without_index_env(trk, repo):
    (repo / "BACKLOG.md").write_text("changed\n")
    gitctl.stage(repo, ["BACKLOG.md"], keep_index_env=False)
    assert git(repo, "diff", "--cached", "--name-only").split() == ["BACKLOG.md"]


def test_merge_writes_render(trk, repo, tmp_path):
    base, theirs = tmp_path / "base", tmp_path / "theirs"
    base.write_text((repo / "BACKLOG.md").read_text())
    theirs.write_text(base.read_text())
    result = tmp_path / "merge-result"
    result.write_text("<<<<<<< conflict\n")
    assert gitglue.merge(trk, "proj", base, result, theirs, "BACKLOG.md") is True
    assert result.read_text() == mdgen.render_text(trk, "proj", "backlog")
    assert gitglue.merge(trk, "proj", base, result, theirs, "docs/BACKLOG.md") is False
    assert gitglue.merge(trk, "other", base, result, theirs, "TODO.md") is False


def test_branch_changes():
    base = "# B\n\n## Inbox\n\n- [ ] a\n- [ ] b [#7]\n- [x] c\n\n## Later\n\n- [ ] d\n"
    theirs = ("# B\n\n## Inbox\n\n- [ ] a\n- [x] b renamed [#7]\n- [x] c\n- [x] new done\n\n## Later\n\n"
              "- [x] d\n- [ ] new later\n\n## Fresh\n\n- [ ] new fresh\n")
    added, ticked = gitglue.branch_changes(base, theirs, "backlog")
    assert added == [("new done", "Inbox", True), ("new later", "Later", False), ("new fresh", "Fresh", False)]
    assert ticked == [(7, "b renamed"), (None, "d")]
    assert gitglue.branch_changes(base, base, "backlog") == ([], [])
    # a repeated title pairs in order: the second (done) copy is not a tick of the first (open) one
    dup = "- [ ] p\n- [x] p\n"
    assert gitglue.branch_changes(dup, dup, "backlog") == ([], [])
    assert gitglue.branch_changes("- [ ] p\n", dup, "backlog") == ([("p", None, True)], [])


def test_merge_takes_branch_items(trk, repo, tmp_path):
    base = (repo / "BACKLOG.md").read_text()
    store.add_item(trk, "proj", "backlog", "main item")  # DB moved on since the base
    theirs = base.replace("- [ ] add support to manage webhooks (2026-10-13)",
                          "- [x] add support to manage webhooks (2026-10-13)\n- [ ] branch item (2026-10-12)\n"
                          "- [ ] main item")  # same title as nothing in the DB (no date): added
    (tmp_path / "o").write_text(base)
    (tmp_path / "b").write_text(theirs)
    res = tmp_path / "a"
    assert gitglue.merge(trk, "proj", tmp_path / "o", res, tmp_path / "b", "BACKLOG.md", actor="claude-x")
    items = {i.title: i for i in store.list_items(trk, "proj", "backlog")}
    assert items["add support to manage webhooks (2026-10-13)"].done
    assert items["branch item (2026-10-12)"].created_by == "claude-x"
    assert "main item" in items and any(t.startswith("main item (") for t in items)
    assert res.read_text() == mdgen.render_text(trk, "proj", "backlog") == (repo / "BACKLOG.md").read_text()
    # a second run (same sides) adds nothing twice
    gitglue.merge(trk, "proj", tmp_path / "o", res, tmp_path / "b", "BACKLOG.md")
    assert len(store.list_items(trk, "proj", "backlog")) == len(items)


def test_toplevel(repo):
    (repo / "a" / "b").mkdir(parents=True)
    assert gitglue.toplevel(repo / "a" / "b") == repo


def test_raw_only_add_and_config(repo):
    for args in (["commit", "-m", "x"], ["push"], []):
        with pytest.raises(gitctl.GitDenied):
            gitctl._raw(repo, args, gitctl.git_env())


def test_git_env(monkeypatch):
    for k in ("GIT_DIR", "GIT_WORK_TREE"):
        monkeypatch.setenv(k, "/elsewhere")
    monkeypatch.setenv("GIT_INDEX_FILE", "rel-index")
    env = gitctl.git_env()
    assert env["LC_ALL"] == "C" and env["GIT_TERMINAL_PROMPT"] == "0"
    assert not {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"} & set(env)
    assert gitctl.git_env(clean=False)["GIT_DIR"] == "/elsewhere"
    env = gitctl.git_env(index="rel-index")
    assert env["GIT_INDEX_FILE"] == os.path.abspath("rel-index") and "GIT_DIR" not in env


def test_ctl_is_ancestor_and_heads(repo):
    head = git(repo, "rev-parse", "HEAD").strip()
    assert gitctl.head_sha(repo) == head and gitctl.merge_head(repo) is None
    assert gitctl.is_ancestor(repo, head) and gitctl.is_ancestor(repo, head[:7], "HEAD")
    assert not gitctl.is_ancestor(repo, "0" * 40)  # unknown commit: False, no raise
    assert not gitctl.is_ancestor(repo, head, "MERGE_HEAD")  # no merge running
    for sha, ref in (("HEAD", "HEAD"), ("abc", "HEAD"), ("ABCDEF1", "HEAD"), ("-h" + "0" * 6, "HEAD"),
                     (head, "main"), (head, "--all"), (head + "0", "HEAD")):
        with pytest.raises(gitctl.GitDenied):
            gitctl.is_ancestor(repo, sha, ref)
    assert gitctl.branch_exists(repo, "main") and not gitctl.branch_exists(repo, "nope")
    assert not gitctl.branch_exists(repo, "--all") and not gitctl.branch_exists(repo, "a..b")


def test_ctl_head_file_allows_doc_files(repo):
    (repo / "CHANGELOG.md").write_text("# C\n")
    git(repo, "add", "CHANGELOG.md")
    git(repo, "commit", "-q", "-m", "c")
    assert gitctl.head_file(repo, "CHANGELOG.md") == "# C\n" and gitctl.head_file(repo, "FEATURES.md") == ""
    with pytest.raises(gitctl.GitDenied):
        gitctl.head_file(repo, "README.md")


def test_post_commit_hook_never_fails(tmp_path):
    hook = tmp_path / "post-commit"
    hook.write_text(gitglue.POST_COMMIT)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "pmctl").write_text("#!/bin/sh\nexit 3\n")
    (bindir / "pmctl").chmod(0o755)
    for path in (str(tmp_path / "empty"), f"{bindir}:/usr/bin:/bin"):
        cp = subprocess.run(["/bin/sh", str(hook)], env={"PATH": path}, capture_output=True, text=True, timeout=20)
        assert cp.returncode == 0


def test_foreign_post_commit_refused_before_writing(repo):
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "post-commit").write_text("#!/bin/sh\necho mine\n")
    (hooks / "pre-commit").unlink(missing_ok=True)
    with pytest.raises(Conflict):
        gitglue.install_hooks(repo)
    assert not (hooks / "pre-commit").exists() and not (repo / ".gitattributes").exists()
    gitglue.install_hooks(repo, force=True)
    assert (hooks / "post-commit.pmctl-old").read_text() == "#!/bin/sh\necho mine\n"


def test_worktree_paths_main_first_skips_gone_trees(repo, tmp_path):
    a, b = tmp_path / "wt" / "a", tmp_path / "wt" / "b"
    git(repo, "worktree", "add", "-q", "-b", "a", str(a))
    git(repo, "worktree", "add", "-q", "-b", "b", str(b))
    shutil.rmtree(b)  # prunable
    assert gitglue.worktree_paths(repo) == [repo, a]
    plain = tmp_path / "plain"
    plain.mkdir()
    assert gitglue.worktree_paths(plain) == [plain]
