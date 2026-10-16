"""Quick chat history: its own SQLite DB ``<state_dir>/qchat.db`` (not pm.db).

Message text is masked with ``pm.security.mask`` before it is stored (user decision 2026-10-11: history
is stored and searchable). It is never logged, audited or sent to ntfy. Sync, short transactions.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import TypedDict

from pm.core.config import Settings
from pm.security.mask import mask

SCHEMA_VERSION = 2  # 2 = convs.agent (aside conversations), added by _migrate on v1 DBs
TITLE_MAX = 60
DEFAULT_TITLE = "New chat"


class Conv(TypedDict):
    id: int
    title: str
    session: str
    model: str
    profile: str
    folder: str
    mcp: list[str]
    skills: list[str]
    agent: str  # agent id of an aside ("" = a plain quick chat)
    created: float
    updated: float
    msgs: int
    archived: bool


class Msg(TypedDict):
    id: int
    role: str
    text: str
    html: str
    tools: list[dict]
    cost: float | None
    model: str
    ts: float


_SCHEMA = """
CREATE TABLE IF NOT EXISTS convs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL DEFAULT 'New chat',
  session TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', profile TEXT NOT NULL DEFAULT 'none',
  folder TEXT NOT NULL DEFAULT '', mcp TEXT NOT NULL DEFAULT '[]', skills TEXT NOT NULL DEFAULT '[]',
  created REAL NOT NULL, updated REAL NOT NULL, agent TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS convs_updated ON convs(updated);
CREATE TABLE IF NOT EXISTS msgs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, conv INTEGER NOT NULL REFERENCES convs(id) ON DELETE CASCADE,
  role TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', html TEXT NOT NULL DEFAULT '',
  tools TEXT NOT NULL DEFAULT '[]', cost REAL, model TEXT NOT NULL DEFAULT '', ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS msgs_conv ON msgs(conv);
"""


def _path(settings: Settings):
    return settings.state_dir / "qchat.db"


def open_db(settings: Settings) -> sqlite3.Connection:
    p = _path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        fd = os.open(p, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
    con = sqlite3.connect(p, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(_SCHEMA)
    _migrate(con)
    if con.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        con.commit()
    for suffix in ("-wal", "-shm"):
        q = p.with_name(p.name + suffix)
        if q.exists():
            os.chmod(q, 0o600)
    return con


def _migrate(con: sqlite3.Connection) -> None:
    """Add columns missing from a table created by an older pm (old rows keep the default)."""
    for table, col, ddl in (("convs", "agent", "TEXT NOT NULL DEFAULT ''"),):
        if col not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
            try:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass  # another process added it first (duplicate column)


def _names(v) -> list[str]:
    return [str(x) for x in (v or []) if isinstance(x, str)]


def _conv(settings: Settings, r: sqlite3.Row) -> Conv:
    from pm.qchat import run as qchat
    try:
        mcp, skills = _names(json.loads(r["mcp"])), _names(json.loads(r["skills"]))
    except ValueError:
        mcp, skills = [], []
    return Conv(id=r["id"], title=r["title"], session=r["session"], model=r["model"], profile=r["profile"],
                folder=r["folder"], mcp=mcp, skills=skills, agent=r["agent"] or "", created=r["created"], updated=r["updated"],
                msgs=r["n"], archived=bool(r["session"]) and not qchat.session_alive(settings, r["session"]))


_SEL = "SELECT c.*, (SELECT COUNT(*) FROM msgs m WHERE m.conv=c.id) AS n FROM convs c"


def create_conv(settings: Settings, *, profile: str, folder: str, model: str, mcp, skills, agent: str = "",
                title: str = "") -> int:
    title = mask(" ".join(str(title).split()))[:TITLE_MAX] or DEFAULT_TITLE
    now = time.time()
    con = open_db(settings)
    try:
        with con:
            cur = con.execute(
                "INSERT INTO convs(title,model,profile,folder,mcp,skills,agent,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                (title, model, profile, folder, json.dumps(_names(mcp)), json.dumps(_names(skills)), agent, now, now))
            return int(cur.lastrowid)
    finally:
        con.close()


def get_conv(settings: Settings, cid: int) -> Conv | None:
    con = open_db(settings)
    try:
        r = con.execute(_SEL + " WHERE c.id=?", (cid,)).fetchone()
    finally:
        con.close()
    return _conv(settings, r) if r else None


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_convs(settings: Settings, q: str = "", limit: int = 200) -> list[Conv]:
    con = open_db(settings)
    try:
        if q.strip():
            rows = con.execute(_SEL + " WHERE c.title LIKE ? ESCAPE '\\' ORDER BY c.updated DESC, c.id DESC LIMIT ?",
                               (_like(q.strip()), limit)).fetchall()
        else:
            rows = con.execute(_SEL + " ORDER BY c.updated DESC, c.id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        con.close()
    return [_conv(settings, r) for r in rows]


def rename_conv(settings: Settings, cid: int, title: str) -> bool:
    title = mask(" ".join(str(title).split()))[:TITLE_MAX]
    if not title:
        return False
    con = open_db(settings)
    try:
        with con:
            return con.execute("UPDATE convs SET title=? WHERE id=?", (title, cid)).rowcount > 0
    finally:
        con.close()


def delete_conv(settings: Settings, cid: int) -> bool:
    con = open_db(settings)
    try:
        with con:
            return con.execute("DELETE FROM convs WHERE id=?", (cid,)).rowcount > 0
    finally:
        con.close()


def delete_all(settings: Settings) -> int:
    con = open_db(settings)
    try:
        with con:
            return con.execute("DELETE FROM convs").rowcount
    finally:
        con.close()


def set_conv_opts(settings: Settings, cid: int, *, profile: str, model: str, mcp, skills) -> None:
    """Folder is deliberately not changeable here (locked once the conversation has a message)."""
    con = open_db(settings)
    try:
        with con:
            con.execute("UPDATE convs SET profile=?, model=?, mcp=?, skills=? WHERE id=?",
                        (profile, model, json.dumps(_names(mcp)), json.dumps(_names(skills)), cid))
    finally:
        con.close()


def set_session(settings: Settings, cid: int, session: str) -> None:
    con = open_db(settings)
    try:
        with con:
            con.execute("UPDATE convs SET session=? WHERE id=?", (session, cid))
    finally:
        con.close()


def _mask_tools(tools) -> list[dict]:
    out = []
    for t in tools or []:
        if isinstance(t, dict):
            out.append({k: (mask(v) if isinstance(v, str) else v) for k, v in t.items()})
    return out


def add_message(settings: Settings, cid: int, role: str, text: str, *, html: str = "", tools=None,
                cost: float | None = None, model: str = "") -> int:
    if role not in ("user", "assistant"):
        raise ValueError("invalid role")
    text, html = mask(text or ""), mask(html or "")
    now = time.time()
    con = open_db(settings)
    try:
        with con:
            first_user = role == "user" and not con.execute(
                "SELECT 1 FROM msgs WHERE conv=? AND role='user' LIMIT 1", (cid,)).fetchone()
            cur = con.execute("INSERT INTO msgs(conv,role,text,html,tools,cost,model,ts) VALUES(?,?,?,?,?,?,?,?)",
                              (cid, role, text, html, json.dumps(_mask_tools(tools)), cost, model, now))
            con.execute("UPDATE convs SET updated=? WHERE id=?", (now, cid))
            if first_user and " ".join(text.split()):  # a set title (aside, renamed) is kept
                con.execute("UPDATE convs SET title=? WHERE id=? AND title=?",
                            (" ".join(text.split())[:TITLE_MAX], cid, DEFAULT_TITLE))
            return int(cur.lastrowid)
    finally:
        con.close()


def messages(settings: Settings, cid: int) -> list[Msg]:
    con = open_db(settings)
    try:
        rows = con.execute("SELECT * FROM msgs WHERE conv=? ORDER BY id", (cid,)).fetchall()
    finally:
        con.close()
    out: list[Msg] = []
    for r in rows:
        try:
            tools = json.loads(r["tools"])
        except ValueError:
            tools = []
        out.append(Msg(id=r["id"], role=r["role"], text=r["text"], html=r["html"],
                       tools=tools if isinstance(tools, list) else [], cost=r["cost"], model=r["model"], ts=r["ts"]))
    return out


def _snippet(text: str, q: str, width: int = 80) -> str:
    flat = " ".join(text.split())
    i = flat.lower().find(q.lower())
    if i < 0:
        return mask(flat[:width * 2])
    a = max(0, i - width // 2)
    return mask(("…" if a else "") + flat[a:a + width * 2])


def search(settings: Settings, q: str, limit: int = 5) -> list[dict]:
    q = q.strip()
    if not q:
        return []
    pat = _like(q)
    con = open_db(settings)
    try:
        rows = con.execute(
            "SELECT c.id, c.title, c.updated, "
            "(SELECT m.text FROM msgs m WHERE m.conv=c.id AND m.text LIKE ? ESCAPE '\\' ORDER BY m.id LIMIT 1) AS hit "
            "FROM convs c WHERE c.title LIKE ? ESCAPE '\\' OR EXISTS "
            "(SELECT 1 FROM msgs m WHERE m.conv=c.id AND m.text LIKE ? ESCAPE '\\') "
            "ORDER BY c.updated DESC LIMIT ?", (pat, pat, pat, limit)).fetchall()
    finally:
        con.close()
    return [{"id": r["id"], "title": r["title"], "snippet": _snippet(r["hit"], q) if r["hit"] else "",
             "ts": r["updated"]} for r in rows]


def stats(settings: Settings) -> dict:
    p = _path(settings)
    con = open_db(settings)
    try:
        convs = con.execute("SELECT COUNT(*) FROM convs").fetchone()[0]
        msgs = con.execute("SELECT COUNT(*) FROM msgs").fetchone()[0]
    finally:
        con.close()
    return {"path": str(p), "size": p.stat().st_size if p.exists() else 0, "convs": convs, "msgs": msgs}


def prune(settings: Settings, keep: int, days: int) -> int:
    n = 0
    con = open_db(settings)
    try:
        with con:
            if days > 0:
                n += con.execute("DELETE FROM convs WHERE updated<?", (time.time() - days * 86400,)).rowcount
            if keep > 0:
                n += con.execute(
                    "DELETE FROM convs WHERE id NOT IN (SELECT id FROM convs ORDER BY updated DESC, id DESC LIMIT ?)",
                    (keep,)).rowcount
    finally:
        con.close()
    return n


def export_md(settings: Settings, cid: int) -> str | None:
    c = get_conv(settings, cid)
    if c is None:
        return None
    parts = [f"# {c['title']}", ""]
    for m in messages(settings, cid):
        parts += [f"**{'You' if m['role'] == 'user' else 'Claude'}**", "", m["text"].strip(), ""]
    return "\n".join(parts).rstrip() + "\n"
