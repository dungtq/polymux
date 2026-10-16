"""schema: tables, autocommit + explicit transactions, file names."""

import sqlite3

import pytest

from pm.core import db
from pm.core.errors import Invalid
from pm.projects.tracker import schema


def test_tables_exist(tk):
    con = schema.connect(tk)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    assert {"tk_file", "tk_line", "tk_item", "tk_attach", "tk_note", "tk_change", "tk_feature",
            "tk_feature_rev", "tk_claim"} <= names


def test_claim_table_on_an_existing_db(tk):
    """A pm.db made before tk_claim existed gets it (and its index) on the next connect."""
    con = schema.connect(tk)
    con.execute("DROP TABLE tk_claim")
    con.close()
    db.inited(schema.SCHEMA).discard(str(tk.db_file))  # a new process: the schema runs again
    con = schema.connect(tk)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(tk_claim)")}
        idx = {r[1] for r in con.execute("PRAGMA index_list(tk_claim)")}
        con.execute("INSERT INTO tk_claim (project, num) VALUES ('p', 1)")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO tk_claim (project, num) VALUES ('p', 1)")  # num unique per project
        r = con.execute("SELECT * FROM tk_claim").fetchone()
    finally:
        con.close()
    assert {"project", "num", "agent", "pane", "title", "branch", "worktree", "paths", "issue_num", "status",
            "created_at", "updated_at", "closed_at", "commit_sha", "note"} <= cols
    assert "tk_claim_status" in idx
    assert (r["status"], r["paths"], r["issue_num"], r["created_at"]) == ("active", "[]", None, 0)


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
