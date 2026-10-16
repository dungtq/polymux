"""Fork an agent conversation into a new agent (a new background tmux window).

POST + CSRF + Origin + audit via ``mutation_guard`` (Tier.MESSAGE, same as resume). The original
agent is left untouched; see ``pm.agents.fork`` for the per-kind behaviour. The optional form field
``text`` is the fork's first message ("Fork and send"; ``agentctl.opt_text``).
"""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from pm.agents import fork as fork_mod
from pm.agents import launch as launch_mod
from pm.agents import registry
from pm.core import executors
from pm.core.errors import PmError
from pm.core.models import Tier
from pm.infra import snapshot as services
from pm.projects import projects
from pm.security.guard import mutation_guard
from pm.web import agentctl

router = APIRouter()


def _new_agent_link(settings, pane_id: str) -> str:
    """Link to the new agent's page when it is already detected (usually not yet)."""
    try:
        snap = services.published(settings)
        a = next((x for x in snap.agents if x.pane_id == pane_id), None) if snap and pane_id else None
    except Exception:  # noqa: BLE001
        a = None
    return f' <a href="/a/{escape(a.id)}">open</a>' if a else ""


def _forkable_kind(request: Request, action: str, settings, rec_id: str, src) -> str:
    """Normalised kind of ``src``; stops with 409 (audited) when this kind or session cannot be forked."""
    kind = fork_mod.norm_kind(src.kind)
    if fork_mod.fork_mode(kind) is None:
        raise agentctl.stop(request, action, rec_id, 409, "cannot fork", reason="unsupported kind",
                    detail={"kind": kind})
    try:
        fork_mod.build_argv(settings, src)
    except launch_mod.LaunchError as e:
        raise agentctl.stop(request, action, rec_id, 409, "cannot fork",
                    reason=str(e)[:80], detail={"kind": kind}) from None
    return kind


@router.post("/a/{agent_id}/fork", response_class=HTMLResponse)
async def agent_fork(request: Request, agent_id: str,
                     _=Depends(mutation_guard("agent_fork", Tier.MESSAGE))):
    action = "agent_fork"
    if not agentctl.AGENT_ID.fullmatch(agent_id):
        raise agentctl.stop(request, action, "", 404, "unknown agent")
    settings = request.app.state.settings
    rec = registry.get(settings, agent_id)
    if rec is None:
        raise agentctl.stop(request, action, agent_id, 404, "unknown agent")
    text = await agentctl.opt_text(request, action, rec.id)
    src = rec if rec.ended else await agentctl.agent(request, action, agent_id)
    kind = _forkable_kind(request, action, settings, rec.id, src)
    pname = src.project or rec.project
    project = next((p for p in projects.discover(settings) if p.name == pname), None)
    if project is None:
        raise agentctl.stop(request, action, rec.id, 404, "unknown project")
    try:
        res = await executors.run_tmux(fork_mod.fork_session, settings, src, project, text)
    except (launch_mod.LaunchError, PmError) as e:  # PmError: the parent's account left [accounts]
        raise agentctl.stop(request, action, rec.id, 400, "fork refused",
                    detail={"why": str(e)[:80], "kind": kind}) from None
    except Exception:  # noqa: BLE001
        raise agentctl.stop(request, action, rec.id, 502, "fork failed", "error",
                    detail={"kind": kind}) from None
    sid = (fork_mod.session_id(src, **registry.config_kw(settings, rec.id, kind)) or "")[:8]
    agentctl.done(request, action, rec.id, "ok", {"kind": kind, "mode": res.mode,
                                          "session": sid, "window": res.window_id, **agentctl.len_detail(text)})
    agentctl.publish(request, agent=rec.id, action=action)
    return agentctl.frag(f"Forked {rec.id} ({res.mode}) in {escape(res.window_name)}.",
                 extra=_new_agent_link(settings, res.pane_id) or
                 f' <span class="muted">{escape(res.window_name)}</span>')
