"""mdgen: item line format, byte stability, write_main into the main tree (never raises)."""

import logging

from pm.projects.tracker import mdgen, mdimport, schema, store


def test_item_line_format():
    row = {"prefix": "  * ", "mark": "X", "status": "done", "gap": "  ", "title": "t", "tail": " ",
           "num": 7, "body_md": "", "n_img": 0, "n_notes": 0}
    assert mdgen.item_line(row) == "  * [X]  t "
    assert mdgen.item_line({**row, "status": "open"}) == "  * [ ]  t "
    assert mdgen.item_line({**row, "n_notes": 1}) == "  * [X]  t [#7] "
    assert mdgen.item_line({**row, "body_md": "  \n"}) == "  * [X]  t "


def test_render_is_byte_stable_and_none_when_not_imported(tk):
    assert mdgen.render_text(tk, "proj", "backlog") is None
    mdimport.import_text(tk, "proj", "backlog", "# x\n- [ ] a\n")
    store.add_item(tk, "proj", "backlog", "b")
    assert mdgen.render_text(tk, "proj", "backlog") == mdgen.render_text(tk, "proj", "backlog")


def test_empty_after_deleting_everything(tk):
    mdimport.import_text(tk, "proj", "backlog", "- [ ] a\n")
    store.delete_item(tk, "proj", "backlog", 1)
    assert mdgen.render_text(tk, "proj", "backlog") == ""


def test_write_main_writes_only_on_change(tk):
    f = tk.dev_root / "proj" / "BACKLOG.md"
    f.write_text("- [ ] a\n")
    mdimport.import_text(tk, "proj", "backlog", "- [ ] a\n")
    mtime = f.stat().st_mtime_ns
    assert mdgen.write_main(tk, "proj", "backlog") is True
    assert f.stat().st_mtime_ns == mtime
    con = schema.connect(tk)
    con.execute("UPDATE tk_item SET status='done'")
    con.close()
    assert mdgen.write_main(tk, "proj", "backlog") is True
    assert f.read_text() == "- [x] a\n"


def test_write_main_unknown_project_or_file(tk):
    mdimport.import_text(tk, "ghost", "backlog", "- [ ] a\n")
    assert mdgen.write_main(tk, "ghost", "backlog") is False  # not in the registry
    assert mdgen.write_main(tk, "proj", "todo") is False  # not imported


def test_write_failure_keeps_db_change_and_warns(tk, tmp_path, caplog):
    target = tmp_path / "elsewhere.md"
    target.write_text("- [ ] a\n")
    (tk.dev_root / "proj" / "BACKLOG.md").symlink_to(target)
    mdimport.import_text(tk, "proj", "backlog", "- [ ] a\n")
    with caplog.at_level(logging.WARNING, logger="pm.tracker"):
        it = store.add_item(tk, "proj", "backlog", "kept in the db")
    assert it.num == 2 and store.get_item(tk, "proj", "backlog", 2).title.startswith("kept in the db")
    assert target.read_text() == "- [ ] a\n"
    assert any("could not write BACKLOG.md of proj" in r.getMessage() for r in caplog.records)
    assert "kept in the db" not in caplog.text


def test_tracked_files_order(tk):
    mdimport.import_text(tk, "proj", "todo", "")
    mdimport.import_text(tk, "proj", "backlog", "")
    assert mdgen.tracked_files(tk, "proj") == ["backlog", "todo"]
    assert mdgen.tracked_files(tk, "nope") == []
