"""New project: POST /p/new (create a directory, Tier SAFE, audited), and the New project dialog's planner + runner:
GET /p/new/plan (spec in the query -> JSON steps) and POST /p/new/run (NDJSON stream of step results, one audit line
per step). Steps and their order: ``pm.projects.newproject``; plugin steps: ``plugins.new_project_steps``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from pm.agents import launch as launch_mod
from pm.core import executors
from pm.core.config import Settings
from pm.core.models import AgentKind, Launch, Project, Tier
from pm.infra import snapshot as services
from pm.projects import newproject, projects
from pm.security.audit import AuditError
from pm.security.guard import audit_result, mutation_guard, require_tier, tier_ok
from pm.web import plugins
from pm.web.deps import AUTH

router = APIRouter()
_NOSTORE = {"Cache-Control": "no-store"}
ACTION = "project_create"
_RESULT = {"done": "ok", "skipped": "ok", "failed": "error"}


def _audit(request: Request, result: str, detail: dict) -> None:
    try:
        audit_result(request, ACTION, "project", result, detail)
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None


@router.post("/p/new", response_class=HTMLResponse)
async def project_new(request: Request,
                      _=Depends(mutation_guard(ACTION, Tier.SAFE))) -> Response:
    form = await request.form()
    name = str(form.get("name", "")).strip()
    git_init, readme = form.get("git_init") == "1", form.get("readme") == "1"
    settings = request.app.state.settings
    try:
        await executors.run_collectors(projects.create_project, settings, name, git_init, readme)
    except projects.NewProjectError as e:
        _audit(request, "denied" if e.status < 500 else "error",
               {"name": name[:64], "reason": e.msg[:60]})
        raise HTTPException(e.status, e.msg) from None
    _audit(request, "ok", {"name": name, "git": int(git_init), "readme": int(readme)})
    try:  # make the new project visible to the redirect target at once
        await executors.run_snapshot(services.build_snapshot, settings, force=True)
    except Exception:  # noqa: BLE001 - the poll loop catches up
        pass
    if request.headers.get("hx-request"):
        return HTMLResponse("", headers={**_NOSTORE, "HX-Redirect": f"/p/{quote(name)}"})
    return RedirectResponse(f"/p/{quote(name)}", status_code=303, headers=_NOSTORE)


# ------------------------------------------------------------------ New project dialog: plan + run
def _fields(src) -> dict[str, str]:
    return {k: v for k, v in src.items() if isinstance(k, str) and isinstance(v, str)}


def _launch_first_agent(settings: Settings, spec: newproject.Spec, path: Path) -> str:
    """The ``agent`` step: the launch form's launch (same validation), in a new window of the project session."""
    a = spec.agent
    kind = AgentKind(a.kind)
    prompt, mode = launch_mod.apply_prompt_commands(kind, a.prompt, a.permission_mode)
    launch = Launch(project=spec.name, kind=kind, model=a.model, permission_mode=mode, prompt=prompt,
                    effort=a.effort, permission_extra=a.permission_extra)
    res = launch_mod.launch_agent(settings, launch, Project(name=spec.name, path=str(path)))
    return f"Launched {kind.value} in window {res.window_name} ({res.window_id})"


def _agent_refusal(request: Request, spec: newproject.Spec, fields: dict[str, str]) -> str:
    """"", or why the first agent may not start: it needs tier 2 (the run route is tier 1; the run itself refuses with
    ``require_tier``) and, for a bypass mode, the same confirm field as the launch form."""
    a = spec.agent
    if a is None:
        return ""
    if not tier_ok(request, Tier.MESSAGE):
        return "launching an agent needs tier 2 (tier_ceiling)"
    if launch_mod.is_bypass(AgentKind(a.kind), a.permission_mode) and fields.get("confirm_bypass") != "1":
        return "bypass mode not confirmed"
    return ""


def _steps_json(steps: list[newproject.Step]) -> list[dict]:
    return [{"id": s.id, "text": s.text, "kind": s.kind} for s in steps]


@router.get("/p/new/plan", dependencies=AUTH)
async def project_new_plan(request: Request) -> JSONResponse:
    """The dialog's live "What will happen" list: ``{ok, error, steps: [{id, text, kind}], templates: [{name,
    description}]}``; ``error`` = why a run would be refused now (bad name, exists, remote host, tier, ...)."""
    settings = request.app.state.settings
    fields = _fields(request.query_params)
    tpls = [{"name": t["name"], "description": t["description"]} for t in newproject.templates(settings)]
    try:
        spec = newproject.parse_spec(fields)
    except newproject.NewProjectError as e:
        return JSONResponse({"ok": False, "error": e.msg, "steps": [], "templates": tpls}, headers=_NOSTORE)
    extra = await executors.run_collectors(plugins.new_project_steps, plugins.of(request), spec)
    steps = newproject.plan(settings, spec, extra)
    error = _agent_refusal(request, spec, fields)
    try:
        await executors.run_collectors(newproject.check, settings, spec)
    except newproject.NewProjectError as e:
        error = e.msg
    return JSONResponse({"ok": not error, "error": error, "steps": _steps_json(steps), "templates": tpls},
                        headers=_NOSTORE)


def _line(d: dict) -> bytes:
    return (json.dumps(d, separators=(",", ":")) + "\n").encode()


def _pool(step: newproject.Step, plugin_ids: set[str]):
    """Where a step runs: plugin steps (compose up, VM create: may take minutes) in the bounded jobs pool, the first
    agent in the tmux pool like every other launch, the quick core steps (folder, files, git) in the collectors."""
    if step.id in plugin_ids:
        return executors.run_jobs
    return executors.run_tmux if step.id == "agent" else executors.run_collectors


async def _stream(request: Request, spec: newproject.Spec, steps: list[newproject.Step],
                  plugin_ids: set[str]) -> AsyncIterator[bytes]:
    """One NDJSON line per StepResult ``{id, ok, text, state}``, audited before it is sent (an audit failure stops
    the run before the next step), then ``{end: true, ok, project, url, done, failed, not_run}``."""
    done: list[str] = []
    not_run: list[str] = []
    failed = ""
    results = newproject.run_steps(request.app.state.settings, spec, steps)
    try:
        for step in steps:  # run_steps yields exactly one result per planned step, in order
            r = await _pool(step, plugin_ids)(next, results, None)
            if r is None:
                break
            if r.state in _RESULT:
                detail = {"name": spec.name, "step": r.id[:64], **({"reason": r.text[:60]} if not r.ok else {})}
                try:
                    audit_result(request, ACTION, "project", _RESULT[r.state], detail)
                except AuditError:
                    yield _line({"id": r.id, "ok": False, "text": "audit unavailable; stopped", "state": "failed"})
                    return
            if r.ok:
                done.append(r.id)
            elif r.state == "not_run":
                not_run.append(r.id)
            else:
                failed = r.id
            yield _line(asdict(r))
    finally:
        try:
            results.close()
        except ValueError:  # still running in a pool (client gone): it stops at its next yield
            pass
    try:  # make the new project visible at once
        await executors.run_snapshot(services.build_snapshot, request.app.state.settings, force=True)
    except Exception:  # noqa: BLE001 - the poll loop catches up
        pass
    yield _line({"end": True, "ok": not failed, "project": spec.name, "url": f"/p/{quote(spec.name)}",
                 "done": done, "failed": failed, "not_run": not_run})


async def _planned(request: Request, fields: dict[str, str]) -> tuple[newproject.Spec, list[newproject.Step], set[str]]:
    """Parse and check the spec (NewProjectError before any step; 403 ``require_tier`` for a first agent without
    tier 2), then plan it with the plugins' steps: (spec, steps, plugin step ids)."""
    settings = request.app.state.settings
    spec = newproject.parse_spec(fields)
    if spec.agent is not None:
        require_tier(request, ACTION, Tier.MESSAGE, "project")
    if why := _agent_refusal(request, spec, fields):
        raise newproject.NewProjectError(why, 400)
    extra = await executors.run_collectors(plugins.new_project_steps, plugins.of(request), spec)
    await executors.run_collectors(newproject.check, settings, spec)
    return spec, newproject.plan(settings, spec, extra, launch=_launch_first_agent), {s.id for s in extra}


@router.post("/p/new/run")
async def project_new_run(request: Request, _=Depends(mutation_guard(ACTION, Tier.SAFE))) -> Response:
    """Run the dialog's spec (form fields as for /p/new/plan). Refusals before the first step: HTTP 4xx (audited
    ``denied``); after that, every result streams as NDJSON (``application/x-ndjson``)."""
    fields = _fields(await request.form())
    try:
        spec, steps, plugin_ids = await _planned(request, fields)
    except newproject.NewProjectError as e:
        _audit(request, "denied" if e.status < 500 else "error",
               {"name": fields.get("name", "")[:64], "reason": e.msg[:60]})
        raise HTTPException(e.status, e.msg) from None
    return StreamingResponse(_stream(request, spec, steps, plugin_ids), media_type="application/x-ndjson",
                             headers=_NOSTORE)
