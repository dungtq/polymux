"""Project compose stack: GET /p/{name}/stack (JSON view model), GET /p/{name}/stack/logs (masked text),
POST /p/{name}/stack/{up,down} (audited). POST /p/{name}/stack/restart lives in ``control`` (registered first).

Theme-neutral: the classic theme does not call these; a theme renders the JSON and posts the forms.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from pm.core import executors
from pm.core.errors import PmError
from pm.core.models import Tier
from pm.infra import stacks
from pm.security.auth import require_session
from pm.security.guard import mutation_guard
from pm.web import controlctl
from pm.web.deps import project_of

router = APIRouter()
_AUTH = [Depends(require_session)]
_NOSTORE = {"Cache-Control": "no-store"}


@router.get("/p/{name}/stack", dependencies=_AUTH)
async def stack_view(request: Request, name: str) -> JSONResponse:
    p = project_of(request, name)
    vm = await executors.run_collectors(stacks.view, p.path)
    return JSONResponse({"project": name, **vm}, headers=_NOSTORE)


@router.get("/p/{name}/stack/logs", dependencies=_AUTH)
async def stack_logs(request: Request, name: str, service: str = "", tail: int = 200) -> PlainTextResponse:
    p = project_of(request, name)
    try:
        text = await executors.run_collectors(stacks.logs, p.path, tail, service=service or None)
    except PmError as e:
        raise HTTPException(e.status, str(e)) from None
    return PlainTextResponse(text, headers=_NOSTORE)


async def _op(request: Request, name: str, action: str) -> HTMLResponse:
    audit = f"stack_{action}"
    try:
        p = project_of(request, name)
    except HTTPException:
        raise controlctl.refuse(request, audit, name, 404, "unknown project") from None
    try:
        msg = await executors.run_jobs(stacks.operate, p.path, action)
    except PmError as e:
        if e.status < 500:
            raise controlctl.refuse(request, audit, name, e.status, str(e)) from None
        controlctl.done(request, audit, name, "error", {"reason": f"docker compose {action} failed"})
        raise HTTPException(e.status, str(e)) from None
    controlctl.done(request, audit, name, "ok")
    return HTMLResponse(controlctl.frag(msg).body, headers={**_NOSTORE, "HX-Trigger": "stack-changed"})


@router.post("/p/{name}/stack/up", response_class=HTMLResponse)
async def stack_up(request: Request, name: str, _=Depends(mutation_guard("stack_up", Tier.SAFE))):
    return await _op(request, name, "up")


@router.post("/p/{name}/stack/down", response_class=HTMLResponse)
async def stack_down(request: Request, name: str, _=Depends(mutation_guard("stack_down", Tier.MESSAGE))):
    return await _op(request, name, "down")
