"""claims: tk_claim add/list/show/update/done/drop/gc, overlap matrix, stale flag, glob checks, audit fail closed,
issue notes. Runs on a tmp pm.db; tmux only through the fake binary (``fake_tmux``) or a private socket."""

import dataclasses
import json

import pytest

from pm.core.errors import Conflict, Invalid, NotFound
from pm.projects.tracker import claims, issues, mdimport, store
from pm.tmux.ctl import SEP

from .conftest import FIX


class Audit:
    def __init__(self, fail: bool = False):
        self.lines, self.fail = [], fail

    def __call__(self, action, detail):
        if self.fail:
            raise RuntimeError("audit down")
        self.lines.append((action, detail))


def _add(tk, title="work", agent="a1", paths=(), pane="", **kw):
    return claims.add(tk, "proj", title, agent=agent, pane=pane, paths=paths, **kw)


# ------------------------------------------------------------------ overlap
@pytest.mark.parametrize(("x", "y", "hit"), [
    ("src/pm/a.py", "src/pm/a.py", True),
    ("src/pm", "src/pm/core/x.py", True),          # a plain directory covers what is below it
    ("src", "srcx/a.py", False),
    ("src/**", "src/pm/*.py", True),
    ("src/**", "docs/*.md", False),
    ("*.py", "src/**", True),                      # nested literal dirs ("" = root)
    ("src/a.py", "src/*.md", False),
    ("src/pm", "src/*/x.py", True),                # the glob's leading parts reach into the directory
    ("src/a*.py", "src/b*.py", True),              # same directory: conservative
    ("docs/*.md", "tests/**", False),
    ("**/test.py", "test.py", True),
    ("README.md", "*.md", True),
])
def test_globs_overlap(x, y, hit):
    assert claims.globs_overlap(x, y) is hit
    assert claims.globs_overlap(y, x) is hit


def test_paths_overlap_empty_is_whole_repo():
    assert claims.paths_overlap([], ["docs/x.md"])
    assert claims.paths_overlap(["docs/x.md"], [])
    assert claims.paths_overlap([], [])
    assert not claims.paths_overlap(["docs/**"], ["src/**", "tests/a.py"])


def test_touches():
    c = dataclasses.replace(_claim(), paths=("src/pm/**", "README.md"))
    files = ["src/pm/a.py", "src/x.py", "README.md", "docs/README.md"]
    assert claims.touches(c, files) == ["src/pm/a.py", "README.md"]
    assert claims.touches(dataclasses.replace(c, paths=()), files) == files
    assert claims.touches(c, []) == []


def _claim(**kw):
    base = dict(id=1, project="proj", num=1, agent="a", pane="", title="t", branch="b", worktree="/w", paths=(),
                issue=None, status="active", created_at=1.0, updated_at=1.0, closed_at=0.0, commit="", note="")
    return claims.Claim(**{**base, **kw})


def test_overlaps_needs_two_active_claims_of_one_project():
    a, b = _claim(num=1, paths=("src/**",)), _claim(num=2, paths=("src/a.py",))
    assert claims.overlaps(a, b)
    assert not claims.overlaps(a, a)
    assert not claims.overlaps(a, dataclasses.replace(b, status="done"))
    assert not claims.overlaps(a, dataclasses.replace(b, project="other"))
    assert not claims.overlaps(a, dataclasses.replace(b, paths=("docs/**",)))


# ------------------------------------------------------------------ validation
@pytest.mark.parametrize("bad", ["/etc/passwd", "../x", "a/../b", "-x", "~/x", "a\\b", "a\nb", "", "a//b",
                                 "x" * 201, "./"])
def test_bad_globs_refused(bad):
    with pytest.raises(Invalid):
        claims.clean_paths([bad])


def test_clean_paths_normalises_and_caps():
    assert claims.clean_paths(["./src/", "src", "docs/**"]) == ("src", "docs/**")
    with pytest.raises(Invalid):
        claims.clean_paths([f"f{i}" for i in range(claims.MAX_PATHS + 1)])


def test_add_refuses_bad_pane_and_title(tk):
    with pytest.raises(Invalid):
        _add(tk, pane="12")
    with pytest.raises(Invalid):
        _add(tk, title="   ")


# ------------------------------------------------------------------ store
def test_add_list_show_numbers_and_json(tk):
    au = Audit()
    c, over = _add(tk, "first", paths=["src/**"], pane="%3", branch="b1", worktree="/w1", audit=au)
    assert (c.num, c.status, c.paths, c.pane, over) == (1, "active", ("src/**",), "%3", [])
    d = c.to_dict()
    assert list(d) == ["num", "project", "agent", "pane", "title", "branch", "worktree", "paths", "issue",
                       "status", "stale", "created_at", "updated_at", "closed_at", "commit", "note"]
    assert d["created_at"].startswith("20") and "T" in d["created_at"] and d["closed_at"] is None
    assert au.lines[0][0] == "tracker_claim_add" and au.lines[0][1]["file"] == "claim"
    assert au.lines[0][1]["num"] == 1 and "title_len" in "".join(au.lines[0][1])  # text as length + hash only
    c2, over = _add(tk, "second", agent="a2", paths=["src/pm/x.py"])
    assert c2.num == 2 and [o.num for o in over] == [1]
    _c3, over = _add(tk, "mine too", agent="a1", paths=["src/pm"])
    assert [o.num for o in over] == [2]  # own claims are no overlap
    assert [c.num for c in claims.list_claims(tk, "proj")] == [1, 2, 3]
    assert [c.num for c in claims.list_claims(tk, "proj", agent="a2")] == [2]
    assert claims.show(tk, "proj", 2).title == "second"
    with pytest.raises(NotFound):
        claims.show(tk, "proj", 9)
    assert claims.list_claims(tk, "other") == []


def test_update_done_drop(tk):
    au = Audit()
    _add(tk, "one", paths=["src/**"])
    _add(tk, "two", agent="a2", paths=["src/a.py"])
    c = claims.update(tk, "proj", 1, paths=["docs/**"], title="one b", audit=au)
    assert (c.title, c.paths) == ("one b", ("docs/**",))
    with pytest.raises(Invalid):
        claims.update(tk, "proj", 1)
    claims.update(tk, "proj", 1, paths=["src/**"])
    c, notify = claims.done(tk, "proj", 1, actor="a1", commit="ABC1234", note="shipped", audit=au)
    assert (c.status, c.commit, c.note, c.closed_at > 0) == ("done", "abc1234", "shipped", True)
    assert [o.num for o in notify] == [2]
    with pytest.raises(Conflict):
        claims.drop(tk, "proj", 1)
    with pytest.raises(Invalid):
        claims.done(tk, "proj", 2, actor="a1", commit="not-a-sha")
    assert claims.drop(tk, "proj", 2, note="later").status == "dropped"
    assert claims.list_claims(tk, "proj") == []
    assert [c.num for c in claims.list_claims(tk, "proj", closed=True)] == [1, 2]
    assert [a for a, _ in au.lines] == ["tracker_claim_update", "tracker_claim_done"]


def test_audit_failure_rolls_back(tk):
    with pytest.raises(RuntimeError):
        _add(tk, audit=Audit(fail=True))
    assert claims.list_claims(tk, "proj") == []
    _add(tk)
    with pytest.raises(RuntimeError):
        claims.done(tk, "proj", 1, actor="a1", audit=Audit(fail=True))
    assert claims.show(tk, "proj", 1).status == "active"
    assert _add(tk)[0].num == 2


# ------------------------------------------------------------------ stale + gc
def test_stale_and_gc(tk):
    _add(tk, "alive", pane="%1")
    _add(tk, "gone", agent="a2", pane="%2", paths=["src/**"])
    _add(tk, "no pane", agent="a3")
    live = frozenset({"%1"})
    by = {c.num: c.stale for c in claims.list_claims(tk, "proj", live=live)}
    assert by == {1: False, 2: True, 3: False}
    assert not any(c.stale for c in claims.list_claims(tk, "proj", live=None))  # tmux unknown: never stale
    _c, over = _add(tk, "new", agent="a9", paths=["src/x.py"], live=live)
    assert 2 not in [o.num for o in over]  # stale claims never block
    assert claims.gc(tk, "proj", live=None) == []
    au = Audit()
    gone = claims.gc(tk, "proj", live=live, audit=au)
    assert [c.num for c in gone] == [2] and gone[0].status == "dropped" and au.lines[0][1]["count"] == 1
    assert claims.show(tk, "proj", 2).note == "stale: pane gone"
    assert claims.gc(tk, "proj", live=live) == []


def test_live_panes_from_fake_tmux(fake_tmux, monkeypatch):
    row = ["$1", "work", "1", "@1", "0", "main", "1", "{pid}", "4242", "claude", "/w", "1", "t", "{dead}"]
    out = "".join(SEP.join(row).format(pid=p, dead=d) + "\n" for p, d in (("%1", "0"), ("%2", "1")))
    monkeypatch.setenv("FAKE_TMUX_OUT", out)
    assert claims.live_panes(fake_tmux) == frozenset({"%1"})
    log = fake_tmux.extras["log"].read_text()
    assert "list-panes\n-a\n" in log and "pmtest-fake" in log
    monkeypatch.setenv("FAKE_TMUX_OUT", "")
    assert claims.live_panes(fake_tmux) is None  # no panes at all: unknown
    monkeypatch.setenv("FAKE_TMUX_ERR", "boom")
    assert claims.live_panes(fake_tmux) is None


# ------------------------------------------------------------------ issues
def test_issue_link_notes(tk):
    with pytest.raises(Invalid, match="no issue tracker"):
        _add(tk, issue=1)
    assert claims.list_claims(tk, "proj") == []
    root = tk.dev_root / "proj"
    (root / "BACKLOG.md").write_text((FIX / "demo-BACKLOG.md").read_text())
    mdimport.import_project(tk, "proj", root, actor="t", files=("backlog",))
    with pytest.raises(NotFound):
        _add(tk, issue=99)
    num = store.list_items(tk, "proj", "backlog")[0].num
    c, _ = _add(tk, "fix it", branch="b1", issue=num)
    claims.done(tk, "proj", c.num, actor="a1", commit="abcdef1", note="fixed")
    notes = [n["body_md"] for n in issues.detail(tk, "proj", num)["notes"]]
    assert notes == [f"claimed by a1 (c{c.num}) on b1: fix it", "done by a1 in abcdef1: fixed"]
    assert json.loads(json.dumps(claims.show(tk, "proj", c.num).to_dict()))["issue"] == num
