"""Tier-1 control mutation plumbing: form access, flash fragments, audited refusals."""

from __future__ import annotations

from html import escape

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from pm.security.audit import AuditError
from pm.security.guard import audit_result

_NOSTORE = {"Cache-Control": "no-store"}


async def form(request: Request, key: str) -> str:
    """Read a form field in the handler (body is cached by the guard dependency)."""
    v = (await request.form()).get(key, "")
    return v if isinstance(v, str) else ""


def frag(msg: str, cls: str = "ok", status: int = 200) -> HTMLResponse:
    return HTMLResponse(f'<p class="{escape(cls)}">{escape(msg)}</p>', status_code=status,
                        headers=_NOSTORE)


def done(request: Request, action: str, target: str, result: str,
          detail: dict | None = None) -> None:
    try:
        audit_result(request, action, target, result, detail)
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None


def refuse(request: Request, action: str, target: str, status: int, why: str) -> HTTPException:
    done(request, action, target, "denied", {"reason": why})
    return HTTPException(status, why)


def fail(request: Request, action: str, target: str, why: str) -> HTTPException:
    done(request, action, target, "error", {"reason": why})
    return HTTPException(502, why)
