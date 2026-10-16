"""Render a tracker file (BACKLOG.md / TODO.md) from pm.db: same DB, same bytes.

``sync_main`` runs a DB change and then puts the render into the project's MAIN work tree, both under the
main file's flock (``backlog.mutate``: flock, symlink refusal, atomic write), so pv, editors and the web
see it at once, and records the rendered text's hash in ``tk_file.last_hash``. Errors of the change
propagate; a failed write only logs a warning (the DB is the truth). ``write_main`` = no change.
CHANGELOG.md / FEATURES.md go through the same ``sync_main`` with their own ``render`` callable
(``changelog.py``, ``featurelist.py``; this module never imports them).
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path

from pm.core.config import Settings
from pm.core.errors import PmError
from pm.core.fsutil import safe_join
from pm.projects import backlog
from pm.projects.tracker import schema

log = logging.getLogger("pm.tracker")


def has_more(row) -> bool:
    """The item has a body, images or notes: its line carries `` [#num]``."""
    return bool((row["body_md"] or "").strip()) or bool(row["n_img"]) or bool(row["n_notes"])


def item_line(row) -> str:
    mark = (row["mark"] or "x") if row["status"] == "done" else " "
    ref = f" [#{row['num']}]" if has_more(row) else ""
    return f"{row['prefix']}[{mark}]{row['gap']}{row['title']}{ref}{row['tail']}"


def render_rows(rows: list[tuple[str, sqlite3.Row]], eol: bool) -> str:
    lines = [item_line(r) if k == "item" else r["text"] for k, r in rows]
    if not lines:
        return ""
    return "\n".join(lines) + ("\n" if eol else "")


def render(con: sqlite3.Connection, project: str, file: str) -> str | None:
    """The file text, or None when (project, file) is not imported."""
    f = schema.imported(con, project, file)
    if f is None:
        return None
    return render_rows(schema.ordered(con, project, file), bool(f["eol"]))


def render_text(settings: Settings, project: str, file: str) -> str | None:
    con = schema.connect(settings)
    try:
        return render(con, project, file)
    finally:
        con.close()


def tracked_files(settings: Settings, project: str, files=schema.FILES) -> list[str]:
    """The imported files of ``project`` among ``files`` (default the item files ``backlog`` / ``todo``;
    ``schema.ALL_FILES`` adds ``changelog`` / ``features``), in that order."""
    con = schema.connect(settings)
    try:
        have = {r["file"] for r in con.execute("SELECT file FROM tk_file WHERE project=?", (project,))}
    finally:
        con.close()
    return [f for f in files if f in have]


def project_root(settings: Settings, project: str) -> Path | None:
    path = backlog.registry(settings).get(project)
    return Path(path) if path else None


def file_path(root: str | Path, file: str) -> Path:
    """``<root>/BACKLOG.md`` (``TODO.md``, ``CHANGELOG.md``, ``FEATURES.md``); refuses a symlink or an escape
    (PmError)."""
    return safe_join(root, schema.file_name(file))


def text_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main_file(settings: Settings, project: str, file: str, *, warn: bool = False) -> Path | None:
    """The file in the project's main work tree; None when the project is unknown or the path is refused
    (a symlink or an escape; logged with ``warn``)."""
    root = project_root(settings, project)
    if root is None:
        return None
    try:
        return file_path(root, file)
    except PmError as e:
        if warn:
            _warn(project, file, e)
        return None


def _warn(project: str, file: str, e: Exception) -> None:
    log.warning("tracker: could not write %s of %s: %s", schema.ALL_FILES.get(file, file), project,
                getattr(e, "msg", None) or e.__class__.__name__)


def _record(settings: Settings, project: str, file: str, text: str) -> None:
    con = schema.connect(settings)
    try:
        con.execute("UPDATE tk_file SET last_hash=? WHERE project=? AND file=?",
                    (text_hash(text.encode()), project, file))
    finally:
        con.close()


class _NotImported(Exception):
    pass


class _Same(Exception):
    """The file already has the render: no write (keeps its mtime)."""


def sync_main(settings: Settings, project: str, file: str, change: Callable[[], object] | None = None,
              *, render: Callable[[], str | None] | None = None) -> tuple[object, bool]:
    """Under the main file's flock: ``change()`` (DB work; its errors propagate, a busy lock is a 503
    PmError before it runs), then render (project, file) into the main work tree and record the hash.
    ``render`` gives the text (default ``render_text``; the doc files pass their main-tree view).
    Returns (change's result, the file now matches the DB)."""
    name = schema.ALL_FILES.get(file, file)
    if render is None:
        def render():
            return render_text(settings, project, file)
    box: dict = {}

    def pre():
        box["result"] = change() if change is not None else None

    path = main_file(settings, project, file, warn=True)
    if path is None:
        pre()
        return box["result"], False

    def fn(cur: str) -> str:
        text = render()
        if text is None:
            raise _NotImported()
        box["text"] = text
        if cur == text:
            raise _Same()
        return text

    try:
        try:
            backlog.mutate(settings, path, fn, pre=pre, name=name, max_bytes=schema.max_bytes(file))
        except _Same:
            pass
        _record(settings, project, file, box["text"])
        return box["result"], True
    except _NotImported:
        return box.get("result"), False
    except (backlog.Bad, PmError, OSError, sqlite3.Error) as e:
        if "result" not in box:  # the change did not run (or failed): the caller sees it
            if isinstance(e, backlog.Bad):
                raise PmError(e.msg, status=e.status) from None
            raise
        _warn(project, file, e)
        return box["result"], False


def write_main(settings: Settings, project: str, file: str) -> bool:
    """Render (project, file) into the main work tree. True when the file now matches the DB. Never raises."""
    try:
        return sync_main(settings, project, file)[1]
    except (PmError, OSError, sqlite3.Error) as e:
        _warn(project, file, e)
        return False
