"""Issues of a tracker project (docs/TRACKER-PLAN.md section 6): the project page's Issues tab and issue
detail (fragments), add / done / reopen / edit / note / delete, and the composer's "Save as issue"
(``POST /a/{agent_id}/issue``). Shared helpers: ``pm.web.issuectx``; the images (``/tracker/img``) are served
next to ``/uploads`` in chat.py.

Project names are checked against the registry and must use the tracker (409 otherwise); the domain is
``pm.projects.tracker`` (it re-renders BACKLOG.md). Every mutation: ``mutation_guard(Tier.SAFE)`` (CSRF from
the form or the ``X-CSRF-Token`` header) and one result line with the project and issue number, never text:
"ok" is written by the store inside its transaction (``issuectx.audit_ok``; a failed write rolls the change
back, 503), a refusal by ``_refuse``. A script call (``X-Requested-With``) gets JSON ``{"ok": ...}``; a plain
form POST goes back to the page (303).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from pm.core import executors
from pm.core.errors import NotFound, PmError
from pm.core.models import Tier
from pm.projects.tracker import issues, store, views
from pm.security.audit import AuditError
from pm.security.guard import audit_result, mutation_guard
from pm.web import deps, issuectx
from pm.web.deps import AUTH, is_navigation, snapshot
from pm.web.issuectx import FILE
from pm.web.render import get_template, page_ctx

router = APIRouter()
_NOSTORE = {"Cache-Control": "no-store"}


# ------------------------------------------------------------------ form fields
def _str(form, key: str) -> str:
    v = form.get(key, "")
    return v if isinstance(v, str) else ""


def _paths(form) -> list[str]:
    return [p.strip() for p in _str(form, "images").splitlines() if p.strip()]


# ------------------------------------------------------------------ responses
def _audit(request: Request, action: str, result: str, detail: dict) -> None:
    try:
        audit_result(request, action, "issues", result, detail)
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None


def _refuse(request: Request, action: str, name: str, e: PmError, *, extra: dict | None = None,
            json_only: bool = False) -> Response:
    """Audit a refused mutation (project + reason), then answer the error (JSON for a script)."""
    _audit(request, action, "conflict" if e.status == 409 else "denied" if e.status < 500 else "error",
           {"project": name[:60], **(extra or {}), "reason": str(e)[:40]})
    if is_navigation(request) and not json_only:
        raise HTTPException(e.status, str(e))
    return JSONResponse({"ok": False, "error": str(e)}, status_code=e.status, headers=_NOSTORE)


def _done(request: Request, name: str, num: int, *, to_list: bool = False) -> Response:
    url = f"/p/{quote(name)}#issue={num}"
    if is_navigation(request):
        return RedirectResponse(f"/p/{quote(name)}#issues" if to_list else url, status_code=303, headers=_NOSTORE)
    return JSONResponse({"ok": True, "num": num, "project": name, "url": url}, headers=_NOSTORE)


async def _run(request: Request, action: str, name: str, fn: Callable, *args, extra: dict | None = None,
               json_only: bool = False):
    """``fn(*args)`` in the pool; a domain / DB / IO failure becomes an audited refusal (a Response), a failed
    audit write a 503 (the store rolled the change back)."""
    try:
        return await executors.run_collectors(fn, *args)
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None
    except PmError as e:
        return _refuse(request, action, name, e, extra=extra, json_only=json_only)
    except sqlite3.Error:
        return _refuse(request, action, name, PmError("tracker database busy", status=503), extra=extra,
                       json_only=json_only)
    except OSError:
        return _refuse(request, action, name, PmError("could not write the tracker", status=502), extra=extra,
                       json_only=json_only)


async def _mutate(request: Request, name: str, action: str, op: Callable[..., int], *,
                  to_list: bool = False) -> Response:
    """Check the project, run ``op(settings, form, audit) -> issue number`` in the pool (``audit`` writes the
    "ok" line inside the store's transaction), answer."""
    form = await request.form()
    settings = deps.settings(request)
    refused = await _run(request, action, name, views.require_tracker, settings, name)
    if isinstance(refused, Response):
        return refused
    num = await _run(request, action, name, op, settings, form,
                     issuectx.audit_ok(request, action, "issues", {"project": name}))
    return num if isinstance(num, Response) else _done(request, name, num, to_list=to_list)


async def _check_get(request: Request, name: str) -> None:
    try:
        await executors.run_collectors(views.require_tracker, deps.settings(request), name)
    except PmError as e:
        raise HTTPException(e.status, str(e)) from None


def _fragment(html: str) -> HTMLResponse:
    return HTMLResponse(html, headers=_NOSTORE)


# ------------------------------------------------------------------ fragments
@router.get("/p/{name}/issues", response_class=HTMLResponse, dependencies=AUTH)
async def issues_tab(request: Request, name: str) -> Response:
    """The Issues tab body (add box, Open/Done, rows); a page navigation opens the tab instead."""
    if is_navigation(request):
        return RedirectResponse(f"/p/{quote(name)}#issues", status_code=303)
    await _check_get(request, name)
    s = await executors.run_collectors(views.issues_summary, deps.settings(request), name)
    return _fragment(get_template("_issues.html").render(
        iss=s, pname=name, csrf=page_ctx(request)["csrf"]))


@router.get("/p/{name}/issues/{num}", response_class=HTMLResponse, dependencies=AUTH)
async def issue_detail(request: Request, name: str, num: int) -> Response:
    """One issue for the Issues tab; a page navigation lands on the project page with it open."""
    if is_navigation(request):
        return RedirectResponse(f"/p/{quote(name)}#issue={num}", status_code=303)
    snap = await snapshot(request)
    await _check_get(request, name)
    try:
        d = await executors.run_collectors(issues.detail, deps.settings(request), name, num)
    except PmError as e:
        raise HTTPException(e.status, str(e)) from None
    it = d["item"]
    return _fragment(get_template("_issue_detail.html").render(
        d=d, it=it, pname=name, csrf=page_ctx(request)["csrf"],
        send=issuectx.send_links(snap.agents, name, it.num, it.title_short)))


# ------------------------------------------------------------------ mutations
@router.post("/p/{name}/issues")
async def issue_add(request: Request, name: str, _=Depends(mutation_guard("issue_add", Tier.SAFE))) -> Response:
    return await _mutate(request, name, "issue_add", lambda s, f, a: issues.create_issue(
        s, name, _str(f, "title"), _str(f, "body"), _paths(f), audit=a).num)


@router.post("/p/{name}/issues/{num}/done")
async def issue_done(request: Request, name: str, num: int,
                     _=Depends(mutation_guard("issue_done", Tier.SAFE))) -> Response:
    return await _mutate(request, name, "issue_done",
                         lambda s, f, a: store.set_done(s, name, FILE, num, True, audit=a).num)


@router.post("/p/{name}/issues/{num}/reopen")
async def issue_reopen(request: Request, name: str, num: int,
                       _=Depends(mutation_guard("issue_reopen", Tier.SAFE))) -> Response:
    return await _mutate(request, name, "issue_reopen",
                         lambda s, f, a: store.set_done(s, name, FILE, num, False, audit=a).num)


@router.post("/p/{name}/issues/{num}/edit")
async def issue_edit(request: Request, name: str, num: int,
                     _=Depends(mutation_guard("issue_edit", Tier.SAFE))) -> Response:
    def op(s, f, a) -> int:
        body = f.get("body")
        return store.edit_item(s, name, FILE, num, title=_str(f, "title"),
                               body=body if isinstance(body, str) else None, audit=a).num
    return await _mutate(request, name, "issue_edit", op)


@router.post("/p/{name}/issues/{num}/note")
async def issue_note(request: Request, name: str, num: int,
                     _=Depends(mutation_guard("issue_note", Tier.SAFE))) -> Response:
    def op(s, f, a) -> int:
        issues.add_note(s, name, num, _str(f, "body"), audit=a)
        return num
    return await _mutate(request, name, "issue_note", op)


@router.post("/p/{name}/issues/{num}/delete")
async def issue_delete(request: Request, name: str, num: int,
                       _=Depends(mutation_guard("issue_delete", Tier.SAFE))) -> Response:
    def op(s, f, a) -> int:
        store.delete_item(s, name, FILE, num, audit=a)
        return num
    return await _mutate(request, name, "issue_delete", op, to_list=True)


@router.post("/p/{name}/issues/{num}/move")
async def issue_move(request: Request, name: str, num: int,
                     _=Depends(mutation_guard("issue_move", Tier.SAFE))) -> Response:
    """Move the issue to the project ``to`` (a registered tracker project); answers with its new place."""
    form = await request.form()
    settings, dst = deps.settings(request), _str(form, "to")[:64]
    refused = await _run(request, "issue_move", name, _check_move, settings, name, dst, extra={"to": dst})
    if isinstance(refused, Response):
        return refused
    audit = issuectx.audit_ok(request, "issue_move", "issues", {"project": name, "to": dst}, keep=("num", "new_num"))
    it = await _run(request, "issue_move", name, lambda: store.move_item(settings, name, dst, num, audit=audit),
                    extra={"to": dst})
    return it if isinstance(it, Response) else _done(request, dst, it.num)


def _check_move(settings, name: str, dst: str) -> None:
    views.require_tracker(settings, name)
    views.require_tracker(settings, dst)


@router.post("/a/{agent_id}/issue")
async def agent_issue(request: Request, agent_id: str,
                      _=Depends(mutation_guard("issue_add", Tier.SAFE))) -> Response:
    """Composer "Save as issue": first line = title, rest = body, ``images`` = upload paths; the issue goes
    to the agent's project. Never sent to the agent. Always JSON."""
    form = await request.form()
    snap = await snapshot(request)
    agent = snap.agent(agent_id) if deps.AGENT_ID_RE.fullmatch(agent_id) else None
    if agent is None:
        return _refuse(request, "issue_add", "", NotFound("unknown agent"), extra={"agent": agent_id[:40]},
                       json_only=True)
    settings, paths, who = deps.settings(request), _paths(form), {"agent": agent.id}
    title, body = issues.split_text(_str(form, "text"))
    name = await _run(request, "issue_add", agent.project or "", issues.project_for_cwd, settings, agent.cwd,
                      extra=who, json_only=True)
    if isinstance(name, Response):
        return name
    audit = issuectx.audit_ok(request, "issue_add", "issues", {"project": name, **who, "images": len(paths)})
    it = await _run(request, "issue_add", name, lambda: issues.create_issue(settings, name, title, body, paths,
                                                                            audit=audit), extra=who, json_only=True)
    if isinstance(it, Response):
        return it
    return JSONResponse({"ok": True, "num": it.num, "project": name, "url": f"/p/{quote(name)}#issue={it.num}"},
                        headers=_NOSTORE)
