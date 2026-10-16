"""CHANGELOG.md in pm.db: parse, render, import, absorb, add / edit / rm,
and what a merged or committed branch adds.

The file is preamble lines (``tk_line``, everything before the first ``## `` line) and entries
(``tk_change``): one ``## `` heading line and its raw body up to the next ``## `` (``###`` sub-headings, bullets,
blank lines: all raw). A render of an import gives the same bytes (CRLF, missing final newline, any UTF-8).
Entries are matched by their exact heading; a heading that occurs more than once pairs by occurrence.

Which entries a render shows is ``drafts.View``: landed ones plus the drafts that view shows. ``sync`` runs a
change under the main file's flock after taking a hand edit of the main file (``absorb``) and landing the
drafts the main tree's HEAD has merged (``promote``), then writes the main tree's render.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pm.core import timefmt
from pm.core.config import Settings
from pm.core.errors import Invalid, NotFound
from pm.projects.tracker import docsync, drafts, mdimport, schema, store

Audit = docsync.Audit

FILE = "changelog"
MAX_TITLE = 200
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_DASH_STYLE = re.compile(r"^## \d{4}-\d{2}-\d{2}( [-\u2013\u2014] )")  # "## 2026-10-12 - Title", also with " \u2013 " / " \u2014 "


@dataclass
class Parsed:
    eol: bool
    pre: list[str] = field(default_factory=list)
    entries: list[tuple[str, str]] = field(default_factory=list)  # (heading line, body: lines + "\n" each)


def parse(text: str) -> Parsed:
    eol = text.endswith("\n")
    out = Parsed(eol)
    raw: list[tuple[str, list[str]]] = []
    for ln in ((text[:-1] if eol else text).split("\n") if text else []):
        if ln.startswith("## "):
            raw.append((ln, []))
        elif raw:
            raw[-1][1].append(ln)
        else:
            out.pre.append(ln)
    out.entries = [(h, "".join(b + "\n" for b in body)) for h, body in raw]
    return out


def body_lines(body: str) -> list[str]:
    return body.split("\n")[:-1] if body else []


def join(lines: list[str], eol: bool) -> str:
    return "\n".join(lines) + ("\n" if eol else "") if lines else ""


def render(con: sqlite3.Connection, project: str, view: drafts.View) -> str | None:
    f = schema.imported(con, project, FILE)
    if f is None:
        return None
    lines = [r["text"] for r in con.execute("SELECT text FROM tk_line WHERE project=? AND file=? ORDER BY pos, id",
                                            (project, FILE))]
    for r in con.execute("SELECT * FROM tk_change WHERE project=? ORDER BY pos, id", (project,)):
        if r["state"] == "landed" or (r["state"] == "draft" and view.shows(r["branch"], r["sha"])):
            lines += [r["heading"], *body_lines(r["body"])]
    return join(lines, bool(f["eol"]))


def render_text(settings: Settings, project: str, root: str | Path | None = None,
                view: drafts.View | None = None) -> str | None:
    """Read-only render for the work tree ``root`` (its branch / HEAD decide the drafts; no root: landed)."""
    view = view or drafts.view_for(settings, project, root)
    con = schema.connect(settings)
    try:
        return render(con, project, view)
    finally:
        con.close()


# ------------------------------------------------------------------ import / absorb / promote
def _day_ts(heading: str, now: float) -> float:
    m = _DAY.search(heading)
    try:
        return timefmt.day_start_epoch(date.fromisoformat(m.group(0))) if m else now
    except ValueError:
        return now


def _match(rows: list[sqlite3.Row], entries: list[tuple[str, str]]) -> list[sqlite3.Row | None]:
    """The row each entry keeps: same heading, k-th occurrence = k-th row (landed rows before drafts)."""
    by: dict[str, list[sqlite3.Row]] = {}
    for r in sorted(rows, key=lambda r: (r["state"] != "landed", r["pos"], r["id"])):
        by.setdefault(r["heading"], []).append(r)
    out: list[sqlite3.Row | None] = []
    for h, _b in entries:
        same = by.get(h)
        out.append(same.pop(0) if same else None)
    return out


def _store(con: sqlite3.Connection, project: str, parsed: Parsed, actor: str, now: float) -> dict:
    """Write ``parsed`` as the landed state (caller's transaction). Matched rows keep their number (a matched
    draft lands); a landed row missing from the text is removed, a draft is kept (at the top)."""
    prev = schema.imported(con, project, FILE)
    rows = list(con.execute("SELECT * FROM tk_change WHERE project=? AND state != 'abandoned'", (project,)))
    keep = _match(rows, parsed.entries)
    used = {r["id"] for r in keep if r is not None}
    dropped = [r["id"] for r in rows if r["id"] not in used and r["state"] == "landed"]
    kept_drafts = sorted((r for r in rows if r["id"] not in used and r["state"] == "draft"),
                         key=lambda r: (r["pos"], r["id"]))
    con.execute("DELETE FROM tk_line WHERE project=? AND file=?", (project, FILE))
    con.executemany("INSERT INTO tk_line (project, file, pos, text) VALUES (?,?,?,?)",
                    [(project, FILE, float(i + 1), t) for i, t in enumerate(parsed.pre)])
    con.executemany("DELETE FROM tk_change WHERE id=?", [(i,) for i in dropped])
    for i, r in enumerate(kept_drafts):
        con.execute("UPDATE tk_change SET pos=? WHERE id=?", (float(i - len(kept_drafts)), r["id"]))
    nxt = max([prev["next_num"] if prev else 1,
               *(n + 1 for (n,) in con.execute("SELECT num FROM tk_change WHERE project=?", (project,)))])
    n = len(parsed.entries)
    for i in range(n - 1, -1, -1):  # bottom up: the oldest new entry gets the lowest number
        (h, body), r = parsed.entries[i], keep[i]
        if r is not None:
            con.execute("UPDATE tk_change SET pos=?, body=?, state='landed' WHERE id=?", (float(i + 1), body, r["id"]))
            continue
        con.execute("INSERT INTO tk_change (project, num, pos, heading, body, state, created_at, created_by)"
                    " VALUES (?,?,?,?,?,'landed',?,'')", (project, nxt, float(i + 1), h, body, _day_ts(h, now)))
        nxt += 1
    con.execute("INSERT OR REPLACE INTO tk_file (project, file, eol, imported_at, imported_by, next_num)"
                " VALUES (?,?,?,?,?,?)", (project, FILE, int(parsed.eol), now, actor, nxt))
    return {"project": project, "file": FILE, "entries": n, "lines": len(parsed.pre), "dropped": len(dropped)}


def import_text(settings: Settings, project: str, text: str, *, replace: bool = False, actor: str = "user",
                audit: Audit = None) -> dict:
    """Import ``text`` as the project's CHANGELOG.md (every entry landed). Refuses an imported file unless
    ``replace``."""
    return docsync.import_text(settings, project, FILE, text, parse(text), _store, replace=replace, actor=actor,
                               audit=audit, sync=lambda change: sync(settings, project, change, absorb_first=False))


def absorb(settings: Settings, project: str, root: str | Path | None, view: drafts.View) -> bool:
    """Take a hand edit of the main tree's CHANGELOG.md (``docsync.hand_edit``). On the default branch it
    becomes the landed state (``_store``); on another branch its new entries become that branch's drafts."""
    con = schema.connect(settings)
    try:
        text = docsync.hand_edit(settings, con, project, FILE, view, render)
        if text is None:
            return False
        if view.main:
            with schema.tx(con):
                _store(con, project, parse(text), mdimport.HAND_EDIT, time.time())
        else:  # the main tree is on another branch: its new entries are that branch's drafts
            new = new_entries(drafts.head_file(root, "CHANGELOG.md"), text)
            with schema.tx(con):
                _take(con, project, new, view, mdimport.HAND_EDIT)
    finally:
        con.close()
    return True


def promote(settings: Settings, project: str, root: str | Path | None, view: drafts.View) -> None:
    """Land the drafts the main tree's HEAD has merged; mark drafts of deleted branches abandoned."""
    con = schema.connect(settings)
    try:
        rows = list(con.execute("SELECT id, branch, sha FROM tk_change WHERE project=? AND state='draft'",
                                (project,)))
        land, gone = drafts.classify(view, root, rows)
        if land or gone:
            with schema.tx(con):
                con.executemany("UPDATE tk_change SET state='landed' WHERE id=?", [(i,) for i in land])
                con.executemany("UPDATE tk_change SET state='abandoned' WHERE id=?", [(i,) for i in gone])
    finally:
        con.close()


def sync(settings: Settings, project: str, change: Callable[[], object] | None = None, *,
         absorb_first: bool = True) -> tuple[object, bool]:
    """``docsync.sync`` for CHANGELOG.md. Returns (change's result, the file now matches the DB)."""
    return docsync.sync(
        settings, project, FILE, change,
        absorb=(lambda root, view: absorb(settings, project, root, view)) if absorb_first else None,
        promote=lambda root, view: promote(settings, project, root, view),
        render_text=lambda root: render_text(settings, project, root))


# ------------------------------------------------------------------ changes
def _run(settings: Settings, project: str, fn: Callable[[sqlite3.Connection], tuple], *, audit: Audit,
         action: str):
    return store.run(settings, project, FILE, fn, audit=audit, action=action,
                     sync=lambda change: sync(settings, project, change))


def clean_title(title: str) -> str:
    t = " ".join((title or "").split())
    if not t:
        raise Invalid("empty title")
    if len(t) > MAX_TITLE:
        raise Invalid(f"title too long (max {MAX_TITLE} chars)")
    return t


def clean_body(body: str, blank_first: bool) -> str:
    """Body lines as given, without leading / trailing blank lines, then one blank line (the gap before the
    next entry); ``blank_first`` (the file's style) adds a blank line under the heading."""
    lines = (body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        raise Invalid("empty body")
    out = ("\n" if blank_first else "") + "".join(ln + "\n" for ln in lines) + "\n"
    if len(out.encode()) > 64 * 1024:
        raise Invalid("body too long (max 64 KB)")
    return out


def _style(con: sqlite3.Connection, project: str) -> tuple[str, bool]:
    """(separator of a dash heading ``## day - title`` or ``## day — title``, "" for ``## day (title)``;
    blank line under the heading) of the top landed entry."""
    r = con.execute("SELECT heading, body FROM tk_change WHERE project=? AND state='landed' ORDER BY pos, id"
                    " LIMIT 1", (project,)).fetchone()
    m = _DASH_STYLE.match(r["heading"]) if r else None
    return (m.group(1) if m else "", bool(r and r["body"].startswith("\n")))


def heading(day: str, title: str, dash: str | bool) -> str:
    if dash is True:
        dash = " - "
    return f"## {day}{dash}{title}" if dash else f"## {day} ({title})"


def _day(day: str | None) -> str:
    if day is None:
        return timefmt.today().isoformat()
    try:
        return date.fromisoformat(day).isoformat()
    except ValueError:
        raise Invalid("date: YYYY-MM-DD") from None


def _insert_top(con: sqlite3.Connection, project: str, head: str, body: str, view: drafts.View, actor: str,
                landed: bool) -> int:
    top = con.execute("SELECT MIN(pos) FROM tk_change WHERE project=?", (project,)).fetchone()[0]
    num = int(con.execute("SELECT MAX(f.next_num, (SELECT COALESCE(MAX(num), 0) + 1 FROM tk_change"
                          " WHERE project=f.project)) FROM tk_file f WHERE project=? AND file=?",
                          (project, FILE)).fetchone()[0])
    con.execute("INSERT INTO tk_change (project, num, pos, heading, body, branch, state, base_sha, created_at,"
                " created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (project, num, (top if top is not None else 1.0) - 1.0, head, body, "" if landed else view.branch,
                 "landed" if landed else "draft", "" if landed else view.head, time.time(), actor))
    con.execute("UPDATE tk_file SET next_num=? WHERE project=? AND file=?", (num + 1, project, FILE))
    return num


def to_dict(r: sqlite3.Row) -> dict:
    return {"num": r["num"], "heading": r["heading"], "title": r["heading"][3:], "body": r["body"],
            "state": r["state"], "branch": r["branch"], "sha": r["sha"], "created_at": r["created_at"],
            "created_by": r["created_by"]}


def _fetch(con: sqlite3.Connection, project: str, num: int) -> sqlite3.Row:
    r = con.execute("SELECT * FROM tk_change WHERE project=? AND num=?", (project, int(num))).fetchone()
    if r is None:
        raise NotFound(f"{project}: no CHANGELOG entry #{num}")
    return r


def add(settings: Settings, project: str, title: str, body: str, *, day: str | None = None,
        here: drafts.View = drafts.MAIN, actor: str = "user", audit: Audit = None) -> dict:
    """New entry at the top: landed when ``here`` (the caller's work tree) is on the default branch, else a
    draft of its branch."""
    t, d = clean_title(title), _day(day)

    def fn(con):
        dash, blank = _style(con, project)
        num = _insert_top(con, project, heading(d, t, dash), clean_body(body, blank), here, actor, here.main)
        return to_dict(_fetch(con, project, num)), {"num": num, "draft": int(not here.main)}
    return _run(settings, project, fn, audit=audit, action="tracker_change_add")


def _mine(r: sqlite3.Row, here: drafts.View) -> None:
    if here.main or (r["state"] == "draft" and r["branch"] == here.branch):
        return
    raise Invalid(f"#{r['num']} is {'landed' if r['state'] == 'landed' else 'a draft of ' + r['branch']}: "
                  "change it on the default branch")


def edit(settings: Settings, project: str, num: int, *, title: str | None = None, body: str | None = None,
         here: drafts.View = drafts.MAIN, actor: str = "user", audit: Audit = None) -> dict:
    """New title (the heading keeps its date and style) and/or body."""
    if title is None and body is None:
        raise Invalid("nothing to change")

    def fn(con):
        r = _fetch(con, project, num)
        _mine(r, here)
        h, b = r["heading"], r["body"]
        if title is not None:
            m = _DAY.search(h)
            sep = _DASH_STYLE.match(h)
            h = heading(m.group(0) if m else _day(None), clean_title(title), sep.group(1) if sep else "")
        if body is not None:
            b = clean_body(body, b.startswith("\n"))
        con.execute("UPDATE tk_change SET heading=?, body=? WHERE id=?", (h, b, r["id"]))
        return to_dict(_fetch(con, project, num)), {"num": int(num), "by": actor[:64]}
    return _run(settings, project, fn, audit=audit, action="tracker_change_edit")


def remove(settings: Settings, project: str, num: int, *, here: drafts.View = drafts.MAIN, actor: str = "user",
           audit: Audit = None) -> None:
    def fn(con):
        r = _fetch(con, project, num)
        _mine(r, here)
        con.execute("DELETE FROM tk_change WHERE id=?", (r["id"],))
        return None, {"num": int(num), "by": actor[:64]}
    _run(settings, project, fn, audit=audit, action="tracker_change_rm")


def entries(settings: Settings, project: str, num: int | None = None) -> list[dict]:
    """All rows in file order (drafts and abandoned ones included), or the one with ``num`` (NotFound)."""
    con = schema.connect(settings)
    try:
        if num is not None:
            return [to_dict(_fetch(con, project, num))]
        return [to_dict(r) for r in con.execute("SELECT * FROM tk_change WHERE project=? ORDER BY pos, id",
                                                (project,))]
    finally:
        con.close()


# ------------------------------------------------------------------ branches: merge driver, worktree commits
def new_entries(base: str, theirs: str) -> list[tuple[str, str]]:
    """Entries of ``theirs`` whose heading is not in ``base`` (a repeated heading counts by occurrence)."""
    left: dict[str, int] = {}
    for h, _b in parse(base).entries:
        left[h] = left.get(h, 0) + 1
    out = []
    for h, b in parse(theirs).entries:
        if left.get(h, 0):
            left[h] -= 1
        else:
            out.append((h, b))
    return out


def _take(con: sqlite3.Connection, project: str, new: list[tuple[str, str]], view: drafts.View,
          actor: str) -> dict:
    """``new`` entries (top-down) into the DB: on the default branch a draft with the same heading lands, an
    unknown heading is added landed at the top; on another branch an unknown heading becomes its draft."""
    rows = list(con.execute("SELECT * FROM tk_change WHERE project=? AND state != 'abandoned'", (project,)))
    landed = {}
    for r in rows:
        if r["state"] == "landed":
            landed[r["heading"]] = landed.get(r["heading"], 0) + 1
    want: dict[str, int] = {}
    n_land, add = 0, []
    for h, b in new:
        want[h] = want.get(h, 0) + 1
        d = next((r for r in rows if r["heading"] == h and r["state"] == "draft"), None) if view.main else None
        if d is not None:
            con.execute("UPDATE tk_change SET state='landed' WHERE id=?", (d["id"],))
            rows.remove(d)
            n_land += 1
        elif not any(r["heading"] == h for r in rows) or (view.main and want[h] > landed.get(h, 0)):
            add.append((h, b))
            landed[h] = landed.get(h, 0) + 1
    for h, b in reversed(add):
        _insert_top(con, project, h, b, view, actor, view.main)
    return {"landed": n_land, "added": len(add)}


def take(settings: Settings, project: str, base: str, theirs: str, view: drafts.View, *, actor: str = "user",
         audit: Audit = None) -> dict:
    """What a branch (``theirs`` vs ``base``) added, by the ``_take`` rules: the merge driver (``view`` = the
    main tree) and the pre-commit hook in a worktree (``view`` = the worktree's branch)."""
    new = new_entries(base, theirs)
    if not new:
        return {"landed": 0, "added": 0}

    def fn(con):
        counts = _take(con, project, new, view, actor)
        return counts, counts
    return _run(settings, project, fn, audit=audit, action="tracker_change_merge")
