"""Request-scoped helpers shared by the route modules: auth dependency, settings, snapshot, sidebar."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import Depends, HTTPException, Request

from pm.agents.lineage import AGENT_ID_RE
from pm.core import executors
from pm.core.config import Settings
from pm.core.models import Sidebar, SideRow
from pm.infra import snapshot as services
from pm.security.auth import require_session

AUTH = [Depends(require_session)]  # every page route; /sw.js alone is public


def _dot_for(states) -> str:
    """Sidebar dot of a group of agents: waiting > running > idle; '' = none."""
    vals = {s.value for s in states}
    if not vals:
        return ""
    for k in ("waiting", "failed", "running"):
        if k in vals:
            return k
    return "idle"


async def side_unread(request: Request, snap: services.Snapshot, kind: str) -> dict:
    """Unread ntfy counts per sidebar row key ("projects" -> by name)."""
    from pm.notify import sidebadges

    st = settings(request)
    return await executors.run_collectors(sidebadges.by_project, st, snap.projects)


def project_dots(snap: services.Snapshot) -> dict[str, str]:
    """Sidebar dot per project name ('' = no agent); the server render and the live refresh share it."""
    by_id = {a.id: a for a in snap.agents}
    return {p.name: _dot_for(by_id[i].state for i in p.agent_ids if i in by_id) for p in snap.projects}


def project_sidebar(snap: services.Snapshot, current: str, unread: dict | None = None) -> Sidebar:
    """Left sidebar of the project pages: every project, the current one marked server-side."""
    by_id = {a.id: a for a in snap.agents}
    dots = project_dots(snap)
    rows = []
    for p in snap.projects:
        ags = [by_id[i] for i in p.agent_ids if i in by_id]
        rows.append(SideRow(p.name, "/p/" + quote(p.name), p.name, dots.get(p.name, ""),
                            str(len(ags)) if ags else "", bool(p.sessions or p.pane_ids or ags),
                            (unread or {}).get(p.name, 0)))
    return Sidebar("Projects", tuple(rows), current, "", "", "Filter projects", True)


def is_navigation(request: Request) -> bool:
    """True for a top-level browser navigation (address bar, plain link), not a pm.fetch/HTMX fragment load."""
    h = request.headers
    if h.get("sec-fetch-mode") == "navigate":
        return True
    return "x-requested-with" not in h and "hx-request" not in h


def settings(request: Request) -> Settings:
    return request.app.state.settings


async def snapshot(request: Request) -> services.Snapshot:
    """The poll loop's latest snapshot; a build happens only when none is fresh enough."""
    snap = await services.snapshot_async(settings(request))
    return services.merge_tracker(snap, getattr(request.app.state, "tracker", None))


def agent_or_404(snap: services.Snapshot, agent_id: str):
    agent = snap.agent(agent_id) if AGENT_ID_RE.fullmatch(agent_id) else None
    if agent is None:
        raise HTTPException(404, "unknown agent")
    return agent


def project_of(request: Request, name: str):
    """The discovered project called ``name``; 404 when unknown."""
    from pm.projects import projects
    for p in projects.discover(settings(request)):
        if p.name == name:
            return p
    raise HTTPException(404, "unknown project")
