"""POST /client-errors (browser error reports) and Clear of the /audit Client errors tab (its panel: auditpage)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from pm.core.models import Tier
from pm.notify import clienterrors
from pm.security.audit import AuditError
from pm.security.csrf import RateLimiter
from pm.security.guard import audit_result, client_of, get_csrf, mutation_guard

router = APIRouter()


def _rate(st) -> RateLimiter:
    if getattr(st, "client_errors_rate", None) is None:
        st.client_errors_rate = RateLimiter(max(1, st.settings.client_error_rate_per_min), 60.0)
    return st.client_errors_rate


@router.post("/client-errors")
async def report(request: Request) -> Response:
    """Not audited per report (it has its own bounded log); same session/CSRF/Origin checks."""
    st = request.app.state
    session = st.sessions.from_request(request)
    if session is None:
        raise HTTPException(401, "login required")
    csrf = get_csrf(request)
    if not csrf.origin_ok(request.headers):
        raise HTTPException(403, "bad origin")
    if not csrf.verify(session.id, request.headers.get("x-csrf-token")):
        raise HTTPException(403, "bad csrf token")
    ce_log = clienterrors.app_log(st)
    if not _rate(st).allow(client_of(request)):
        raise HTTPException(429, "too many reports")
    raw = await request.body()
    if len(raw) > clienterrors.MAX_BODY:
        raise HTTPException(413, "too large")
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "bad json") from None
    entry = (clienterrors.sanitize(data, client_of(request), ua=request.headers.get("user-agent", ""))
             if isinstance(data, dict) else None)
    if entry is None:
        raise HTTPException(400, "bad report")
    try:
        ce_log.append(entry)
    except OSError:
        raise HTTPException(503, "client error log unavailable") from None
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/audit/client-errors/clear", dependencies=[Depends(mutation_guard("client_errors_clear", Tier.SAFE))])
async def clear(request: Request) -> Response:
    """Audit line first (fail closed: no audit, nothing cleared), then the log is emptied."""
    try:
        audit_result(request, "client_errors_clear", "", "ok")
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None
    try:
        clienterrors.app_log(request.app.state).clear()
    except OSError:
        raise HTTPException(503, "client error log unavailable") from None
    return Response(status_code=204, headers={"HX-Trigger": "ce-refresh"})
