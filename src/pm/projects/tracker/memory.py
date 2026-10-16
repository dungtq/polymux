"""Agent memory (``tk_memory``): one memory per project, shared by every agent kind (Claude, Codex, Devin, Cursor,
...), read and written with ``pmctl memory`` while the web service is down.

A memory is a named note: ``type`` (``TYPES``, Claude Code's four kinds), a one-line ``description`` (the index
line), a Markdown body. ``project`` '' holds the global memories (type ``user`` only), shown with every
project's. ``meta`` keeps the frontmatter keys of a Claude Code memory file that are not modelled here (with the
type at its place, e.g. ``metadata.type``), so ``memsync`` round-trips those files without loss; ``source`` is
the file name it came from (Claude's names may differ from the ``name:`` slug), ``hook`` its MEMORY.md index
text when that differs from the description.

Writes go straight to pm.db like ``claims``: one ``BEGIN IMMEDIATE`` transaction, audit line before the commit
(fail closed); audit details carry lengths and hashes of the texts, never the texts.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field

from pm.core import timefmt
from pm.core.config import Settings
from pm.core.errors import Conflict, Invalid, NotFound
from pm.projects.tracker import schema, store
from pm.projects.tracker.store import Audit

TYPES = ("user", "feedback", "project", "reference")
GLOBAL = ""
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_DESC = 1000
MAX_TITLE = 200
MAX_META = 16 * 1024
MAX_QUERY = 200
SEARCH_LIMIT = 50


@dataclass(frozen=True)
class Memory:
    id: int
    project: str
    name: str
    type: str
    title: str
    description: str
    body_md: str
    meta: dict = field(default_factory=dict)
    source: str = ""
    hook: str = ""
    created_by: str = ""
    updated_by: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def scope(self) -> str:
        return "global" if self.project == GLOBAL else "project"

    @property
    def file(self) -> str:
        """The file name it renders to in a memory dir."""
        return self.source or f"{self.name}.md"

    def to_dict(self) -> dict:
        return {"name": self.name, "project": self.project, "scope": self.scope, "type": self.type,
                "title": self.title, "description": self.description, "body_md": self.body_md, "meta": self.meta,
                "source": self.source, "created_by": self.created_by, "updated_by": self.updated_by,
                "created_at": timefmt.iso(self.created_at), "updated_at": timefmt.iso(self.updated_at)}


def from_row(r: sqlite3.Row) -> Memory:
    try:
        meta = json.loads(r["meta"] or "{}")
    except ValueError:
        meta = {}
    return Memory(id=r["id"], project=r["project"], name=r["name"], type=r["type"], title=r["title"],
                  description=r["description"], body_md=r["body_md"], meta=meta if isinstance(meta, dict) else {},
                  source=r["source"], hook=r["hook"], created_by=r["created_by"], updated_by=r["updated_by"],
                  created_at=r["created_at"], updated_at=r["updated_at"])


# ------------------------------------------------------------------ validation
def clean_name(name: str) -> str:
    n = (name or "").strip()
    if not NAME.match(n):
        raise Invalid(f"bad memory name {n[:80]!r}: lower-case letters, digits, '-' and '_', at most 64 chars")
    return n


def clean_type(t: str) -> str:
    if t not in TYPES:
        raise Invalid(f"type must be one of {', '.join(TYPES)}")
    return t


def clean_description(text: str, *, collapse: bool = True) -> str:
    """Not empty, at most ``MAX_DESC`` chars; ``collapse`` (typed input) makes it one line. Imports keep it as
    written (only stripped), so a file round-trips."""
    d = " ".join((text or "").split()) if collapse else (text or "").strip()
    if not d:
        raise Invalid("empty description")
    if len(d) > MAX_DESC:
        raise Invalid(f"description too long (max {MAX_DESC} chars)")
    return d


def _line(text: str | None, cap: int = MAX_TITLE, what: str = "title") -> str:
    t = " ".join((text or "").split())
    if len(t) > cap:
        raise Invalid(f"{what} too long (max {cap} chars)")
    return t


def _hook(text: str | None) -> str:
    return _line(text, MAX_DESC, "index text")


def with_type(meta: dict | None, t: str) -> dict:
    """``meta`` with the type at its place: ``metadata.type`` or a top-level ``type`` when the file had one there,
    else Claude Code's current layout (``metadata: {node_type: memory, type: ...}``)."""
    m = dict(meta or {})
    md = m.get("metadata")
    if isinstance(md, dict) and "type" in md:
        m["metadata"] = {**md, "type": t}
    elif "type" in m:
        m["type"] = t
    elif isinstance(md, dict):
        m["metadata"] = {**md, "type": t}
    else:
        m["metadata"] = {"node_type": "memory", "type": t}
    return m


def _meta_json(meta: dict) -> str:
    try:
        text = json.dumps(meta, ensure_ascii=False)
    except (TypeError, ValueError):
        raise Invalid("frontmatter holds values that are not plain text, numbers or lists") from None
    if len(text.encode()) > MAX_META:
        raise Invalid(f"frontmatter too large (max {MAX_META // 1024} KB)")
    return text


def _scope(project: str, is_global: bool) -> str:
    return GLOBAL if is_global else project


# ------------------------------------------------------------------ read
_SELECT = "SELECT * FROM tk_memory"


def _get(con: sqlite3.Connection, project: str, name: str) -> sqlite3.Row | None:
    return con.execute(_SELECT + " WHERE project=? AND name=?", (project, name)).fetchone()


def _read(settings: Settings, sql: str, args: tuple) -> list[Memory]:
    con = schema.connect(settings)
    try:
        return [from_row(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def find(settings: Settings, project: str, name: str) -> Memory | None:
    """The memory ``name`` of exactly ``project`` ('' = global), or None."""
    got = _read(settings, _SELECT + " WHERE project=? AND name=?", (project, name))
    return got[0] if got else None


def get(settings: Settings, project: str, name: str, *, is_global: bool = False) -> Memory:
    """The project's memory ``name``, else the global one (only the global one with ``is_global``)."""
    name = clean_name(name)
    for scope in ((GLOBAL,) if is_global else (project, GLOBAL)):
        m = find(settings, scope, name)
        if m is not None:
            return m
    raise NotFound(f"no memory {name!r}" + ("" if is_global else f" in {project} or global"))


def list_memories(settings: Settings, project: str, *, type: str | None = None,
                  include_global: bool = False) -> list[Memory]:
    """A project's memories in the order they were added; ``include_global`` appends the global ones."""
    scopes = (project, GLOBAL) if include_global and project != GLOBAL else (project,)
    sql = _SELECT + f" WHERE project IN ({','.join('?' * len(scopes))})"
    args: tuple = scopes
    if type is not None:
        sql, args = sql + " AND type=?", (*args, clean_type(type))
    return _read(settings, sql + " ORDER BY project = '', id", args)


def search(settings: Settings, project: str, query: str, *, include_global: bool = True,
           limit: int = SEARCH_LIMIT) -> list[Memory]:
    """Memories whose name, title, description or body contain ``query`` (case-insensitive for ASCII), at most
    ``limit`` (<= ``SEARCH_LIMIT``)."""
    q = " ".join((query or "").split())
    if not q or len(q) > MAX_QUERY:
        raise Invalid(f"search text must be 1-{MAX_QUERY} chars")
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    scopes = (project, GLOBAL) if include_global and project != GLOBAL else (project,)
    cond = " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in ("name", "title", "description", "body_md"))
    sql = (_SELECT + f" WHERE project IN ({','.join('?' * len(scopes))}) AND ({cond})"
           " ORDER BY project = '', id LIMIT ?")
    return _read(settings, sql, (*scopes, like, like, like, like, max(1, min(int(limit), SEARCH_LIMIT))))


def projects_with_memories(settings: Settings) -> list[str]:
    con = schema.connect(settings)
    try:
        return [r[0] for r in con.execute("SELECT DISTINCT project FROM tk_memory WHERE project != '' ORDER BY 1")]
    finally:
        con.close()


# ------------------------------------------------------------------ write
def _write(settings: Settings, project: str, fn, *, audit: Audit, action: str):
    """``fn(con) -> (result, audit detail)`` in one write transaction; audit before the commit (fail closed)."""
    con = schema.connect(settings)
    try:
        with schema.tx(con):
            result, detail = fn(con)
            if audit is not None:
                audit(action, {"project": project, "file": "memory", **detail})
    finally:
        con.close()
    return result


def _detail(name: str, description: str, body: str) -> dict:
    return {"name": name} | store.text_detail("description", description) | store.text_detail("body", body)


def add(settings: Settings, project: str, name: str, *, type: str, description: str, body: str = "",
        title: str = "", meta: dict | None = None, source: str = "", hook: str = "", is_global: bool = False,
        actor: str, audit: Audit = None, action: str = "tracker_memory_add", collapse: bool = True) -> Memory:
    """A new memory (Conflict when the name is taken in that scope). Global ones must be type ``user``."""
    scope, name, t = _scope(project, is_global), clean_name(name), clean_type(type)
    if scope == GLOBAL and t != "user":
        raise Invalid("only type user memories can be global")
    d, b = clean_description(description, collapse=collapse), store.clean_body(body)
    meta_text, ti, hk = _meta_json(with_type(meta, t)), _line(title), _hook(hook) if hook != d else ""

    def fn(con):
        if _get(con, scope, name) is not None:
            raise Conflict(f"memory {name!r} already exists" + (" (global)" if scope == GLOBAL else ""))
        now = time.time()
        con.execute("INSERT INTO tk_memory (project, name, type, title, description, body_md, meta, source, hook,"
                    " created_by, updated_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (scope, name, t, ti, d, b, meta_text, source, hk, actor, actor, now, now))
        return from_row(_get(con, scope, name)), _detail(name, d, b) | {"type": t}
    return _write(settings, scope, fn, audit=audit, action=action)


def update(settings: Settings, project: str, name: str, *, type: str | None = None, description: str | None = None,
           body: str | None = None, title: str | None = None, meta: dict | None = None, hook: str | None = None,
           is_global: bool = False, actor: str, audit: Audit = None, action: str = "tracker_memory_set",
           collapse: bool = True) -> Memory:
    """Replace the given fields of an existing memory, keep the others. A new description drops an old index
    ``hook`` (unless one is given)."""
    scope, name = _scope(project, is_global), clean_name(name)
    if all(v is None for v in (type, description, body, title, meta, hook)):
        raise Invalid("nothing to change (--type, --description, --title, --text / --body-file)")
    t = clean_type(type) if type is not None else None
    if scope == GLOBAL and t not in (None, "user"):
        raise Invalid("only type user memories can be global")
    d = clean_description(description, collapse=collapse) if description is not None else None
    b = store.clean_body(body) if body is not None else None

    def fn(con):
        r = _get(con, scope, name)
        if r is None:
            raise NotFound(f"no memory {name!r}" + (" (global)" if scope == GLOBAL else f" in {project}"))
        cur = from_row(r)
        nt, nd, nb = t or cur.type, d if d is not None else cur.description, b if b is not None else cur.body_md
        hk = _hook(hook) if hook is not None else ("" if d is not None else cur.hook)
        meta_text = _meta_json(with_type(meta if meta is not None else cur.meta, nt))
        con.execute("UPDATE tk_memory SET type=?, title=?, description=?, body_md=?, meta=?, hook=?, updated_by=?,"
                    " updated_at=? WHERE id=?",
                    (nt, _line(title) if title is not None else cur.title, nd, nb, meta_text,
                     "" if hk == nd else hk, actor, time.time(), r["id"]))
        return from_row(_get(con, scope, name)), _detail(name, nd, nb) | {"type": nt}
    return _write(settings, scope, fn, audit=audit, action=action)


def remove(settings: Settings, project: str, name: str, *, is_global: bool = False, audit: Audit = None) -> Memory:
    """Delete a memory; ``memsync`` removes its rendered files when nobody edited them since."""
    scope, name = _scope(project, is_global), clean_name(name)

    def fn(con):
        r = _get(con, scope, name)
        if r is None:
            raise NotFound(f"no memory {name!r}" + (" (global)" if scope == GLOBAL else f" in {project}"))
        con.execute("DELETE FROM tk_memory WHERE id=?", (r["id"],))
        return from_row(r), {"name": name}
    return _write(settings, scope, fn, audit=audit, action="tracker_memory_rm")
