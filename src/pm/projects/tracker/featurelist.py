"""FEATURES.md in pm.db: render, import, absorb, set / rm / list, and what
a merged or committed branch changed. Rows and grammar: ``featurestore``.

A render of an import gives the same bytes. An entry of an area that has no heading line yet (a new area)
gets ``## <Area>`` (with blank lines around it) in front of it. The ``Summary: N features in M areas.``
preamble line is kept as imported until a feature changes after the import (``tk_feature.updated_at`` later
than ``tk_file.imported_at``, or a draft the view shows); from then on it is recomputed (active entries,
areas that have one). ``set`` / ``rm`` from a work tree on another branch than the default one write that
branch's draft revision instead (``drafts``); the main tree shows it once its commit is merged.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

from pm.core import timefmt
from pm.core.config import Settings
from pm.core.errors import Invalid, NotFound
from pm.projects.tracker import docsync, drafts, mdimport, schema, store
from pm.projects.tracker import featurestore as fs

Audit = docsync.Audit
FILE = fs.FILE
SUMMARY = re.compile(r"^Summary: \d+ features in \d+ areas\.$")
AUDITED = re.compile(r"^Last audited: ")
parse = fs.parse


def render(con: sqlite3.Connection, project: str, view: drafts.View) -> str | None:
    f = schema.imported(con, project, FILE)
    if f is None:
        return None
    eff, applied = fs.effective(con, project, view)
    out: list[str] = []
    area: str | None = None
    areas: set[str] = set()
    n = 0
    for kind, r in fs.rows(con, project, removed=True):
        if kind == "line":
            area = fs.area_of(r["text"]) if r["text"].startswith("## ") else area
            out.append(r["text"])
            continue
        text, extra, state = eff[r["id"]]
        if state != "active":
            continue
        if r["area"] != area:  # a new area: its heading
            out += ([""] if out and out[-1].strip() else []) + [f"## {r['area']}", ""]
            area = r["area"]
        out += [fs.entry_line(r["name"], text), *(extra.split("\n")[:-1] if extra else [])]
        areas.add(r["area"])
        n += 1
    newest = con.execute("SELECT MAX(updated_at) FROM tk_feature WHERE project=?", (project,)).fetchone()[0] or 0
    if applied or newest > f["imported_at"]:
        out = [f"Summary: {n} features in {len(areas)} areas." if SUMMARY.match(t) else t for t in out]
    return "\n".join(out) + ("\n" if f["eol"] else "") if out else ""


def render_text(settings: Settings, project: str, root: str | Path | None = None,
                view: drafts.View | None = None) -> str | None:
    """Read-only render for the work tree ``root`` (no root: landed only)."""
    view = view or drafts.view_for(settings, project, root)
    con = schema.connect(settings)
    try:
        return render(con, project, view)
    finally:
        con.close()


def import_text(settings: Settings, project: str, text: str, *, replace: bool = False, actor: str = "user",
                audit: Audit = None) -> dict:
    return docsync.import_text(settings, project, FILE, text, parse(text), fs.store, replace=replace, actor=actor,
                               audit=audit, sync=lambda change: sync(settings, project, change, absorb_first=False))


def absorb(settings: Settings, project: str, root: str | Path | None, view: drafts.View) -> bool:
    """A hand edit of the main FEATURES.md: the landed state on the default branch (``fs.store``), the
    branch's drafts on another branch."""
    con = schema.connect(settings)
    try:
        text = docsync.hand_edit(settings, con, project, FILE, view, render)
        if text is None:
            return False
        if view.main:
            with schema.tx(con):
                fs.store(con, project, parse(text), mdimport.HAND_EDIT, time.time())
        else:
            chs = fs.changes(drafts.head_file(root, "FEATURES.md"), text)
            with schema.tx(con):
                fs.take(con, project, chs, view, mdimport.HAND_EDIT)
    finally:
        con.close()
    return True


def promote(settings: Settings, project: str, root: str | Path | None, view: drafts.View) -> None:
    """Land the draft revisions the main tree's HEAD has merged (oldest first); abandon those of deleted
    branches."""
    con = schema.connect(settings)
    try:
        revs = fs.draft_revs(con, project)
        land, gone = drafts.classify(view, root, revs)
        if land or gone:
            with schema.tx(con):
                for d in revs:
                    if d["id"] in land:
                        fs.land(con, d)
                con.executemany("UPDATE tk_feature_rev SET state='abandoned' WHERE id=?", [(i,) for i in gone])
    finally:
        con.close()


def sync(settings: Settings, project: str, change: Callable[[], object] | None = None, *,
         absorb_first: bool = True) -> tuple[object, bool]:
    return docsync.sync(
        settings, project, FILE, change,
        absorb=(lambda root, view: absorb(settings, project, root, view)) if absorb_first else None,
        promote=lambda root, view: promote(settings, project, root, view),
        render_text=lambda root: render_text(settings, project, root))


def _run(settings: Settings, project: str, fn, *, audit: Audit, action: str):
    return store.run(settings, project, FILE, fn, audit=audit, action=action,
                     sync=lambda change: sync(settings, project, change))


# ------------------------------------------------------------------ changes
def to_dict(r: sqlite3.Row, view: drafts.View | None = None, eff: dict | None = None) -> dict:
    text, extra, state = (eff or {}).get(r["id"], (r["text"], r["extra"], r["state"]))
    return {"area": r["area"], "name": r["name"], "text": text, "extra": extra, "state": state,
            "landed_state": r["state"], "branch": r["branch"], "updated_at": r["updated_at"],
            "updated_by": r["updated_by"]}


def set_feature(settings: Settings, project: str, area: str, name: str, text: str, *,
                here: drafts.View = drafts.MAIN, actor: str = "user", audit: Audit = None) -> dict:
    """Upsert (area, name) with ``text`` (the whole line after ``- **Name** — ``; continuation lines are
    dropped). A new name goes at the end of its area, a new area at the end of the file."""
    a, n = fs.check_key(area, name)
    t = fs.check_text(text)

    def fn(con):
        if here.main:
            fid = fs.set_landed(con, project, a, n, t, actor)
        else:
            fid = fs.set_draft(con, project, a, n, "set", t, here, actor)
        r = con.execute("SELECT * FROM tk_feature WHERE id=?", (fid,)).fetchone()
        return to_dict(r, eff=fs.effective(con, project, here)[0]), {"draft": int(not here.main)}
    return _run(settings, project, fn, audit=audit, action="tracker_feature_set")


def rm_feature(settings: Settings, project: str, area: str, name: str, *, here: drafts.View = drafts.MAIN,
               actor: str = "user", audit: Audit = None) -> None:
    a, n = fs.check_key(area, name)

    def fn(con):
        r = fs.find(con, project, a, n)
        if here.main:
            if r is None or r["state"] != "active":
                raise NotFound(f"{project}: no feature {a} / {n}")
            fs.rm_landed(con, r, actor)
        else:
            fs.set_draft(con, project, a, n, "rm", "", here, actor)
        return None, {"draft": int(not here.main)}
    _run(settings, project, fn, audit=audit, action="tracker_feature_rm")


def set_audited(settings: Settings, project: str, day: str | None = None, *, actor: str = "user",
                audit: Audit = None) -> str:
    """Set the ``Last audited: <day>`` preamble line (the audit skill's)."""
    try:
        d = date.fromisoformat(day).isoformat() if day else timefmt.today().isoformat()
    except ValueError:
        raise Invalid("date: YYYY-MM-DD") from None

    def fn(con):
        r = next((r for k, r in fs.rows(con, project) if k == "line" and AUDITED.match(r["text"])), None)
        if r is None:
            raise NotFound(f"{project}: FEATURES.md has no 'Last audited:' line")
        line = f"Last audited: {d}" + ("\r" if r["text"].endswith("\r") else "")
        con.execute("UPDATE tk_line SET text=? WHERE id=?", (line, r["id"]))
        return line, {"day": d}
    return _run(settings, project, fn, audit=audit, action="tracker_feature_audited")


def features(settings: Settings, project: str, *, area: str | None = None, name: str | None = None,
             here: drafts.View = drafts.MAIN) -> list[dict]:
    """Entries in file order as ``here`` shows them (active only), filtered by area (case-insensitive) and
    exact name (NotFound when a named one is missing)."""
    con = schema.connect(settings)
    try:
        if schema.imported(con, project, FILE) is None:
            raise Invalid(f"{project}: FEATURES.md is not in the tracker (run `pmctl import` first)")
        eff, _ = fs.effective(con, project, here)
        out = [to_dict(r, eff=eff) for k, r in fs.rows(con, project, removed=True)
               if k == "feature" and eff[r["id"]][2] == "active"
               and (area is None or r["area"].casefold() == area.casefold())
               and (name is None or r["name"] == name)]
    finally:
        con.close()
    if name is not None and not out:
        raise NotFound(f"{project}: no feature {area} / {name}")
    return out


def merged_text(settings: Settings, project: str, base: str, theirs: str, view: drafts.View,
                actor: str = "user") -> str | None:
    """The merge driver's result on the default branch (``fs.take`` previewed, pm.db unchanged)."""
    chs = fs.changes(base, theirs)
    return docsync.preview(settings, project, view, lambda con: fs.take(con, project, chs, view, actor), render)


def take(settings: Settings, project: str, base: str, theirs: str, view: drafts.View, *, actor: str = "user",
         audit: Audit = None) -> dict:
    """What a branch changed (``theirs`` vs ``base``), by the ``featurestore.take`` rules."""
    chs = fs.changes(base, theirs)
    if not chs:
        return {"landed": 0, "taken": 0}

    def fn(con):
        counts = fs.take(con, project, chs, view, actor)
        return counts, counts
    return _run(settings, project, fn, audit=audit, action="tracker_feature_merge")
