"""mutation_guard / csrf_session_guard: FastAPI dependencies enforcing the PLAN section 6 checks on POST routes.

Wiring (app.state): settings (Settings), sessions (store with
`from_request(request) -> session | None`; session has .id and .unlocked_until).
The unlocked check applies only when settings.require_unlock is true (default
false: login is the gate). audit / csrf are created lazily on app.state if absent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import parse_qs

from fastapi import HTTPException, Request

from pm.core.models import AuditEntry, Tier
from pm.security.audit import AuditError, AuditLog, session_tag
from pm.security.csrf import Csrf


class Session(Protocol):
    id: str
    unlocked_until: float


class SessionStore(Protocol):
    def from_request(self, request: Request) -> Session | None: ...


def client_of(request: Request) -> str:
    return request.client.host if request.client else "?"


def get_audit(request: Request) -> AuditLog:
    st = request.app.state
    if getattr(st, "audit", None) is None:
        st.audit = AuditLog(st.settings.audit_file)
    return st.audit


def audit_session(conn) -> str:
    """The audit tag of the request's (or websocket's) login session: the one the guard admitted, else the cookie's;
    "" when there is none. Never raises (an audit line must not fail on it)."""
    try:
        s = getattr(conn.state, "session", None)
        if s is None:
            store = getattr(conn.app.state, "sessions", None)
            s = store.from_request(conn) if store is not None else None
        return session_tag(getattr(s, "id", None))
    except Exception:
        return ""


def entry(conn, action: str, target: str, result: str, detail: dict | None = None) -> AuditEntry:
    """An audit line for a request or websocket: now, its client, its session tag."""
    return AuditEntry(time.time(), client_of(conn), action, target, result, dict(detail or {}), audit_session(conn))


def get_csrf(request: Request) -> Csrf:
    st = request.app.state
    if getattr(st, "csrf", None) is None:
        s = st.settings
        st.csrf = Csrf(s.state_dir, s.allowed_origins)
    return st.csrf


async def _csrf_value(request: Request) -> str | None:
    hdr = request.headers.get("x-csrf-token")
    if hdr:
        return hdr
    if "application/x-www-form-urlencoded" in request.headers.get("content-type", ""):
        body = await request.body()  # cached by Starlette; handler can still read form
        vals = parse_qs(body.decode("utf-8", "replace")).get("csrf")
        return vals[0] if vals else None
    return None


def _deny_audit(request: Request, action: str, why: str, target: str = "") -> None:
    try:
        get_audit(request).write(entry(request, action, target, "denied", {"reason": why}))
    except AuditError:
        pass


@dataclass(frozen=True)
class GuardTexts:
    """403 detail texts per failed check (the audit reason is always origin/csrf/locked/tier)."""
    origin: str
    csrf: str
    locked: str


MUTATION_TEXTS = GuardTexts("bad origin", "bad csrf token", "locked: unlock required")
CONTROL_TEXTS = GuardTexts("bad origin", "bad csrf", "locked: unlock required")  # /unlock* never check locked
PASSKEY_TEXTS = GuardTexts("origin", "csrf", "locked")


def csrf_session_guard(action: str, *, tier: Tier | None = None, check_locked: bool = True,
                       stepup_only: bool = False,
                       csrf_source: Literal["header", "header_or_form"] = "header_or_form",
                       texts: GuardTexts = MUTATION_TEXTS, attempt_detail: bool = True):
    """Dependency: session + Origin + CSRF (+ unlocked, + tier) + attempt audit line.

    Order: 404 (stepup_only while require_unlock is off), 401, origin, csrf, locked (only with
    check_locked and require_unlock), tier (only with a tier), attempt audit (503 when it fails).
    The attempt detail is ``{"tier": n}`` when a tier is given and attempt_detail, else ``{}``.
    """
    async def dep(request: Request) -> Session:
        st = request.app.state
        settings = st.settings
        if stepup_only and not settings.require_unlock:
            raise HTTPException(404, "step-up disabled (require_unlock is off)")
        session = st.sessions.from_request(request)
        if session is None:
            raise HTTPException(401, "login required")
        if not get_csrf(request).origin_ok(request.headers):
            _deny_audit(request, action, "origin")
            raise HTTPException(403, texts.origin)
        token = (await _csrf_value(request) if csrf_source == "header_or_form"
                 else request.headers.get("x-csrf-token"))
        if not get_csrf(request).verify(session.id, token):
            _deny_audit(request, action, "csrf")
            raise HTTPException(403, texts.csrf)
        if check_locked and settings.require_unlock and session.unlocked_until <= time.time():
            _deny_audit(request, action, "locked")
            raise HTTPException(403, texts.locked)
        if tier is not None and not tier_ok(request, tier):
            _deny_audit(request, action, "tier")
            raise HTTPException(403, "tier not enabled")
        detail = {"tier": int(tier)} if attempt_detail and tier is not None else {}
        try:
            get_audit(request).write(AuditEntry(time.time(), client_of(request), action, "", "attempt", detail,
                                                session_tag(session.id)))
        except AuditError:
            raise HTTPException(503, "audit unavailable; refusing") from None
        request.state.session = session
        return session

    return dep


def mutation_guard(action: str, tier: Tier):
    return csrf_session_guard(action, tier=tier)


def tier_ok(request: Request, tier: Tier) -> bool:
    """The tier ceiling allows ``tier`` (the guard's check, for code that only reports it, e.g. a plan)."""
    return request.app.state.settings.tier_ceiling >= tier


def require_tier(request: Request, action: str, tier: Tier, target: str = "") -> None:
    """The guard's tier check inside a handler whose tier depends on its input (a merge commit, a first agent):
    the same denial as ``csrf_session_guard`` (audit ``denied`` ``{"reason": "tier"}``, 403 "tier not enabled")."""
    if not tier_ok(request, tier):
        _deny_audit(request, action, "tier", target)
        raise HTTPException(403, "tier not enabled")


def audit_result(request: Request, action: str, target: str, result: str,
                 detail: dict[str, str | int] | None = None) -> None:
    """Record the outcome (ok | denied | error). Raises AuditError on failure."""
    get_audit(request).write(entry(request, action, target, result, detail))
