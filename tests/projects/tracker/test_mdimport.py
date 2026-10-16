"""mdimport: render(import(md)) == md byte for byte; numbering; --replace keeps rows."""

import pytest

from pm.core.errors import Conflict
from pm.projects.tracker import issues, mdgen, mdimport, store

from .conftest import SAMPLES, sample_kind


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name)
def test_round_trip_real_files(tk, path):
    proj, file = sample_kind(path)
    text = path.read_bytes().decode()
    mdimport.import_text(tk, proj, file, text)
    assert mdgen.render_text(tk, proj, file).encode() == path.read_bytes()


EDGE = [
    "",
    "\n",
    "\n\n",
    "# t",
    "# t\n- [ ] a\n- [x] b",
    "- [ ] a  \n- [X] b\t\n  - [ ] sub\n",
    "- [ ]\n- [x]x\n-  [ ] not an item\n",
    "intro\r\n- [ ] crlf item\r\n",
    "- [ ]   spaced    title   \n",
    "```\n- [ ] in a fence\n```\n",
]
EDGE_TODO = EDGE + ["* [ ] star\n    * [x] deep\n- [ ]  two spaces\n", "- [ ] \n"]


@pytest.mark.parametrize("text", EDGE)
def test_round_trip_backlog_edge_cases(tk, text):
    mdimport.import_text(tk, "proj", "backlog", text)
    assert mdgen.render_text(tk, "proj", "backlog") == text


@pytest.mark.parametrize("text", EDGE_TODO)
def test_round_trip_todo_edge_cases(tk, text):
    mdimport.import_text(tk, "proj", "todo", text)
    assert mdgen.render_text(tk, "proj", "todo") == text


def test_items_use_file_mode_grammar():
    p = mdimport.parse("- [ ] a\n* [ ] star\n  - [x] sub\n", "backlog")
    assert [i.title for i in p.items] == ["a", "sub"]
    assert [ln for _pos, ln in p.lines] == ["* [ ] star"]
    t = mdimport.parse("- [ ] a\n* [ ] star\n  - [X] sub\n", "todo")
    assert [(i.title, i.mark, i.prefix) for i in t.items] == [("a", " ", "- "), ("star", " ", "* "),
                                                              ("sub", "X", "  - ")]


def test_numbers_follow_file_order_and_ref_suffix(tk):
    mdimport.import_text(tk, "proj", "backlog", "- [ ] a\n- [ ] b [#2]\n- [ ] c\n- [ ] d [#9]\n")
    got = [(i.num, i.title) for i in store.list_items(tk, "proj", "backlog")]
    assert got == [(1, "a"), (2, "b"), (3, "c"), (9, "d")]
    assert store.add_item(tk, "proj", "backlog", "e").num == 10


def test_duplicate_ref_gets_a_new_number(tk):
    mdimport.import_text(tk, "proj", "backlog", "- [ ] a [#1]\n- [ ] b [#1]\n")
    assert [(i.num, i.title) for i in store.list_items(tk, "proj", "backlog")] == [(1, "a"), (2, "b")]


def test_status_and_indent(tk):
    mdimport.import_text(tk, "proj", "todo", "- [x] done\n  - [ ] open sub\n")
    a, b = store.list_items(tk, "proj", "todo")
    assert (a.status, a.indent, b.status, b.indent) == ("done", 0, "open", 2)
    assert [i.num for i in store.list_items(tk, "proj", "todo", "open")] == [2]


def test_imported_date_suffix_sets_created_at(tk):
    mdimport.import_text(tk, "proj", "backlog", "- [ ] old thing (2026-10-07)\n")
    from datetime import datetime

    from pm.core import timefmt

    # midnight in the display offset (conftest: the Settings default), not the host's
    assert store.get_item(tk, "proj", "backlog", 1).created_at == datetime(2026, 10, 7, tzinfo=timefmt.tz()).timestamp()


def test_reimport_refused_without_replace(tk):
    mdimport.import_text(tk, "proj", "backlog", "- [ ] a\n")
    with pytest.raises(Conflict):
        mdimport.import_text(tk, "proj", "backlog", "- [ ] b\n")
    assert [i.title for i in store.list_items(tk, "proj", "backlog")] == ["a"]


def test_replace_keeps_rows_by_ref_and_title(tk):
    mdimport.import_text(tk, "proj", "backlog", "# B\n- [ ] keep me\n- [ ] with body\n- [ ] gone\n")
    store.edit_item(tk, "proj", "backlog", 2, body="details")
    issues.add_note(tk, "proj", 2, "a note")
    rendered = mdgen.render_text(tk, "proj", "backlog")
    assert "- [ ] with body [#2]\n" in rendered
    hand = rendered.replace("- [ ] gone\n", "- [x] new one\n").replace("# B", "# Backlog")
    res = mdimport.import_text(tk, "proj", "backlog", hand, replace=True)
    assert res["dropped"] == 1
    got = {i.title: i for i in store.list_items(tk, "proj", "backlog")}
    assert got["keep me"].num == 1 and got["with body"].num == 2 and got["new one"].num == 4
    assert got["with body"].body_md == "details" and got["with body"].n_notes == 1
    assert got["new one"].done
    assert mdgen.render_text(tk, "proj", "backlog") == hand


def test_import_project_reads_main_tree(tk):
    root = tk.dev_root / "proj"
    (root / "BACKLOG.md").write_text("# b\n- [ ] x\n")
    res = mdimport.import_project(tk, "proj", root)
    assert [r["file"] for r in res] == ["backlog"]  # no TODO.md: stays in file mode
    (root / "TODO.md").write_text("- [ ] t\n")
    res = mdimport.import_project(tk, "proj", root, files=("todo",))
    assert res[0]["items"] == 1
    assert mdgen.tracked_files(tk, "proj") == ["backlog", "todo"]


def test_import_project_without_backlog_starts_empty(tk):
    res = mdimport.import_project(tk, "proj", tk.dev_root / "proj")
    assert res == [{"project": "proj", "file": "backlog", "items": 0, "lines": 0, "dropped": 0}]
    store.add_item(tk, "proj", "backlog", "first")
    text = (tk.dev_root / "proj" / "BACKLOG.md").read_text()
    assert text.startswith("## Inbox\n\n- [ ] first (")
