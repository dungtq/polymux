"""The change / sync skeleton CHANGELOG.md and FEATURES.md share (``changelog.py``, ``featurelist.py``).

``sync`` runs under the main file's flock (``mdgen.sync_main``): take a hand edit of the main file
(``absorb``), land the drafts the main tree's HEAD has merged (``promote``), run the change, then write the
main tree's render. Changes run through ``store.run(..., sync=)`` (one audited transaction, fail closed).
``preview`` is the merge driver's result (nothing written); ``import_text`` is the doc files' import;
``hand_edit`` is the hash / render check that decides whether the main file was edited outside pm.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from pm.core.config import Settings
from pm.core.errors import Invalid
from pm.projects import backlog
from pm.projects.tracker import drafts, mdgen, mdimport, schema

Audit = Callable[[str, dict], None] | None
Render = Callable[[sqlite3.Connection, str, drafts.View], str | None]
log = logging.getLogger("pm.tracker")


def hand_edit(settings: Settings, con: sqlite3.Connection, project: str, file: str, view: drafts.View,
              render: Render) -> str | None:
    """The main tree file's text when it was edited outside pm (its hash is not the last render's and it
    differs from the render), else None. An empty file or one with conflict markers is never taken; a file
    equal to the render gets its hash recorded."""
    path = mdgen.main_file(settings, project, file)
    if path is None:
        return None
    data = backlog.read(path, schema.DOC_MAX_BYTES * 4)
    f = schema.imported(con, project, file)
    if f is None or not data or f["last_hash"] == mdgen.text_hash(data):
        return None
    text = data.decode("utf-8", "replace")
    if render(con, project, view) == text:
        con.execute("UPDATE tk_file SET last_hash=? WHERE project=? AND file=?", (mdgen.text_hash(data), project, file))
        return None
    if mdimport.has_conflict(text):
        log.warning("tracker: %s of %s has merge conflict markers; not taken", schema.file_name(file), project)
        return None
    log.warning("tracker: %s of %s was edited outside pm; taken into pm.db", schema.file_name(file), project)
    return text


def sync(settings: Settings, project: str, file: str, change: Callable[[], object] | None, *,
         absorb: Callable[[Path | None, drafts.View], object] | None,
         promote: Callable[[Path | None, drafts.View], object],
         render_text: Callable[[Path | None], str | None]) -> tuple[object, bool]:
    """``change()`` under the main file's flock after ``absorb`` (None: skipped) and ``promote``; then the
    main tree's render goes into the file. Returns (change's result, the file now matches the DB)."""
    root = mdgen.project_root(settings, project)

    def pre():
        view = drafts.view_for(settings, project, root)
        if absorb is not None:
            absorb(root, view)
        promote(root, view)
        return change() if change is not None else None
    return mdgen.sync_main(settings, project, file, pre, render=lambda: render_text(root))


def preview(settings: Settings, project: str, view: drafts.View, take: Callable[[sqlite3.Connection], object],
            render: Render) -> str | None:
    """The render for ``view`` after ``take(con)``, in a transaction that is rolled back: what a merge would
    write, pm.db unchanged (the merge driver: git also runs it for trial merges, ``merge-tree`` and rebases)."""
    con = schema.connect(settings)
    try:
        con.execute("BEGIN")
        try:
            take(con)
            return render(con, project, view)
        finally:
            con.execute("ROLLBACK")
    finally:
        con.close()


def import_text(settings: Settings, project: str, file: str, text: str, parsed: object,
                store_fn: Callable[[sqlite3.Connection, str, object, str, float], dict], *,
                sync: Callable[[Callable[[], object]], tuple[object, bool]], replace: bool = False,
                actor: str = "user", audit: Audit = None) -> dict:
    """Import ``text`` (already ``parsed``) as (project, file): ``store_fn(con, project, parsed, actor,
    now)`` in one audited transaction inside ``sync(change)``. Refuses an imported file unless ``replace``."""
    def change() -> dict:
        con = schema.connect(settings)
        try:
            with schema.tx(con):
                if schema.imported(con, project, file) is not None and not replace:
                    raise Invalid(f"{project}: {schema.file_name(file)} is already imported (use --replace)")
                out = store_fn(con, project, parsed, actor, time.time())
                if audit is not None:
                    audit("tracker_import", {**out, "replace": int(replace), "len": len(text.encode())})
        finally:
            con.close()
        return out
    return sync(change)[0]
