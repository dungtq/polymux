"""Auto-exit of idle agents: settings section, dry-run preview, ended note.

The mutation (timeout) are POST + CSRF + Origin + audit via ``mutation_guard``. The
sweeper itself lives in ``autoexit.py``.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request, Response

from pm.agents import autoexit, registry
from pm.agents import head as agenthead
from pm.core.models import AgentState, Tier
from pm.infra import snapshot as services
from pm.security.guard import audit_result, mutation_guard
from pm.tmux.kill import KillRefused, _self_session_id
from pm.web import deps
from pm.web.deps import AGENT_ID_RE, AUTH
from pm.web.render import get_template, page_ctx
from pm.web.settings_panel import reply

router = APIRouter()


def _render(request: Request, name: str, **ctx) -> str:
    return get_template(name).render(**{**page_ctx(request), **ctx})


def _resp(html: str) -> Response:
    return Response(html, media_type="text/html", headers={"Cache-Control": "no-store"})


def _section(request: Request, notice: str = "", ok: bool = True) -> Response:
    secs = autoexit.idle_timeout_s(deps.settings(request))
    return reply(request, notice, ok, lambda: _render(request, "_autoexit.html", notice=notice, secs=secs,
                                                      hours="%g" % round(secs / 3600, 2)))


async def _snap(request: Request):
    s = deps.settings(request)
    return services.published(s) or await services.snapshot_async(s, max_age_s=30.0)


@router.get("/settings/autoexit", dependencies=AUTH)
async def section(request: Request) -> Response:
    return _section(request)


@router.get("/settings/autoexit/preview", dependencies=AUTH)
async def preview(request: Request) -> Response:
    s = deps.settings(request)
    snap = await _snap(request)
    try:
        own = _self_session_id(s, snap.sessions)
    except KillRefused:
        own = None
    cands = autoexit.plan(s, snap, own_session=own)
    idle_all = sum(1 for a in snap.agents if a.state is AgentState.IDLE)
    return _resp(_render(request, "_autoexit_preview.html", cands=cands, idle_all=idle_all,
                         secs=autoexit.idle_timeout_s(s), human=autoexit.human))


@router.post("/settings/autoexit", dependencies=[Depends(mutation_guard("autoexit_save", Tier.SAFE))])
async def save(request: Request) -> Response:
    form = await request.form()
    raw = str(form.get("hours", "")).strip()
    try:
        hours = float(raw)
        if not 0 <= hours <= 24 * 365:
            raise ValueError
    except ValueError:
        audit_result(request, "autoexit_save", "", "denied", {"why": "hours"})
        return _section(request, "Give hours between 0 and 8760 (0 = disabled).", ok=False)
    secs = autoexit.set_idle_timeout_s(deps.settings(request), int(hours * 3600))
    audit_result(request, "autoexit_save", "", "ok", {"idle_s": secs})
    return _section(request, "Auto-exit disabled." if secs == 0
                    else f"Saved: exit after {autoexit.human(secs)} idle.")


@router.get("/a/{agent_id}/autoexit", dependencies=AUTH)
async def ended_note(request: Request, agent_id: str) -> Response:
    settings = deps.settings(request)
    info = autoexit.info_for(settings, agent_id)
    rec = registry.get(settings, agent_id) if AGENT_ID_RE.fullmatch(agent_id) else None
    ident = (rec.ident if rec else "") or (info or {}).get("session") or ""
    model = (info or {}).get("model") or (agenthead.model_from_record_transcript(rec.transcript) if rec else "")
    facts = {"session": ident, "model": model, "window": (info or {}).get("window") or ""}
    return _resp(_render(request, "_autoexit_note.html", info=info, facts=facts, human=autoexit.human, now=time.time()))
