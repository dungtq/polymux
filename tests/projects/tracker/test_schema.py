"""schema: tables, autocommit + explicit transactions, file names."""

import pytest

from pm.core.errors import Invalid
from pm.projects.tracker import schema


def test_tables_exist(tk):
    con = schema.connect(tk)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    assert {"tk_file", "tk_line", "tk_item", "tk_attach", "tk_note", "tk_change", "tk_feature",
            "tk_feature_rev"} <= names


def test_tx_commits_and_rolls_back(tk):
    con = schema.connect(tk)
    try:
        with schema.tx(con):
            con.execute("INSERT INTO tk_line (project, file, pos, text) VALUES ('p', 'backlog', 1, 'a')")
        with pytest.raises(ValueError), schema.tx(con):
            con.execute("INSERT INTO tk_line (project, file, pos, text) VALUES ('p', 'backlog', 2, 'b')")
            raise ValueError
        assert [r["text"] for r in con.execute("SELECT text FROM tk_line")] == ["a"]
        assert not con.in_transaction
    finally:
        con.close()


def test_check_file():
    assert schema.check_file("todo") == "todo"
    with pytest.raises(Invalid):
        schema.check_file("CHANGELOG")
    with pytest.raises(Invalid):
        schema.check_file("changelog")  # FILES = the item files only
    assert schema.file_name("features") == "FEATURES.md" and set(schema.ALL_FILES) == {*schema.FILES, *schema.DOC_FILES}
    with pytest.raises(Invalid):
        schema.file_name("readme")
    assert schema.max_bytes("changelog") > schema.max_bytes("backlog")


def test_shares_pm_db_with_chat_stores(tk):
    from pm.chat import items

    items.connect(tk).close()
    schema.connect(tk).close()
    assert tk.db_file.is_file() and not (tk.state_dir / "tracker.db").exists()
