"""Quick chat popup backend (history in qchat.db, settings in qchat.json).

GET routes need a session. Every POST goes through mutation_guard (session + Origin + CSRF + tier
MESSAGE + audit attempt, fail closed) like every other mutation. Audit details are lengths, ids and
the profile only; message text is masked before it is stored and is never logged, audited or ntfy'd.
``full`` works in any logged-in session (no unlock gate). MCP header / env values are never returned.
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response, StreamingResponse

from pm.core.models import Tier
from pm.projects import projects
from pm.qchat import cfg as qchatcfg
from pm.qchat import db as qchatdb
from pm.qchat import run as qchat
from pm.qchat import send as qsend
from pm.security.audit import AuditError
from pm.security.guard import audit_result, mutation_guard
from pm.web import chatctx
from pm.web.deps import AGENT_ID_RE, AUTH, snapshot
from pm.web.deps import settings as _settings

router = APIRouter()
ACTION = "qchat_send"
_NOSTORE = {"Cache-Control": "no-store"}
MAX_TITLE = 120


def _json(data, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers=_NOSTORE)


def folder_names(settings) -> list[str]:
    return [p.name for p in projects.discover(settings)]


def agent_resolver(s, snap):
    """Sync ``id -> Agent | None`` for an aside: a running agent, or the stand-in of an ended one."""
    def resolve(aid: str):
        if not AGENT_ID_RE.fullmatch(aid):
            return None
        return snap.agent(aid) or chatctx.ended_standin(s, snap, aid)
    return resolve


def _cid(raw: str) -> int:
    if not raw.isdigit() or len(raw) > 9:
        raise HTTPException(404, "no such conversation")
    return int(raw)


@router.get("/qchat/options", dependencies=AUTH)
async def qchat_options(request: Request, folder: str = ""):
    s = _settings(request)

    def build():
        cfg = qchatcfg.load(s)
        names = folder_names(s)
        fol = folder if folder in names else ""
        mcp = [{"name": m["name"], "source": m["source"], "type": m["type"], "on": True}
               for m in qchatcfg.discover_mcp(s) if m.get("enabled") and m.get("type") != "connector"]
        skills = [{"name": k["name"], "source": k["source"], "description": k.get("description", ""), "on": True}
                  for k in qchatcfg.discover_skills(s, fol) if k.get("enabled")]
        profiles = [p for p in qchat.PROFILES if p == "none" or cfg["profiles"].get(p)]
        dp = cfg["default_profile"] if cfg["default_profile"] in profiles else "none"
        return {"models": list(qchat.MODELS), "profiles": profiles, "default_profile": dp,
                "folders": names, "default_folder": cfg["default_folder"] if cfg["default_folder"] in names else "",
                "mcp": mcp, "skills": skills, "skills_in_none": bool(cfg["skills_in_none"]),
                "nav_search": bool(cfg["nav_search"])}

    return _json(await run_in_threadpool(build))


@router.get("/qchat/convs", dependencies=AUTH)
async def qchat_convs(request: Request, q: str = ""):
    s = _settings(request)
    return _json({"convs": await run_in_threadpool(qchatdb.list_convs, s, q.strip()[:80])})


@router.get("/qchat/convs/{cid}", dependencies=AUTH)
async def qchat_conv(request: Request, cid: str):
    s = _settings(request)
    n = _cid(cid)
    conv = await run_in_threadpool(qchatdb.get_conv, s, n)
    if conv is None:
        raise HTTPException(404, "no such conversation")
    return _json({"conv": conv, "messages": await run_in_threadpool(qchatdb.messages, s, n)})


@router.get("/qchat/convs/{cid}/export", dependencies=AUTH)
async def qchat_export(request: Request, cid: str):
    md = await run_in_threadpool(qchatdb.export_md, _settings(request), _cid(cid))
    if md is None:
        raise HTTPException(404, "no such conversation")
    return Response(md, media_type="text/markdown; charset=utf-8", headers=_NOSTORE)


def _result(request, action: str, target: str, result: str, detail: dict, strict: bool = True) -> None:
    try:
        audit_result(request, action, target, result, detail)
    except AuditError:
        if strict:
            raise HTTPException(503, "audit unavailable; refusing") from None


@router.post("/qchat/convs/{cid}/rename")
async def qchat_rename(request: Request, cid: str, _=Depends(mutation_guard("qchat_rename", Tier.MESSAGE))):
    n = _cid(cid)
    title = " ".join(str((await request.form()).get("title") or "").split())[:MAX_TITLE]
    if not title:
        _result(request, "qchat_rename", str(n), "denied", {"reason": "title"})
        raise HTTPException(400, "title required")
    ok = await run_in_threadpool(qchatdb.rename_conv, _settings(request), n, title)
    _result(request, "qchat_rename", str(n), "ok" if ok else "denied",
            {"len": len(title)} if ok else {"reason": "unknown"})
    if not ok:
        raise HTTPException(404, "no such conversation")
    return _json({"ok": True, "title": title})


@router.post("/qchat/convs/{cid}/delete")
async def qchat_delete(request: Request, cid: str, _=Depends(mutation_guard("qchat_delete", Tier.MESSAGE))):
    n = _cid(cid)
    ok = await run_in_threadpool(qchatdb.delete_conv, _settings(request), n)
    _result(request, "qchat_delete", str(n), "ok" if ok else "denied", {} if ok else {"reason": "unknown"})
    if not ok:
        raise HTTPException(404, "no such conversation")
    return _json({"ok": True})


@router.post("/qchat/delete-all")
async def qchat_delete_all(request: Request, _=Depends(mutation_guard("qchat_delete_all", Tier.MESSAGE))):
    n = await run_in_threadpool(qchatdb.delete_all, _settings(request))
    _result(request, "qchat_delete_all", "", "ok", {"deleted": n})
    return _json({"ok": True, "deleted": n})


def _deny(request, reason: str, msg: str) -> NoReturn:
    _result(request, ACTION, "claude", "denied", {"reason": reason})
    raise HTTPException(400, msg)


@router.post("/qchat/send")
async def qchat_send(request: Request, _=Depends(mutation_guard(ACTION, Tier.MESSAGE))):
    form = await request.form()
    req = qsend.SendReq(text=str(form.get("text") or ""), model=str(form.get("model") or ""),
                        conv=str(form.get("conv") or ""), profile=str(form.get("profile") or "none"),
                        folder=str(form.get("folder") or ""), mcp=[str(v) for v in form.getlist("mcp")][:50],
                        skills=[str(v) for v in form.getlist("skills")][:100], agent=str(form.get("agent") or ""))
    s = _settings(request)
    resolve = agent_resolver(s, await snapshot(request))
    try:
        prep = await run_in_threadpool(qsend.prepare_send, s, req, folder_names=folder_names, resolve_agent=resolve)
    except qsend.SendInvalid as e:
        _deny(request, e.field, str(e))

    def on_end(outcome: str, detail: dict) -> None:
        _result(request, ACTION, "claude", outcome, detail, strict=False)

    return StreamingResponse(qsend.consume_stream(s, prep, on_end=on_end, offload=run_in_threadpool),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
