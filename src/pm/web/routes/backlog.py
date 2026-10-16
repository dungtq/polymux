"""Per-project backlogs: ``<project path>/BACKLOG.md`` edited from the web.

Routes take a project NAME, validated against the registry (``projects.discover``); the
path is derived server-side, never taken from the client. A symlinked BACKLOG.md is
refused. The file is the source of truth. Every write is atomic (temp file + rename), capped
at 256 KB, and refused when the file changed since the page was loaded (the form
carries a content hash). Audit details carry sizes and the action, never the text.

Concurrency: every read-modify-write runs under ``filelock.locked`` (OS flock on a lock file
in ``state_dir/locks/``, see filelock.py) and re-verifies the file bytes right before the
atomic replace. Item operations (toggle/add) apply to the CURRENT content, so unrelated edits
by agents never break them. Full-file saves keep the token check, show a diff on conflict and
honour an edit lease (people editing at the same time).

Tracker projects keep BACKLOG.md in pm.db: add / toggle / edit go to the store
(``line`` is the issue number) and the whole-file editor is refused (409).
"""

from __future__ import annotations

import sqlite3
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from pm.core import executors, filelock
from pm.core.errors import Conflict, Invalid, PmError
from pm.core.models import Tier
from pm.infra import snapshot as services
from pm.projects.backlog import (
    MAX_BYTES,
    MAX_ITEM,
    TOKEN,
    Bad,
    build_op,
    counts,
    diff_text,
    lookup,
    mutate,
    norm_content,
    read,
    registry,
    resolve_path,
    token_of,
)
from pm.projects.backlog import since_label as _since
from pm.projects.tracker import store, views
from pm.security.audit import AuditError
from pm.security.guard import audit_result, client_of, mutation_guard
from pm.web import issuectx
from pm.web.deps import AUTH
from pm.web.issuectx import audit_ok, num_of
from pm.web.listing import clean_q
from pm.web.render import html

router = APIRouter()


TRACKED = "{} uses the issue tracker: edit items one by one (or with pmctl)"


def _label(request: Request) -> str:
    return client_of(request)


def _page(request: Request, status: int = 200, *, project: str = "", edit: bool = False,
          error: str = "", content: str | None = None, token: str | None = None,
          back: str = "", lease: str = "", diff: str = "", new_token: str = "",
          done: bool = False) -> HTMLResponse:
    settings = request.app.state.settings
    ctx = dict(nav="backlog", drawer=True, error=error, max_kb=MAX_BYTES // 1024, max_item=MAX_ITEM,
               edit=False, project=project, back=back, cancel=_back(back, project),
               embed=back == "embed", done=done,
               lease_id="", lease_other=None, diff=diff, new_token=new_token)
    if edit and project and views.is_tracker(settings, project, "backlog"):
        edit, ctx["error"], status = False, TRACKED.format(project), 409
    if edit and project:
        try:
            path = lookup(settings, project)
            data = read(path)
        except Bad as e:
            return html(request, "backlog.html", status=e.status,
                         **{**ctx, "error": e.msg, "entries": [], "total_open": 0})
        live = filelock.lease_get(settings.state_dir, path)
        if live and live["id"] == lease:
            ctx["lease_id"] = live["id"]
        elif live:
            ctx["lease_other"] = {"owner": live["owner"], "since": _since(live["since"])}
        text = data.decode("utf-8", "replace")
        ctx.update(edit=True, token=token or token_of(data),
                   content=text if content is None else content, open_n=counts(text)[0])
        return html(request, "backlog.html", status=status, **ctx)
    entries = views.entries(settings)
    return html(request, "backlog.html", status=status, entries=entries,
                 total_open=sum(e["open_n"] for e in entries), **ctx, **_lists(request, settings))


def _int(v: str | None, default: int) -> int:
    try:
        return int(v or default)
    except ValueError:
        return default


def _lists(request: Request, settings) -> dict:
    """Cross-project lists for themes (classic does not use them): ``issue_list`` (``views.issue_rows``: one
    page of issues, every row with ``agent`` = {id, state} of a live agent dispatched to it, else None) and
    ``todo_list`` (``views.todo_groups``). Query: ``status``, ``project``, ``q``, ``limit``, ``offset`` for the
    issues, ``todo`` (open|done|all) for the TODO groups."""
    qp = request.query_params
    project = qp.get("project", "")[:64] or None
    try:
        issues = views.issue_rows(settings, status=qp.get("status", "open"), project=project, q=clean_q(request),
                                  limit=_int(qp.get("limit"), views.LIST_LIMIT), offset=_int(qp.get("offset"), 0))
        todos = views.todo_groups(settings, status=qp.get("todo", "open"), project=project)
    except sqlite3.Error:  # the page itself still renders; a theme shows the lists as unavailable
        return {"issue_list": None, "todo_list": None}
    snap = services.published(settings)
    on = issuectx.agents_on(settings, snap.agents if snap is not None else (), issues["rows"])
    for r in issues["rows"]:
        r["agent"] = on.get((r["project"], r["num"]))
    return {"issue_list": issues, "todo_list": todos}


@router.get("/backlog", response_class=HTMLResponse, dependencies=AUTH)
async def backlog_page(request: Request, p: str = "", edit: int = 0, back: str = "",
                       lease: str = "", embed: int = 0, done: int = 0) -> Response:
    if p or edit:
        if p not in registry(request.app.state.settings):
            raise HTTPException(404, "unknown project")
    back = "embed" if embed else back   # the project page's Backlog tab edits in an iframe
    resp = await executors.run_collectors(_page, request, 200, project=p, edit=bool(edit and p),
                                          back=back if back in ("project", "embed") else "",
                                          lease=lease[:32], done=bool(done and embed and p))
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ------------------------------------------------------------------ mutations
def _back(back: str, name: str) -> str:
    """Where Cancel/Save/Add return to: an allow-list, never a client-supplied URL."""
    if back == "project":
        return f"/p/{quote(name)}#backlog"
    if back == "embed":   # the framed editor tells the project page to reload (static/js/backlog.js)
        return f"/backlog?p={quote(name)}&embed=1&done=1"
    return f"/backlog#bl-{quote(name)}" if name else "/backlog"


def _str(form, key: str) -> str:
    v = form.get(key, "")
    return v if isinstance(v, str) else ""


async def _known(request: Request, settings, name: str, action: str) -> dict:
    try:
        known = await executors.run_collectors(registry, settings)
    except OSError:
        raise HTTPException(502, "registry unavailable") from None
    if name not in known:
        try:
            audit_result(request, action, "backlog", "denied", {"reason": "unknown project"})
        except AuditError:
            raise HTTPException(503, "audit unavailable") from None
        raise HTTPException(404, "unknown project")
    return known


def _audit(request: Request, action: str, result: str, detail: dict) -> None:
    try:
        audit_result(request, action, "backlog", result, detail)
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None


async def _conflict_response(request: Request, action: str, fn_name: str, form, e: Bad, *, name: str,
                             back: str, lease_id: str) -> Response:
    """Audit a refused write and re-render the page with the error; a refused save keeps the text (+ diff)."""
    _audit(request, action, "conflict" if e.status == 409 else "denied",
           {"project": name, "reason": e.msg[:40]})
    keep = fn_name == "save"
    diff, new_tok = "", ""
    if keep and e.current is not None:
        diff = diff_text(norm_content(str(form.get("content", ""))),
                         e.current.decode("utf-8", "replace"))
        new_tok = token_of(e.current)
    return await executors.run_collectors(
        _page, request, e.status, project=name, edit=keep, error=e.msg,
        content=str(form.get("content", "")) if keep else None,
        token=(_str(form, "token") if TOKEN.match(_str(form, "token")) else None) if keep else None,
        back=back if back in ("project", "embed") else "", lease=lease_id, diff=diff,
        new_token=new_tok)


async def _act(request: Request, action: str, fn_name: str) -> Response:
    form = await request.form()
    settings = request.app.state.settings
    name = _str(form, "project")
    lease_id = _str(form, "lease")[:32]
    force = fn_name == "save" and _str(form, "force") == "1"
    back = _str(form, "back")
    back = back if back in ("project", "embed") else "group"
    known = await _known(request, settings, name, action)
    if await executors.run_collectors(views.is_tracker, settings, name, "backlog"):
        return await _tracker_act(request, action, fn_name, form, name, back)
    detail = {"project": name}
    path = None
    try:
        path = resolve_path(known[name])
        fn, pre, token = build_op(fn_name, form, settings=settings, path=path, lease_id=lease_id)
        old, new = await executors.run_collectors(
            lambda: mutate(settings, path, fn, token=token, pre=pre))
    except Bad as e:
        return await _conflict_response(request, action, fn_name, form, e, name=name, back=back,
                                        lease_id=lease_id)
    except OSError:
        try:
            audit_result(request, action, "backlog", "error", {**detail, "reason": "io"})
        except AuditError:
            pass
        raise HTTPException(502, "could not write backlog") from None
    _audit(request, "backlog_overwrite" if force else action, "ok",
           {**detail, "bytes_before": old, "bytes_after": new})
    if fn_name == "save" and lease_id and path is not None:
        try:
            filelock.lease_release(settings.state_dir, path, lease_id)
        except (filelock.LockTimeout, OSError):
            pass
    dest = _back(back, name)
    if fn_name == "add" and back == "project":
        # A distinct URL (not the one the form was on) forces a fresh navigation in every
        # browser and lets the page confirm the new item.
        dest = f"/p/{quote(name)}?added=1#backlog"
    return RedirectResponse(dest, status_code=303,
                            headers={"Cache-Control": "no-store"})


def _tracker_op(settings, name: str, fn_name: str, form, audit) -> int:
    """One item op on a tracker project's BACKLOG (pm.db); the issue number. ``audit`` writes the "ok" line
    inside the store's transaction."""
    if fn_name == "add":
        return store.add_item(settings, name, "backlog", _str(form, "text"), audit=audit).num
    if fn_name == "toggle":
        return store.toggle(settings, name, "backlog", num_of(form.get("line", "")), audit=audit).num
    if fn_name == "edit":
        if not isinstance(form.get("new_text"), str):
            raise Invalid("missing item text; reload the page")
        return store.edit_item(settings, name, "backlog", num_of(form.get("line", "")), title=_str(form, "new_text"),
                               audit=audit).num
    raise Conflict(TRACKED.format(name))


async def _tracker_act(request: Request, action: str, fn_name: str, form, name: str, back: str) -> Response:
    """Tracker project: the op goes to the store (which writes the "ok" audit line in its transaction: a failed
    write rolls the change back); same redirects and error page as file mode."""
    settings = request.app.state.settings
    try:
        await executors.run_collectors(_tracker_op, settings, name, fn_name, form,
                                       audit_ok(request, action, "backlog", {"project": name}, keep=("num",)))
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None
    except (PmError, sqlite3.Error) as e:
        status = e.status if isinstance(e, PmError) else 503
        msg = str(e) if isinstance(e, PmError) else "tracker database busy"
        result = "conflict" if status == 409 else "denied" if status < 500 else "error"
        _audit(request, action, result, {"project": name, "reason": msg[:40]})
        return await executors.run_collectors(_page, request, status, project=name, error=msg,
                                              back=back if back in ("project", "embed") else "")
    dest = f"/p/{quote(name)}?added=1#backlog" if fn_name == "add" and back == "project" else _back(back, name)
    return RedirectResponse(dest, status_code=303, headers={"Cache-Control": "no-store"})


@router.post("/backlog/add", response_class=HTMLResponse)
async def backlog_add(request: Request, _=Depends(mutation_guard("backlog_add", Tier.SAFE))):
    return await _act(request, "backlog_add", "add")


@router.post("/backlog/toggle", response_class=HTMLResponse)
async def backlog_toggle(request: Request, _=Depends(mutation_guard("backlog_toggle", Tier.SAFE))):
    return await _act(request, "backlog_toggle", "toggle")


@router.post("/backlog/edit", response_class=HTMLResponse)
async def backlog_edit(request: Request, _=Depends(mutation_guard("backlog_edit", Tier.SAFE))):
    return await _act(request, "backlog_edit", "edit")


@router.post("/backlog/save", response_class=HTMLResponse)
async def backlog_save(request: Request, _=Depends(mutation_guard("backlog_save", Tier.SAFE))):
    return await _act(request, "backlog_save", "save")


# ------------------------------------------------------------------ edit leases
def _lease_json(lease: dict) -> dict:
    return {"ok": True, "lease": lease["id"], "expires": lease["expires"]}


async def _lease_path(request: Request, action: str):
    form = await request.form()
    settings = request.app.state.settings
    name = _str(form, "project")
    known = await _known(request, settings, name, action)
    try:
        return form, settings, name, resolve_path(known[name])
    except Bad as e:
        raise HTTPException(e.status, e.msg) from None


def _nostore(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.post("/backlog/lease/acquire")
async def lease_acquire(request: Request, _=Depends(mutation_guard("backlog_lease", Tier.SAFE))):
    form, settings, name, path = await _lease_path(request, "backlog_lease_take")
    mine = _str(form, "lease")[:32]
    try:
        lease, holder = filelock.lease_acquire(settings.state_dir, path, _label(request))
        if lease is None and holder and holder["id"] == mine:  # reload by the same editor
            lease = filelock.lease_renew(settings.state_dir, path, mine)
    except (filelock.LockTimeout, OSError):
        raise HTTPException(503, "lease store busy") from None
    if lease is None:
        _audit(request, "backlog_lease_take", "conflict", {"project": name})
        return _nostore(JSONResponse({"ok": False, "owner": holder["owner"] if holder else "",
                                      "since": _since(holder["since"]) if holder else ""},
                                     status_code=409))
    _audit(request, "backlog_lease_take", "ok", {"project": name})
    return _nostore(JSONResponse(_lease_json(lease)))


@router.post("/backlog/lease/renew")
async def lease_renew(request: Request, _=Depends(mutation_guard("backlog_lease", Tier.SAFE))):
    form, settings, name, path = await _lease_path(request, "backlog_lease_renew")
    try:
        lease = filelock.lease_renew(settings.state_dir, path, _str(form, "lease")[:32])
    except (filelock.LockTimeout, OSError):
        raise HTTPException(503, "lease store busy") from None
    if lease is None:
        return _nostore(JSONResponse({"ok": False}, status_code=409))
    return _nostore(JSONResponse(_lease_json(lease)))


@router.post("/backlog/lease/release")
async def lease_release(request: Request, _=Depends(mutation_guard("backlog_lease", Tier.SAFE))):
    form, settings, name, path = await _lease_path(request, "backlog_lease_release")
    try:
        filelock.lease_release(settings.state_dir, path, _str(form, "lease")[:32])
    except (filelock.LockTimeout, OSError):
        pass
    return Response(status_code=204)


@router.post("/backlog/lease/take")
async def lease_take(request: Request, _=Depends(mutation_guard("backlog_lease_takeover", Tier.SAFE))):
    form, settings, name, path = await _lease_path(request, "backlog_lease_takeover")
    try:
        lease, prev = filelock.lease_acquire(settings.state_dir, path, _label(request),
                                             takeover=True)
    except (filelock.LockTimeout, OSError):
        raise HTTPException(503, "lease store busy") from None
    _audit(request, "backlog_lease_takeover", "ok", {"project": name, "had_holder": int(bool(prev))})
    back = "&embed=1" if _str(form, "back") == "embed" else "&back=project" if _str(form, "back") == "project" else ""
    return RedirectResponse(f"/backlog?p={quote(name)}&edit=1{back}&lease={lease['id']}",
                            status_code=303, headers={"Cache-Control": "no-store"})
