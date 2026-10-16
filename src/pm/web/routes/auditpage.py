"""Audit page (``GET /audit``), its CSV export and the Client errors panel (first paint and fragment).

Mounted inside ``pages.router`` (see ``web/routes/__init__.py``), not listed in ``ROUTERS`` on its own.
Read-only: the audit log is never changed from here.
"""

from __future__ import annotations

import time
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from pm.core import executors
from pm.notify import clienterrors
from pm.projects.tracker import views as tracker_views
from pm.security import audit_view
from pm.security.guard import get_audit
from pm.web.deps import AUTH, is_navigation, settings
from pm.web.listing import wants_fragment
from pm.web.render import fragment, get_template, html, page_ctx

router = APIRouter()
TABS = ("activity", "errors")


def _q(q: str, action: str) -> str:
    """The filter box text (?q=, or the old ?action=): whitespace folded, max 60 chars."""
    return " ".join((q or action).split())[:60]


def _qs(kind: str, q: str, page: int) -> str:
    return urlencode({"ce_kind": kind, "q": q, "ce_page": page})


async def ce_ctx(request: Request, ce_kind: str = "all", ce_q: str = "", ce_page: int = 1) -> dict:
    """Template context of the Client errors panel: ``ce`` (raw page: rows, total, page, has_older, start),
    ``ce_groups`` (``ClientErrorLog.grouped``), ``ce_kind``, ``ce_q``, ``kinds``, ``qs``, ``ce_page_size`` and
    ``issue_projects`` (the tracker projects an error can be saved to as an issue; ``tracker_views.issue_projects``).
    Shared by the panel fragment and the /audit first paint; inputs are cleaned here."""
    ce_kind = ce_kind if ce_kind in clienterrors.KINDS else "all"
    ce_q = " ".join(ce_q.split())[:60]
    ce_page = min(max(ce_page, 1), 100)
    log, st = clienterrors.app_log(request.app.state), settings(request)
    ce, groups, targets = await executors.run_collectors(
        lambda: (log.page(ce_page, ce_kind, ce_q), log.grouped(ce_page, ce_kind, ce_q),
                 tracker_views.issue_projects(st)))
    return dict(ce=ce, ce_groups=groups, ce_kind=ce_kind, ce_q=ce_q, kinds=clienterrors.KINDS, qs=_qs,
                ce_page_size=clienterrors.PAGE_SIZE, issue_projects=targets)


@router.get("/audit", response_class=HTMLResponse, dependencies=AUTH)
async def audit_page(request: Request, page: int = 1, result: str = "all", q: str = "", action: str = "",
                     attempts: int = 0, cat: str = "", tab: str = "activity", ce_kind: str = "all",
                     ce_page: int = 1) -> Response:
    """Audit log. ?q= is the filter box (all words, over action, target, client and detail values; ?action= is the
    old name), ?cat= the category; the live box re-fetches only the list (HX-Request -> _audit_list.html).
    A full page also carries the Client errors context (``ce_ctx`` with the same q; ?tab=errors paints
    that tab first) and ``tab_counts {activity, errors}`` for the tab badges."""
    page = min(max(page, 1), 100_000)
    q = _q(q, action)
    cat = cat if cat in audit_view.CATEGORIES else ""
    ap = await executors.run_collectors(
        audit_view.read_page, get_audit(request).path, page, result, q, bool(attempts), audit_view.PAGE_SIZE, cat)
    ctx = dict(ap=ap, f_result=result if result in audit_view.RESULTS else "all", f_q=q, f_cat=cat,
               f_attempts=1 if attempts else 0, page_size=audit_view.PAGE_SIZE, categories=audit_view.CATEGORIES)
    if wants_fragment(request):
        return fragment(request, get_template("_audit_list.html").render(**page_ctx(request), **ctx))
    ce = await ce_ctx(request, ce_kind, q, ce_page)
    return html(request, "audit.html", nav="audit", tab=tab if tab in TABS else "activity",
                tab_counts={"activity": ap.counts["all"], "errors": ce["ce_groups"]["counts"]["all"]}, **ctx, **ce)


@router.get("/audit/export.csv", dependencies=AUTH)
async def audit_export(request: Request, result: str = "all", q: str = "", action: str = "", attempts: int = 0,
                       cat: str = "") -> Response:
    """The audit rows the page's filters select (every page, up to the scan cap) as a CSV download, streamed from a
    worker thread. Cells that a spreadsheet would run as a formula are quoted (``audit_view.csv_cell``)."""
    rows = audit_view.csv_lines(get_audit(request).path, result, _q(q, action), bool(attempts), cat)
    name = time.strftime("pm-audit-%Y%m%d-%H%M%S.csv", time.gmtime())
    return StreamingResponse(rows, media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.get("/audit/client-errors", response_class=HTMLResponse, dependencies=AUTH)
async def fragment_view(request: Request, ce_kind: str = "all", q: str = "", ce_q: str = "",
                        ce_page: int = 1) -> Response:
    """The /audit "Client errors" panel. ?q= is the page's filter box (?ce_q= the old name); a request from the
    live box or the pager (HX-Target: ce-list) gets only the list, the first load the box and the list. Opened
    directly in the address bar it redirects to the /audit page section (the bare partial has no page around it)."""
    ce_q = " ".join((q or ce_q).split())[:60]
    if is_navigation(request):
        return RedirectResponse("/audit" + (f"?{urlencode({'q': ce_q})}" if ce_q else "") + "#client-errors", 303)
    ctx = await ce_ctx(request, ce_kind, ce_q, ce_page)
    body = get_template("_client_errors.html").render(
        **page_ctx(request), **ctx, list_only=request.headers.get("hx-target") == "ce-list")
    return fragment(request, body)
