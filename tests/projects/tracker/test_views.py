"""views: same dict shapes as the file-mode summaries; tracker projects come from pm.db."""

import pytest

from pm.core.errors import Conflict, NotFound
from pm.projects import backlog, todo
from pm.projects.tracker import issues, mdimport, schema, store, views

from .conftest import FIX

TRACKER_KEYS = {"id", "num", "has_more", "n_img", "n_notes", "title_short", "created_by", "created_at", "age_at", "tracker"}


def _two_projects(tk):
    """``proj`` (file mode) and ``trk`` (tracker) with the same BACKLOG.md and TODO.md."""
    for name in ("proj", "trk"):
        d = tk.dev_root / name
        d.mkdir(exist_ok=True)
        (d / "BACKLOG.md").write_bytes((FIX / "alpha-BACKLOG.md").read_bytes())
        (d / "TODO.md").write_bytes((FIX / "alpha-TODO.md").read_bytes())
    mdimport.import_project(tk, "trk", tk.dev_root / "trk")


def test_is_tracker(tk):
    _two_projects(tk)
    assert views.is_tracker(tk, "trk") and views.is_tracker(tk, "trk", "todo")
    assert not views.is_tracker(tk, "proj")


def test_file_mode_delegates(tk):
    _two_projects(tk)
    assert views.project_summary(tk, "proj") == backlog.project_summary(tk, "proj")
    assert views.todo_summary(tk, "proj") == todo.todo_summary(tk, "proj")


def test_project_summary_matches_file_mode(tk):
    _two_projects(tk)
    f, t = backlog.project_summary(tk, "proj", 5), views.project_summary(tk, "trk", 5)
    assert set(f) == set(t)
    for k in ("exists", "token", "open_n", "done_n", "more", "done_more", "blocked", "max_item"):
        assert f[k] == t[k], k
    for fi, ti in zip(f["items"] + f["done_items"], t["items"] + t["done_items"], strict=True):
        assert set(ti) == set(fi) | TRACKER_KEYS
        assert {k: ti[k] for k in ("kind", "done", "text", "inbox")} == \
               {k: fi[k] for k in ("kind", "done", "text", "inbox")}
        assert ti["html"] == backlog.inline(store.short_title(fi["text"]))  # display drops the date suffix
        assert ti["line"] == ti["num"] and ti["tracker"] is True


def test_todo_summary_matches_file_mode(tk):
    _two_projects(tk)
    f, t = todo.todo_summary(tk, "proj"), views.todo_summary(tk, "trk")
    assert set(f) == set(t)
    for k in ("exists", "blocked", "token", "open_n", "done_n", "total"):
        assert f[k] == t[k], k
    for part in ("shown", "rest"):
        assert [g["heading"] for g in f[part]] == [g["heading"] for g in t[part]]
        for fg, tg in zip(f[part], t[part], strict=True):
            for fi, ti in zip(fg["items"], tg["items"], strict=True):
                assert set(ti) == set(fi) | {"id", "num", "tracker"}
                assert {k: ti[k] for k in ("done", "text", "heading", "html", "label")} == \
                       {k: fi[k] for k in ("done", "text", "heading", "html", "label")}


def test_entries_mix_tracker_and_file_projects(tk):
    _two_projects(tk)
    es = views.entries(tk)
    assert [e["name"] for e in es] == ["proj", "trk"]  # same open count: by name
    f, t = es
    assert set(t) == set(f) | {"tracker"}
    assert [b["kind"] for b in f["blocks"]] == [b["kind"] for b in t["blocks"]]
    assert [b.get("html") for b in f["blocks"] if b["kind"] == "md"] == \
           [b.get("html") for b in t["blocks"] if b["kind"] == "md"]
    assert backlog.entry("proj", str(tk.dev_root / "proj")) == f


def test_tracker_items_carry_issue_data(tk):
    mdimport.import_text(tk, "proj", "backlog", "## Inbox\n")
    it = issues.create_issue(tk, "proj", "with body", "text", actor="claude-z")
    store.set_done(tk, "proj", "backlog", it.num, False)
    s = views.project_summary(tk, "proj")
    (b,) = s["items"]
    assert b["num"] == it.num and b["has_more"] and b["created_by"] == "claude-z" and b["inbox"]
    assert s["exists"] and s["open_n"] == 1


def test_items_carry_notes_and_short_title(tk):
    mdimport.import_text(tk, "proj", "backlog", "## Inbox\n\n- [ ] old one (2026-01-02)\n")
    issues.add_note(tk, "proj", 1, "n1")
    issues.add_note(tk, "proj", 1, "n2")
    (b,) = views.project_summary(tk, "proj")["items"]
    assert b["n_notes"] == 2 and b["text"] == "old one (2026-01-02)"
    assert b["title_short"] == "old one" and b["html"] == "old one"
    (e,) = [x for x in views.entries(tk) if x["name"] == "proj"]
    assert [x["html"] for x in e["blocks"] if x["kind"] == "item"] == ["old one"]


def test_issues_summary_limit_and_flag(tk):
    _two_projects(tk)
    s = views.issues_summary(tk, "trk")
    assert s["tracker"] is True and s == {**views.project_summary(tk, "trk", views.ISSUES_LIMIT), "tracker": True}
    f = views.issues_summary(tk, "proj")
    assert f["tracker"] is False and f == {**backlog.project_summary(tk, "proj"), "tracker": False}


def test_require_tracker(tk):
    _two_projects(tk)
    views.require_tracker(tk, "trk")
    views.require_tracker(tk, "trk", "todo")
    with pytest.raises(Conflict):
        views.require_tracker(tk, "proj")
    with pytest.raises(NotFound):
        views.require_tracker(tk, "nope")


def test_age_at_ignores_import_time(tk):
    """An imported item without a date in its title was stamped with the import time: no age (0) instead of
    "2m ago". Dated items count from the title date; a done item from its real close, else its created time."""
    mdimport.import_text(tk, "proj", "backlog",
                         "## Inbox\n\n- [ ] undated open\n- [ ] dated open (2026-01-02)\n"
                         "- [x] undated done\n- [x] dated done (2026-01-03)\n")
    s = views.project_summary(tk, "proj")
    by = {b["text"]: b for b in s["items"] + s["done_items"]}
    assert by["undated open"]["age_at"] == 0 and by["undated done"]["age_at"] == 0
    assert by["dated open (2026-01-02)"]["age_at"] == mdimport.title_date("(2026-01-02)") > 0
    assert by["dated done (2026-01-03)"]["age_at"] == mdimport.title_date("(2026-01-03)")
    assert all(store.get_item(tk, "proj", "backlog", b["num"]).closed_at == 0 for b in s["done_items"])
    n = by["undated done"]["num"]
    store.set_done(tk, "proj", "backlog", n, False)
    store.set_done(tk, "proj", "backlog", n, True)  # a real close: counts from then
    closed = store.get_item(tk, "proj", "backlog", n).closed_at
    assert closed > 0 and next(b for b in views.project_summary(tk, "proj")["done_items"]
                               if b["num"] == n)["age_at"] == closed
    added = store.add_item(tk, "proj", "backlog", "typed in pm")  # has an author: its created time is real
    assert next(b for b in views.project_summary(tk, "proj")["items"] if b["num"] == added.num)["age_at"] > 0


def test_detail_created_unknown_for_undated_import(tk):
    mdimport.import_text(tk, "proj", "backlog", "## Inbox\n\n- [ ] undated\n- [ ] dated (2026-01-02)\n")
    nums = {b["text"]: b["num"] for b in views.project_summary(tk, "proj")["items"]}
    assert issues.detail(tk, "proj", nums["undated"])["created_at"] == 0
    assert issues.detail(tk, "proj", nums["dated (2026-01-02)"])["created_at"] == mdimport.title_date("2026-01-02")


# ------------------------------------------------------------------ cross-project lists
def _three(tk):
    """``proj`` (tracker: BACKLOG + TODO), ``other`` (tracker BACKLOG, file TODO.md), ``plain`` (file mode)."""
    files = {"proj": ("## Inbox\n\n- [ ] alpha bug\n- [x] fixed thing\n- [ ] gamma\n",
                      "# T\n\n## P1 first\n- [ ] t1\n- [x] t2\n\n## P2 `second`\n- [ ] t3\n"),
             "other": ("## Inbox\n\n- [ ] Alpha in other\n", "## Now\n- [x] o1\n- [ ] o2\n"),
             "plain": ("## Inbox\n\n- [ ] plain issue\n", "- [ ] p1\n")}
    for name, (bl, td) in files.items():
        d = tk.dev_root / name
        d.mkdir(exist_ok=True)
        (d / "BACKLOG.md").write_text(bl)
        (d / "TODO.md").write_text(td)
    mdimport.import_project(tk, "proj", tk.dev_root / "proj")
    mdimport.import_project(tk, "other", tk.dev_root / "other", files=("backlog",))


def _keys(out):
    return [(r["project"], r["num"]) for r in out["rows"]]


def test_issue_rows_filters_counts_and_paging(tk):
    _three(tk)
    store.add_item(tk, "proj", "backlog", "newest one")
    store.set_done(tk, "proj", "backlog", 3, True)
    out = views.issue_rows(tk)
    assert out["counts"] == {"open": 3, "done": 2, "all": 5} and out["total"] == 3
    assert out["projects"] == ["other", "proj"] and out["status"] == "open"
    assert _keys(out)[0] == ("proj", 4) and sorted(_keys(out)[1:]) == [("other", 1), ("proj", 1)]  # newest first
    row = out["rows"][0]
    assert row["title"] == "newest one" and not row["done"] and row["created_at"] > 0 and row["created_by"] == "user"
    assert set(row) >= {"project", "num", "title", "html", "done", "n_notes", "n_img", "created_at", "created_by",
                        "closed_at", "age_at", "labels", "has_body"}
    done = views.issue_rows(tk, status="done")
    assert _keys(done)[0] == ("proj", 3) and all(r["done"] for r in done["rows"]) and done["total"] == 2
    assert views.issue_rows(tk, status="all")["total"] == 5
    assert views.issue_rows(tk, status="bogus")["status"] == "open"
    # q: title (folded), project, #num; counts follow the q, not the status
    q = views.issue_rows(tk, q="  ALPHA ")
    assert sorted(_keys(q)) == [("other", 1), ("proj", 1)] and q["counts"] == {"open": 2, "done": 0, "all": 2}
    assert _keys(views.issue_rows(tk, q="other")) == [("other", 1)]
    assert _keys(views.issue_rows(tk, q="#4")) == [("proj", 4)]
    assert _keys(views.issue_rows(tk, project="other")) == [("other", 1)]
    assert views.issue_rows(tk, project="plain")["rows"] == []  # not in the tracker
    page = views.issue_rows(tk, status="all", limit=2, offset=2)
    assert len(page["rows"]) == 2 and page["total"] == 5 and page["limit"] == 2 and page["offset"] == 2
    assert views.issue_rows(tk, limit=10**6)["limit"] == views.LIST_MAX
    assert views.issue_rows(tk, limit=-1, offset=-5)["limit"] == 1


def test_issue_rows_has_no_per_project_cap(tk):
    _three(tk)
    con = schema.connect(tk)
    with schema.tx(con):
        for i in range(views.ISSUES_LIMIT + 20):
            store.insert_item(con, "proj", "backlog", f"bulk {i}")
    con.close()
    out = views.issue_rows(tk, project="proj", limit=views.LIST_MAX)
    assert out["total"] == views.ISSUES_LIMIT + 22 and len(out["rows"]) == out["total"]


def test_issue_projects_are_main_projects_with_a_tracker_backlog(tk):
    assert views.issue_projects(tk) == []  # nothing imported yet
    _three(tk)
    (tk.dev_root / "todo-only").mkdir()
    (tk.dev_root / "todo-only" / "TODO.md").write_text("- [ ] t\n")
    mdimport.import_project(tk, "todo-only", tk.dev_root / "todo-only", files=("todo",))
    mdimport.import_text(tk, "gone", "backlog", "## Inbox\n\n- [ ] not a registered project\n")
    assert views.is_tracker(tk, "todo-only", "todo") and not views.is_tracker(tk, "todo-only", "backlog")
    assert views.issue_projects(tk) == ["other", "proj"]  # not plain (file mode), todo-only, gone
    assert views.issue_rows(tk)["projects"] == views.issue_projects(tk)


def test_open_issue_counts(tk):
    _three(tk)
    assert views.open_issue_counts(tk) == {"proj": 2, "other": 1}


def test_todo_groups_tracker_and_file_mode(tk):
    _three(tk)
    out = views.todo_groups(tk)
    assert out["counts"] == {"open": 4, "done": 2, "all": 6}
    by = {g["name"]: g for g in out["groups"]}
    assert set(by) == {"proj", "other", "plain"}
    p = by["proj"]
    assert p["tracker"] and (p["open_n"], p["done_n"]) == (2, 1)
    assert [(s["heading"], s["open_n"], s["done_n"], [i["text"] for i in s["items"]]) for s in p["sections"]] == [
        ("P1 first", 1, 1, ["t1"]), ("P2 second", 1, 0, ["t3"])]
    assert p["sections"][0]["items"][0]["line"] == p["sections"][0]["items"][0]["num"] == 1
    o = by["other"]
    assert not o["tracker"] and o["sections"][0]["items"][0]["text"] == "o2"
    assert o["sections"][0]["items"][0]["line"] == 2  # file mode: the line index
    done = views.todo_groups(tk, status="done")
    assert sorted(i["text"] for g in done["groups"] for s in g["sections"] for i in s["items"]) == ["o1", "t2"]
    assert [g["name"] for g in views.todo_groups(tk, project="plain")["groups"]] == ["plain"]


def test_todo_summary_unchanged_by_shared_loader(tk):
    _three(tk)
    s = views.todo_summary(tk, "proj")
    assert s["open_n"] == 2 and s["done_n"] == 1 and s["shown"][0]["heading"] == "P1 first"
    assert set(s["shown"][0]["items"][0]) == {"line", "done", "text", "heading", "html", "label", "id", "num",
                                              "tracker"}
