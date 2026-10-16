"""Hand edits of the main tree's BACKLOG.md / TODO.md are taken into pm.db before the next change."""

import logging

import pytest

from pm.projects.tracker import issues, mdgen, mdimport, schema, store

from .conftest import display_today

TODAY = display_today()
BASE = "## Inbox\n\n- [ ] alpha\n- [ ] beta\n"


@pytest.fixture
def main(tk):
    f = tk.dev_root / "proj" / "BACKLOG.md"
    f.write_text(BASE)
    mdimport.import_project(tk, "proj", f.parent, files=("backlog",))
    return f


def _last_hash(tk):
    con = schema.connect(tk)
    try:
        return schema.imported(con, "proj", "backlog")["last_hash"]
    finally:
        con.close()


def test_hash_recorded_on_render(tk, main):
    assert _last_hash(tk) == mdgen.text_hash(main.read_bytes())
    store.add_item(tk, "proj", "backlog", "gamma")
    assert _last_hash(tk) == mdgen.text_hash(main.read_bytes())


def test_hand_added_line_kept_after_mutation(tk, main, caplog):
    main.write_text(BASE + "- [ ] by hand\n")
    with caplog.at_level(logging.WARNING, logger="pm.tracker"):
        store.add_item(tk, "proj", "backlog", "from web")
    assert main.read_text() == BASE + f"- [ ] by hand\n- [ ] from web ({TODAY})\n"
    assert [i.title for i in store.list_items(tk, "proj", "backlog")] == [
        "alpha", "beta", "by hand", f"from web ({TODAY})"]
    assert any("edited outside pm" in r.getMessage() for r in caplog.records)
    assert "by hand" not in caplog.text  # no file text in the log


def test_hand_ticked_item_stays_ticked(tk, main):
    main.write_text(BASE.replace("- [ ] alpha", "- [x] alpha"))
    store.set_done(tk, "proj", "backlog", 2, True)
    assert [i.done for i in store.list_items(tk, "proj", "backlog")] == [True, True]
    assert main.read_text() == "## Inbox\n\n- [x] alpha\n- [x] beta\n"


def test_body_survives_a_hand_edited_line_with_its_ref(tk, main):
    store.edit_item(tk, "proj", "backlog", 1, body="details")
    issues.add_note(tk, "proj", 1, "a note")
    assert "- [ ] alpha [#1]\n" in main.read_text()
    main.write_text(main.read_text().replace("- [ ] alpha [#1]", "- [x] alpha renamed [#1]"))
    store.add_item(tk, "proj", "backlog", "next")
    it = store.get_item(tk, "proj", "backlog", 1)
    assert (it.title, it.done, it.body_md, it.n_notes) == ("alpha renamed", True, "details", 1)
    assert "- [x] alpha renamed [#1]\n" in main.read_text()


def test_hand_removed_item_is_dropped(tk, main):
    main.write_text(BASE.replace("- [ ] beta\n", ""))
    store.add_item(tk, "proj", "backlog", "x")
    assert [i.num for i in store.list_items(tk, "proj", "backlog")] == [1, 3]


def test_hand_removed_item_with_more_is_kept(tk, main, caplog):
    store.edit_item(tk, "proj", "backlog", 1, body="secret body")
    issues.add_note(tk, "proj", 2, "a note")
    main.write_text("## Inbox\n\n- [ ] new line\n")  # alpha [#1] and beta [#2] gone
    with caplog.at_level(logging.WARNING, logger="pm.tracker"):
        store.add_item(tk, "proj", "backlog", "x")
    items = store.list_items(tk, "proj", "backlog")
    by = {i.num: i for i in items}
    assert {n: i.title for n, i in by.items()} == {1: "alpha", 2: "beta", 3: "new line", 4: f"x ({TODAY})"}
    assert by[1].body_md == "secret body" and by[2].n_notes == 1
    text = main.read_text()
    assert "- [ ] alpha [#1]\n" in text and "- [ ] beta [#2]\n" in text and "- [ ] new line\n" in text
    msgs = [r.getMessage() for r in caplog.records]
    assert any("#1 (body, notes or images) is missing" in m for m in msgs) and any("#2 (" in m for m in msgs)
    assert "secret" not in caplog.text and "alpha" not in caplog.text


def test_no_absorb_when_hash_matches(tk, main, monkeypatch):
    calls = []
    real = mdimport._store
    monkeypatch.setattr(mdimport, "_store", lambda *a, **k: calls.append(a) or real(*a, **k))
    store.add_item(tk, "proj", "backlog", "one")
    store.set_done(tk, "proj", "backlog", 1, True)
    assert calls == []
    assert mdimport.absorb(tk, "proj", "backlog") is False


def test_same_text_without_hash_only_records_it(tk, main):
    con = schema.connect(tk)
    con.execute("UPDATE tk_file SET last_hash='' WHERE project='proj'")
    con.close()
    assert mdimport.absorb(tk, "proj", "backlog") is False
    assert _last_hash(tk) == mdgen.text_hash(main.read_bytes())


@pytest.mark.parametrize("text", ["", "## Inbox\n\n<<<<<<< HEAD\n- [ ] a\n=======\n- [ ] b\n>>>>>>> b\n"])
def test_empty_or_conflicted_file_not_taken(tk, main, text):
    main.write_text(text)
    assert mdimport.absorb(tk, "proj", "backlog") is False
    store.add_item(tk, "proj", "backlog", "c")
    assert [i.title for i in store.list_items(tk, "proj", "backlog")][:2] == ["alpha", "beta"]


def test_missing_file_not_taken(tk, main):
    main.unlink()
    store.add_item(tk, "proj", "backlog", "c")
    assert len(store.list_items(tk, "proj", "backlog")) == 3 and main.exists()


def test_absorb_survives_old_db_without_column(tk, main):
    """An old pm.db without tk_file.last_hash gets the column on connect."""
    from pm.core import db
    con = schema.connect(tk)
    con.execute(schema.SCHEMA[0].replace("IF NOT EXISTS tk_file", "tk_file_old"))
    con.execute("INSERT INTO tk_file_old SELECT project, file, eol, imported_at, imported_by, next_num FROM tk_file")
    con.execute("DROP TABLE tk_file")
    con.execute("ALTER TABLE tk_file_old RENAME TO tk_file")
    con.close()
    db.inited(schema.SCHEMA).clear()
    main.write_text(BASE + "- [ ] by hand\n")
    store.add_item(tk, "proj", "backlog", "z")
    assert "by hand" in [i.title for i in store.list_items(tk, "proj", "backlog")]
