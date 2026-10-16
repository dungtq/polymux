"""Tracker tables in pm.db (``tk_*``) and the connection / transaction helpers.

A rendered file (``file`` = ``backlog`` for BACKLOG.md, ``todo`` for TODO.md) is one ``pos`` order over
``tk_line`` (fixed text lines) and ``tk_item`` (checklist items). ``tk_file`` marks a (project, file) as
imported: only those are in tracker mode; it also keeps whether the file ends with a newline, the next
item number (numbers are never reused, even after a delete) and the hash of the last render (``last_hash``).

An item line renders as ``prefix + "[" + mark + "]" + gap + title [+ " [#num]"] + tail``: ``prefix`` is the
indent and bullet (``"  - "``), ``gap`` the whitespace after ``]``, ``tail`` trailing whitespace (or ``\\r``),
``mark`` the done mark as imported (``x`` or ``X``). That keeps a render of an import byte-exact.

CHANGELOG.md and FEATURES.md (``DOC_FILES``, T3) are not item files: ``tk_line`` keeps their fixed lines
(``file`` = ``changelog`` / ``features``), ``tk_change`` the CHANGELOG entries, ``tk_feature`` the FEATURES
entries and ``tk_feature_rev`` old texts and branch drafts of a feature (``changelog.py``, ``featurelist.py``,
``drafts.py``). ``FILES`` stays the item files only; ``ALL_FILES`` has all four. ``tk_claim`` holds the agents'
work claims (``claims.py``); it belongs to no file.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from pm.core import db
from pm.core.config import Settings
from pm.core.errors import Invalid
from pm.projects import backlog

FILES = {"backlog": "BACKLOG.md", "todo": "TODO.md"}
DOC_FILES = {"changelog": "CHANGELOG.md", "features": "FEATURES.md"}
ALL_FILES = {**FILES, **DOC_FILES}
DOC_MAX_BYTES = 8 * 1024 * 1024  # CHANGELOG.md grows by ~10 KB a day: the item files' 256 KB cap is too small

SCHEMA = (
    ("CREATE TABLE IF NOT EXISTS tk_file ("
     " project TEXT NOT NULL, file TEXT NOT NULL, eol INTEGER NOT NULL DEFAULT 1,"
     " imported_at REAL NOT NULL DEFAULT 0, imported_by TEXT NOT NULL DEFAULT '',"
     " next_num INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (project, file))"),
    ("CREATE TABLE IF NOT EXISTS tk_line ("
     " id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, file TEXT NOT NULL,"
     " pos REAL NOT NULL, text TEXT NOT NULL DEFAULT '')"),
    "CREATE INDEX IF NOT EXISTS tk_line_pos ON tk_line (project, file, pos)",
    ("CREATE TABLE IF NOT EXISTS tk_item ("
     " id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, file TEXT NOT NULL,"
     " num INTEGER NOT NULL, pos REAL NOT NULL, title TEXT NOT NULL DEFAULT '',"
     " body_md TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open',"
     " indent INTEGER NOT NULL DEFAULT 0, prefix TEXT NOT NULL DEFAULT '- ', gap TEXT NOT NULL DEFAULT ' ',"
     " tail TEXT NOT NULL DEFAULT '', mark TEXT NOT NULL DEFAULT 'x', labels TEXT NOT NULL DEFAULT '',"
     " created_at REAL NOT NULL DEFAULT 0, created_by TEXT NOT NULL DEFAULT '',"
     " updated_at REAL NOT NULL DEFAULT 0, closed_at REAL NOT NULL DEFAULT 0,"
     " closed_by TEXT NOT NULL DEFAULT '', commit_sha TEXT NOT NULL DEFAULT '',"
     " UNIQUE (project, file, num))"),
    "CREATE INDEX IF NOT EXISTS tk_item_pos ON tk_item (project, file, pos)",
    ("CREATE TABLE IF NOT EXISTS tk_attach ("
     " id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL, sha TEXT NOT NULL, ext TEXT NOT NULL,"
     " name TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL DEFAULT 0, created_by TEXT NOT NULL DEFAULT '')"),
    "CREATE INDEX IF NOT EXISTS tk_attach_item ON tk_attach (item_id)",
    ("CREATE TABLE IF NOT EXISTS tk_note ("
     " id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL, author TEXT NOT NULL DEFAULT '',"
     " body_md TEXT NOT NULL DEFAULT '', ts REAL NOT NULL DEFAULT 0)"),
    "CREATE INDEX IF NOT EXISTS tk_note_item ON tk_note (item_id, ts)",
    # CHANGELOG entries: ``heading`` is the whole ``## `` line, ``body`` its raw lines, each ending in "\n".
    # state: landed | draft (added on a branch, ``branch``; ``sha`` = commit stamped by the post-commit hook,
    # ``base_sha`` = HEAD when it was added) | abandoned (its branch is gone unmerged; never shown again)
    ("CREATE TABLE IF NOT EXISTS tk_change ("
     " id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, num INTEGER NOT NULL, pos REAL NOT NULL,"
     " heading TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', branch TEXT NOT NULL DEFAULT '',"
     " state TEXT NOT NULL DEFAULT 'landed', sha TEXT NOT NULL DEFAULT '', base_sha TEXT NOT NULL DEFAULT '',"
     " created_at REAL NOT NULL DEFAULT 0, created_by TEXT NOT NULL DEFAULT '', UNIQUE (project, num))"),
    "CREATE INDEX IF NOT EXISTS tk_change_pos ON tk_change (project, pos)",
    # FEATURES entries ``- **name** — text`` (+ ``extra``: indented continuation lines, raw). state: active |
    # removed | pending (created on a branch, shown only through its draft revision)
    ("CREATE TABLE IF NOT EXISTS tk_feature ("
     " id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, area TEXT NOT NULL, name TEXT NOT NULL,"
     " pos REAL NOT NULL, text TEXT NOT NULL DEFAULT '', extra TEXT NOT NULL DEFAULT '',"
     " state TEXT NOT NULL DEFAULT 'active', branch TEXT NOT NULL DEFAULT '', sha TEXT NOT NULL DEFAULT '',"
     " updated_at REAL NOT NULL DEFAULT 0, updated_by TEXT NOT NULL DEFAULT '')"),
    "CREATE INDEX IF NOT EXISTS tk_feature_pos ON tk_feature (project, pos)",
    # revisions of a feature: state old (a replaced landed text) | draft (a branch's pending set/rm, one per
    # feature and branch) | landed (a draft that reached main) | abandoned
    ("CREATE TABLE IF NOT EXISTS tk_feature_rev ("
     " id INTEGER PRIMARY KEY AUTOINCREMENT, feature_id INTEGER NOT NULL, op TEXT NOT NULL DEFAULT 'set',"
     " text TEXT NOT NULL DEFAULT '', extra TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'old',"
     " branch TEXT NOT NULL DEFAULT '', sha TEXT NOT NULL DEFAULT '', base_sha TEXT NOT NULL DEFAULT '',"
     " ts REAL NOT NULL DEFAULT 0, by TEXT NOT NULL DEFAULT '')"),
    "CREATE INDEX IF NOT EXISTS tk_feature_rev_f ON tk_feature_rev (feature_id, state)",
    # agent work claims (``claims.py``): any pm project, imported or not. ``paths`` = JSON list of repo-relative
    # globs ([] = whole repo); status: active | done | dropped (stale is computed from tmux, never stored)
    ("CREATE TABLE IF NOT EXISTS tk_claim ("
     " id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, num INTEGER NOT NULL,"
     " agent TEXT NOT NULL DEFAULT '', pane TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',"
     " branch TEXT NOT NULL DEFAULT '', worktree TEXT NOT NULL DEFAULT '', paths TEXT NOT NULL DEFAULT '[]',"
     " issue_num INTEGER, status TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL DEFAULT 0,"
     " updated_at REAL NOT NULL DEFAULT 0, closed_at REAL NOT NULL DEFAULT 0,"
     " commit_sha TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', UNIQUE (project, num))"),
    "CREATE INDEX IF NOT EXISTS tk_claim_status ON tk_claim (project, status)",
)


# columns added after a table was first created: (table, column, DDL); old DBs get them on connect.
# tk_file.last_hash: sha256 of the text last rendered into (or imported from) the main work tree file; a
# main file with another hash was edited by hand and is re-imported before the next change (mdimport.absorb)
ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (("tk_file", "last_hash", "TEXT NOT NULL DEFAULT ''"),)


def _migrate(con: sqlite3.Connection) -> None:
    for table, col, ddl in ADDED_COLUMNS:
        if col not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
            try:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass  # another process added it first


def connect(settings: Settings) -> sqlite3.Connection:
    """pm.db in autocommit mode (transactions are explicit, see ``tx``), rows as ``sqlite3.Row``."""
    con = db.connect(settings, SCHEMA, _migrate)
    con.isolation_level = None
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 5000")
    return con


@contextmanager
def tx(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """``BEGIN IMMEDIATE`` ... ``COMMIT`` (write lock up front: num/pos allocation never races);
    any exception rolls back and propagates."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def check_file(file: str) -> str:
    if file not in FILES:
        raise Invalid(f"file must be one of {', '.join(FILES)}")
    return file


def file_name(file: str) -> str:
    """The file name of any tracker file (item or doc file)."""
    if file not in ALL_FILES:
        raise Invalid(f"file must be one of {', '.join(ALL_FILES)}")
    return ALL_FILES[file]


def max_bytes(file: str) -> int:
    """Largest render pm writes for ``file``."""
    return DOC_MAX_BYTES if file in DOC_FILES else backlog.MAX_BYTES


def imported(con: sqlite3.Connection, project: str, file: str) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM tk_file WHERE project=? AND file=?", (project, file)).fetchone()


ITEM_SELECT = ("SELECT i.*, (SELECT COUNT(*) FROM tk_attach a WHERE a.item_id = i.id) AS n_img,"
               " (SELECT COUNT(*) FROM tk_note n WHERE n.item_id = i.id) AS n_notes FROM tk_item i")


def ordered(con: sqlite3.Connection, project: str, file: str) -> list[tuple[str, sqlite3.Row]]:
    """Lines and items of one file in render order: ``("line", row)`` / ``("item", row)``."""
    lines = con.execute("SELECT id, pos, text FROM tk_line WHERE project=? AND file=?", (project, file))
    items = con.execute(ITEM_SELECT + " WHERE i.project=? AND i.file=?", (project, file))
    rows = [("line", r) for r in lines] + [("item", r) for r in items]
    rows.sort(key=lambda kr: (kr[1]["pos"], kr[0] == "item", kr[1]["id"]))
    return rows
