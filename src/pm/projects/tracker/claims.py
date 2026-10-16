"""Agent work claims (``tk_claim``): an agent announces what it is about to change (title, branch, work tree,
repo-relative path globs) before it starts, closes the claim when it is done, and checks the other agents' claims
for overlaps before it starts or merges (``mergecheck``).

Claims work in any pm project, whether its tracker files are imported or not: writes go straight to pm.db
(``schema.connect`` + ``schema.tx``, ``num`` = MAX + 1 under ``BEGIN IMMEDIATE``, audit line before the commit:
fail closed), never through ``store.run`` (that needs imported files and re-renders them). Only ``--issue``
needs the project's BACKLOG.md in the tracker.

Paths: fnmatch globs (``*`` also crosses ``/``, so ``**`` works), a plain path also covers everything below it;
``[]`` = unknown = the whole repo. Stale: an active claim whose pane is set but not alive in tmux; ``live`` is
the set of live pane ids (``live_panes``: one ``list-panes -a`` per command), None when tmux is unknown (then
nothing is stale). Stale claims are shown, never block, and ``gc`` drops them.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatchcase

from pm.core import timefmt
from pm.core.config import Settings
from pm.core.errors import Conflict, Invalid, NotFound
from pm.projects.tracker import issues, schema, store, views
from pm.projects.tracker.store import Audit
from pm.tmux import ctl as tmuxctl

STATUSES = ("active", "done", "dropped")
MAX_PATHS = 50
MAX_GLOB = 200
MAX_NOTE = 1000
CLOSED_SHOWN = 50
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_PANE = re.compile(r"^%\d{1,9}$")
_COMMIT = re.compile(r"^[0-9a-f]{4,40}$")
_WILD = "*?["


@dataclass(frozen=True)
class Claim:
    id: int
    project: str
    num: int
    agent: str
    pane: str
    title: str
    branch: str
    worktree: str
    paths: tuple[str, ...]
    issue: int | None
    status: str
    created_at: float
    updated_at: float
    closed_at: float
    commit: str
    note: str
    stale: bool = False

    @property
    def active(self) -> bool:
        return self.status == "active"

    def to_dict(self) -> dict:
        return {"num": self.num, "project": self.project, "agent": self.agent, "pane": self.pane,
                "title": self.title, "branch": self.branch, "worktree": self.worktree, "paths": list(self.paths),
                "issue": self.issue, "status": self.status, "stale": self.stale,
                "created_at": _iso(self.created_at), "updated_at": _iso(self.updated_at),
                "closed_at": _iso(self.closed_at), "commit": self.commit, "note": self.note}


def _iso(epoch: float) -> str | None:
    """ISO 8601 in the display offset (``timefmt``); None for 0 (not set)."""
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, timefmt.tz()).isoformat(timespec="seconds")


def from_row(r: sqlite3.Row, live: frozenset[str] | None = None) -> Claim:
    try:
        paths = tuple(p for p in json.loads(r["paths"] or "[]") if isinstance(p, str))
    except ValueError:
        paths = ()
    stale = r["status"] == "active" and bool(r["pane"]) and live is not None and r["pane"] not in live
    return Claim(id=r["id"], project=r["project"], num=r["num"], agent=r["agent"], pane=r["pane"], title=r["title"],
                 branch=r["branch"], worktree=r["worktree"], paths=paths, issue=r["issue_num"], status=r["status"],
                 created_at=r["created_at"], updated_at=r["updated_at"], closed_at=r["closed_at"],
                 commit=r["commit_sha"], note=r["note"], stale=stale)


def live_panes(settings: Settings) -> frozenset[str] | None:
    """Ids of the panes alive in tmux (one ``list-panes -a``); None when tmux cannot be asked or shows no pane
    at all (another server than the agents' one: unknown, so nothing turns stale)."""
    try:
        sessions = tmuxctl.list_sessions(settings)
    except tmuxctl.TmuxError:
        return None
    live = frozenset(p.id for s in sessions for w in s.windows for p in w.panes if not p.dead)
    return live or None


# ------------------------------------------------------------------ validation
def clean_paths(paths: Iterable[str] | None) -> tuple[str, ...]:
    """Repo-relative globs: no leading ``/`` ``-`` ``~``, no ``..`` / ``.`` / empty part, no control chars or backslash,
    ``./`` and trailing ``/`` dropped, duplicates removed; at most ``MAX_PATHS`` of ``MAX_GLOB`` chars."""
    out: list[str] = []
    for p in paths or ():
        g = (p or "").strip() if isinstance(p, str) else ""
        while g.startswith("./"):
            g = g[2:]
        g = g.rstrip("/")
        if (not g or len(g) > MAX_GLOB or _CTRL.search(g) or "\\" in g or g.startswith(("/", "-", "~"))
                or any(part in ("", ".", "..") for part in g.split("/"))):
            raise Invalid(f"bad path glob {str(p)[:80]!r}: repo-relative, no '..', no leading '/'")
        if g not in out:
            out.append(g)
    if len(out) > MAX_PATHS:
        raise Invalid(f"at most {MAX_PATHS} path globs per claim")
    return tuple(out)


def _line(text: str | None, cap: int, what: str) -> str:
    t = " ".join((text or "").split())
    if len(t) > cap:
        raise Invalid(f"{what} too long (max {cap} chars)")
    return t


def _pane(pane: str | None) -> str:
    p = (pane or "").strip()
    if p and not _PANE.match(p):
        raise Invalid("pane must be a tmux pane id like %12")
    return p


# ------------------------------------------------------------------ overlap
def _is_glob(g: str) -> bool:
    return any(c in g for c in _WILD)


def _dir_of(g: str) -> str:
    """A glob's literal directory part (``src/pm/*.py`` -> ``src/pm``, ``*.py`` -> ""); a plain path itself."""
    if not _is_glob(g):
        return g
    head = g[:min(g.find(c) for c in _WILD if c in g)]
    return head.rpartition("/")[0]


def _under(d: str, top: str) -> bool:
    return not top or d == top or d.startswith(top + "/")


def match_file(glob: str, path: str) -> bool:
    """``path`` (repo-relative) is covered by ``glob``: fnmatch, below a plain directory, or ``**/x`` at the root."""
    return (fnmatchcase(path, glob) or path.startswith(glob + "/")
            or (glob.startswith("**/") and fnmatchcase(path, glob[3:])))


def globs_overlap(x: str, y: str) -> bool:
    """Some path may match both: fnmatch both ways, a plain path containing the other's directory (or reached
    by the other's leading parts), or two globs whose literal directories nest."""
    if fnmatchcase(x, y) or fnmatchcase(y, x) or match_file(x, y) or match_file(y, x):
        return True
    for a, b in ((x, y), (y, x)):
        if not _is_glob(a):
            if _under(_dir_of(b), a):
                return True
            parts, bparts = a.split("/"), b.split("/")
            if len(bparts) > len(parts) and fnmatchcase(a, "/".join(bparts[:len(parts)])):
                return True
    if _is_glob(x) and _is_glob(y):
        dx, dy = _dir_of(x), _dir_of(y)
        return _under(dx, dy) or _under(dy, dx)
    return False


def paths_overlap(a: Sequence[str], b: Sequence[str]) -> bool:
    return not a or not b or any(globs_overlap(x, y) for x in a for y in b)


def overlaps(a: Claim, b: Claim) -> bool:
    """Two different active claims of one project that may touch the same files ([] = the whole repo)."""
    return (a.project == b.project and a.num != b.num and a.active and b.active
            and paths_overlap(a.paths, b.paths))


def touches(claim: Claim, files: Iterable[str]) -> list[str]:
    """The ``files`` (repo-relative) the claim covers: all of them when its paths are []."""
    files = list(files)
    if not claim.paths:
        return files
    return [f for f in files if any(match_file(g, f) for g in claim.paths)]


# ------------------------------------------------------------------ read
_SELECT = "SELECT * FROM tk_claim WHERE project=?"


def _get(con: sqlite3.Connection, project: str, num: int) -> sqlite3.Row:
    r = con.execute(_SELECT + " AND num=?", (project, int(num))).fetchone()
    if r is None:
        raise NotFound(f"{project}: no claim c{num}")
    return r


def list_claims(settings: Settings, project: str, *, closed: bool = False, agent: str | None = None,
                live: frozenset[str] | None = None) -> list[Claim]:
    """Active claims by number; ``closed`` adds the last ``CLOSED_SHOWN`` done / dropped ones; ``agent``: only
    that agent's."""
    con = schema.connect(settings)
    try:
        rows = con.execute(_SELECT + " AND status='active' ORDER BY num", (project,)).fetchall()
        if closed:
            rows += con.execute(_SELECT + " AND status!='active' ORDER BY closed_at DESC, num DESC LIMIT ?",
                                (project, CLOSED_SHOWN)).fetchall()
    finally:
        con.close()
    out = sorted((from_row(r, live) for r in rows), key=lambda c: c.num)
    return [c for c in out if agent is None or c.agent == agent]


def show(settings: Settings, project: str, num: int, *, live: frozenset[str] | None = None) -> Claim:
    con = schema.connect(settings)
    try:
        return from_row(_get(con, project, num), live)
    finally:
        con.close()


def others(settings: Settings, project: str, claim: Claim, live: frozenset[str] | None) -> list[Claim]:
    """Other agents' active, non-stale claims whose paths overlap ``claim``'s (whatever its status)."""
    return [c for c in list_claims(settings, project, live=live)
            if c.num != claim.num and c.agent != claim.agent and not c.stale
            and paths_overlap(claim.paths, c.paths)]


# ------------------------------------------------------------------ write
def _write(settings: Settings, project: str, fn, *, audit: Audit, action: str):
    """``fn(con) -> (result, audit detail)`` in one write transaction; audit before the commit (fail closed)."""
    con = schema.connect(settings)
    try:
        with schema.tx(con):
            result, detail = fn(con)
            if audit is not None:
                audit(action, {"project": project, "file": "claim", **detail})
    finally:
        con.close()
    return result


def _active(con: sqlite3.Connection, project: str, num: int) -> sqlite3.Row:
    r = _get(con, project, num)
    if r["status"] != "active":
        raise Conflict(f"c{num} is already {r['status']}")
    return r


def _check_issue(settings: Settings, project: str, num: int) -> None:
    if not views.is_tracker(settings, project, issues.FILE):
        raise Invalid(f"{project} has no issue tracker (BACKLOG.md not imported): drop --issue")
    store.get_item(settings, project, issues.FILE, num)  # NotFound for an unknown issue


def add(settings: Settings, project: str, title: str, *, agent: str, pane: str = "", branch: str = "",
        worktree: str = "", paths: Iterable[str] = (), issue: int | None = None, audit: Audit = None,
        live: frozenset[str] | None = None) -> tuple[Claim, list[Claim]]:
    """A new active claim and the overlapping claims of other agents. With ``issue``: that issue (checked
    first) gets the note "claimed by <agent> (c<num>) on <branch>: <title>"."""
    t = store.clean_title(title)
    globs, pane = clean_paths(paths), _pane(pane)
    branch, worktree = _line(branch, 200, "branch"), _line(worktree, 4096, "worktree")
    if issue is not None:
        _check_issue(settings, project, issue)

    def fn(con):
        num = con.execute("SELECT COALESCE(MAX(num), 0) + 1 FROM tk_claim WHERE project=?", (project,)).fetchone()[0]
        now = time.time()
        con.execute("INSERT INTO tk_claim (project, num, agent, pane, title, branch, worktree, paths, issue_num,"
                    " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,'active',?,?)",
                    (project, num, agent, pane, t, branch, worktree, json.dumps(globs), issue, now, now))
        detail = {"num": num, "agent": agent, "paths": len(globs)} | store.text_detail("title", t)
        return from_row(_get(con, project, num), live), detail | ({"issue": issue} if issue is not None else {})
    c = _write(settings, project, fn, audit=audit, action="tracker_claim_add")
    if issue is not None:
        issues.add_note(settings, project, issue, f"claimed by {agent} (c{c.num}) on {branch or '?'}: {t}",
                        actor=agent, audit=audit)
    return c, others(settings, project, c, live)


def update(settings: Settings, project: str, num: int, *, paths: Iterable[str] | None = None,
           title: str | None = None, audit: Audit = None, live: frozenset[str] | None = None) -> Claim:
    """Replace an active claim's paths and/or title."""
    if paths is None and title is None:
        raise Invalid("nothing to change (--path and/or --title)")
    globs = clean_paths(paths) if paths is not None else None
    t = store.clean_title(title) if title is not None else None

    def fn(con):
        r = _active(con, project, num)
        con.execute("UPDATE tk_claim SET paths=?, title=?, updated_at=? WHERE id=?",
                    (json.dumps(globs) if globs is not None else r["paths"], t or r["title"], time.time(), r["id"]))
        return from_row(_get(con, project, num), live), {"num": int(num)} | (
            {"paths": len(globs)} if globs is not None else {}) | (store.text_detail("title", t) if t else {})
    return _write(settings, project, fn, audit=audit, action="tracker_claim_update")


def _close(settings: Settings, project: str, num: int, status: str, *, commit: str = "", note: str = "",
           audit: Audit, live: frozenset[str] | None) -> Claim:
    def fn(con):
        r = _active(con, project, num)
        now = time.time()
        con.execute("UPDATE tk_claim SET status=?, closed_at=?, updated_at=?, commit_sha=?, note=? WHERE id=?",
                    (status, now, now, commit, note, r["id"]))
        detail = {"num": int(num)} | ({"commit": commit} if commit else {}) | (
            store.text_detail("note", note) if note else {})
        return from_row(_get(con, project, num), live), detail
    return _write(settings, project, fn, audit=audit, action=f"tracker_claim_{'done' if status == 'done' else 'drop'}")


def done(settings: Settings, project: str, num: int, *, actor: str, commit: str | None = None, note: str = "",
         audit: Audit = None, live: frozenset[str] | None = None) -> tuple[Claim, list[Claim]]:
    """Close an active claim as done; its issue gets "done by <actor> in <sha>: <note>". Returns the claim and
    the other agents' active claims on the same paths (to notify)."""
    sha = (commit or "").strip().lower()
    if sha and not _COMMIT.match(sha):
        raise Invalid("commit must be a hex sha")
    text = _line(note, MAX_NOTE, "note")
    c = _close(settings, project, num, "done", commit=sha, note=text, audit=audit, live=live)
    if c.issue is not None and views.is_tracker(settings, project, issues.FILE):
        msg = f"done by {actor}" + (f" in {sha}" if sha else "") + (f": {text}" if text else f" (c{c.num})")
        issues.add_note(settings, project, c.issue, msg, actor=actor, audit=audit)
    return c, others(settings, project, c, live)


def drop(settings: Settings, project: str, num: int, *, note: str = "", audit: Audit = None,
         live: frozenset[str] | None = None) -> Claim:
    return _close(settings, project, num, "dropped", note=_line(note, MAX_NOTE, "note"), audit=audit, live=live)


def gc(settings: Settings, project: str, *, live: frozenset[str] | None, audit: Audit = None) -> list[Claim]:
    """Drop the stale claims (none while tmux is unknown); the claims dropped."""
    stale = [c for c in list_claims(settings, project, live=live) if c.stale]
    if not stale:
        return []

    def fn(con):
        now = time.time()
        ids = [c.id for c in stale]
        con.executemany("UPDATE tk_claim SET status='dropped', closed_at=?, updated_at=?, note=? WHERE id=?"
                        " AND status='active'", [(now, now, "stale: pane gone", i) for i in ids])
        rows = [con.execute("SELECT * FROM tk_claim WHERE id=?", (i,)).fetchone() for i in ids]
        gone = [from_row(r) for r in rows if r["note"] == "stale: pane gone" and r["status"] == "dropped"]
        return gone, {"count": len(gone)}
    return _write(settings, project, fn, audit=audit, action="tracker_claim_gc")
