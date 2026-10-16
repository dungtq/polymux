"""Sidebar of the agent chat page (TMUX tree + ended-agent history), shared by the page routes."""

from __future__ import annotations

from posixpath import basename
from urllib.parse import quote

from pm.agents.head import pill_model
from pm.core import executors
from pm.core.models import Agent, Sidebar, SideHist, SideNode


def _leaf(cwd: str) -> str:
    return basename((cwd or "").rstrip("/"))


def _project_tree(snap, agent: Agent) -> tuple[SideNode, ...]:
    """Live agents of the viewed agent's project (any tmux session), one row each: window index + session name."""
    def mine(a: Agent) -> bool:
        return bool(a.project and a.project == agent.project) or (bool(a.cwd) and a.cwd == agent.cwd)

    by_pane = {a.pane_id: a for a in snap.agents if mine(a)}
    out: list[SideNode] = []
    for sess in sorted(snap.sessions, key=lambda s: s.name):
        for w in sess.windows:
            for p in w.panes:
                a = by_pane.get(p.id)
                if a:
                    out.append(SideNode("/a/" + quote(a.id), a.id, str(w.index), a.state.value, sess.name,
                                        f"{sess.name} {w.index}: {w.name} - {a.id}", False, a.id == agent.id,
                                        a.kind.value, True, model=pill_model(a.transcript)))
    return tuple(out)


def agent_dots(snap, agent: Agent) -> dict[str, str]:
    """Dot (agent state) per live agent id of the viewed agent's tmux tree, for the live sidebar refresh."""
    return {n.name: n.dot for n in _project_tree(snap, agent)}


def agent_sidebar_data(snap, settings, agent: Agent, recs, total: int) -> Sidebar:
    """Sidebar of the agent chat page: live agents of its project, HISTORY of ended agents of its project."""
    from pm.agents import registry
    from pm.agents.recap import human_duration
    from pm.web.render import urlid

    tree = _project_tree(snap, agent)
    hist = tuple(
        SideHist(r.id, r.id, r.kind, r.ended_at or r.last_seen,
                 human_duration((r.ended_at or r.last_seen) - r.first_seen)
                 if (r.ended_at or r.last_seen) > r.first_seen else "",
                 r.kind == "claude" and bool(registry.UUID_RE.fullmatch(r.ident or "")),
                 pill_model(r.transcript))
        for r in recs)
    session = snap.session(agent.session_id)
    all_href = "/tmux/" + urlid(session.id) if session is not None else "/tmux"
    return Sidebar("Windows", (), agent.id, all_href, "All sessions", "Filter windows, panes and history",
                   tmux=True, title=_leaf(agent.cwd) or "tmux", tree=tree,
                   hist=hist, hist_total=total, proj=_leaf(agent.cwd), proj_dir=agent.cwd)


async def agent_sidebar(snap, settings, agent: Agent, limit: int = 12) -> Sidebar:
    """The viewed agent stays in HISTORY once it has ended (its row is the current one, like in the ACTIVE tree)."""
    from pm.agents import registry

    recs, total = await executors.run_tmux(registry.ended_history, settings, project=agent.project,
                                           cwd=agent.cwd, limit=limit)
    return agent_sidebar_data(snap, settings, agent, recs, total)
