"""Passkey (WebAuthn) JSON endpoints.

Wiring: ``app.include_router(web.routes.passkey.router)``. Requires app.state:
settings, sessions. ``app.state.passkeys`` (PasskeyManager) is created lazily.
Clients send ``X-CSRF-Token`` and a JSON body. Registration needs a logged-in
session (plus an unlocked one only when settings.require_unlock). /passkey/auth/*
(step-up) exist only when require_unlock is true, else 404. /login/passkey/*
work without a session (they ARE the login). Credential material is never audited.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from pm.core import executors
from pm.core.models import AuditEntry
from pm.security import auth
from pm.security.audit import AuditError, session_tag
from pm.security.guard import PASSKEY_TEXTS, audit_result, client_of, csrf_session_guard, get_audit
from pm.security.passkey import CHALLENGE_TTL_S, PasskeyError, PasskeyManager, manager_of

router = APIRouter()
_NOSTORE = {"Cache-Control": "no-store"}
MAX_BODY = 32 * 1024


def get_manager(request: Request) -> PasskeyManager:
    return manager_of(request.app)


def _guard(action: str, *, need_unlocked: bool, stepup: bool = False):
    """Session + Origin + X-CSRF-Token (header only) + attempt audit; unlocked check only if need_unlocked."""
    return csrf_session_guard(action, check_locked=need_unlocked, stepup_only=stepup, csrf_source="header",
                              texts=PASSKEY_TEXTS)


def _done(request: Request, action: str, result: str, detail: dict | None = None) -> None:
    try:
        audit_result(request, action, "", result, detail)
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None


async def _json(request: Request):
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(413, "too large")
    try:
        return await request.json()
    except ValueError:
        raise HTTPException(400, "bad json") from None


def _reply(request: Request, action: str, call):
    """Run a manager call; audit ok / denied(reason)."""
    try:
        out = call()
    except PasskeyError as exc:
        _done(request, action, "denied", {"reason": exc.reason})
        return JSONResponse({"error": exc.reason}, status_code=403, headers=_NOSTORE)
    _done(request, action, "ok")
    return JSONResponse(out, headers=_NOSTORE)


@router.post("/passkey/register/begin")
async def register_begin(request: Request,
                         session=Depends(_guard("passkey_register_begin", need_unlocked=True))):
    mgr = get_manager(request)
    return _reply(request, "passkey_register_begin", lambda: mgr.begin_registration(session))


@router.post("/passkey/register/finish")
async def register_finish(request: Request,
                          session=Depends(_guard("passkey_register_finish", need_unlocked=True))):
    body = await _json(request)
    if not isinstance(body, dict):
        raise HTTPException(400, "bad json")
    name = body.get("name", "") if isinstance(body.get("name", ""), str) else ""
    cred = body.get("credential", body)
    mgr = get_manager(request)
    return await executors.run_collectors(
        _reply, request, "passkey_register_finish",
        lambda: mgr.finish_registration(session, cred, name))


@router.post("/passkey/auth/begin")
async def auth_begin(request: Request,
                     session=Depends(_guard("passkey_auth_begin", need_unlocked=False, stepup=True))):
    mgr = get_manager(request)
    return _reply(request, "passkey_auth_begin", lambda: mgr.begin_auth(session))


@router.post("/passkey/auth/finish")
async def auth_finish(request: Request,
                      session=Depends(_guard("passkey_auth_finish", need_unlocked=False, stepup=True))):
    body = await _json(request)
    if not isinstance(body, dict):
        raise HTTPException(400, "bad json")
    cred = body.get("credential", body)
    mgr = get_manager(request)
    client = client_of(request)
    return await executors.run_collectors(
        _reply, request, "passkey_auth_finish",
        lambda: {"unlocked": mgr.finish_auth(session, cred, client=client)})


# ------------------------------------------------------------------ login
PREAUTH_COOKIE = "pm_preauth"
PREAUTH_PATH = "/login/passkey"


def _login_audit(request: Request, result: str, reason: str = "", sid: str = "") -> None:
    """Best effort: never raises, never carries credential material. ``sid``: the new session (tag only)."""
    try:
        get_audit(request).write(AuditEntry(time.time(), auth.client_id(request), "login_passkey",
                                            "", result, {"reason": reason} if reason else {}, session_tag(sid)))
    except Exception:
        pass


def _login_pre(request: Request) -> str:
    """Origin + rate limit; returns the client id."""
    if not auth._origin_ok(request):
        _login_audit(request, "denied", "origin")
        raise HTTPException(403, "bad origin")
    client = auth.client_id(request)
    wait = request.app.state.login_limiter.retry_after(client)
    if wait:
        _login_audit(request, "denied", "rate")
        raise HTTPException(429, f"too many attempts; retry in {wait}s",
                            headers={"Retry-After": str(wait)})
    return client


def _clear_nonce(resp):
    resp.delete_cookie(PREAUTH_COOKIE, path=PREAUTH_PATH, secure=True, httponly=True,
                       samesite="strict")


@router.post("/login/passkey/begin")
async def login_begin(request: Request):
    client = _login_pre(request)
    mgr = get_manager(request)
    try:
        nonce, opts = mgr.begin_login()
    except PasskeyError as exc:
        request.app.state.login_limiter.failure(client)
        _login_audit(request, "denied", exc.reason)
        return JSONResponse({"error": exc.reason}, status_code=403, headers=_NOSTORE)
    resp = JSONResponse(opts, headers=_NOSTORE)
    resp.set_cookie(PREAUTH_COOKIE, nonce, max_age=int(CHALLENGE_TTL_S), httponly=True,
                    secure=True, samesite="strict", path=PREAUTH_PATH)
    return resp


@router.post("/login/passkey/finish")
async def login_finish(request: Request):
    client = _login_pre(request)
    app = request.app
    body = await _json(request)
    if not isinstance(body, dict):
        raise HTTPException(400, "bad json")
    cred = body.get("credential", body)
    nxt = auth.safe_next(body.get("next") if isinstance(body.get("next"), str) else "/")
    nonce = request.cookies.get(PREAUTH_COOKIE, "")
    mgr = get_manager(request)
    try:
        await executors.run_collectors(mgr.finish_login, nonce, cred)
    except PasskeyError as exc:
        app.state.login_limiter.failure(client)
        _login_audit(request, "denied", exc.reason)
        resp = JSONResponse({"error": exc.reason}, status_code=403, headers=_NOSTORE)
        _clear_nonce(resp)
        return resp
    app.state.login_limiter.success(client)
    store = app.state.sessions
    session = store.create(client)
    _login_audit(request, "ok", sid=session.id)
    resp = JSONResponse({"ok": True, "next": nxt}, headers=_NOSTORE)
    auth._set_cookie(resp, store.cookie_value(session), app.state.settings)
    _clear_nonce(resp)
    return resp
