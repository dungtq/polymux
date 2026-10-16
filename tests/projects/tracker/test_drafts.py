"""Branch gating end to end on a tmp git repo with the real hooks and merge driver (``pmctl`` on PATH runs
this checkout with the tmp config): drafts hidden on main, stamped by post-commit, landed by a merge (driver
or fast-forward), abandoned with their branch; ``drafts`` unit checks."""

import pytest

from pm.projects.tracker import changelog, drafts, featurelist

from .conftest import git

CL = "# Changelog\n\n## 2026-10-02 (two)\n- b\n\n## 2026-10-01 (one)\n- a\n"
FT = "# F\n\nSummary: 2 features in 1 areas.\n\n## Chat\n\n- **Send** — send.\n- **Copy** — copy.\n"


@pytest.fixture
def setup(tk, repo, pmctl, on_path, tmp_path):
    (repo / "CHANGELOG.md").write_text(CL)
    (repo / "FEATURES.md").write_text(FT)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "docs")
    assert pmctl("import")[0] == 0
    assert pmctl("hooks", "install")[0] == 0
    git(repo, "add", ".gitattributes")
    git(repo, "commit", "-q", "-m", "attrs")
    body = tmp_path / "body.md"
    body.write_text("- did it\n")
    return body


def _wt(tk, repo, name="b"):
    wt = tk.dev_root / f"proj-wt-{name}"
    git(repo, "worktree", "add", "-q", "-b", name, str(wt))
    return wt


def _commit(cwd, msg="x"):
    (cwd / f"f-{msg}").write_text(msg)
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", msg)


def _states(tk):
    return {e["title"]: e["state"] for e in changelog.entries(tk, "proj")}


def test_branch_entry_lands_through_the_merge_driver(tk, repo, pmctl, setup):
    wt = _wt(tk, repo)
    code, out, _ = pmctl("change", "add", "on b", "--body-file", str(setup), cwd=wt)
    assert code == 0 and "draft of b" in out
    assert pmctl("feature", "set", "Chat", "Send", "--text", "send faster.", cwd=wt)[0] == 0
    # main does not show it; the worktree's copy does
    assert "on b" not in (repo / "CHANGELOG.md").read_text()
    assert "(on b)\n- did it\n" in (wt / "CHANGELOG.md").read_text()
    assert "send faster." in (wt / "FEATURES.md").read_text() and "send faster." not in (repo / "FEATURES.md").read_text()
    _commit(wt, "work-on-b")  # pre-commit stages the branch view, post-commit stamps the drafts
    head = git(wt, "rev-parse", "HEAD").strip()
    e = next(e for e in changelog.entries(tk, "proj") if e["title"].endswith("(on b)"))
    assert (e["state"], e["sha"]) == ("draft", head)
    assert "(on b)" in git(wt, "show", "HEAD:CHANGELOG.md")
    # main moves on: both sides changed both files -> the driver runs
    assert pmctl("change", "add", "on main", "--body-file", str(setup))[0] == 0
    assert pmctl("feature", "set", "Chat", "Copy", "--text", "copy on main.")[0] == 0
    _commit(repo, "main-work")
    assert "(on b)" not in (repo / "CHANGELOG.md").read_text()
    git(repo, "merge", "-q", "--no-edit", "b")
    st = _states(tk)
    assert st[f"{e['title']}"] == "landed" and all(s == "landed" for s in st.values())
    text = (repo / "CHANGELOG.md").read_text()
    assert text == changelog.render_text(tk, "proj", repo) and "(on b)" in text and "(on main)" in text
    ft = (repo / "FEATURES.md").read_text()
    assert "- **Send** — send faster.\n- **Copy** — copy on main.\n" in ft
    assert git(repo, "status", "--porcelain") == ""
    assert "<<<<<<<" not in text + ft


def test_fast_forward_lands_on_the_next_main_render(tk, repo, pmctl, setup):
    wt = _wt(tk, repo)
    pmctl("change", "add", "ff entry", "--body-file", str(setup), cwd=wt)
    pmctl("feature", "set", "Chat", "Edit", "--text", "edit.", cwd=wt)
    _commit(wt, "b1")
    git(repo, "merge", "-q", "--ff-only", "b")  # no driver, no hooks on main
    assert "(ff entry)" in (repo / "CHANGELOG.md").read_text()
    assert pmctl("render")[0] == 0
    assert set(_states(tk).values()) == {"landed"}
    assert [f["name"] for f in featurelist.features(tk, "proj")] == ["Send", "Copy", "Edit"]
    assert "Summary: 3 features in 1 areas." in (repo / "FEATURES.md").read_text()
    assert pmctl("render", "--check")[0] == 0
    assert git(repo, "status", "--porcelain") == ""


def test_abandoned_branch_never_reaches_main(tk, repo, pmctl, setup):
    wt = _wt(tk, repo, "gone")
    pmctl("change", "add", "dead end", "--body-file", str(setup), cwd=wt)
    _commit(wt, "g1")
    git(repo, "worktree", "remove", "--force", str(wt))
    git(repo, "branch", "-D", "gone")
    assert pmctl("render")[0] == 0
    e = next(e for e in changelog.entries(tk, "proj", None) if "dead end" in e["title"])
    assert e["state"] == "abandoned"
    assert "dead end" not in pmctl("change", "list")[1] and "dead end" in pmctl("change", "list", "--all")[1]
    # a new branch with the same name: its commits do not revive the old draft
    wt2 = _wt(tk, repo, "gone")
    _commit(wt2, "g2")
    git(repo, "merge", "-q", "--no-edit", "gone")
    pmctl("render")
    assert "dead end" not in (repo / "CHANGELOG.md").read_text()


def test_amend_restamps_and_hand_edit_in_worktree_becomes_a_draft(tk, repo, pmctl, setup):
    wt = _wt(tk, repo)
    # an agent with the old rules edits the worktree's CHANGELOG.md by hand
    (wt / "CHANGELOG.md").write_text(CL.replace("# Changelog\n\n", "# Changelog\n\n## 2026-10-03 (by hand)\n- h\n\n"))
    _commit(wt, "hand")
    e = next(e for e in changelog.entries(tk, "proj") if e["title"] == "2026-10-03 (by hand)")
    assert (e["state"], e["branch"], e["sha"]) == ("draft", "b", git(wt, "rev-parse", "HEAD").strip())
    git(wt, "commit", "-q", "--amend", "-m", "hand amended")
    assert changelog.entries(tk, "proj", e["num"])[0]["sha"] == git(wt, "rev-parse", "HEAD").strip()
    git(repo, "merge", "-q", "--no-edit", "b")  # only the branch changed the file: plain fast-forward
    pmctl("render")
    assert _states(tk)["2026-10-03 (by hand)"] == "landed"
    assert (repo / "CHANGELOG.md").read_text().startswith("# Changelog\n\n## 2026-10-03 (by hand)\n")


def test_untracked_doc_file_merges_like_git(tk, repo, pmctl, on_path):
    """hooks install adds merge=pmctl for CHANGELOG.md; while it is not imported the driver does git's own
    3-way merge (clean here) instead of a conflict."""
    (repo / "CHANGELOG.md").write_text("# C\n\nmiddle\n\nend\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "c")
    pmctl("import", "--file", "backlog")
    pmctl("hooks", "install")
    git(repo, "add", ".gitattributes")
    git(repo, "commit", "-q", "-m", "attrs")
    wt = _wt(tk, repo)
    (wt / "CHANGELOG.md").write_text("# C\n\nmiddle\n\nend\nbranch\n")
    git(wt, "commit", "-q", "-am", "b")
    (repo / "CHANGELOG.md").write_text("# C main\n\nmiddle\n\nend\n")
    git(repo, "commit", "-q", "-am", "m")
    git(repo, "merge", "-q", "--no-edit", "b")
    assert (repo / "CHANGELOG.md").read_text() == "# C main\n\nmiddle\n\nend\nbranch\n"


def test_view_rules(tk, repo, git_env):
    head = git(repo, "rev-parse", "HEAD").strip()
    v = drafts.view_for(tk, "proj", repo)
    assert (v.branch, v.main, v.head) == ("main", True, head)
    assert v.merged(head) and v.merged(head[:7]) and not v.merged("")
    assert v.shows("x", head) and not v.shows("x", "")
    wt = _wt(tk, repo)
    w = drafts.view_for(tk, "proj", wt)
    assert (w.branch, w.main) == ("b", False) and w.shows("b", "") and not w.shows("c", "")
    assert drafts.view_for(tk, "proj", None).main and drafts.view_for(tk, "proj", tk.dev_root).head == ""
    git(wt, "checkout", "-q", "--detach")
    assert drafts.view_for(tk, "proj", wt).main  # detached HEAD counts as main
