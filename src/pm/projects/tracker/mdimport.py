"""Import BACKLOG.md / TODO.md into pm.db so that ``mdgen.render`` gives the same bytes back.

Item lines use the file-mode grammar: ``backlog.ITEM`` for BACKLOG.md, ``projects.ITEM`` for TODO.md
(indented sub-items included); every other line is a ``tk_line``. A trailing `` [#N]`` on an item is its
number. ``num`` follows file order (1, 2, ...), skipping numbers taken by `` [#N]`` suffixes.

``replace=True`` re-imports on purpose: an item keeps its row (number, body, images, notes) when its
`` [#N]`` matches, else when its title matches an existing item's title; the rest of the old items are
dropped.

``absorb`` (hand-edit safety, run by ``sync`` before every change): when the main work tree file's hash
is not the one pm last rendered (``tk_file.last_hash``) and the file differs from the render, someone
edited it by hand; it is re-imported with the ``replace`` rules before the change goes on, except that an
item with a body, notes or images is never dropped (kept at its old place, with a warning).
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from pm.core import filelock, timefmt
from pm.core.config import Settings
from pm.core.errors import Conflict
from pm.projects import backlog, projects
from pm.projects.tracker import mdgen, schema

Audit = Callable[[str, dict], None] | None

log = logging.getLogger("pm.tracker")
HAND_EDIT = "hand-edit"  # tk_file.imported_by of an absorbed hand edit

_REF = re.compile(r"^(.*\S) \[#(\d{1,9})\]$")
_SPLIT = re.compile(r"(\s*)(.*?)(\s*)", re.S)
_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


@dataclass
class ParsedItem:
    pos: float
    prefix: str
    mark: str
    gap: str
    title: str
    tail: str
    ref: int | None = None
    num: int = 0
    id: int = 0


@dataclass
class Parsed:
    eol: bool
    lines: list[tuple[float, str]] = field(default_factory=list)
    items: list[ParsedItem] = field(default_factory=list)


def _grammar(file: str) -> tuple[re.Pattern, int]:
    """(item regex, group number of the done mark)."""
    return (backlog.ITEM, 2) if schema.check_file(file) == "backlog" else (projects.ITEM, 1)


def split_item(line: str, m: re.Match, mark_group: int) -> ParsedItem:
    at = m.start(mark_group)
    gap, title, tail = _SPLIT.fullmatch(line[at + 2:]).groups()
    item = ParsedItem(0.0, line[:at - 1], line[at], gap, title, tail)
    r = _REF.match(title)
    if r:
        item.title, item.ref = r.group(1), int(r.group(2))
    return item


def parse(text: str, file: str) -> Parsed:
    """Pure: the rows of ``text`` with positions 1, 2, ... in file order."""
    pat, g = _grammar(file)
    eol = text.endswith("\n")
    body = text[:-1] if eol else text
    out = Parsed(eol)
    if not text:
        return out
    for i, ln in enumerate(body.split("\n")):
        m = pat.match(ln)
        if m:
            it = split_item(ln, m, g)
            it.pos = float(i + 1)
            out.items.append(it)
        else:
            out.lines.append((float(i + 1), ln))
    return out


def _date_ts(title: str, now: float) -> float:
    """Created time of an imported item: its last ``YYYY-MM-DD`` (midnight in the display timezone), else now."""
    hits = _DATE.findall(title)
    if hits:
        try:
            return timefmt.day_start_epoch(date(*map(int, hits[-1])))
        except ValueError:
            pass
    return now


def title_date(title: str) -> float:
    """Display-timezone midnight of the last ``YYYY-MM-DD`` in ``title``; 0 when it has none."""
    return _date_ts(title, 0.0)


def _assign(con: sqlite3.Connection, project: str, file: str, parsed: Parsed, floor: int) -> list[int]:
    """Set ``num`` / ``id`` (existing row to keep) on every parsed item; the old item ids that are dropped.
    New numbers start at ``floor`` (1 on a first import, the file's ``next_num`` on a re-import)."""
    old = {r["num"]: (r["id"], r["title"]) for r in con.execute(
        "SELECT id, num, title FROM tk_item WHERE project=? AND file=? ORDER BY pos, id", (project, file))}
    free = dict(old)
    taken: set[int] = set()
    for it in parsed.items:  # pass 1: [#N] suffixes
        if it.ref is not None and it.ref not in taken:
            it.num = it.ref
            taken.add(it.ref)
            hit = free.pop(it.ref, None)
            it.id = hit[0] if hit else 0
    by_title: dict[str, list[int]] = {}
    for num, (_id, title) in free.items():
        by_title.setdefault(title, []).append(num)
    nxt = max([floor, *(n + 1 for n in old)])
    for it in parsed.items:  # pass 2: same title, then a new number
        if it.num:
            continue
        cands = [n for n in by_title.get(it.title, []) if n in free and n not in taken]
        if cands:
            it.num, it.id = cands[0], free.pop(cands[0])[0]
        else:
            while nxt in taken:
                nxt += 1
            it.num, nxt = nxt, nxt + 1
        taken.add(it.num)
    return [i for i, _t in free.values()]


def _store(con: sqlite3.Connection, project: str, file: str, parsed: Parsed, actor: str, now: float,
           *, keep_more: bool = False) -> tuple[int, list[int]]:
    """Write the parsed rows (inside the caller's transaction); (number of dropped items, numbers of the
    items kept although missing from the text). ``keep_more`` (absorb) keeps a missing item that has a
    body, notes or images (at its old position) instead of dropping it."""
    prev = schema.imported(con, project, file)
    dropped = _assign(con, project, file, parsed, prev["next_num"] if prev else 1)
    kept: list[tuple[int, int]] = []  # (id, num)
    if keep_more and dropped:
        marks = ",".join("?" * len(dropped))
        kept = [(r["id"], r["num"]) for r in con.execute(
            schema.ITEM_SELECT + f" WHERE i.id IN ({marks})", dropped) if mdgen.has_more(r)]
        dropped = [i for i in dropped if i not in {k for k, _n in kept}]
    con.execute("DELETE FROM tk_line WHERE project=? AND file=?", (project, file))
    for iid in dropped:
        con.execute("DELETE FROM tk_note WHERE item_id=?", (iid,))
        con.execute("DELETE FROM tk_attach WHERE item_id=?", (iid,))
        con.execute("DELETE FROM tk_item WHERE id=?", (iid,))
    con.execute("UPDATE tk_item SET num = -id WHERE project=? AND file=?", (project, file))  # free UNIQUE nums
    con.executemany("INSERT INTO tk_line (project, file, pos, text) VALUES (?,?,?,?)",
                    [(project, file, pos, text) for pos, text in parsed.lines])
    for it in parsed.items:
        status = "open" if it.mark == " " else "done"
        mark = it.mark if status == "done" else "x"
        indent = len(it.prefix) - len(it.prefix.lstrip())
        if it.id:
            con.execute(
                "UPDATE tk_item SET num=?, pos=?, title=?, status=?, indent=?, prefix=?, gap=?, tail=?, mark=?,"
                " updated_at=?, closed_at=CASE WHEN ?='done' THEN closed_at ELSE 0 END WHERE id=?",
                (it.num, it.pos, it.title, status, indent, it.prefix, it.gap, it.tail, mark, now, status, it.id))
        else:
            con.execute(
                "INSERT INTO tk_item (project, file, num, pos, title, status, indent, prefix, gap, tail, mark,"
                " created_at, created_by, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (project, file, it.num, it.pos, it.title, status, indent, it.prefix, it.gap, it.tail, mark,
                 _date_ts(it.title, now), "", now))  # imported: author unknown (tk_file.imported_by)
    con.executemany("UPDATE tk_item SET num=? WHERE id=?", [(n, i) for i, n in kept])
    nxt = max([prev["next_num"] if prev else 1, *(it.num + 1 for it in parsed.items), *(n + 1 for _i, n in kept)])
    con.execute("INSERT OR REPLACE INTO tk_file (project, file, eol, imported_at, imported_by, next_num)"
                " VALUES (?,?,?,?,?,?)", (project, file, int(parsed.eol), now, actor, nxt))
    return len(dropped), [n for _i, n in kept]


def import_text(settings: Settings, project: str, file: str, text: str, *, replace: bool = False,
                actor: str = "user", audit: Audit = None) -> dict:
    """Import ``text`` as (project, file). Refuses an already imported file unless ``replace``.
    Returns {"project", "file", "items", "lines", "dropped"}."""
    parsed = parse(text, file)

    def change() -> dict:
        con = schema.connect(settings)
        try:
            with schema.tx(con):
                if schema.imported(con, project, file) is not None and not replace:
                    raise Conflict(f"{project}: {schema.FILES[file]} is already imported (use --replace)")
                dropped, _kept = _store(con, project, file, parsed, actor, time.time())
                out = {"project": project, "file": file, "items": len(parsed.items), "lines": len(parsed.lines),
                       "dropped": dropped}
                if audit is not None:
                    audit("tracker_import", {**out, "replace": int(replace), "len": len(text.encode())})
        finally:
            con.close()
        return out
    return mdgen.sync_main(settings, project, file, change)[0]


_CONFLICT = re.compile(r"^(<<<<<<<|>>>>>>>) ", re.M)


def has_conflict(text: str) -> bool:
    """``text`` has git merge conflict markers."""
    return _CONFLICT.search(text) is not None


def absorb(settings: Settings, project: str, file: str) -> bool:
    """Re-import the main work tree file when it was edited outside pm (see the module doc). The caller
    holds the file's flock (``sync``). True when it re-imported. A missing or empty file, or one with
    merge conflict markers, is never taken (the next render overwrites it)."""
    path = mdgen.main_file(settings, project, file)
    if path is None:
        return False
    data = backlog.read(path)
    con = schema.connect(settings)
    try:
        f = schema.imported(con, project, file)
        if f is None or not data or f["last_hash"] == mdgen.text_hash(data):
            return False
        text = data.decode("utf-8", "replace")
        if mdgen.render(con, project, file) == text:  # same text, hash not recorded yet
            con.execute("UPDATE tk_file SET last_hash=? WHERE project=? AND file=?",
                        (mdgen.text_hash(data), project, file))
            return False
        if has_conflict(text):
            log.warning("tracker: %s of %s has merge conflict markers; not taken", schema.FILES[file], project)
            return False
        parsed = parse(text, file)
        with schema.tx(con):
            dropped, kept = _store(con, project, file, parsed, HAND_EDIT, time.time(), keep_more=True)
    finally:
        con.close()
    log.warning("tracker: %s of %s was edited outside pm; re-imported (%d items, %d dropped)",
                schema.FILES[file], project, len(parsed.items), dropped)
    for num in kept:
        log.warning("tracker: %s of %s: #%d (body, notes or images) is missing from the hand edit; kept",
                    schema.FILES[file], project, num)
    return True


def sync(settings: Settings, project: str, file: str, change: Callable[[], object] | None = None,
         ) -> tuple[object, bool]:
    """``change()`` under the main file's flock, after absorbing a hand edit of the main file; then the
    render goes into the main work tree. Returns (change's result, the file now matches the DB)."""
    def pre():
        absorb(settings, project, file)
        return change() if change is not None else None
    return mdgen.sync_main(settings, project, file, pre)


def absorb_locked(settings: Settings, project: str, file: str, fn: Callable[[], bool] | None = None) -> bool:
    """``absorb`` (or the given one: the doc files') under the main file's flock, without writing the file
    (``pmctl render --check``)."""
    path = mdgen.main_file(settings, project, file)
    if path is None:
        return False
    try:
        with filelock.locked(filelock.lock_path(settings.state_dir, path)):
            return fn() if fn is not None else absorb(settings, project, file)
    except filelock.LockTimeout:
        raise Conflict(f"{schema.file_name(file)} is busy (locked by another writer); try again") from None


def import_project(settings: Settings, project: str, root, *, replace: bool = False, actor: str = "user",
                   audit: Audit = None, files: tuple[str, ...] = tuple(schema.FILES)) -> list[dict]:
    """Import the project's files from its work tree ``root``. BACKLOG.md is imported even when missing
    (empty: the issue list starts there); a missing TODO.md stays in file mode."""
    out = []
    for file in files:
        path = mdgen.file_path(root, file)
        data = backlog.read(path)
        if not data and file != "backlog" and not path.exists():
            continue
        out.append(import_text(settings, project, file, data.decode("utf-8", "replace"), replace=replace,
                               actor=actor, audit=audit))
    return out
