"""Plans UI: ``/p/{name}/plans`` (list + create) and ``/p/{name}/plans/{plan}`` (read, edit, rename,
delete). Files live in the agents' plan directories (~/.claude/plans, ~/.cursor/plans, filtered
to the project) and in ``<project>/plans/*.md`` (see ``pm.projects.plans``). The location is
the ``src`` query/form field (default ``project``); it is validated against a fixed allowlist.

The project NAME is validated against the registry and the path is derived server-side; plan
names are validated by ``plans.stem_of``. Every mutation is POST + CSRF + Origin + session +
tier + audit (the attempt line is written by ``mutation_guard`` and the request is refused with
503 when it cannot be written; the outcome line carries project, plan name and sizes, never the
text).
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from pm.core import executors
from pm.core.markdown_render import render_markdown
from pm.core.models import Tier
from pm.projects import backlog as rb
from pm.projects import plans
from pm.security.audit import AuditError
from pm.security.guard import audit_result, mutation_guard
from pm.web.deps import AUTH, project_sidebar, snapshot
from pm.web.render import html

router = APIRouter()

_NOSTORE = {"Cache-Control": "no-store"}


async def _project(request: Request, name: str):
    snap = await snapshot(request)
    project = snap.project(name)
    if project is None:
        raise HTTPException(404, "unknown project")
    return snap, project


def _page(request, snap, project, template: str, status: int = 200, **ctx) -> Response:
    base = "/p/" + quote(project.name)
    embed = bool(ctx.pop("embed", False))  # right-hand drawer iframe on the project page: framable, no chrome
    return html(request, template, status=status, nav="projects", project=project, embed=embed,
                 sidebar=None if embed else project_sidebar(snap, project.name), base=base,
                 max_kb=plans.MAX_BYTES // 1024, **ctx)


def _src(v) -> str:
    v = str(v or "project")
    if v not in plans.SOURCES:
        raise HTTPException(404, "no such plan location")
    return v


def _form_src(v) -> str:
    """Default location of the New-plan form; read-only locations fall back to claude."""
    v = _src(v or "claude")
    return "claude" if v in plans.READONLY else v


def _q(src: str, first: str = "?") -> str:
    return "" if src == "project" else f"{first}src={src}"


def _bad_name(name: str) -> str:
    try:
        return plans.stem_of(name)
    except plans.PlanError:
        raise HTTPException(404, "no such plan") from None


# ------------------------------------------------------------------ pages
@router.get("/p/{name}/plans", response_class=HTMLResponse, dependencies=AUTH)
async def plans_page(request: Request, name: str) -> Response:
    snap, project = await _project(request, name)
    show_all = request.query_params.get("all") == "1"
    items = await executors.run_collectors(plans.list_plans, project.path, show_all)
    return _page(request, snap, project, "plans.html", embed=request.query_params.get("embed") == "1",
                 items=items, show_all=show_all,
                 sources=plans.SOURCES, form_src=_form_src(request.query_params.get("src")),
                 error=request.query_params.get("err", "")[:200], form_name="", form_text="",
                 notice=request.query_params.get("ok", "")[:20])


@router.get("/p/{name}/plans/{plan}", response_class=HTMLResponse, dependencies=AUTH)
async def plan_page(request: Request, name: str, plan: str) -> Response:
    snap, project = await _project(request, name)
    stem = _bad_name(plan)
    src = _src(request.query_params.get("src"))
    try:
        text, token, mtime = await executors.run_collectors(plans.read_plan, project.path, stem, src)
    except plans.PlanError as e:
        raise HTTPException(e.status, e.msg) from None
    embed = request.query_params.get("embed") == "1"
    edit = request.query_params.get("edit") == "1" and not embed and src not in plans.READONLY
    return _page(request, snap, project, "plan.html", embed=embed, plan=stem, src=src, qs=_q(src), label=plans.SOURCES[src],
                 readonly=src in plans.READONLY, token=token, text=text, html=render_markdown(text, plans.MAX_BYTES), edit=edit, diff="",
                 error=request.query_params.get("err", "")[:200],
                 size=len(text.encode()), mtime=mtime,
                 notice=request.query_params.get("ok", "")[:20])


# ------------------------------------------------------------------ mutations
async def _run(request: Request, name: str, action: str, fn, detail=None):
    """Registry check -> fn(settings, project_path) in a worker -> audit. Returns fn's result.
    PlanError is audited as denied and re-raised; success audits ``detail(result)``."""
    settings = request.app.state.settings
    try:
        known = await executors.run_collectors(rb.registry, settings)
    except OSError:
        raise HTTPException(502, "registry unavailable") from None
    if name not in known:
        _audit(request, action, "denied", {"project": name[:60], "reason": "unknown project"})
        raise HTTPException(404, "unknown project")
    try:
        res = await executors.run_collectors(fn, settings, known[name])
    except plans.PlanError as e:
        _audit(request, action, "denied", {"project": name, "reason": e.msg[:60]})
        raise
    except OSError:
        try:
            _audit(request, action, "error", {"project": name, "reason": "io"})
        except HTTPException:
            pass
        raise HTTPException(502, "could not write the plan") from None
    _audit(request, action, "ok", {"project": name, **(detail(res) if detail else {})})
    return res


def _audit(request: Request, action: str, result: str, detail: dict) -> None:
    try:
        audit_result(request, action, "plan", result, detail)
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None


def _redirect(url: str) -> Response:
    return RedirectResponse(url, status_code=303, headers=_NOSTORE)


@router.post("/p/{name}/plans/new", response_class=HTMLResponse)
async def plan_new(request: Request, name: str,
                   _=Depends(mutation_guard("plan_create", Tier.SAFE))) -> Response:
    form = await request.form()
    pname, text = str(form.get("name", "")), str(form.get("text", ""))
    src = _src(form.get("src"))
    embed = form.get("embed") == "1"   # the project page's Plans popup (iframe)
    try:
        stem, n = await _run(request, name, "plan_create",
                             lambda s, p: plans.create_plan(s, p, pname, text, src),
                             lambda r: {"plan": r[0], "bytes": r[1], "src": src})
    except plans.PlanError as e:
        if e.status in (400, 409, 413):
            snap, project = await _project(request, name)
            items = await executors.run_collectors(plans.list_plans, project.path)
            return _page(request, snap, project, "plans.html", status=e.status, embed=embed,
                         items=items, show_all=False, sources=plans.SOURCES, form_src=_form_src(src),
                         error=e.msg,
                         form_name=pname[:80], form_text=text[:plans.MAX_BYTES], notice="")
        raise HTTPException(e.status, e.msg) from None
    return _redirect(f"/p/{quote(name)}/plans/{quote(stem)}?ok=created{_q(src, '&')}{'&embed=1' if embed else ''}")


@router.post("/p/{name}/plans/{plan}/save", response_class=HTMLResponse)
async def plan_save(request: Request, name: str, plan: str,
                    _=Depends(mutation_guard("plan_save", Tier.SAFE))) -> Response:
    form = await request.form()
    stem = _bad_name(plan)
    text, token = str(form.get("text", "")), str(form.get("token", ""))
    src = _src(form.get("src"))
    try:
        await _run(request, name, "plan_save",
                   lambda s, p: plans.save_plan(s, p, stem, text, token, src),
                   lambda r: {"plan": stem, "src": src, "bytes_before": r[0], "bytes_after": r[1]})
    except plans.PlanError as e:
        if e.status in (400, 409, 413):
            snap, project = await _project(request, name)
            cur = e.current.decode("utf-8", "replace") if e.current is not None else None
            return _page(request, snap, project, "plan.html", status=e.status, plan=stem,
                         src=src, qs=_q(src), label=plans.SOURCES[src],
                         token=plans.token_of(e.current) if e.current is not None else token,
                         text=text, html="", edit=True, error=e.msg, notice="",
                         diff=rb.diff_text(text, cur) if cur is not None else "",
                         size=len(text.encode()), mtime=None)
        raise HTTPException(e.status, e.msg) from None
    return _redirect(f"/p/{quote(name)}/plans/{quote(stem)}?ok=saved{_q(src, '&')}")


@router.post("/p/{name}/plans/{plan}/rename", response_class=HTMLResponse)
async def plan_rename(request: Request, name: str, plan: str,
                      _=Depends(mutation_guard("plan_rename", Tier.SAFE))) -> Response:
    form = await request.form()
    stem = _bad_name(plan)
    new = str(form.get("new_name", ""))
    src = _src(form.get("src"))
    base = f"/p/{quote(name)}"
    try:
        sn = await _run(request, name, "plan_rename",
                        lambda s, p: plans.rename_plan(s, p, stem, new, src),
                        lambda r: {"plan": stem, "new": r, "src": src})
    except plans.PlanError as e:
        if e.status in (400, 409):
            return _redirect(f"{base}/plans/{quote(stem)}?err={quote(e.msg[:150])}{_q(src, '&')}")
        raise HTTPException(e.status, e.msg) from None
    return _redirect(f"{base}/plans/{quote(sn)}?ok=renamed{_q(src, '&')}")


@router.post("/p/{name}/plans/{plan}/delete", response_class=HTMLResponse)
async def plan_delete(request: Request, name: str, plan: str,
                      _=Depends(mutation_guard("plan_delete", Tier.SAFE))) -> Response:
    form = await request.form()
    stem = _bad_name(plan)
    base = f"/p/{quote(name)}"
    src = _src(form.get("src"))
    if form.get("confirm") != "1":
        _audit(request, "plan_delete", "denied", {"project": name[:60], "reason": "no confirm"})
        raise HTTPException(400, "delete needs confirmation")
    try:
        await _run(request, name, "plan_delete",
                   lambda s, p: plans.delete_plan(s, p, stem, src),
                   lambda r: {"plan": stem, "src": src, "bytes": r})
    except plans.PlanError as e:
        raise HTTPException(e.status, e.msg) from None
    return _redirect(f"{base}/plans?ok=deleted")
