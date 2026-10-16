"""pmctl claim / merge-check: JSON shapes of the contract, text lines, exit codes, audit lines, one tmux call per
command. tmux is faked (``list_sessions`` patched): the pmctl config here has no private socket."""

import json

import pytest

from pm.core.models import TmuxPane, TmuxSession, TmuxWindow
from pm.tmux import ctl as tmuxctl

from .conftest import git

CLAIM_KEYS = ["num", "project", "agent", "pane", "title", "branch", "worktree", "paths", "issue", "status", "stale",
              "created_at", "updated_at", "closed_at", "commit", "note"]


@pytest.fixture(autouse=True)
def panes(monkeypatch):
    """Live tmux panes the fake ``list_sessions`` reports (edit the set); ``calls`` counts the calls."""
    state = {"live": {"%1"}, "dead": {"%7"}, "calls": 0}

    def fake(_settings):
        state["calls"] += 1
        ps = tuple(TmuxPane(id=p, window_id="@1", session_id="$1", pid=1, command="claude", cwd="/", active=False,
                            title="", dead=p in state["dead"]) for p in sorted(state["live"] | state["dead"]))
        return (TmuxSession(id="$1", name="work", windows=(TmuxWindow(id="@1", session_id="$1", index=0,
                                                                      name="w", active=True, panes=ps),)),)
    monkeypatch.setattr(tmuxctl, "list_sessions", fake)
    monkeypatch.setenv("TMUX_PANE", "%1")
    return state


def _j(out):
    return json.loads(out)


def _audit(tk):
    return [json.loads(ln) for ln in tk.audit_file.read_text().splitlines()]


def test_claim_add_list_show_json(pmctl, tk, repo, panes, monkeypatch):
    code, out, _ = pmctl("--json", "claim", "add", "Refactor chat", "--path", "src/chat/**", "--path", "tests/chat")
    assert code == 0 and panes["calls"] == 1
    d = _j(out)
    assert set(d) == {"claim", "overlaps"} and d["overlaps"] == []
    c = d["claim"]
    assert list(c) == CLAIM_KEYS
    assert (c["num"], c["project"], c["agent"], c["pane"], c["branch"], c["worktree"], c["paths"], c["status"],
            c["stale"], c["issue"]) == (1, "proj", "claude-test01", "%1", "main", str(repo.resolve()),
                                        ["src/chat/**", "tests/chat"], "active", False, None)
    a = _audit(tk)[-1]
    assert (a["action"], a["target"], a["client"]) == ("tracker_claim_add", "proj/claim#1", "pmctl:claude-test01")
    monkeypatch.setenv("PM_ACTOR", "codex-other")
    monkeypatch.setenv("TMUX_PANE", "%7")
    code, out, _ = pmctl("claim", "add", "Fix chat test", "--path", "tests/chat/test_x.py", "--branch", "fix")
    assert code == 0 and out.splitlines() == ["c2", "overlap: c1 claude-test01 main Refactor chat [src/chat/**, tests/chat]"]
    code, out, _ = pmctl("--json", "claim", "list")
    rows = _j(out)
    assert [r["num"] for r in rows] == [1, 2] and [r["stale"] for r in rows] == [False, True]  # %7 is dead
    assert all(list(r) == CLAIM_KEYS for r in rows)
    code, out, _ = pmctl("claim", "list", "--mine")
    assert out.startswith("c2 ") and "active stale" in out and "c1" not in out
    code, out, _ = pmctl("--json", "claim", "show", "c1")
    assert _j(out)["title"] == "Refactor chat" and list(_j(out)) == CLAIM_KEYS
    code, _, err = pmctl("claim", "show", "9")
    assert code == 1 and "no claim c9" in err
    with pytest.raises(SystemExit) as ex:
        pmctl("claim", "show", "x9")
    assert ex.value.code == 1


def test_claim_update_done_drop_gc(pmctl, tk, repo, panes, monkeypatch):
    pmctl("claim", "add", "A", "--path", "src/**")
    monkeypatch.setenv("PM_ACTOR", "codex-other")
    pmctl("claim", "add", "B", "--path", "src/a.py", "--pane", "%7")
    pmctl("claim", "add", "C", "--path", "docs/**")
    code, out, _ = pmctl("--json", "claim", "update", "3", "--title", "C2", "--path", "docs/x.md")
    assert code == 0 and (_j(out)["title"], _j(out)["paths"]) == ("C2", ["docs/x.md"])
    monkeypatch.setenv("PM_ACTOR", "claude-test01")
    code, out, _ = pmctl("--json", "claim", "done", "1", "--commit", "abc1234", "--note", "merged")
    d = _j(out)
    assert code == 0 and set(d) == {"claim", "overlaps"}
    assert (d["claim"]["status"], d["claim"]["commit"], d["claim"]["note"]) == ("done", "abc1234", "merged")
    assert d["claim"]["closed_at"] and d["overlaps"] == []  # c2 overlaps but is stale (dead pane)
    code, _, err = pmctl("claim", "done", "1")
    assert code == 1 and "already done" in err
    code, out, _ = pmctl("claim", "gc")
    assert code == 0 and out.strip() == "1 stale claim(s) dropped"
    code, out, _ = pmctl("--json", "claim", "drop", "3", "--note", "not needed")
    assert code == 0 and _j(out)["status"] == "dropped"
    code, out, _ = pmctl("--json", "claim", "list", "--all")
    assert [(r["num"], r["status"]) for r in _j(out)] == [(1, "done"), (2, "dropped"), (3, "dropped")]
    assert pmctl("claim", "list")[1].strip() == "(none)"
    acts = [a["action"] for a in _audit(tk)]
    assert acts == ["tracker_claim_add"] * 3 + ["tracker_claim_update", "tracker_claim_done", "tracker_claim_gc",
                                                "tracker_claim_drop"]


def test_claim_issue_needs_imported_backlog(pmctl, tk, repo):
    code, _, err = pmctl("claim", "add", "x", "--issue", "1")
    assert code == 1 and "no issue tracker" in err
    assert pmctl("import")[0] == 0
    code, out, _ = pmctl("--json", "claim", "add", "x", "--issue", "2")
    assert code == 0 and _j(out)["claim"]["issue"] == 2
    code, out, _ = pmctl("--json", "issue", "show", "2")
    assert _j(out)["notes"][0]["body_md"] == "claimed by claude-test01 (c1) on main: x"


def test_claim_refuses_bad_glob(pmctl, tk):
    code, _, err = pmctl("claim", "add", "x", "--path", "../etc")
    assert code == 1 and "bad path glob" in err
    assert not tk.audit_file.exists() or "tracker_claim" not in tk.audit_file.read_text()


def test_merge_check_exit_codes_and_json(pmctl, tk, repo, panes, monkeypatch):
    wt = tk.dev_root / "proj-wt-b"
    git(repo, "worktree", "add", "-q", "-b", "b", str(wt))
    (wt / "src").mkdir()
    (wt / "src" / "a.py").write_text("x\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "a")
    code, out, _ = pmctl("--json", "merge-check", cwd=wt)
    d = _j(out)
    assert code == 0 and list(d) == ["base", "branch", "files", "base_moved", "behind", "claims", "worktrees"]
    assert (d["base"], d["branch"], d["files"], d["base_moved"], d["behind"]) == ("main", "b", ["src/a.py"], False, 0)
    (repo / "z.txt").write_text("z")
    git(repo, "add", "z.txt")
    git(repo, "commit", "-q", "-m", "z")
    code, out, _ = pmctl("merge-check", cwd=wt)
    assert code == 0 and "warning: main has 1 commit(s) not in HEAD: rebase/merge main first" in out
    monkeypatch.setenv("PM_ACTOR", "codex-other")
    pmctl("claim", "add", "theirs", "--path", "src/**")
    monkeypatch.setenv("PM_ACTOR", "claude-test01")
    panes["calls"] = 0
    code, out, _ = pmctl("--json", "merge-check", "--base", "main", cwd=wt)
    d = _j(out)
    assert code == 1 and panes["calls"] == 1
    assert [c["title"] for c in d["claims"]] == ["theirs"] and list(d["claims"][0]) == CLAIM_KEYS
    code, out, _ = pmctl("merge-check", cwd=wt)
    assert code == 1 and "claim c1 codex-other main theirs: src/a.py" in out and out.rstrip().endswith("overlap found")
    code, _, err = pmctl("merge-check", "--base", "nope", cwd=wt)
    assert code == 2 and "no such branch" in err
    code, _, err = pmctl("merge-check", "--base=-x", cwd=wt)
    assert code == 2 and "bad base" in err
