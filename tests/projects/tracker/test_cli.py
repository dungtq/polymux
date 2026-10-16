"""pmctl CLI: commands, JSON, exit codes, audit lines; end to end with the pre-commit hook and merge driver."""

import json
import subprocess
import sys

import pytest

from pm.projects.tracker import cli, mdgen, store

from .conftest import display_today, git

TODAY = display_today()
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 40


def _audit(tk):
    return [json.loads(ln) for ln in tk.audit_file.read_text().splitlines()]


def test_cli_import_does_not_load_the_web_stack():
    code = ("import sys, pm.projects.tracker.cli\n"
            "bad = [m for m in sys.modules if m.split('.')[0] in ('fastapi', 'starlette', 'jinja2', 'pm.web')]\n"
            "bad += [m for m in sys.modules if m.startswith('pm.web')]\n"
            "print(','.join(bad))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True)
    assert out.stdout.strip() == ""


def test_import_add_list_show_done(pmctl, tk, repo, tmp_path):
    code, out, _ = pmctl("import")
    assert code == 0 and "BACKLOG.md: 3 items" in out and "TODO.md: 20 items" in out
    code, _, err = pmctl("import")
    assert code == 1 and "already imported" in err
    body = tmp_path / "body.md"
    body.write_text("Steps:\n1. open\n")
    img = tmp_path / "shot.png"
    img.write_bytes(PNG)
    code, out, _ = pmctl("issue", "add", "Crash on save", "--body-file", str(body), "--image", str(img),
                       "--label", "ui")
    assert code == 0 and out.strip() == f"#4 added: Crash on save ({TODAY})"
    assert f"- [ ] Crash on save ({TODAY}) [#4]\n" in (repo / "BACKLOG.md").read_text()
    code, out, _ = pmctl("issue", "show", "4")
    assert code == 0 and "Steps:" in out and str(tk.tracker_dir) in out and "labels: ui" in out
    code, out, _ = pmctl("--json", "issue", "show", "4")
    d = json.loads(out)
    assert d["num"] == 4 and d["created_by"] == "claude-test01" and d["images"][0]["path"].startswith("/")
    code, out, _ = pmctl("issue", "done", "4", "--commit", "abc1234")
    assert code == 0 and "#4 done" in out
    code, out, _ = pmctl("issue", "list", "--json")
    assert [i["num"] for i in json.loads(out)] == [2, 3]
    code, out, _ = pmctl("backlog", "list", "--all")
    assert out.count("\n") == 4 and "#4    [x] Crash on save" in out
    code, _, err = pmctl("issue", "done", "99")
    assert code == 1 and "no issue #99" in err


def test_note_edit_reopen_rm(pmctl, tk, repo, monkeypatch):
    pmctl("import")
    assert pmctl("issue", "note", "2", "looked at it")[0] == 0
    assert store.get_item(tk, "proj", "backlog", 2).n_notes == 1
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("new body\n"))
    code, out, _ = pmctl("issue", "edit", "2", "--title", "Renamed", "--body-file", "-")
    assert code == 0 and store.get_item(tk, "proj", "backlog", 2).body_md == "new body"
    assert pmctl("issue", "edit", "2")[0] == 1
    assert pmctl("issue", "reopen", "1")[0] == 0
    assert pmctl("issue", "rm", "3")[0] == 0
    assert [i.num for i in store.list_items(tk, "proj", "backlog")] == [1, 2]
    assert "- [ ] Renamed [#2]\n" in (repo / "BACKLOG.md").read_text()


def test_todo_commands(pmctl, tk, repo):
    pmctl("import")
    code, out, _ = pmctl("todo", "add", "--section", "Later", "new task")
    assert code == 0 and out.startswith("#21 added")
    assert (repo / "TODO.md").read_text().endswith(f"## Later\n\n- [ ] new task ({TODAY})\n")
    assert pmctl("todo", "done", "21")[0] == 0
    code, out, _ = pmctl("todo", "list", "--open", "--json")
    assert 21 not in [i["num"] for i in json.loads(out)]
    assert pmctl("todo", "reopen", "21")[0] == 0


def test_audit_lines_without_text(pmctl, tk):
    pmctl("import")
    pmctl("issue", "add", "very secret title")
    pmctl("issue", "note", "4", "secret note")
    lines = _audit(tk)
    assert [a["action"] for a in lines] == ["tracker_import", "tracker_import", "tracker_add", "tracker_note"]
    assert all(a["client"] == "pmctl:claude-test01" and a["result"] == "ok" for a in lines)
    assert lines[2]["target"] == "proj/backlog#4"
    assert "secret" not in tk.audit_file.read_text()


def test_audit_failure_rolls_back(pmctl, tk):
    pmctl("import")
    tk.audit_file.unlink()
    tk.audit_file.mkdir()  # cannot append: fail closed
    code, _, err = pmctl("issue", "add", "never stored")
    assert code == 1 and "audit" in err
    assert [i.num for i in store.list_items(tk, "proj", "backlog")] == [1, 2, 3]


def test_render_check_and_render(pmctl, tk, repo):
    assert pmctl("render")[0] == 1  # not imported yet
    pmctl("import")
    assert pmctl("render", "--check")[0] == 0
    # a hand edit of the main file is taken into pm.db, not reported
    (repo / "BACKLOG.md").write_text((repo / "BACKLOG.md").read_text() + "- [ ] by hand\n")
    assert pmctl("render", "--check")[0] == 0
    assert store.list_items(tk, "proj", "backlog")[-1].title == "by hand"
    # the render still differs after taking it (an item with a body gets its [#N] back): reported
    store.edit_item(tk, "proj", "backlog", 4, body="b")
    (repo / "BACKLOG.md").write_text((repo / "BACKLOG.md").read_text().replace(" [#4]", ""))
    code, out, err = pmctl("render", "--check")
    assert code == 1 and str(repo / "BACKLOG.md") in out and "differ from pm.db" in err
    assert store.get_item(tk, "proj", "backlog", 4).body_md == "b"
    assert pmctl("render")[0] == 0
    assert (repo / "BACKLOG.md").read_text() == mdgen.render_text(tk, "proj", "backlog")
    assert "- [ ] by hand [#4]\n" in (repo / "BACKLOG.md").read_text()


def test_unknown_project_and_usage_errors(pmctl):
    code, _, err = pmctl("--project", "nope", "issue", "list")
    assert code == 1 and "unknown project" in err
    with pytest.raises(SystemExit) as e:
        pmctl("issue", "frobnicate")
    assert e.value.code == 1


# ------------------------------------------------------------------ end to end through git
def _branch(tk, repo, pmctl, name="b"):
    pmctl("import")
    assert pmctl("hooks", "install")[0] == 0
    git(repo, "add", ".gitattributes")
    git(repo, "commit", "-q", "-m", "attrs")
    wt = tk.dev_root / f"proj-wt-{name}"
    git(repo, "worktree", "add", "-q", "-b", name, str(wt))
    return wt


def test_hook_stages_render_and_driver_resolves_conflict(pmctl, on_path, tk, repo):
    wt = _branch(tk, repo, pmctl)
    # branch b: a hand edit of BACKLOG.md, committed without the hook
    (wt / "BACKLOG.md").write_text((wt / "BACKLOG.md").read_text().replace("list page very slow", "LIST PAGE VERY SLOW"))
    git(wt, "commit", "-q", "--no-verify", "-am", "hand edit on b")
    # main: a new issue through pmctl; the hook stages the re-rendered file into an unrelated commit
    pmctl("issue", "add", "from main")
    (repo / "README").write_text("x\n")
    git(repo, "add", "README")
    git(repo, "commit", "-q", "-m", "readme")
    assert "BACKLOG.md" in git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert git(repo, "status", "--porcelain") == ""
    # merging b: both sides changed BACKLOG.md; the driver writes the DB render, no conflict
    git(repo, "merge", "-q", "--no-edit", "b")
    assert (repo / "BACKLOG.md").read_text() == mdgen.render_text(tk, "proj", "backlog")
    assert git(repo, "status", "--porcelain") == ""


def test_merge_keeps_what_an_old_branch_added_and_ticked(pmctl, on_path, tk, repo):
    """An agent with old rules hand-adds an item and ticks one on a branch; main adds another through
    pm. The merge has no conflict and the file has all three changes."""
    wt = _branch(tk, repo, pmctl)
    text = (wt / "BACKLOG.md").read_text()
    text = text.replace("- [ ] add support to manage webhooks", "- [x] add support to manage webhooks")
    (wt / "BACKLOG.md").write_text(text + "- [ ] added on the branch (2026-10-13)\n")
    git(wt, "commit", "-q", "--no-verify", "-am", "old-rules hand edit")
    store.add_item(tk, "proj", "backlog", "added on main")
    git(repo, "commit", "-q", "-am", "main item")
    git(repo, "merge", "-q", "--no-edit", "b")
    final = (repo / "BACKLOG.md").read_text()
    assert "- [x] add support to manage webhooks (2026-10-13)\n" in final
    assert "- [ ] added on the branch (2026-10-13)\n" in final
    assert f"- [ ] added on main ({TODAY})\n" in final
    assert final == mdgen.render_text(tk, "proj", "backlog")
    assert git(repo, "status", "--porcelain") == ""
    assert "<<<<<<<" not in final


def test_stage_in_a_worktree_takes_branch_additions_and_main_hand_edit(pmctl, on_path, tk, repo):
    """The hook in a worktree takes the branch's hand-added item and tick (vs HEAD) but no removal; the
    main tree's hand edit is absorbed too."""
    wt = _branch(tk, repo, pmctl)
    text = (wt / "BACKLOG.md").read_text()
    text = text.replace("- [ ] add support to manage webhooks", "- [x] add support to manage webhooks")
    text = "\n".join(ln for ln in text.split("\n") if "add export support" not in ln)  # removal: ignored
    (wt / "BACKLOG.md").write_text(text + "- [ ] worktree hand item\n")
    (repo / "BACKLOG.md").write_text((repo / "BACKLOG.md").read_text() + "- [ ] main hand line\n")
    (wt / "x").write_text("x\n")
    git(wt, "add", "x", "BACKLOG.md")
    git(wt, "commit", "-q", "-m", "x")  # hook: render --stage in the worktree
    items = {i.title: i for i in store.list_items(tk, "proj", "backlog")}
    assert {"main hand line", "worktree hand item"} <= set(items)
    assert items["add support to manage webhooks (2026-10-13)"].done
    assert any(t.startswith("add export support") for t in items)
    render = mdgen.render_text(tk, "proj", "backlog")
    assert "- [ ] worktree hand item\n" in render
    assert (wt / "BACKLOG.md").read_text() == render == (repo / "BACKLOG.md").read_text()
    assert git(wt, "show", "HEAD:BACKLOG.md") == render
    # the next commit in the worktree takes nothing twice
    (wt / "y").write_text("y\n")
    git(wt, "add", "y")
    git(wt, "commit", "-q", "-m", "y")
    assert len(store.list_items(tk, "proj", "backlog")) == len(items)


def test_stage_in_a_worktree_skips_conflicted_copy(pmctl, on_path, tk, repo):
    wt = _branch(tk, repo, pmctl)
    (wt / "BACKLOG.md").write_text("<<<<<<< HEAD\n- [ ] a\n=======\n- [ ] b\n>>>>>>> x\n")
    from pm.projects.tracker import gitglue
    assert gitglue.take_worktree(tk, "proj", wt) == {}
    assert [i.title for i in store.list_items(tk, "proj", "backlog")].count("a") == 0


def test_hook_is_a_noop_for_non_tracker_projects(pmctl, on_path, repo):
    assert pmctl("hooks", "install")[0] == 0
    (repo / "BACKLOG.md").write_text("hand edit is fine here\n")
    git(repo, "commit", "-q", "-am", "edit")
    assert (repo / "BACKLOG.md").read_text() == "hand edit is fine here\n"


# ------------------------------------------------------------------ change / feature (T3)
CL = "# Changelog\n\n## 2026-10-01 (one)\n- a\n"
FT = "# F\n\nLast audited: 2026-10-01\nSummary: 1 features in 1 areas.\n\n## Chat\n\n- **Send** — send.\n"


def _docs(repo, pmctl):
    (repo / "CHANGELOG.md").write_text(CL)
    (repo / "FEATURES.md").write_text(FT)
    code, out, _ = pmctl("import", "--file", "changelog", "--file", "features")
    assert code == 0 and "CHANGELOG.md: 1 entries" in out and "FEATURES.md: 1 entries" in out
    assert pmctl("import", "--file", "changelog")[0] == 1  # already imported


def test_change_commands(pmctl, tk, repo, tmp_path, monkeypatch):
    _docs(repo, pmctl)
    body = tmp_path / "b.md"
    body.write_text("- did a thing\n")
    code, out, _ = pmctl("change", "add", "Thing", "--date", "2026-10-13", "--body-file", str(body))
    assert code == 0 and out.strip() == "#2 added: 2026-10-13 (Thing)"
    assert (repo / "CHANGELOG.md").read_text() == "# Changelog\n\n## 2026-10-13 (Thing)\n- did a thing\n\n## 2026-10-01 (one)\n- a\n"
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("- from stdin\n"))
    assert pmctl("change", "edit", "2", "--title", "Thing 2", "--body-file", "-")[0] == 0
    code, out, _ = pmctl("change", "show", "2")
    assert "## 2026-10-13 (Thing 2)" in out and "- from stdin" in out and "landed" in out
    code, out, _ = pmctl("--json", "change", "list")
    assert [e["num"] for e in json.loads(out)] == [2, 1]
    assert pmctl("change", "rm", "1")[0] == 0
    assert "one" not in (repo / "CHANGELOG.md").read_text()
    with pytest.raises(SystemExit) as e:
        pmctl("change", "add", "no body")  # --body-file is required
    assert e.value.code == 1
    acts = [a["action"] for a in _audit(tk)]
    assert acts[-3:] == ["tracker_change_add", "tracker_change_edit", "tracker_change_rm"]
    assert "did a thing" not in tk.audit_file.read_text()


def test_feature_commands(pmctl, tk, repo):
    _docs(repo, pmctl)
    assert pmctl("feature", "set", "Chat", "Copy", "--text", "copy a reply.")[0] == 0
    assert pmctl("feature", "set", "Git", "Push", "--text-file", "/dev/null")[0] == 1  # empty text
    code, out, _ = pmctl("feature", "list")
    assert out.splitlines() == ["## Chat", "- Send", "- Copy"]
    code, out, _ = pmctl("--json", "feature", "show", "Chat", "Copy")
    assert json.loads(out)["text"] == "copy a reply."
    assert pmctl("feature", "rm", "Chat", "Send")[0] == 0
    assert pmctl("feature", "audited", "--date", "2026-10-13")[0] == 0
    text = (repo / "FEATURES.md").read_text()
    assert text == "# F\n\nLast audited: 2026-10-13\nSummary: 1 features in 1 areas.\n\n## Chat\n\n- **Copy** — copy a reply.\n"
    code, _, err = pmctl("feature", "show", "Chat", "Send")
    assert code == 1 and "no feature" in err
    assert [a["action"] for a in _audit(tk)][-3:] == ["tracker_feature_set", "tracker_feature_rm",
                                                       "tracker_feature_audited"]


def test_doc_audit_failure_rolls_back(pmctl, tk, repo, tmp_path):
    _docs(repo, pmctl)
    tk.audit_file.unlink()
    tk.audit_file.mkdir()
    code, _, err = pmctl("feature", "set", "Chat", "X", "--text", "x")
    assert code == 1 and "audit" in err
    assert (repo / "FEATURES.md").read_text() == FT


def test_render_check_covers_doc_files(pmctl, tk, repo):
    _docs(repo, pmctl)
    assert pmctl("render", "--check")[0] == 0
    (repo / "CHANGELOG.md").write_text(CL.replace("# Changelog\n\n", "# Changelog\n\n## 2026-10-02 (hand)\n- h\n\n"))
    assert pmctl("render", "--check")[0] == 0  # absorbed
    code, out, _ = pmctl("change", "list")
    assert "#2    2026-10-02 (hand)" in out


def test_backup_command(pmctl, tk):
    import sqlite3

    code, _, err = pmctl("backup")
    assert code == 1 and "no pm.db" in err
    pmctl("import")
    code, out, _ = pmctl("backup")
    path = tk.state_dir / "backups" / f"pm-{TODAY}.db"
    assert code == 0 and out.strip() == str(path)
    assert sqlite3.connect(path).execute("SELECT count(*) FROM sqlite_master").fetchone()[0] > 0
    code, out, _ = pmctl("backup")
    assert code == 0 and "already there" in out
    code, out, _ = pmctl("--json", "backup", "--force")
    assert code == 0 and json.loads(out) == {"path": str(path)}


def test_issue_move(pmctl, tk, repo):
    other = tk.dev_root / "other"
    other.mkdir()
    (other / "BACKLOG.md").write_text("## Inbox\n")
    pmctl("import")
    assert pmctl("--project", "other", "import", "--file", "backlog")[0] == 0
    code, out, _ = pmctl("issue", "move", "2", "--to", "other")
    assert code == 0 and out.startswith("#2 moved to other as #1: ")
    code, out, _ = pmctl("--json", "--project", "other", "issue", "show", "1")
    d = json.loads(out)
    assert d["notes"][-1]["body_md"] == "Moved from proj #2." and d["notes"][-1]["author"] == "claude-test01"
    line = _audit(tk)[-1]
    assert line["action"] == "tracker_move" and line["target"] == "proj/backlog#2"
    assert line["detail"]["to"] == "other" and line["detail"]["new_num"] == 1
    code, _, err = pmctl("issue", "move", "2", "--to", "other")
    assert code == 1 and "no issue #2" in err
    code, _, err = pmctl("issue", "move", "3", "--to", "nope")
    assert code == 1 and "unknown project" in err
    with pytest.raises(SystemExit):  # argparse usage error
        pmctl("issue", "move", "3")
