"""/dt routes of the dt theme (SHELL.md §3): the tab strip, its JSON, the sessions sidebar, and the v7 "all
devices" prefs (Settings › Display, Chat colours; ``pm.web.dt.prefs``), and the project quick look's listing
(``GET /dt/peek/<project>``, js/peek.js), and the "Add an issue" form for any page (``GET /dt/add-issue``, js/keys.js).

GETs need a login session; they are plain ``def`` (threadpool: the tabs / prefs file is stat'ed / read; the peek is
``async``: snapshot, then the listing on the collectors pool). Every POST:
``mutation_guard("dt.<area>.<verb>", Tier.SAFE)``, an audit line, the store write on the collectors pool, then a hub
event ``ui`` ``{"area": "tabs" | "prefs", "rev": n}`` so other browsers re-fetch.
The fragments are dt-only templates: a request in another UI theme (cookie ``pm_ui`` changed by another tab) gets
204 instead (a POST still applies its change).
"""

from __future__ import annotations

import sqlite3
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from pm.core import executors
from pm.core.errors import Invalid
from pm.core.models import Tier
from pm.projects import projfiles
from pm.projects.tracker import views
from pm.security.audit import AuditError
from pm.security.guard import audit_result, mutation_guard
from pm.web import render, themes
from pm.web.deps import AUTH, snapshot
from pm.web.dt import files, prefs, shell, stores

router = APIRouter(prefix="/dt")
TABS_TPL = "shell/_tabs.html"
SESS_TPL = "shell/_sessions.html"
PEEK_TPL = "project/_peek_list.html"
ADD_ISSUE_TPL = "_add_issue.html"
_NOSTORE = {"Cache-Control": "no-store"}
THEME = "dt"


def _field(form, name: str) -> str:
    v = form.get(name, "")
    return v.strip() if isinstance(v, str) else ""


def _audit(request: Request, verb: str, target: str, result: str, detail: dict | None = None,
           area: str = "tabs") -> None:
    """One audit line ``dt.<area>.<verb>``; 503 when the audit log cannot be written (fail closed)."""
    try:
        audit_result(request, f"dt.{area}.{verb}", target[:80], result, detail)
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None


def _ref(request: Request, verb: str, kind: str, id_: str) -> stores.Ref:
    """A checked (kind, id); bad input is audited as denied and answered 400."""
    try:
        return stores.check_ref(kind, id_)
    except Invalid:
        _audit(request, verb, f"{kind[:8]}:{id_[:64]}", "denied", {"reason": "bad tab"})
        raise HTTPException(400, "bad tab") from None


# 400 / 503 texts per store area
_AREA_MSG = {"tabs": ("bad tab", "tabs are busy"), "prefs": ("bad setting", "prefs are busy")}


async def _change(request: Request, area: str, verb: str, target: str, store_fn, *args):
    """Run ``store_fn(settings, *args)`` (a store's locked apply: ``stores.apply`` -> (TabState, extra),
    ``prefs.apply`` -> Prefs) on the collectors pool, audit ``dt.<area>.<verb>``, publish hub ``ui`` {area, rev}.
    Returns what ``store_fn`` returned. ``Invalid`` -> denied + 400; ``OSError`` (lock busy) -> error + 503."""
    bad, busy = _AREA_MSG[area]
    try:
        got = await executors.run_collectors(store_fn, request.app.state.settings, *args)
    except Invalid as e:
        _audit(request, verb, target, "denied", {"reason": str(e)[:60]}, area=area)
        raise HTTPException(400, str(e) or bad) from None
    except OSError:
        _audit(request, verb, target, "error", {"reason": "busy"}, area=area)
        raise HTTPException(503, busy) from None
    rev = (got[0] if isinstance(got, tuple) else got).rev
    _audit(request, verb, target, "ok", {"rev": rev}, area=area)
    hub = getattr(request.app.state, "hub", None)
    if hub is not None:
        hub.publish("ui", {"area": area, "rev": rev})
    return got


def _no_shell() -> Response | None:
    """204 when this request renders another UI theme (no dt templates there), else None."""
    return None if themes.current() == THEME else Response(status_code=204, headers=_NOSTORE)


def _render(request: Request, name: str, sh: shell.Shell) -> str:
    ctx = render.page_ctx(request)
    ctx["shell"] = sh  # by key: archcheck refuses any ``shell=`` keyword argument
    return render.get_template(name).render(**ctx)


def _strip(request: Request, st: stores.TabState, headers: dict | None = None) -> Response:
    """The tab strip fragment for ``st``, current tab from the page that asked (HX-Current-URL / Referer); 204 in
    another UI theme."""
    if (none := _no_shell()) is not None:
        return none
    sh = shell.build(shell.snapshot_of(request), st, shell.page_path(request))
    return render.fragment(request, _render(request, TABS_TPL, sh), headers)


def _target(ref: stores.Ref | None) -> str:
    return f"{ref[0]}:{ref[1]}" if ref else "home"


@router.get("/side", dependencies=AUTH)
def side(request: Request) -> Response:
    if (none := _no_shell()) is not None:
        return none
    return render.fragment(request, _render(request, SESS_TPL, shell.for_request(request)))


@router.get("/tabs", dependencies=AUTH)
def tabs(request: Request) -> Response:
    return _strip(request, stores.read_cached(request.app.state.settings))


@router.get("/tabs.json", dependencies=AUTH)
def tabs_json(request: Request) -> Response:
    st = stores.read_cached(request.app.state.settings)
    sh = shell.build(shell.snapshot_of(request), st, shell.page_path(request))
    return JSONResponse({"rev": st.rev,
                         "tabs": [{"kind": t.kind, "id": t.id, "title": t.title, "href": t.href, "state": t.state}
                                  for t in sh.tabs],
                         "closed": [{"kind": k, "id": i, "href": shell.tab_href((k, i))} for k, i in st.closed]},
                        headers=_NOSTORE)


@router.post("/tabs/open")
async def tabs_open(request: Request, _=Depends(mutation_guard("dt.tabs.open", Tier.SAFE))) -> Response:
    form = await request.form()
    ref = _ref(request, "open", _field(form, "kind"), _field(form, "id"))
    st, _x = await _change(request, "tabs", "open", _target(ref), stores.apply, stores.open_tab, ref)
    return _strip(request, st)


@router.post("/tabs/close")
async def tabs_close(request: Request, _=Depends(mutation_guard("dt.tabs.close", Tier.SAFE))) -> Response:
    form = await request.form()
    ref = _ref(request, "close", _field(form, "kind"), _field(form, "id"))
    st, _x = await _change(request, "tabs", "close", _target(ref), stores.apply, stores.close_tab, ref)
    return _strip(request, st)


@router.post("/tabs/reopen")
async def tabs_reopen(request: Request, _=Depends(mutation_guard("dt.tabs.reopen", Tier.SAFE))) -> Response:
    st, ref = await _change(request, "tabs", "reopen", "last", stores.apply, stores.reopen_tab)
    if ref is None or _no_shell() is not None:
        return Response(status_code=204, headers=_NOSTORE)
    return _strip(request, st, {"X-Dt-Href": shell.tab_href(ref)})


@router.post("/tabs/close-all")
async def tabs_close_all(request: Request, _=Depends(mutation_guard("dt.tabs.close-all", Tier.SAFE))) -> Response:
    """Settings › Close all session tabs: every tab goes to the reopen list (on every device: the ui event)."""
    st, n = await _change(request, "tabs", "close-all", "all", stores.apply, stores.close_all)
    resp = _strip(request, st)
    resp.headers["X-Dt-Closed"] = str(n)
    return resp


@router.post("/tabs/current")
async def tabs_current(request: Request, _=Depends(mutation_guard("dt.tabs.current", Tier.SAFE))) -> Response:
    form = await request.form()
    kind, id_ = _field(form, "kind"), _field(form, "id")
    ref = None if kind == "home" and not id_ else _ref(request, "current", kind, id_)
    await _change(request, "tabs", "current", _target(ref), stores.apply, stores.set_current, ref)
    return Response(status_code=204, headers=_NOSTORE)


@router.post("/tabs/move")
async def tabs_move(request: Request, _=Depends(mutation_guard("dt.tabs.move", Tier.SAFE))) -> Response:
    form = await request.form()
    ref = _ref(request, "move", _field(form, "kind"), _field(form, "id"))
    bk, bid = _field(form, "before_kind"), _field(form, "before_id")
    before = _ref(request, "move", bk, bid) if bk or bid else None
    st, _x = await _change(request, "tabs", "move", _target(ref), stores.apply, stores.move_tab, ref, before)
    return _strip(request, st)


# ------------------------------------------------------------------ v7 prefs (Settings › Display, Chat colours)
def _prefs_json(p: prefs.Prefs, notice: str = "") -> JSONResponse:
    body = prefs.as_json(p)
    if notice:
        body = {"ok": True, "notice": notice, **body}
    return JSONResponse(body, headers=_NOSTORE)


@router.get("/prefs", dependencies=AUTH)
def prefs_get(request: Request) -> Response:
    return _prefs_json(prefs.read_cached(request.app.state.settings))


@router.post("/prefs")
async def prefs_save(request: Request, _=Depends(mutation_guard("dt.prefs.save", Tier.SAFE))) -> Response:
    """Partial change: form fields ``<key>=<value>`` (``""`` = back to the default). Answer ``{ok, notice, rev,
    prefs}``; a bad key or value is audited as denied and answered 400."""
    form = await request.form()
    change = {k: v for k, v in form.multi_items() if k != "csrf" and isinstance(v, str)}
    target = ",".join(sorted(change))[:80]
    p = await _change(request, "prefs", "save", target, prefs.apply, change)
    return _prefs_json(p, "Saved")


# ------------------------------------------------------------------ project quick look (js/peek.js)
def _peek_read(settings, path: str, rel: str) -> tuple[dict, bool]:
    """(the directory listing, open in a new browser tab?) on the collectors pool: ``projfiles.read_tree`` (relative
    path inside the project only, no symlink, the Files tab's rules) and the ``peek_open`` pref."""
    return projfiles.read_tree(path, rel), prefs.read_cached(settings).get("peek_open") != "same"


@router.get("/peek/{project}", dependencies=AUTH)
async def peek(request: Request, project: str, path: str = "") -> Response:
    """The quick look's body: one folder of ``project`` (``path`` relative to its root) as a grid table; folders
    drill down in the dialog, files and folders link to the Files tab (``/p/<name>/files?path=``). Read-only."""
    if (none := _no_shell()) is not None:
        return none
    p = (await snapshot(request)).project(project)
    if p is None:
        raise HTTPException(404, "unknown project")
    try:
        tree, new_tab = await executors.run_collectors(_peek_read, request.app.state.settings, p.path, path)
    except projfiles.FileRefused as e:
        raise HTTPException(e.status, e.msg) from None
    if tree.get("kind") != "dir":
        raise HTTPException(404, "not a folder")
    g = p.git
    meta = " · ".join(x for x in (g.branch or "", f"{g.dirty} changed" if g.dirty else "clean") if x) if g else ""
    html = render.get_template(PEEK_TPL).render(
        fv=files.listing(tree, "/p/" + quote(p.name), p.name), name=p.name, meta=meta, new_tab=new_tab)
    return render.fragment(request, html)


# ------------------------------------------------------------------ Add an issue from anywhere (js/keys.js)
@router.get("/add-issue", dependencies=AUTH)
def add_issue(request: Request, project: str = "") -> Response:
    """The search command "Add an issue": the Backlog's Add issue dialog with the tracker projects to pick from
    (``_add_issue.html``); ``project`` (the page's project) is preselected when it is one of them. The add itself is
    the core's ``POST /p/{n}/issues``. Read-only; 503 while pm.db is busy."""
    if (none := _no_shell()) is not None:
        return none
    try:
        names = views.issue_projects(request.app.state.settings)
    except (sqlite3.Error, OSError):
        raise HTTPException(503, "tracker busy") from None
    return render.html(request, ADD_ISSUE_TPL, projects=names, project=project if project in names else "")
