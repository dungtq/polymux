"""Token login, signed session cookie, server-side session store.

The token lives in settings.token_file (0600). It is never printed, logged or
compared against any other credential (pv's token is simply a different
string and fails verification).
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from jinja2 import Environment, FileSystemLoader, select_autoescape

from pm.core.config import Settings
from pm.core.models import AuditEntry
from pm.security import sessions_store as sstore
from pm.security.csrf import origin_ok
from pm.security.guard import get_audit
from pm.security.passkey import manager_of

log = logging.getLogger("pm.auth")
COOKIE_NAME = "pm_session"
Clock = Callable[[], float]

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)


# ------------------------------------------------------------------ token
def _ensure_file(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_urlsafe(32) + "\n")


def dev_token_path(settings: Settings) -> Path:
    return settings.dev_token_file or settings.token_file.with_name("dev-token")


def ensure_token(settings: Settings) -> None:
    """Create the token files (0600) if missing. Returns nothing on purpose."""
    _ensure_file(settings.token_file)
    _ensure_file(dev_token_path(settings))


def _read(path: Path) -> bytes:
    try:
        return path.read_text().strip().encode()
    except OSError:
        return b""


def verify_token_kind(candidate: str, settings: Settings) -> str | None:
    """"token" or "dev-token" when the candidate matches, else None. Both are checked every time."""
    if not candidate:
        return None
    cand = candidate.strip().encode()
    ensure_token(settings)
    kind = None
    for name, path in (("token", settings.token_file), ("dev-token", dev_token_path(settings))):
        real = _read(path)
        if real and hmac.compare_digest(cand, real) and kind is None:
            kind = name
    return kind


def verify_token(candidate: str, settings: Settings) -> bool:
    return verify_token_kind(candidate, settings) is not None


# ------------------------------------------------------------------ sessions
@dataclass
class Session:
    id: str
    created: float
    last_seen: float
    client: str = ""
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    unlocked_until: float = 0.0  # set by the unlock agent


class SessionStore:
    SAVE_INTERVAL_S = 30.0

    def __init__(self, settings: Settings, clock: Clock = time.time):
        self.settings = settings
        self.clock = clock
        # in memory keyed by raw id (never written); sessions restored from disk wait
        # in _pending keyed by sha256(id) until their cookie shows up.
        self._sessions: dict[str, Session] = {}
        self._pending: dict[str, Session] = {}
        self._path = Path(settings.state_dir) / "sessions.json"
        self._last_save = 0.0
        self._dirty = False
        self._persist = True
        try:
            secret = sstore.load_secret(settings.state_dir)
        except OSError as exc:
            log.warning("session secret not persisted (%s)", type(exc).__name__)
            secret = secrets.token_bytes(32)
            self._persist = False
        self._signer = URLSafeTimedSerializer(secret, salt="pm-session")
        if self._persist:
            self._load()

    def _expired(self, s: Session, now: float) -> bool:
        return now - s.created > self.settings.cookie_ttl_s or now - s.last_seen > self.settings.idle_ttl_s

    def _load(self) -> None:
        now = self.clock()
        for e in sstore.read_entries(self._path):
            s = Session(id="", created=e["created"], last_seen=e["last_seen"],
                        client=e["client"], csrf=e["csrf"], unlocked_until=0.0)
            if not self._expired(s, now):
                self._pending[e["h"]] = s
        self._trim()

    def _trim(self) -> None:
        allx = [(sstore.sid_hash(k), k, v) for k, v in self._sessions.items()]
        allx += [(h, None, v) for h, v in self._pending.items()]
        if len(allx) > sstore.MAX_SESSIONS:
            allx.sort(key=lambda t: t[2].last_seen, reverse=True)
            allx = allx[: sstore.MAX_SESSIONS]
            self._sessions = {k: v for _, k, v in allx if k is not None}
            self._pending = {h: v for h, k, v in allx if k is None}

    def _save(self) -> None:
        now = self.clock()
        for d in (self._sessions, self._pending):
            for k in [k for k, s in d.items() if self._expired(s, now)]:
                del d[k]
        self._trim()
        self._dirty = False
        self._last_save = now
        if not self._persist:
            return
        hashed = [(sstore.sid_hash(k), s) for k, s in self._sessions.items()]
        hashed += list(self._pending.items())
        entries = [{"h": h, "created": s.created, "last_seen": s.last_seen,
                    "client": s.client, "csrf": s.csrf} for h, s in hashed]
        try:
            sstore.write_entries(self._path, entries)
        except Exception as exc:
            log.warning("sessions not saved (%s)", type(exc).__name__)

    def _maybe_save(self) -> None:
        if self._dirty and self.clock() - self._last_save >= self.SAVE_INTERVAL_S:
            self._save()

    def create(self, client: str = "") -> Session:
        now = self.clock()
        s = Session(id=secrets.token_urlsafe(32), created=now, last_seen=now, client=client)
        self._sessions[s.id] = s
        self._save()
        return s

    def get(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        s = self._sessions.get(sid)
        if s is None:
            s = self._pending.pop(sstore.sid_hash(sid), None)
            if s is None:
                return None
            s.id = sid
            self._sessions[sid] = s
        if self._expired(s, self.clock()):
            self._sessions.pop(sid, None)
            self._dirty = True
            return None
        self._maybe_save()
        return s

    def mark_seen(self, s: Session) -> None:
        s.last_seen = self.clock()
        self._dirty = True
        self._maybe_save()

    def touch(self, sid: str) -> Session | None:
        s = self.get(sid)
        if s:
            self.mark_seen(s)
        return s

    def revoke(self, sid: str | None) -> None:
        if not sid:
            return
        a = self._sessions.pop(sid, None)
        b = self._pending.pop(sstore.sid_hash(sid), None)
        if a is not None or b is not None:
            self._save()

    def listing(self, current_sid: str | None = None) -> list[dict]:
        """Sessions for the Settings page: hashed id only, never the raw id."""
        cur = sstore.sid_hash(current_sid) if current_sid else ""
        rows = [(sstore.sid_hash(k), v) for k, v in self._sessions.items()]
        rows += list(self._pending.items())
        out = [{"h": h, "created": s.created, "last_seen": s.last_seen,
                "client": s.client, "current": h == cur} for h, s in rows]
        out.sort(key=lambda r: r["last_seen"], reverse=True)
        return out

    def revoke_hash(self, h: str) -> bool:
        for k in [k for k in self._sessions if sstore.sid_hash(k) == h]:
            del self._sessions[k]
            self._save()
            return True
        if self._pending.pop(h, None) is not None:
            self._save()
            return True
        return False

    def revoke_others(self, keep_hash: str) -> int:
        """Drop every session but the one hashed ``keep_hash`` (Settings "Revoke all others"); saved once.
        Returns how many were dropped."""
        if not keep_hash:
            raise ValueError("keep_hash required")
        gone = [k for k in self._sessions if sstore.sid_hash(k) != keep_hash]
        for k in gone:
            del self._sessions[k]
        pend = [h for h in self._pending if h != keep_hash]
        for h in pend:
            del self._pending[h]
        if gone or pend:
            self._save()
        return len(gone) + len(pend)

    def purge(self) -> None:
        now = self.clock()
        for d in (self._sessions, self._pending):
            for k in [k for k, s in d.items() if self._expired(s, now)]:
                del d[k]
                self._dirty = True
        self._maybe_save()

    # signed cookie value <-> session id
    def cookie_value(self, s: Session) -> str:
        return self._signer.dumps(s.id)

    def session_from_cookie(self, value: str | None) -> Session | None:
        if not value:
            return None
        try:
            sid = self._signer.loads(value, max_age=self.settings.cookie_ttl_s)
        except BadSignature:
            return None
        return self.get(sid)

    def from_request(self, request) -> Session | None:
        """Interface used by security.guard."""
        return self.session_from_cookie(request.cookies.get(COOKIE_NAME))


# ------------------------------------------------------------------ rate limit
class LoginLimiter:
    """5 free failures per client, then exponential backoff (2s, 4s, ... max 300s)."""

    FREE = 5

    def __init__(self, clock: Clock = time.time):
        self.clock = clock
        self._state: dict[str, tuple[int, float]] = {}  # client -> (failures, blocked_until)

    def retry_after(self, client: str) -> int:
        _, until = self._state.get(client, (0, 0.0))
        return max(0, int(until - self.clock() + 0.999))

    def failure(self, client: str) -> None:
        n, _ = self._state.get(client, (0, 0.0))
        n += 1
        wait = 0 if n < self.FREE else min(300, 2 ** (n - self.FREE + 1))
        self._state[client] = (n, self.clock() + wait)
        if len(self._state) > 10000:
            self._state.pop(next(iter(self._state)))

    def success(self, client: str) -> None:
        self._state.pop(client, None)


# ------------------------------------------------------------------ helpers
def client_id(request: Request) -> str:
    host = request.client.host if request.client else "?"
    if host in {"127.0.0.1", "::1"}:  # behind nginx on loopback
        fwd = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[-1].strip()[:64]
    return host


def safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith(("//", "/\\")) and "\n" not in value:
        return value
    return "/"


def _origin_ok(request: Request) -> bool:
    """Lenient mode (login, logout, passkey login): missing Origin ok, same host+scheme ok, no slash trim."""
    settings: Settings = request.app.state.settings
    return origin_ok(request.headers, settings.allowed_origins, allow_missing=True,
                     host=request.headers.get("host"), scheme=request.url.scheme, trim_slash=False)


def _set_cookie(resp: Response, value: str, settings: Settings) -> None:
    resp.set_cookie(
        COOKIE_NAME, value, max_age=settings.cookie_ttl_s,
        httponly=True, secure=True, samesite="strict", path="/",
    )


def current_session(request: Request) -> Session | None:
    store: SessionStore = request.app.state.sessions
    s = store.session_from_cookie(request.cookies.get(COOKIE_NAME))
    if s:
        store.mark_seen(s)
    return s


def require_session(request: Request) -> Session:
    s = current_session(request)
    if s:
        return s
    if request.method == "GET" and "hx-request" not in request.headers:
        nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise HTTPException(303, headers={"Location": "/login?next=" + _quote(nxt)})
    raise HTTPException(401, "login required")


def _quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="/")


# ------------------------------------------------------------------ router
router = APIRouter()


LOGIN_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
             "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def _audit_login(request: Request, method: str, result: str, reason: str = "") -> None:
    """Best effort (login must not depend on the audit disk); no secrets."""
    try:
        get_audit(request).write(AuditEntry(time.time(), client_id(request), f"login_{method}",
                                            "", result, {"reason": reason} if reason else {}))
    except Exception:
        pass


def _page(request: Request, next_: str, error: str = "", status: int = 200,
          headers: dict | None = None) -> HTMLResponse:
    try:
        has_passkeys = manager_of(request.app).has_credentials()
    except Exception:
        has_passkeys = False
    html = _env.get_template("login.html").render(next=next_, error=error,
                                                  has_passkeys=has_passkeys)
    return HTMLResponse(html, status_code=status, headers={
        "Cache-Control": "no-store", "Content-Security-Policy": LOGIN_CSP, **(headers or {})})


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/") -> Response:
    return _page(request, safe_next(next))


@router.post("/login")
def login_submit(request: Request, token: str = Form(""), next: str = Form("/")) -> Response:
    settings: Settings = request.app.state.settings
    store: SessionStore = request.app.state.sessions
    limiter: LoginLimiter = request.app.state.login_limiter
    nxt = safe_next(next)
    if not _origin_ok(request):
        _audit_login(request, "token", "denied", "origin")
        return _page(request, nxt, "Bad origin.", 403)
    client = client_id(request)
    wait = limiter.retry_after(client)
    if wait:
        _audit_login(request, "token", "denied", "rate")
        return _page(request, nxt, f"Too many attempts. Retry in {wait}s.", 429, {"Retry-After": str(wait)})
    kind = verify_token_kind(token, settings)
    if kind is None:
        limiter.failure(client)
        _audit_login(request, "token", "denied", "token")
        return _page(request, nxt, "Invalid token.", 401)
    limiter.success(client)
    _audit_login(request, kind, "ok")
    session = store.create(client)
    resp = RedirectResponse(nxt, status_code=303)
    _set_cookie(resp, store.cookie_value(session), settings)
    return resp


@router.post("/logout")
def logout(request: Request, session: Session = Depends(require_session)) -> Response:
    if not _origin_ok(request):
        raise HTTPException(403, "bad origin")
    request.app.state.sessions.revoke(session.id)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/", secure=True, httponly=True, samesite="strict")
    return resp


def install(app, settings: Settings, clock: Clock = time.time) -> None:
    ensure_token(settings)
    app.state.sessions = SessionStore(settings, clock)
    app.state.login_limiter = LoginLimiter(clock)
    app.include_router(router)
