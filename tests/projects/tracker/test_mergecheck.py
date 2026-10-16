"""mergecheck on a tmp repo with linked work trees: files of the branch, overlap through another work tree's
dirty file or commits, through another agent's claim, base moved; ``ops.files_since`` / ``check_base``."""

import pytest

from pm.core.errors import Invalid, NotFound
from pm.git import ops
from pm.projects.tracker import claims, mergecheck

from .conftest import git


def _wt(tk, repo, name):
    wt = tk.dev_root / f"proj-wt-{name}"
    git(repo, "worktree", "add", "-q", "-b", name, str(wt))
    return wt


def _commit(cwd, path, text="x\n", msg="c"):
    p = cwd / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", msg)


def _check(tk, cwd, agent="me", **kw):
    return mergecheck.check(tk, "proj", cwd, agent=agent, **kw)


def test_files_since_and_check_base(tk, repo):
    wt = _wt(tk, repo, "b")
    _commit(wt, "src/a.py")
    git(wt, "mv", "BACKLOG.md", "OLD.md")
    git(wt, "commit", "-q", "-m", "mv")
    assert ops.files_since(wt, "main") == ["BACKLOG.md", "OLD.md", "src/a.py"]  # a rename is delete + add
    assert ops.files_since(repo, "main") == []
    for bad in ("-x", "--output=/tmp/x", "a..b", "/main", ".x", "a//b", "x.lock", "", "a b"):
        with pytest.raises(Invalid):
            ops.check_base(bad)
    assert ops.check_base("gitdev/main") == "gitdev/main"


def test_clear_branch(tk, repo):
    wt = _wt(tk, repo, "b")
    _commit(wt, "src/a.py")
    (wt / "notes.txt").write_text("dirty")
    rep = _check(tk, wt)
    assert (rep.base, rep.branch, rep.files, rep.base_moved, rep.behind) == ("main", "b", ["notes.txt", "src/a.py"],
                                                                             False, 0)
    assert rep.exit_code == mergecheck.CLEAR and rep.to_dict()["claims"] == [] and rep.worktrees == []
    assert mergecheck.where(wt) == ("b", str(wt.resolve()))
    assert mergecheck.where(tk.state_dir) == ("", "")


def test_overlap_through_other_worktree(tk, repo):
    wt = _wt(tk, repo, "b")
    _commit(wt, "src/a.py")
    other = _wt(tk, repo, "c")
    (other / "src").mkdir()
    (other / "src" / "a.py").write_text("theirs, uncommitted")
    third = _wt(tk, repo, "d")
    _commit(third, "src/a.py", "committed on d\n")
    _commit(third, "docs/x.md")
    rep = _check(tk, wt)
    assert rep.exit_code == mergecheck.OVERLAP
    got = {w["branch"]: w["files"] for w in rep.worktrees}
    assert got == {"c": ["src/a.py"], "d": ["src/a.py"]}
    assert all(w["path"].startswith(str(tk.dev_root.resolve())) for w in rep.worktrees)


def test_overlap_through_claims(tk, repo):
    wt = _wt(tk, repo, "b")
    _commit(wt, "src/pm/a.py")
    claims.add(tk, "proj", "mine", agent="me", paths=["src/**"])
    claims.add(tk, "proj", "same tree", agent="other1", worktree=str(wt.resolve()), paths=["src/**"])
    claims.add(tk, "proj", "elsewhere", agent="other2", paths=["docs/**"])
    claims.add(tk, "proj", "dead pane", agent="other3", pane="%9", paths=["src/**"])
    c, _ = claims.add(tk, "proj", "hit", agent="other4", pane="%1", paths=["src/pm"])
    rep = _check(tk, wt, live=frozenset({"%1"}))
    assert [x.num for x in rep.claims] == [c.num] and rep.exit_code == mergecheck.OVERLAP
    assert rep.to_dict()["claims"][0]["agent"] == "other4"
    assert [x.num for x in _check(tk, wt, agent="other4", live=frozenset({"%1"})).claims] == [1]


def test_base_moved_is_a_warning(tk, repo):
    wt = _wt(tk, repo, "b")
    _commit(wt, "src/a.py")
    _commit(repo, "other.txt")
    rep = _check(tk, wt, base="main")
    assert rep.base_moved and rep.behind == 1 and rep.exit_code == mergecheck.CLEAR
    assert "other.txt" not in rep.files


def test_bad_or_unknown_base(tk, repo):
    with pytest.raises(Invalid):
        _check(tk, repo, base="--all")
    with pytest.raises(NotFound):
        _check(tk, repo, base="nope")
    git(repo, "branch", "-m", "main", "trunk")
    with pytest.raises(Invalid, match="no main branch"):
        _check(tk, repo)
    with pytest.raises(NotFound):
        _check(tk, tk.state_dir)



def test_generated_tracker_files_never_overlap(tk, repo, monkeypatch):
    monkeypatch.setattr(mergecheck.mdgen, "tracked_files", lambda s, p, files: ["changelog", "features"])
    assert mergecheck.generated(tk, "proj") == {"CHANGELOG.md", "FEATURES.md"}
    wt = _wt(tk, repo, "b")
    _commit(wt, "CHANGELOG.md", "mine\n")
    _commit(wt, "src/a.py")
    other = _wt(tk, repo, "c")
    _commit(other, "CHANGELOG.md", "theirs\n")
    rep = _check(tk, wt)
    assert rep.files == ["src/a.py"] and rep.worktrees == [] and rep.exit_code == mergecheck.CLEAR
