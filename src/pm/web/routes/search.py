"""Global navbar search: names (projects, tmux sessions/windows, agent sessions), then, for q >= 3 chars,
content: chat transcripts and project files. HTML fragment, read-only; snippets are masked, hits never logged."""
from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import APIRouter, Request

from pm.agents import registry
from pm.agents.head import pill_model
from pm.chat import live as chat
from pm.chat import log as chatlog
from pm.core import executors
from pm.projects import projfiles
from pm.qchat import cfg as qchatcfg
from pm.qchat import db as qchatdb
from pm.web import deps
from pm.web.deps import AUTH, snapshot
from pm.web.render import fragment, get_template, urlid

router = APIRouter()

MAX_PER_GROUP = 8
MAX_Q = 80
MAX_CONTENT = 5  # per content group
MIN_CONTENT_Q = 3
FILES_BUDGET_S = 1.5  # all projects together


@dataclass(frozen=True)
class Hit:
    href: str
    name: str
    sub: str = ""
    kind: str = ""  # agent hits: shown as the agent-name macro (id + model pill)
    model: str = ""
    snippet: bool = False  # content hits: name is a path/id, sub a masked excerpt


def _match(q: str, *fields: str) -> bool:
    return any(q in (f or "").lower() for f in fields)


def _task(agent) -> str:
    try:
        return " ".join(chat.last_user_task(agent).split())[:90]
    except Exception:  # noqa: BLE001 - a title is optional
        return ""


def search_hits(snap, ended_rows, q: str) -> list[tuple[str, str, list[Hit]]]:
    """[(group key, group title, hits)] for a lower-cased query; empty groups dropped, <= MAX_PER_GROUP each."""
    projects = [Hit("/p/" + quote(p.name), p.name, p.path) for p in snap.projects if _match(q, p.name)]
    tmux: list[Hit] = []
    for s in snap.sessions:
        sid = "/tmux/" + urlid(s.id)
        if _match(q, s.name):
            tmux.append(Hit(sid, s.name, f"{len(s.windows)} windows"))
        for w in s.windows:
            if _match(q, w.name):
                tmux.append(Hit(sid, w.name, f"{s.name}:{w.index}"))
    agents: list[Hit] = []
    for a in snap.agents:
        task = _task(a) if len(agents) < MAX_PER_GROUP * 4 else ""
        if _match(q, a.id, a.label, a.window_name, a.project or "", task):
            agents.append(Hit("/a/" + quote(a.id), a.id, " - ".join(x for x in (a.project or "", task) if x),
                              a.kind.value, pill_model(a.transcript)))
    ended: list[Hit] = []
    for r in ended_rows:
        if _match(q, r.id, r.label, r.project or ""):
            ended.append(Hit("/a/" + quote(r.id), r.id, r.project or r.label, r.kind, pill_model(r.transcript)))
    groups = [("project", "Projects", projects), ("tmux", "tmux", tmux), ("agent", "Agents", agents),
              ("ended", "Ended agents", ended)]
    return [(k, t, h[:MAX_PER_GROUP]) for k, t, h in groups if h]


def _ago(ts: float) -> str:
    if not ts:
        return ""
    d = max(0, int(time.time() - ts))
    return "now" if d < 60 else f"{d // 60}m" if d < 3600 else f"{d // 3600}h" if d < 86400 else f"{d // 86400}d"


def chat_hits(settings, q: str) -> list[Hit]:
    """Transcript hits (newest first); the link is the agent page (the ended-session page for ended agents)."""
    out: list[Hit] = []
    for m in chatlog.search(settings, q, MAX_CONTENT):
        who = "you" if m["role"] == "user" else "agent"
        out.append(Hit("/a/" + quote(m["agent_id"]), m["agent_id"],
                       " - ".join(x for x in (who, _ago(m["ts"]), m["snippet"]) if x), snippet=True))
    return out


def qchat_hits(settings, q: str) -> list[Hit]:
    """Quick chat history hits (qchat.db, masked snippets); empty when switched off in Settings."""
    try:
        if not qchatcfg.load(settings)["nav_search"]:
            return []
        rows = qchatdb.search(settings, q, MAX_CONTENT)
    except Exception:  # noqa: BLE001 - a broken history DB must not break the navbar search
        return []
    return [Hit(f"/?qchat={int(r['id'])}", r["title"] or "Quick chat",
                " - ".join(x for x in (_ago(r["ts"]), r["snippet"]) if x), snippet=True) for r in rows]


def file_hits(projects, q: str) -> list[Hit]:
    """Project file hits: first matching line per file, bounded by MAX_CONTENT and a shared time budget."""
    out: list[Hit] = []
    end = time.monotonic() + FILES_BUDGET_S
    for p in projects:
        left = end - time.monotonic()
        if left <= 0 or len(out) >= MAX_CONTENT:
            break
        for h in projfiles.grep_project(p.path, q, MAX_CONTENT - len(out), left):
            out.append(Hit(f"/p/{quote(p.name)}/files?path={quote(h['rel'])}", f"{p.name}/{h['rel']}:{h['line']}",
                           h["snippet"], snippet=True))
    return out


@router.get("/search", dependencies=AUTH)
async def search(request: Request):
    q = " ".join(request.query_params.get("q", "").split())[:MAX_Q].lower()
    groups: list = []
    if q:
        snap = await snapshot(request)
        live = {i for a in snap.agents for i in (a.ident, a.session_uuid) if i}
        rows, _ = await executors.run_collectors(
            lambda: registry.ended_sessions(deps.settings(request), live_idents=live, limit=200))
        groups = search_hits(snap, rows, q)
        if len(q) >= MIN_CONTENT_Q:
            st = deps.settings(request)
            chats, files, qchats = await executors.run_collectors(
                lambda: (chat_hits(st, q), file_hits(snap.projects, q), qchat_hits(st, q)))
            groups += [(k, t, h) for k, t, h in (("chat", "Chats", chats), ("qchat", "Quick chat", qchats),
                                                  ("file", "Files", files)) if h]
    return fragment(request, get_template("_search.html").render(groups=groups, q=q))
