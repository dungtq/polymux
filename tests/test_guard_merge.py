"""Characterization of every session/Origin/CSRF route guard (control /unlock*, passkey, mutation_guard).

The guard of each real route is taken from its router and mounted on a stub endpoint, so this pins the
status, detail text and audit lines of the guard itself, whatever implements it. Written before the
guards were merged into ``security.guard.csrf_session_guard``; it must stay green unchanged.
"""
import time
from dataclasses import dataclass, field

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from pm.core.config import Settings
from pm.core.models import Tier
from pm.security.audit import AuditError
from pm.security.csrf import Csrf
from pm.security.guard import mutation_guard
from pm.web.routes import control as routes_control
from pm.web.routes import passkey as routes_passkey

ORIGIN = "https://pm.example.test"
STEPUP_OFF = "step-up disabled (require_unlock is off)"


@dataclass(frozen=True)
class Spec:
    action: str
    stepup: bool  # 404 while require_unlock is off
    locked: bool  # refuses a locked session when require_unlock is on
    form_csrf: bool  # accepts the token from an urlencoded form field, not only X-CSRF-Token
    texts: tuple[str, str, str]  # origin, csrf, locked
    tier: Tier | None = None  # tier check + attempt detail {"tier": n}


CONTROL = ("bad origin", "bad csrf", "")
PASSKEY = ("origin", "csrf", "locked")
MUTATION = ("bad origin", "bad csrf token", "locked: unlock required")

SPECS = {
    ("control", "/unlock"): Spec("unlock", True, False, True, CONTROL),
    ("control", "/unlock/provision"): Spec("unlock_provision", True, False, True, CONTROL),
    ("passkey", "/passkey/register/begin"): Spec("passkey_register_begin", False, True, False, PASSKEY),
    ("passkey", "/passkey/register/finish"): Spec("passkey_register_finish", False, True, False, PASSKEY),
    ("passkey", "/passkey/auth/begin"): Spec("passkey_auth_begin", True, False, False, PASSKEY),
    ("passkey", "/passkey/auth/finish"): Spec("passkey_auth_finish", True, False, False, PASSKEY),
    ("control", "/tmux/session/new"): Spec("tmux_session_new", False, True, True, MUTATION, Tier.SAFE),
    ("control", "/service/reload"): Spec("pm_reload", False, True, True, MUTATION, Tier.MESSAGE),
    ("direct", "/mut"): Spec("mut", False, True, True, MUTATION, Tier.MESSAGE),
}
ROUTERS = {"control": routes_control.router, "passkey": routes_passkey.router}


def _route_guard(router_name: str, path: str):
    if router_name == "direct":
        return mutation_guard("mut", Tier.MESSAGE)
    (route,) = [r for r in ROUTERS[router_name].routes
                if isinstance(r, APIRoute) and r.path == path and "POST" in r.methods]
    (dep,) = route.dependant.dependencies
    return dep.call


@dataclass
class Sess:
    id: str = "sess1"
    unlocked_until: float = field(default_factory=lambda: time.time() + 600)


class Store:
    def __init__(self, s):
        self.s = s

    def from_request(self, request):
        return self.s if request.cookies.get("sid") else None


class RecAudit:
    def __init__(self, fail=False):
        self.lines, self.fail = [], fail

    def write(self, e):
        if self.fail:
            raise AuditError("down")
        self.lines.append((e.action, e.target, e.result, dict(e.detail)))


def build(tmp_path, key, *, require_unlock=True, ceiling=Tier.MESSAGE, fail_audit=False, **sess):
    settings = Settings(state_dir=tmp_path / "st", tier_ceiling=ceiling, require_unlock=require_unlock)
    app = FastAPI()
    app.state.settings = settings
    s = Sess(**sess)
    app.state.sessions = Store(s)
    app.state.audit = RecAudit(fail_audit)
    guard = _route_guard(*key)

    @app.post("/x")
    async def x(request: Request, session=Depends(guard)):
        assert request.state.session is session
        return {"sid": session.id}

    c = TestClient(app, cookies={"sid": "x"})
    tok = Csrf(settings.state_dir, settings.allowed_origins).token(s.id)
    return c, tok, app.state.audit, s


def hdr(tok=None, origin=ORIGIN):
    h = {"Origin": origin} if origin else {}
    if tok:
        h["X-CSRF-Token"] = tok
    return h


KEYS = list(SPECS)
IDS = [p for _, p in KEYS]


@pytest.fixture(params=KEYS, ids=IDS)
def key(request):
    return request.param


def test_every_guard_found():
    for k in KEYS:
        assert callable(_route_guard(*k))


def test_happy_path_and_attempt_line(tmp_path, key):
    spec = SPECS[key]
    c, tok, audit, _ = build(tmp_path, key)
    r = c.post("/x", headers=hdr(tok))
    assert r.status_code == 200 and r.json() == {"sid": "sess1"}
    attempt = {"tier": int(spec.tier)} if spec.tier is not None else {}
    assert audit.lines == [(spec.action, "", "attempt", attempt)]


def test_require_unlock_off(tmp_path, key):
    spec = SPECS[key]
    c, tok, audit, _ = build(tmp_path, key, require_unlock=False, unlocked_until=0.0)
    r = c.post("/x", headers=hdr(tok))
    if spec.stepup:
        assert (r.status_code, r.json()["detail"]) == (404, STEPUP_OFF)
        c.cookies.clear()  # 404 comes before the session check
        r = c.post("/x", headers=hdr(tok))
        assert (r.status_code, r.json()["detail"]) == (404, STEPUP_OFF)
        assert audit.lines == []
    else:
        assert r.status_code == 200  # locked session is fine while require_unlock is off
        assert [ln[2] for ln in audit.lines] == ["attempt"]


def test_no_session_401(tmp_path, key):
    c, tok, audit, _ = build(tmp_path, key)
    c.cookies.clear()
    r = c.post("/x", headers=hdr(tok, origin="https://evil.example"))
    assert (r.status_code, r.json()["detail"]) == (401, "login required")
    assert audit.lines == []


@pytest.mark.parametrize("origin", ["https://evil.example", None])
def test_bad_or_missing_origin(tmp_path, key, origin):
    spec = SPECS[key]
    c, tok, audit, _ = build(tmp_path, key)
    r = c.post("/x", headers=hdr(tok, origin=origin))
    assert (r.status_code, r.json()["detail"]) == (403, spec.texts[0])
    assert audit.lines == [(spec.action, "", "denied", {"reason": "origin"})]


@pytest.mark.parametrize("how", ["missing", "bad-header", "bad-form", "other-session"])
def test_bad_or_missing_csrf(tmp_path, key, how):
    spec = SPECS[key]
    c, tok, audit, _ = build(tmp_path, key)
    if how == "missing":
        r = c.post("/x", headers=hdr())
    elif how == "bad-header":
        r = c.post("/x", headers=hdr("bad"))
    elif how == "bad-form":
        r = c.post("/x", headers=hdr(), data={"csrf": "bad"})
    else:
        other = Csrf(tmp_path / "st", (ORIGIN,)).token("someone-else")
        r = c.post("/x", headers=hdr(other))
    assert (r.status_code, r.json()["detail"]) == (403, spec.texts[1])
    assert audit.lines == [(spec.action, "", "denied", {"reason": "csrf"})]


def test_csrf_source_form_vs_header(tmp_path, key):
    spec = SPECS[key]
    c, tok, audit, _ = build(tmp_path, key)
    r = c.post("/x", headers=hdr(), data={"csrf": tok})
    if spec.form_csrf:
        assert r.status_code == 200
        assert [ln[2] for ln in audit.lines] == ["attempt"]
    else:
        assert (r.status_code, r.json()["detail"]) == (403, spec.texts[1])
        assert audit.lines == [(spec.action, "", "denied", {"reason": "csrf"})]


def test_header_wins_over_form(tmp_path, key):
    spec = SPECS[key]
    c, tok, audit, _ = build(tmp_path, key)
    r = c.post("/x", headers=hdr("bad"), data={"csrf": tok})
    assert (r.status_code, r.json()["detail"]) == (403, spec.texts[1])


def test_locked(tmp_path, key):
    spec = SPECS[key]
    c, tok, audit, _ = build(tmp_path, key, unlocked_until=0.0)
    r = c.post("/x", headers=hdr(tok))
    if spec.locked:
        assert (r.status_code, r.json()["detail"]) == (403, spec.texts[2])
        assert audit.lines == [(spec.action, "", "denied", {"reason": "locked"})]
    else:  # step-up itself must work while locked
        assert r.status_code == 200
        assert [ln[2] for ln in audit.lines] == ["attempt"]


def test_tier_ceiling(tmp_path, key):
    spec = SPECS[key]
    c, tok, audit, _ = build(tmp_path, key, ceiling=Tier.VIEW)
    r = c.post("/x", headers=hdr(tok))
    if spec.tier is not None:
        assert (r.status_code, r.json()["detail"]) == (403, "tier not enabled")
        assert audit.lines == [(spec.action, "", "denied", {"reason": "tier"})]
    else:
        assert r.status_code == 200


def test_check_order_origin_before_csrf_before_locked(tmp_path, key):
    spec = SPECS[key]
    c, tok, audit, _ = build(tmp_path, key, unlocked_until=0.0)
    r = c.post("/x", headers=hdr("bad", origin="https://evil.example"))
    assert r.json()["detail"] == spec.texts[0]
    r = c.post("/x", headers=hdr("bad"))
    assert r.json()["detail"] == spec.texts[1]


def test_audit_failure(tmp_path, key):
    spec = SPECS[key]
    c, tok, audit, _ = build(tmp_path, key, fail_audit=True)
    r = c.post("/x", headers=hdr(tok))
    assert (r.status_code, r.json()["detail"]) == (503, "audit unavailable; refusing")
    r = c.post("/x", headers=hdr(tok, origin="https://evil.example"))  # deny line failing: still 403
    assert (r.status_code, r.json()["detail"]) == (403, spec.texts[0])
