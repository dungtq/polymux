"""GET /pm/reload-need?served=<epoch>: restart vs browser refresh after pm's own code changed (read-only)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from pm.notify import reloadneed
from pm.web.deps import AUTH

router = APIRouter()


@router.get("/pm/reload-need", dependencies=AUTH)
async def reload_need(request: Request, served: float = 0.0) -> Response:
    if not (0 < served < 4_102_444_800):
        served = 0.0
    return JSONResponse(reloadneed.need(served), headers={"Cache-Control": "no-store"})
