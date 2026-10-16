"""Page data for BACKLOG / TODO boxes: tracker projects from pm.db, the others from their files.

The dicts have exactly the keys of the file-mode functions (``backlog.project_summary``,
``backlog.entry``, ``todo.todo_summary``), so templates need no branch. Tracker item blocks add
``id``, ``num``, ``has_more``, ``n_img``, ``n_notes``, ``created_by``, ``created_at``, ``age_at`` and ``tracker: True``;
their ``line`` is the item number (what the toggle/edit routes post back), ``text`` the stored title and
``html`` / ``title_short`` the title without its `` (YYYY-MM-DD)`` suffix (display only).
"""

from __future__ import annotations

import sqlite3

from pm.core.config import Settings
from pm.core.errors import Conflict, NotFound
from pm.core.markdown_render import render_markdown
from pm.projects import backlog, projects, todo
from pm.projects.backlog import token_of
from pm.projects.tracker import mdgen, mdimport, schema, store

ISSUES_LIMIT = 100  # rows per state (open, done) in a tracker project's Issues tab


def _tracked(settings: Settings) -> set[tuple[str, str]]:
    con = schema.connect(settings)
    try:
        return {(r["project"], r["file"]) for r in con.execute("SELECT project, file FROM tk_file")}
    except sqlite3.Error:
        return set()
    finally:
        con.close()


def is_tracker(settings: Settings, name: str, file: str | None = None) -> bool:
    """``name`` is imported (any file, or the given ``file``)."""
    return any(p == name and (file is None or f == file) for p, f in _tracked(settings))


def require_tracker(settings: Settings, name: str, file: str = "backlog") -> None:
    """``name`` is a known project whose ``file`` is in the tracker: NotFound / Conflict otherwise."""
    if name not in backlog.registry(settings):
        raise NotFound("unknown project")
    if not is_tracker(settings, name, file):
        raise Conflict(f"{name} does not use the issue tracker")


def _load(settings: Settings, name: str, file: str) -> tuple[list[tuple[str, sqlite3.Row]], str]:
    """(rows in render order, rendered text)."""
    con = schema.connect(settings)
    try:
        f = schema.imported(con, name, file)
        rows = schema.ordered(con, name, file)
    finally:
        con.close()
    return rows, mdgen.render_rows(rows, bool(f["eol"]) if f else True)


def known_created(created_at: float, created_by: str, title: str) -> float:
    """``created_at``, or 0 (unknown) for an imported item (no author) whose title has no date: the import
    stamped it with the import time, which is not when it was written."""
    return created_at if created_by or mdimport.title_date(title) else 0.0


def age_at(r: sqlite3.Row) -> float:
    """What an Issues row's age counts from: a done item's ``closed_at`` (0 = unknown: the import never sets
    it), else its known created time; 0 when neither is known (the row shows no age)."""
    if r["status"] == "done" and r["closed_at"]:
        return r["closed_at"]
    return known_created(r["created_at"], r["created_by"], r["title"])


def item_block(r: sqlite3.Row, **extra) -> dict:
    short = store.short_title(r["title"])
    return {"kind": "item", "line": r["num"], "done": r["status"] == "done", "text": r["title"],
            "title_short": short, "html": backlog.inline(short), "id": r["id"], "num": r["num"],
            "has_more": mdgen.has_more(r), "n_img": r["n_img"], "n_notes": r["n_notes"],
            "created_by": r["created_by"], "created_at": r["created_at"], "age_at": age_at(r), "tracker": True,
            **extra}


def blocks(rows: list[tuple[str, sqlite3.Row]]) -> list[dict]:
    """Same as ``backlog.blocks`` (markdown chunks + items with ``inbox``), from DB rows."""
    out: list[dict] = []
    buf: list[str] = []
    inbox = False

    def flush() -> None:
        if any(x.strip() for x in buf):
            out.append({"kind": "md", "html": render_markdown("\n".join(buf))})
        buf.clear()

    for kind, r in rows:
        if kind == "item":
            flush()
            out.append(item_block(r, inbox=inbox))
            continue
        if backlog.HEAD.match(r["text"]):
            inbox = bool(backlog.INBOX.match(r["text"]))
        buf.append(r["text"])
    flush()
    return out


def _counts(bs: list[dict]) -> tuple[int, int]:
    items = [b for b in bs if b["kind"] == "item"]
    done = sum(1 for b in items if b["done"])
    return len(items) - done, done


def project_summary(settings: Settings, name: str, limit: int = 30) -> dict:
    """``backlog.project_summary`` for any project."""
    if not is_tracker(settings, name, "backlog"):
        return backlog.project_summary(settings, name, limit)
    rows, text = _load(settings, name, "backlog")
    bs = blocks(rows)
    items = [b for b in bs if b["kind"] == "item" and not b["done"]]
    items.sort(key=lambda b: not b["inbox"])
    done = [b for b in bs if b["kind"] == "item" and b["done"]]
    o, d = _counts(bs)
    return {"name": name, "exists": True, "token": token_of(text.encode()), "items": items[:limit],
            "done_items": done[-limit:][::-1], "open_n": o, "done_n": d, "more": max(0, len(items) - limit),
            "done_more": max(0, len(done) - limit), "blocked": False, "max_item": backlog.MAX_ITEM}


def issues_summary(settings: Settings, name: str) -> dict:
    """The project page's Backlog / Issues tab: ``project_summary`` (``ISSUES_LIMIT`` rows per state for a
    tracker project) plus ``tracker``."""
    trk = is_tracker(settings, name, "backlog")
    s = project_summary(settings, name, ISSUES_LIMIT) if trk else project_summary(settings, name)
    s["tracker"] = trk
    return s


def _entry(settings: Settings, name: str) -> dict:
    rows, text = _load(settings, name, "backlog")
    bs = blocks(rows)
    o, d = _counts(bs)
    return {"name": name, "token": token_of(text.encode()), "blocks": bs, "open_n": o, "done_n": d,
            "tracker": True}


def entries(settings: Settings) -> list[dict]:
    """Every main project's backlog (``backlog.entry`` shape), most open items first."""
    tracked = {p for p, f in _tracked(settings) if f == "backlog"}
    out = [e for n, p in backlog.registry(settings, main_only=True).items()
           if (e := (_entry(settings, n) if n in tracked else backlog.entry(n, p)))]
    out.sort(key=lambda e: (-e["open_n"], e["name"].lower()))
    return out


def todo_summary(settings: Settings, name: str) -> dict:
    """``todo.todo_summary`` for any project."""
    if not is_tracker(settings, name, "todo"):
        return todo.todo_summary(settings, name)
    items, text = _todo_tracker(settings, name)
    opens = [it for it in items if not it["done"]]
    return {"exists": bool(items), "blocked": False, "token": token_of(text.encode()), "open_n": len(opens),
            "done_n": len(items) - len(opens), "total": len(items), "shown": todo._group(opens[:todo.VISIBLE]),
            "rest": todo._group(opens[todo.VISIBLE:])}


# ------------------------------------------------------------------ cross-project lists (Backlog page)
STATUSES = ("open", "done", "all")
LIST_LIMIT = 50  # issue rows per page of the cross-project list
LIST_MAX = 500


def _fold(s: str | None) -> str:
    return " ".join((s or "").lower().split())


def _issue_where(projects: list[str], project: str | None, q: str) -> tuple[str, list]:
    """WHERE clause over tk_item ``i``: BACKLOG items of the given (registered) projects, one ``project``,
    and ``q`` as a substring of ``project #num title`` (folded like the list filters)."""
    names = [project] if project else projects
    where = f"i.file='backlog' AND i.project IN ({','.join('?' * len(names))})"
    args: list = list(names)
    if q:
        where += " AND instr(pm_fold(i.project || ' #' || i.num || ' ' || i.title), ?) > 0"
        args.append(q)
    return where, args


def issue_projects(settings: Settings) -> list[str]:
    """The main (not worktree) registered projects whose BACKLOG is in the tracker, sorted: where issues live."""
    tracked = {p for p, f in _tracked(settings) if f == "backlog"}
    return sorted(n for n in backlog.registry(settings, main_only=True) if n in tracked)


def issue_rows(settings: Settings, *, status: str = "open", project: str | None = None, q: str = "",
               limit: int = LIST_LIMIT, offset: int = 0) -> dict:
    """Issues of every main tracker project as flat rows (newest first; done ones by close time), one page of
    ``limit`` from ``offset``. ``counts`` = {open, done, all} for the same project / ``q`` filter (not the
    status). Unknown ``status`` = open; an unknown or non-tracker ``project`` gives no rows."""
    status = status if status in STATUSES else "open"
    limit, offset = max(1, min(int(limit), LIST_MAX)), max(0, min(int(offset), 10**9))
    q = _fold(q)
    names = issue_projects(settings)
    out = {"rows": [], "total": 0, "counts": {"open": 0, "done": 0, "all": 0}, "status": status,
           "project": project or "", "q": q, "limit": limit, "offset": offset, "projects": names}
    if not names or (project and project not in names):
        return out
    where, args = _issue_where(names, project, q)
    con = schema.connect(settings)
    try:
        con.create_function("pm_fold", 1, lambda s: _fold(str(s)), deterministic=True)
        for r in con.execute(f"SELECT i.status, COUNT(*) AS n FROM tk_item i WHERE {where} GROUP BY i.status", args):
            out["counts"]["done" if r["status"] == "done" else "open"] += r["n"]
        out["counts"]["all"] = out["counts"]["open"] + out["counts"]["done"]
        if status != "all":
            where, args = where + " AND i.status=?", [*args, status]
        order = ("(i.status='done'), CASE WHEN i.status='done' THEN i.closed_at ELSE i.created_at END DESC,"
                 " i.id DESC")
        rows = con.execute(f"{schema.ITEM_SELECT} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                           [*args, limit, offset]).fetchall()
    finally:
        con.close()
    out["total"] = out["counts"][status]
    out["rows"] = [issue_row(r) for r in rows]
    return out


def issue_row(r: sqlite3.Row) -> dict:
    """One row of the cross-project issue list (name-type fields only, no body text)."""
    short = store.short_title(r["title"])
    return {"project": r["project"], "num": r["num"], "title": short, "html": backlog.inline(short),
            "done": r["status"] == "done", "n_notes": r["n_notes"], "n_img": r["n_img"],
            "has_body": bool(r["body_md"].strip()), "labels": [x for x in r["labels"].split(",") if x],
            "created_at": known_created(r["created_at"], r["created_by"], r["title"]), "created_by": r["created_by"],
            "closed_at": r["closed_at"], "closed_by": r["closed_by"], "age_at": age_at(r)}


def open_issue_counts(settings: Settings) -> dict[str, int]:
    """{project: open issues} of every tracker project (one query); projects without any are left out."""
    con = schema.connect(settings)
    try:
        return {r["project"]: r["n"] for r in con.execute(
            "SELECT project, COUNT(*) AS n FROM tk_item WHERE file='backlog' AND status='open' GROUP BY project")}
    except sqlite3.Error:
        return {}
    finally:
        con.close()


def _todo_tracker(settings: Settings, name: str) -> tuple[list[dict], str]:
    """(TODO items of a tracker project in file order, rendered text); ``line`` = the item number."""
    rows, text = _load(settings, name, "todo")
    head, out = "", []
    for kind, r in rows:
        if kind == "line":
            h = projects.HEADING.match(r["text"])
            head = h.group(1) if h else head
            continue
        label = todo._label(head)
        out.append({"line": r["num"], "done": r["status"] == "done", "text": r["title"], "heading": label,
                    "html": backlog.inline(r["title"]), "label": label, "id": r["id"], "num": r["num"],
                    "tracker": True})
    return out, text


def _todo_file(settings: Settings, path: str) -> list[dict]:
    try:
        text = backlog.read(todo.resolve_path(path)).decode("utf-8", "replace")
    except (backlog.Bad, OSError):
        return []
    return [{"line": it["line"], "num": 0, "done": it["done"], "text": it["text"], "heading": todo._label(it["heading"]),
             "label": todo._label(it["heading"]), "html": backlog.inline(it["text"]), "tracker": False} for it in projects.todo_items(text)]


def todo_groups(settings: Settings, *, status: str = "open", project: str | None = None) -> dict:
    """TODO items of every main project grouped by project, then section (file order): ``groups`` =
    [{name, tracker, open_n, done_n, sections: [{heading, open_n, done_n, items}]}] (projects with open items
    first, then by name; items filtered by ``status``, counts never), ``counts`` = {open, done, all}. A tracker
    project's items come from pm.db (``line`` = item number), the others from TODO.md (``line`` = line index)."""
    status = status if status in STATUSES else "open"
    tracked = {p for p, f in _tracked(settings) if f == "todo"}
    groups, counts = [], {"open": 0, "done": 0, "all": 0}
    for name, path in backlog.registry(settings, main_only=True).items():
        if project and name != project:
            continue
        items = _todo_tracker(settings, name)[0] if name in tracked else _todo_file(settings, path)
        if not items:
            continue
        sections: list[dict] = []
        for it in items:
            if not sections or sections[-1]["heading"] != it["heading"]:
                sections.append({"heading": it["heading"], "open_n": 0, "done_n": 0, "items": []})
            sec = sections[-1]
            sec["done_n" if it["done"] else "open_n"] += 1
            if status == "all" or it["done"] == (status == "done"):
                sec["items"].append(it)
        o = sum(s["open_n"] for s in sections)
        d = sum(s["done_n"] for s in sections)
        counts["open"], counts["done"] = counts["open"] + o, counts["done"] + d
        groups.append({"name": name, "tracker": name in tracked, "open_n": o, "done_n": d,
                       "sections": [s for s in sections if s["items"]]})
    counts["all"] = counts["open"] + counts["done"]
    groups.sort(key=lambda g: (not g["open_n"], g["name"].lower()))
    return {"groups": groups, "counts": counts, "status": status, "project": project or ""}
