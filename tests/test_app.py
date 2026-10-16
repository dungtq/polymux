from dataclasses import replace

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from pm.core.config import Settings
from pm.core.models import Tier
from pm.security.guard import mutation_guard
from pm.web.app import create_app


@pytest.fixture
def app(tmp_path):
    s = Settings(
        state_dir=tmp_path / "state",
        token_file=tmp_path / "cfg/token",
        totp_file=tmp_path / "cfg/totp",
        tier_ceiling=Tier.SAFE,
    )
    return create_app(s)


def test_healthz(app):
    c = TestClient(app, base_url="https://testserver")
    body = c.get("/healthz").json()
    assert body["ok"] is True
    assert set(body) == {"ok", "loop_lag_ms", "snapshot_age_s"}


def test_guard_uses_auth_sessions(app):
    @app.post("/t")
    def t(_=Depends(mutation_guard("test", Tier.SAFE))):
        return {"ok": True}

    c = TestClient(app, base_url="https://testserver")
    assert c.post("/t").status_code in (401, 403)

    token = app.state.settings.token_file.read_text().strip()
    r = c.post("/login", data={"token": token}, follow_redirects=False)
    assert r.status_code == 303
    # logged in but no CSRF: still refused, and not with 401
    r = c.post("/t", headers={"Origin": "https://pm.example.test"})
    assert r.status_code in (400, 403)
    # default (no step-up): login + Origin + CSRF is enough
    from pm.security.csrf import Csrf
    sid = next(iter(app.state.sessions._sessions))
    tok = Csrf(app.state.settings.state_dir, app.state.settings.allowed_origins).token(sid)
    r = c.post("/t", headers={"Origin": "https://pm.example.test", "X-CSRF-Token": tok})
    assert r.status_code == 200


def test_login_page_and_passkey_routes_open(app):
    c = TestClient(app, base_url="https://pm.example.test")
    assert c.get("/login").status_code == 200
    # reachable without a session (403 = no passkeys enrolled, not 401/404)
    assert c.post("/login/passkey/begin", headers={"Origin": "https://pm.example.test"}).status_code == 403
    assert c.get("/unlock").status_code in (401, 404)


def test_events_hub_exists_without_polling(app):
    # disable_poll must not leave /events without a hub (it used to 500)
    assert app.state.hub is not None and app.state.tracker is not None
