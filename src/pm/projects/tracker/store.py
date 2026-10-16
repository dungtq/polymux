"""Tracker items (BACKLOG.md issues, TODO.md tasks) in pm.db: add, done/reopen, edit, delete, read.

Every mutator runs in one ``BEGIN IMMEDIATE`` transaction, calls ``audit(action, detail)`` before the
commit (a raising audit rolls back: fail closed), commits, then re-renders the file into the project's
main work tree. All of it holds the main file's flock and first takes in a hand edit of that file
(``mdimport.sync``). Only imported (project, file) pairs can be changed.

A new item goes where ``backlog.add_item`` would put it: at the end of its section (``## Inbox`` for
BACKLOG.md, created at the end when missing; for TODO.md the ``section`` heading, or the last section),
with the same blank-line rules, and gets `` (YYYY-MM-DD)`` appended to its title.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from pm.core import timefmt
from pm.core.config import Settings
from pm.core.errors import Invalid, NotFound
from pm.projects import backlog, projects
from pm.projects.tracker import mdgen, mdimport, schema

Audit = Callable[[str, dict], None] | None

MAX_ITEM = backlog.MAX_ITEM
MAX_BODY = 64 * 1024
MAX_SECTION = 200
MAX_LABELS = 10
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
_COMMIT = re.compile(r"^[0-9a-f]{4,40}$")
_POS_EPS = 1e-6
_DATE_SUFFIX = re.compile(r"\s+\(\d{4}-\d\d-\d\d\)$")


def short_title(title: str) -> str:
    """The title for display: without the trailing `` (YYYY-MM-DD)`` a new item gets (the stored title and
    the rendered file keep it)."""
    return _DATE_SUFFIX.sub("", title) or title


@dataclass
class Item:
    id: int
    project: str
    file: str
    num: int
    pos: float
    title: str
    body_md: str
    status: str  # "open" | "done"
    indent: int
    prefix: str
    labels: tuple[str, ...]
    created_at: float
    created_by: str
    updated_at: float
    closed_at: float
    closed_by: str
    commit: str
    n_img: int = 0
    n_notes: int = 0

    @property
    def done(self) -> bool:
        return self.status == "done"

    @property
    def title_short(self) -> str:
        return short_title(self.title)

    @property
    def has_more(self) -> bool:
        return bool(self.body_md.strip()) or bool(self.n_img) or bool(self.n_notes)

    def to_dict(self) -> dict:
        return {**asdict(self), "labels": list(self.labels), "done": self.done, "has_more": self.has_more}


def from_row(r: sqlite3.Row) -> Item:
    return Item(id=r["id"], project=r["project"], file=r["file"], num=r["num"], pos=r["pos"], title=r["title"],
                body_md=r["body_md"], status=r["status"], indent=r["indent"], prefix=r["prefix"],
                labels=tuple(x for x in r["labels"].split(",") if x), created_at=r["created_at"],
                created_by=r["created_by"], updated_at=r["updated_at"], closed_at=r["closed_at"],
                closed_by=r["closed_by"], commit=r["commit_sha"], n_img=r["n_img"], n_notes=r["n_notes"])


# ------------------------------------------------------------------ validation
def clean_title(title: str) -> str:
    """One line, whitespace collapsed, not empty, at most MAX_ITEM chars."""
    t = " ".join((title or "").split())
    if not t:
        raise Invalid("empty title")
    if len(t) > MAX_ITEM:
        raise Invalid(f"title too long (max {MAX_ITEM} chars)")
    return t


def clean_body(body: str) -> str:
    b = (body or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if len(b.encode()) > MAX_BODY:
        raise Invalid(f"body too long (max {MAX_BODY // 1024} KB)")
    return b


def clean_labels(labels: Sequence[str]) -> str:
    out: list[str] = []
    for lb in labels or ():
        lb = (lb or "").strip()
        if not _LABEL.match(lb):
            raise Invalid("label: letters, digits, . _ - (max 32)")
        if lb not in out:
            out.append(lb)
    if len(out) > MAX_LABELS:
        raise Invalid(f"at most {MAX_LABELS} labels")
    return ",".join(out)


def text_detail(key: str, text: str) -> dict:
    """Audit detail for user text: length + hash only."""
    from pm.security.audit import message_detail  # local: pm.security's package init loads starlette

    return {f"{key}_{k}": v for k, v in message_detail(text).items()}


# ------------------------------------------------------------------ transaction + render
def require(con: sqlite3.Connection, project: str, file: str) -> None:
    if schema.imported(con, project, file) is None:
        raise Invalid(f"{project}: {schema.file_name(file)} is not in the tracker (run `pmctl import` first)")


def run(settings: Settings, project: str, file: str, fn: Callable[[sqlite3.Connection], tuple],
        *, audit: Audit = None, action: str = "", sync: Callable | None = None):
    """``fn(con) -> (result, audit detail)`` inside one write transaction; audit before commit; then
    re-render the file into the main tree (all under the main file's lock, after absorbing a hand edit of
    it: ``sync(change)``, default ``mdimport.sync``; the doc files pass their own). Returns ``result``."""
    def change():
        con = schema.connect(settings)
        try:
            with schema.tx(con):
                require(con, project, file)
                result, detail = fn(con)
                if audit is not None:
                    audit(action, {"project": project, "file": file, **detail})
        finally:
            con.close()
        return result
    if sync is None:
        return mdimport.sync(settings, project, file, change)[0]
    return sync(change)[0]


def fetch(con: sqlite3.Connection, project: str, file: str, num: int) -> sqlite3.Row:
    r = con.execute(schema.ITEM_SELECT + " WHERE i.project=? AND i.file=? AND i.num=?",
                    (project, file, int(num))).fetchone()
    if r is None:
        raise NotFound(f"{project}: no {'issue' if file == 'backlog' else 'TODO item'} #{num}")
    return r


# ------------------------------------------------------------------ placement
def _texts(rows: list[tuple[str, sqlite3.Row]]) -> list[str]:
    return [mdgen.item_line(r) if k == "item" else r["text"] for k, r in rows]


def _heading_text(line: str) -> str | None:
    h = projects.HEADING.match(line)
    return h.group(1).strip() if h else None


def _find_section(texts: list[str], file: str, section: str | None) -> int | None:
    if section is None:
        if file == "backlog":
            return next((i for i, t in enumerate(texts) if backlog.INBOX.match(t)), None)
        heads = [i for i, t in enumerate(texts) if backlog.HEAD.match(t)]
        return heads[-1] if heads else None
    want = section.casefold()
    return next((i for i, t in enumerate(texts)
                 if backlog.HEAD.match(t) and (_heading_text(t) or "").casefold() == want), None)


def plan_insert(texts: list[str], file: str, section: str | None) -> tuple[int, list[str | None]]:
    """Where a new item goes: (index, new rows), a row is a text line or None for the item itself.
    Same result as ``backlog.add_item`` on the rendered text."""
    start = _find_section(texts, file, section)
    if start is None:
        if file == "todo" and section is None:  # no heading at all: after the last non-blank line
            at = len(texts)
            while at > 0 and not texts[at - 1].strip():
                at -= 1
            return at, [None]
        head = [""] if texts and texts[-1].strip() else []
        return len(texts), [*head, f"## {section or 'Inbox'}", "", None]
    end = start + 1
    while end < len(texts) and not backlog.HEAD.match(texts[end]):
        end += 1
    at = end
    while at > start + 1 and not texts[at - 1].strip():
        at -= 1
    new: list[str | None] = [""] if at == start + 1 else []
    new.append(None)
    if at < len(texts) and texts[at].strip():
        new.append("")
    return at, new


def _renumber(con: sqlite3.Connection, rows: list[tuple[str, sqlite3.Row]]) -> None:
    for i, (k, r) in enumerate(rows):
        con.execute(f"UPDATE {'tk_item' if k == 'item' else 'tk_line'} SET pos=? WHERE id=?", (float(i + 1), r["id"]))


def positions(con: sqlite3.Connection, project: str, file: str, at: int, k: int) -> list[float]:
    """``k`` free positions between row ``at - 1`` and row ``at`` (renumbers the file when too tight)."""
    for _ in (0, 1):
        rows = schema.ordered(con, project, file)
        lo = rows[at - 1][1]["pos"] if at > 0 else 0.0
        hi = rows[at][1]["pos"] if at < len(rows) else lo + k + 1
        step = (hi - lo) / (k + 1)
        if step >= _POS_EPS:
            return [lo + step * (i + 1) for i in range(k)]
        _renumber(con, rows)
    raise AssertionError("unreachable")


def insert_item(con: sqlite3.Connection, project: str, file: str, title: str, *, body: str = "",
                section: str | None = None, actor: str = "user", labels: Sequence[str] = (),
                stamp: bool = True) -> int:
    """Insert a new open item (caller holds the write transaction); its id. ``stamp`` appends today's
    display-timezone date to the title (off for items taken from a merged branch: their title is kept as it was)."""
    title = clean_title(title) + (f" ({timefmt.today().isoformat()})" if stamp else "")
    body, lab = clean_body(body), clean_labels(labels)
    if section is not None:
        section = " ".join(section.split())[:MAX_SECTION] or None
    at, new = plan_insert(_texts(schema.ordered(con, project, file)), file, section)
    pos = positions(con, project, file, at, len(new))
    num = int(con.execute("SELECT MAX(f.next_num, (SELECT COALESCE(MAX(num), 0) + 1 FROM tk_item"
                          " WHERE project=f.project AND file=f.file)) FROM tk_file f WHERE project=? AND file=?",
                          (project, file)).fetchone()[0])
    now, iid = time.time(), 0
    for p, text in zip(pos, new):
        if text is not None:
            con.execute("INSERT INTO tk_line (project, file, pos, text) VALUES (?,?,?,?)", (project, file, p, text))
            continue
        iid = int(con.execute(
            "INSERT INTO tk_item (project, file, num, pos, title, body_md, status, prefix, gap, labels,"
            " created_at, created_by, updated_at) VALUES (?,?,?,?,?,?,'open','- ',' ',?,?,?,?)",
            (project, file, num, p, title, body, lab, now, actor, now)).lastrowid or 0)
    con.execute("UPDATE tk_file SET eol=1, next_num=? WHERE project=? AND file=?", (num + 1, project, file))
    return iid


def _by_id(con: sqlite3.Connection, iid: int) -> Item:
    return from_row(con.execute(schema.ITEM_SELECT + " WHERE i.id=?", (iid,)).fetchone())


# ------------------------------------------------------------------ public API
def add_item(settings: Settings, project: str, file: str, title: str, *, body: str = "",
             section: str | None = None, actor: str = "user", audit: Audit = None,
             labels: Sequence[str] = ()) -> Item:
    def fn(con):
        it = _by_id(con, insert_item(con, project, file, title, body=body, section=section, actor=actor,
                                     labels=labels))
        detail = {"num": it.num} if audit is None else {"num": it.num, **text_detail("title", it.title)}
        return it, detail
    return run(settings, project, file, fn, audit=audit, action="tracker_add")


def set_done(settings: Settings, project: str, file: str, num: int, done: bool, *, actor: str = "user",
             commit: str | None = None, audit: Audit = None) -> Item:
    if commit is not None and not _COMMIT.match(commit):
        raise Invalid("commit: 4-40 lowercase hex chars")

    def fn(con):
        r = fetch(con, project, file, num)
        return _set_status(con, r, done, actor, commit), {"num": int(num)}
    return run(settings, project, file, fn, audit=audit, action="tracker_done" if done else "tracker_reopen")


def toggle(settings: Settings, project: str, file: str, num: int, *, actor: str = "user",
           audit: Audit = None) -> Item:
    """The web's checkbox: done <-> open, read and flipped in one transaction."""
    def fn(con):
        r = fetch(con, project, file, num)
        it = _set_status(con, r, r["status"] != "done", actor, None)
        return it, {"num": int(num), "done": int(it.done)}
    return run(settings, project, file, fn, audit=audit, action="tracker_toggle")


def merge_branch(settings: Settings, project: str, file: str, added: Sequence[tuple[str, str | None, bool]],
                 ticked: Sequence[tuple[int | None, str]], *, actor: str = "user", audit: Audit = None) -> dict:
    """Take what a merged branch did to the file (merge driver): ``added`` = (title, section heading or
    None, done) of items the branch added, each inserted (title kept, in that section when the DB file has
    it, else the default one) unless an item with that title exists; ``ticked`` = ([#N] or None, title) of
    items the branch ticked, set done (matched by number, else title) when open. Returns the counts."""
    def fn(con):
        n_add = n_done = 0
        have = {r["title"] for r in con.execute("SELECT title FROM tk_item WHERE project=? AND file=?",
                                                (project, file))}
        for title, section, done in added:
            try:
                t = clean_title(title)
            except Invalid:
                continue
            if t in have:
                continue
            sec = section if section and _find_section(_texts(schema.ordered(con, project, file)), file,
                                                       section) is not None else None
            iid = insert_item(con, project, file, t, section=sec, actor=actor, stamp=False)
            have.add(t)
            n_add += 1
            if done:
                _set_status(con, con.execute(schema.ITEM_SELECT + " WHERE i.id=?", (iid,)).fetchone(), True,
                            actor, None)
        for ref, title in ticked:
            r = None
            if ref is not None:
                r = con.execute(schema.ITEM_SELECT + " WHERE i.project=? AND i.file=? AND i.num=?",
                                (project, file, ref)).fetchone()
            if r is None:
                r = con.execute(schema.ITEM_SELECT + " WHERE i.project=? AND i.file=? AND i.title=?"
                                " ORDER BY i.pos, i.id", (project, file, title)).fetchone()
            if r is not None and r["status"] != "done":
                _set_status(con, r, True, actor, None)
                n_done += 1
        counts = {"added": n_add, "done": n_done}
        return counts, counts
    return run(settings, project, file, fn, audit=audit, action="tracker_merge")


def _set_status(con: sqlite3.Connection, r: sqlite3.Row, done: bool, actor: str, commit: str | None) -> Item:
    now = time.time()
    if done:
        con.execute("UPDATE tk_item SET status='done', closed_at=?, closed_by=?, commit_sha=?, updated_at=?"
                    " WHERE id=?", (now, actor, commit or r["commit_sha"], now, r["id"]))
    else:
        con.execute("UPDATE tk_item SET status='open', closed_at=0, closed_by='', commit_sha='', updated_at=?"
                    " WHERE id=?", (now, r["id"]))
    return _by_id(con, r["id"])


def edit_item(settings: Settings, project: str, file: str, num: int, *, title: str | None = None,
              body: str | None = None, actor: str = "user", audit: Audit = None) -> Item:
    t = clean_title(title) if title is not None else None
    b = clean_body(body) if body is not None else None

    def fn(con):
        r = fetch(con, project, file, num)
        con.execute("UPDATE tk_item SET title=COALESCE(?, title), body_md=COALESCE(?, body_md), updated_at=?"
                    " WHERE id=?", (t, b, time.time(), r["id"]))
        detail: dict = {"num": int(num), "by": actor[:64]}
        if audit is not None:
            detail |= (text_detail("title", t) if t is not None else {}) | (
                text_detail("body", b) if b is not None else {})
        return _by_id(con, r["id"]), detail
    return run(settings, project, file, fn, audit=audit, action="tracker_edit")


def delete_item(settings: Settings, project: str, file: str, num: int, *, actor: str = "user",
                audit: Audit = None) -> None:
    def fn(con):
        r = fetch(con, project, file, num)
        names = [f"{a['sha']}.{a['ext']}" for a in con.execute("SELECT sha, ext FROM tk_attach WHERE item_id=?",
                                                              (r["id"],))]
        for table, col in (("tk_note", "item_id"), ("tk_attach", "item_id"), ("tk_item", "id")):
            con.execute(f"DELETE FROM {table} WHERE {col}=?", (r["id"],))
        return names, {"num": int(num), "by": actor[:64]}
    names = run(settings, project, file, fn, audit=audit, action="tracker_delete")
    drop_unused_images(settings, names)


def move_item(settings: Settings, src: str, dst: str, num: int, *, actor: str = "user",
              audit: Audit = None) -> Item:
    """Move issue ``num`` of ``src`` to ``dst`` (BACKLOG.md of both in the tracker): it gets the next number
    of ``dst`` (in its Inbox), keeps title, body, labels, status, created/closed stamps; its notes and
    images move with it and a note records where it came from. One transaction; both files' flocks are
    held (taken in name order, so two opposite moves never deadlock) and both files are re-rendered."""
    file = "backlog"
    if src == dst:
        raise Invalid("the issue is already in that project")

    def change() -> Item:
        con = schema.connect(settings)
        try:
            with schema.tx(con):
                require(con, src, file)
                require(con, dst, file)
                r = fetch(con, src, file, num)
                iid = insert_item(con, dst, file, "moved", stamp=False)  # the row's own fields follow, as stored
                con.execute("UPDATE tk_item SET title=?, body_md=?, labels=?, status=?, mark=?, created_at=?,"
                            " created_by=?, updated_at=?, closed_at=?, closed_by=?, commit_sha=? WHERE id=?",
                            (r["title"], r["body_md"], r["labels"], r["status"], r["mark"], r["created_at"],
                             r["created_by"], time.time(), r["closed_at"], r["closed_by"], r["commit_sha"], iid))
                for table in ("tk_note", "tk_attach"):
                    con.execute(f"UPDATE {table} SET item_id=? WHERE item_id=?", (iid, r["id"]))
                con.execute("INSERT INTO tk_note (item_id, author, body_md, ts) VALUES (?,?,?,?)",
                            (iid, actor, f"Moved from {src} #{int(num)}.", time.time()))
                con.execute("DELETE FROM tk_item WHERE id=?", (r["id"],))
                it = _by_id(con, iid)
                if audit is not None:
                    audit("tracker_move", {"project": src, "file": file, "num": int(num), "to": dst,
                                           "new_num": it.num, "by": actor[:64]})
        finally:
            con.close()
        return it

    first, second = sorted((src, dst))
    return mdimport.sync(settings, first, file, lambda: mdimport.sync(settings, second, file, change)[0])[0]


def drop_unused_images(settings: Settings, names: Sequence[str]) -> None:
    """Remove image files no attachment row points at any more."""
    if not names:
        return
    con = schema.connect(settings)
    try:
        for name in names:
            sha, _, ext = name.partition(".")
            if con.execute("SELECT 1 FROM tk_attach WHERE sha=? AND ext=? LIMIT 1", (sha, ext)).fetchone() is None:
                Path(settings.tracker_dir, name).unlink(missing_ok=True)
    except OSError:
        pass
    finally:
        con.close()


def get_item(settings: Settings, project: str, file: str, num: int) -> Item:
    con = schema.connect(settings)
    try:
        return from_row(fetch(con, project, schema.check_file(file), num))
    finally:
        con.close()


def list_items(settings: Settings, project: str, file: str, status: str | None = None) -> list[Item]:
    """Items in file order; ``status`` "open" / "done" filters."""
    if status not in (None, "open", "done"):
        raise Invalid("status must be open or done")
    con = schema.connect(settings)
    try:
        q = schema.ITEM_SELECT + " WHERE i.project=? AND i.file=?"
        args: list = [project, schema.check_file(file)]
        if status:
            q += " AND i.status=?"
            args.append(status)
        return [from_row(r) for r in con.execute(q + " ORDER BY i.pos, i.id", args)]
    finally:
        con.close()
