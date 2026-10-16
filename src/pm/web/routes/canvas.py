"""Canvas JSON API for the chat side panel: ``/a/{agent}/canvas`` (see canvas.py).

Reads need a session. Every mutation is POST + CSRF + Origin + session + tier + audit (attempt line
by ``mutation_guard``, 503 when it cannot be written; the outcome line carries agent, canvas id,
title and sizes, never the text). ``preview`` is read-only: session + Origin + CSRF, no audit.
Agent ids are keyed like the chat routes (live or ended), so canvases survive exit/resume.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from pm.core import executors
from pm.core.markdown_render import render_markdown
from pm.core.models import Tier
from pm.projects import canvas
from pm.security.audit import AuditError
from pm.security.guard import _csrf_value, audit_result, get_csrf, mutation_guard
from pm.web import deps
from pm.web.chatctx import live_or_ended
from pm.web.deps import AGENT_ID_RE, AUTH, snapshot

router = APIRouter()
_NOSTORE = {"Cache-Control": "no-store"}


def _json(data, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers=_NOSTORE)


def _err(e: canvas.CanvasError) -> JSONResponse:
    return _json({"error": e.extra.get("error", e.msg), "message": e.msg,
                  **{k: v for k, v in e.extra.items() if k != "error"}}, e.status)


async def _agent(request: Request, aid: str) -> str:
    if not AGENT_ID_RE.fullmatch(aid):
        raise HTTPException(404, "unknown agent")
    snap = await snapshot(request)
    return live_or_ended(request, snap, aid)[0].id


def _audit(request: Request, action: str, aid: str, result: str, detail: dict) -> None:
    try:
        audit_result(request, action, "canvas", result, {"agent": aid, **detail})
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None


def _int(v, default: int = -1) -> int:
    try:
        return int(str(v).strip())
    except ValueError:
        return default


async def _mutate(request: Request, action: str, aid: str, fn, detail):
    """fn(state_dir) in a worker -> audit outcome. CanvasError -> audited as denied, JSON error."""
    sd = deps.settings(request).state_dir
    try:
        res = await executors.run_collectors(fn, sd)
    except canvas.CanvasError as e:
        _audit(request, action, aid, "denied", {"reason": e.msg[:60]})
        return None, _err(e)
    except OSError:
        try:
            _audit(request, action, aid, "error", {"reason": "io"})
        except HTTPException:
            pass
        return None, _json({"error": "io", "message": "could not write the canvas"}, 502)
    _audit(request, action, aid, "ok", detail(res))
    return res, None


@router.get("/a/{aid}/canvas", dependencies=AUTH)
async def canvas_list(request: Request, aid: str):
    aid = await _agent(request, aid)
    try:
        items = await executors.run_collectors(canvas.list_canvases, deps.settings(request).state_dir, aid)
    except canvas.CanvasError as e:
        return _err(e)
    return _json({"canvases": items})


@router.get("/a/{aid}/canvas/{cid}", dependencies=AUTH)
async def canvas_get(request: Request, aid: str, cid: str):
    aid = await _agent(request, aid)
    try:
        return _json(await executors.run_collectors(canvas.get, deps.settings(request).state_dir, aid, cid))
    except canvas.CanvasError as e:
        return _err(e)


@router.post("/a/{aid}/canvas/preview")
async def canvas_preview(request: Request, aid: str):
    session = request.app.state.sessions.from_request(request)
    if session is None:
        raise HTTPException(401, "login required")
    csrf = get_csrf(request)
    if not csrf.origin_ok(request.headers):
        raise HTTPException(403, "bad origin")
    if not csrf.verify(session.id, await _csrf_value(request)):
        raise HTTPException(403, "bad csrf token")
    await _agent(request, aid)
    form = await request.form()
    text = str(form.get("text", ""))
    if len(text.encode()) > canvas.MAX_TEXT:
        return _json({"error": "too_large", "message": "text too large"}, 413)
    return _json({"html": render_markdown(text, canvas.MAX_TEXT)})


@router.post("/a/{aid}/canvas")
async def canvas_create(request: Request, aid: str,
                        _=Depends(mutation_guard("canvas_create", Tier.SAFE))):
    aid = await _agent(request, aid)
    form = await request.form()
    title, text = str(form.get("title", "")), str(form.get("text", ""))
    res, err = await _mutate(request, "canvas_create", aid,
                             lambda sd: canvas.create(sd, aid, title, text),
                             lambda r: {"canvas": r["id"], "title": r["title"][:120],
                                        "bytes": len(text.encode())})
    return err or _json(res)


@router.post("/a/{aid}/canvas/{cid}/save")
async def canvas_save(request: Request, aid: str, cid: str,
                      _=Depends(mutation_guard("canvas_save", Tier.SAFE))):
    aid = await _agent(request, aid)
    form = await request.form()
    text, note = str(form.get("text", "")), str(form.get("note", ""))
    title = str(form.get("title", "")) or None
    base = _int(form.get("base"))
    res, err = await _mutate(request, "canvas_save", aid,
                             lambda sd: canvas.save(sd, aid, cid, text, base, note, title),
                             lambda r: {"canvas": cid, "title": r[0]["title"][:120], "cur": r[0]["cur"],
                                        "bytes": len(text.encode()), "changed": int(r[1])})
    return err or _json(res[0])


@router.post("/a/{aid}/canvas/{cid}/restore")
async def canvas_restore(request: Request, aid: str, cid: str,
                         _=Depends(mutation_guard("canvas_restore", Tier.SAFE))):
    aid = await _agent(request, aid)
    form = await request.form()
    n = _int(form.get("n"))
    res, err = await _mutate(request, "canvas_restore", aid,
                             lambda sd: canvas.restore(sd, aid, cid, n),
                             lambda r: {"canvas": cid, "title": r["title"][:120], "from": n, "cur": r["cur"]})
    return err or _json(res)


@router.post("/a/{aid}/canvas/{cid}/delete")
async def canvas_delete(request: Request, aid: str, cid: str,
                        _=Depends(mutation_guard("canvas_delete", Tier.SAFE))):
    aid = await _agent(request, aid)
    res, err = await _mutate(request, "canvas_delete", aid,
                             lambda sd: canvas.delete(sd, aid, cid),
                             lambda r: {"canvas": cid, "versions": r})
    return err or _json({"ok": True, "id": cid})
