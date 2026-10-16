"""FEATURES.md rows in pm.db: the grammar, where a new entry goes, landed set / rm (old texts kept in
``tk_feature_rev``), branch drafts and landing one, import / absorb, and what a branch changed (merge driver,
worktree commit). Every write here runs inside the caller's transaction (``featurelist`` owns the flow).

Grammar: an entry is ``- **<Name>** — <text>`` under a ``## <Area>`` heading; indented non-blank lines right
under it are its ``extra`` (raw, each ending in "\\n"). Every other line is a ``tk_line`` (preamble, headings,
blank lines, prose, other bullets). Entries are keyed by (area, name); a repeated key pairs by occurrence.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field

from pm.core.errors import Invalid, NotFound
from pm.projects.tracker import drafts

FILE = "features"
ENTRY = re.compile(r"^- \*\*(.+?)\*\* — (.*)$")
_POS_EPS = 1e-6


@dataclass
class PEntry:
    pos: float
    area: str
    name: str
    text: str
    extra: str = ""


@dataclass
class Parsed:
    eol: bool
    lines: list[tuple[float, str]] = field(default_factory=list)
    entries: list[PEntry] = field(default_factory=list)


def area_of(line: str) -> str | None:
    return line[3:].strip() if line.startswith("## ") else None


def entry_line(name: str, text: str) -> str:
    return f"- **{name}** — {text}"


def parse(text: str) -> Parsed:
    eol = text.endswith("\n")
    out = Parsed(eol)
    area: str | None = None
    last: PEntry | None = None
    for i, ln in enumerate((text[:-1] if eol else text).split("\n") if text else []):
        m = ENTRY.match(ln) if area is not None else None
        if m:
            last = PEntry(float(i + 1), area, m.group(1), m.group(2))
            out.entries.append(last)
        elif last is not None and ln[:1] in (" ", "\t") and ln.strip():
            last.extra += ln + "\n"
        else:
            area = area_of(ln) if ln.startswith("## ") else area
            last = None
            out.lines.append((float(i + 1), ln))
    return out


# ------------------------------------------------------------------ reading
def rows(con: sqlite3.Connection, project: str, *, removed: bool = False) -> list[tuple[str, sqlite3.Row]]:
    """Lines and entries in file order: ``("line", row)`` / ``("feature", row)`` (removed entries only with
    ``removed``: their position is stale)."""
    lines = con.execute("SELECT id, pos, text FROM tk_line WHERE project=? AND file=?", (project, FILE))
    feats = con.execute("SELECT * FROM tk_feature WHERE project=?" + ("" if removed else " AND state != 'removed'"),
                        (project,))
    out = [("line", r) for r in lines] + [("feature", r) for r in feats]
    out.sort(key=lambda kr: (kr[1]["pos"], kr[0] == "feature", kr[1]["id"]))
    return out


def find(con: sqlite3.Connection, project: str, area: str, name: str) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM tk_feature WHERE project=? AND area=? AND name=? ORDER BY state != 'active',"
                       " pos, id LIMIT 1", (project, area, name)).fetchone()


def draft_revs(con: sqlite3.Connection, project: str) -> list[sqlite3.Row]:
    """Pending draft revisions of the project, oldest first."""
    return list(con.execute("SELECT r.* FROM tk_feature_rev r JOIN tk_feature f ON f.id = r.feature_id"
                            " WHERE f.project=? AND r.state='draft' ORDER BY r.ts, r.id", (project,)))


def effective(con: sqlite3.Connection, project: str, view: drafts.View) -> tuple[dict[int, tuple], bool]:
    """{feature id: (text, extra, state)} after the drafts ``view`` shows (newest wins), and whether any
    draft applied."""
    out = {r["id"]: (r["text"], r["extra"], r["state"])
           for r in con.execute("SELECT id, text, extra, state FROM tk_feature WHERE project=?", (project,))}
    applied = False
    for d in draft_revs(con, project):
        if view.shows(d["branch"], d["sha"]):
            out[d["feature_id"]] = (d["text"], d["extra"], "active" if d["op"] == "set" else "removed")
            applied = True
    return out, applied


# ------------------------------------------------------------------ placement
def _after(con: sqlite3.Connection, project: str, area: str) -> float:
    for _ in (0, 1):
        rs = rows(con, project)
        at = _last_of_area(rs, area)
        lo = rs[at][1]["pos"] if at >= 0 else 0.0
        hi = rs[at + 1][1]["pos"] if at + 1 < len(rs) else lo + 2.0
        if hi - lo >= 2 * _POS_EPS:
            return (lo + hi) / 2
        for i, (k, r) in enumerate(rs):  # too tight: renumber
            con.execute(f"UPDATE {'tk_feature' if k == 'feature' else 'tk_line'} SET pos=? WHERE id=?",
                        (float(i + 1), r["id"]))
    raise AssertionError("unreachable")


def _last_of_area(rs: list[tuple[str, sqlite3.Row]], area: str) -> int:
    """Index of the row a new entry of ``area`` goes after: the area's last entry or non-blank line (after
    the blank line under a bare heading); an area without a heading line: after its last entry, else at the
    end of the file."""
    head = next((i for i, (k, r) in enumerate(rs) if k == "line" and area_of(r["text"]) == area), None)
    if head is None:
        same = [i for i, (k, r) in enumerate(rs) if k == "feature" and r["area"] == area]
        return same[-1] if same else len(rs) - 1
    end = head + 1
    while end < len(rs) and not (rs[end][0] == "line" and area_of(rs[end][1]["text"]) is not None):
        end += 1
    last = head
    for i in range(head + 1, end):
        if rs[i][0] == "feature" or rs[i][1]["text"].strip():
            last = i
    if last == head and head + 1 < end:
        last = head + 1
    return last


# ------------------------------------------------------------------ landed changes
def _old(con: sqlite3.Connection, r: sqlite3.Row, by: str, now: float) -> None:
    con.execute("INSERT INTO tk_feature_rev (feature_id, op, text, extra, state, branch, sha, ts, by)"
                " VALUES (?,'set',?,?,'old',?,?,?,?)", (r["id"], r["text"], r["extra"], r["branch"], r["sha"], now, by))


def set_landed(con: sqlite3.Connection, project: str, area: str, name: str, text: str, actor: str,
               extra: str = "", now: float | None = None) -> int:
    now = time.time() if now is None else now
    r = find(con, project, area, name)
    if r is None:
        return int(con.execute(
            "INSERT INTO tk_feature (project, area, name, pos, text, extra, state, updated_at, updated_by)"
            " VALUES (?,?,?,?,?,?,'active',?,?)",
            (project, area, name, _after(con, project, area), text, extra, now, actor)).lastrowid or 0)
    if r["state"] == "active" and (r["text"], r["extra"]) == (text, extra):
        return r["id"]
    if r["state"] == "active":
        _old(con, r, actor, now)
    pos = _after(con, project, area) if r["state"] == "removed" else r["pos"]
    con.execute("UPDATE tk_feature SET text=?, extra=?, state='active', pos=?, updated_at=?, updated_by=? WHERE id=?",
                (text, extra, pos, now, actor, r["id"]))
    return r["id"]


def rm_landed(con: sqlite3.Connection, r: sqlite3.Row, actor: str) -> None:
    now = time.time()
    _old(con, r, actor, now)
    con.execute("UPDATE tk_feature SET state='removed', updated_at=?, updated_by=? WHERE id=?", (now, actor, r["id"]))


# ------------------------------------------------------------------ drafts
def set_draft(con: sqlite3.Connection, project: str, area: str, name: str, op: str, text: str,
              view: drafts.View, actor: str, extra: str = "") -> int:
    """The branch's one pending revision of (area, name): ``op`` ``set`` (text) or ``rm``. A new name gets a
    ``pending`` row (shown only through a draft)."""
    now = time.time()
    r = find(con, project, area, name)
    if r is None:
        if op == "rm":
            raise NotFound(f"no feature {area} / {name}")
        fid = int(con.execute(
            "INSERT INTO tk_feature (project, area, name, pos, state, branch, updated_by) VALUES (?,?,?,?,'pending',?,?)",
            (project, area, name, _after(con, project, area), view.branch, actor)).lastrowid or 0)
    else:
        fid = r["id"]
    d = con.execute("SELECT id FROM tk_feature_rev WHERE feature_id=? AND state='draft' AND branch=?",
                    (fid, view.branch)).fetchone()
    if d is not None:
        con.execute("UPDATE tk_feature_rev SET op=?, text=?, extra=?, ts=?, by=? WHERE id=?",
                    (op, text, extra, now, actor, d["id"]))
    else:
        con.execute("INSERT INTO tk_feature_rev (feature_id, op, text, extra, state, branch, base_sha, ts, by)"
                    " VALUES (?,?,?,?,'draft',?,?,?,?)", (fid, op, text, extra, view.branch, view.head, now, actor))
    return fid


def land(con: sqlite3.Connection, d: sqlite3.Row) -> None:
    """A draft revision reaches main: it becomes the entry's text / state (the old text is kept)."""
    now = time.time()
    r = con.execute("SELECT * FROM tk_feature WHERE id=?", (d["feature_id"],)).fetchone()
    if r["state"] == "active":
        _old(con, r, d["by"], now)
    state = "active" if d["op"] == "set" else "removed"
    pos = _after(con, r["project"], r["area"]) if r["state"] == "removed" and state == "active" else r["pos"]
    text, extra = (d["text"], d["extra"]) if state == "active" else (r["text"], r["extra"])
    con.execute("UPDATE tk_feature SET text=?, extra=?, state=?, pos=?, branch=?, sha=?, updated_at=?, updated_by=?"
                " WHERE id=?", (text, extra, state, pos, d["branch"], d["sha"], now, d["by"], r["id"]))
    con.execute("UPDATE tk_feature_rev SET state='landed' WHERE id=?", (d["id"],))


# ------------------------------------------------------------------ import / absorb
def store(con: sqlite3.Connection, project: str, parsed: Parsed, actor: str, now: float) -> dict:
    """``parsed`` becomes the landed state: entries matched by (area, name) keep their row (a changed text
    keeps the old one as a revision), new ones are added, missing active ones are marked removed; pending
    rows (a branch's new names) are kept and placed again."""
    old = list(con.execute("SELECT * FROM tk_feature WHERE project=? ORDER BY state != 'active', pos, id", (project,)))
    by: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for r in old:
        if r["state"] != "pending":
            by.setdefault((r["area"], r["name"]), []).append(r)
    used: set[int] = set()
    for e in parsed.entries:
        same = by.get((e.area, e.name))
        r = same.pop(0) if same else None
        if r is None:
            con.execute("INSERT INTO tk_feature (project, area, name, pos, text, extra, state, updated_at)"
                        " VALUES (?,?,?,?,?,?,'active',?)", (project, e.area, e.name, e.pos, e.text, e.extra, now))
            continue
        used.add(r["id"])
        if (r["text"], r["extra"], r["state"]) != (e.text, e.extra, "active"):
            if r["state"] == "active":
                _old(con, r, actor, now)
            con.execute("UPDATE tk_feature SET text=?, extra=?, state='active', updated_at=?, updated_by=? WHERE id=?",
                        (e.text, e.extra, now, actor, r["id"]))
        con.execute("UPDATE tk_feature SET pos=? WHERE id=?", (e.pos, r["id"]))
    gone = [r for r in old if r["id"] not in used and r["state"] == "active"]
    con.executemany("UPDATE tk_feature SET state='removed', updated_at=? WHERE id=?", [(now, r["id"]) for r in gone])
    con.execute("DELETE FROM tk_line WHERE project=? AND file=?", (project, FILE))
    con.executemany("INSERT INTO tk_line (project, file, pos, text) VALUES (?,?,?,?)",
                    [(project, FILE, pos, text) for pos, text in parsed.lines])
    for r in old:
        if r["state"] == "pending":
            con.execute("UPDATE tk_feature SET pos=? WHERE id=?", (_after(con, project, r["area"]), r["id"]))
    con.execute("INSERT OR REPLACE INTO tk_file (project, file, eol, imported_at, imported_by, next_num)"
                " VALUES (?,?,?,?,?,1)", (project, FILE, int(parsed.eol), now, actor))
    return {"project": project, "file": FILE, "entries": len(parsed.entries), "lines": len(parsed.lines),
            "dropped": len(gone)}


# ------------------------------------------------------------------ what a branch changed
Change = tuple[str, str, str, str, str, PEntry | None]  # (area, name, op, text, extra, base entry)


def changes(base: str, theirs: str) -> list[Change]:
    """Entries ``theirs`` added or changed (``set``) or dropped (``rm``) against ``base``."""
    b: dict[tuple[str, str], PEntry] = {}
    for e in parse(base).entries:
        b.setdefault((e.area, e.name), e)
    t: dict[tuple[str, str], PEntry] = {}
    for e in parse(theirs).entries:
        t.setdefault((e.area, e.name), e)
    out: list[Change] = []
    for k, e in t.items():
        be = b.get(k)
        if be is None or (be.text, be.extra) != (e.text, e.extra):
            out.append((e.area, e.name, "set", e.text, e.extra, be))
    out += [(be.area, be.name, "rm", "", "", be) for k, be in b.items() if k not in t]
    return out


def take(con: sqlite3.Connection, project: str, chs: list[Change], view: drafts.View, actor: str) -> dict:
    """On the default branch (merge driver): a draft that made the change lands, else the change is taken
    when main still has the base's state (main changed it too: main wins). On another branch (worktree
    commit, hand edit): each change the branch's view does not show yet becomes its draft."""
    n_land = n_set = 0
    eff, _ = effective(con, project, view)
    for area, name, op, text, extra, be in chs:
        r = find(con, project, area, name)
        if not view.main:
            cur = eff.get(r["id"]) if r is not None else None
            want = (text, extra, "active") if op == "set" else None
            if (op == "set" and cur != want) or (op == "rm" and cur is not None and cur[2] == "active"):
                set_draft(con, project, area, name, op, text, view, actor, extra)
                n_set += 1
            continue
        d = None if r is None else next((d for d in draft_revs(con, project) if d["feature_id"] == r["id"]
                                         and d["op"] == op and (op == "rm" or (d["text"], d["extra"]) == (text, extra))),
                                        None)
        if d is not None:
            land(con, d)
            n_land += 1
            continue
        cur = (r["text"], r["extra"]) if r is not None and r["state"] == "active" else None
        if cur != ((be.text, be.extra) if be is not None else None):
            continue
        if op == "set":
            set_landed(con, project, area, name, text, actor, extra)
        else:
            rm_landed(con, r, actor)
        n_set += 1
    return {"landed": n_land, "taken": n_set}


def check_key(area: str, name: str) -> tuple[str, str]:
    a, n = " ".join((area or "").split()), " ".join((name or "").split())
    if not a or not n:
        raise Invalid("area and name must not be empty")
    if len(a) > 200 or len(n) > 200 or "**" in n:
        raise Invalid("area / name: max 200 chars, no ** in a name")
    return a, n


def check_text(text: str) -> str:
    t = (text or "").replace("\r\n", "\n").strip()
    if not t or "\n" in t:
        raise Invalid("text: one non-empty line")
    if len(t) > 8000:
        raise Invalid("text too long (max 8000 chars)")
    return t
