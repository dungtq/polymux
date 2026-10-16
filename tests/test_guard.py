import os
import time
from dataclasses import dataclass, replace

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from pm.core.config import Settings
from pm.core.models import Tier
from pm.security.audit import message_detail
from pm.security.csrf import Csrf
from pm.security.guard import audit_result, mutation_guard

ORIGIN = "https://pm.example.test"


@dataclass
class Sess:
    id: str = "sess1"
    unlocked_until: float = 0.0


class Store:
    def __init__(self, s):
        self.s = s

    def from_request(self, request):
        return self.s if request.cookies.get("sid") else None


def build(tmp_path, ceiling=Tier.MESSAGE, require_unlock=False, **sess):
    settings = Settings(state_dir=tmp_path / "st", tier_ceiling=ceiling,
                        require_unlock=require_unlock)
    app = FastAPI()
    app.state.settings = settings
    s = Sess(**sess)
    app.state.sessions = Store(s)
    ran = []

    @app.post("/send")
    async def send(request: Request, _=Depends(mutation_guard("send", Tier.MESSAGE))):
        ran.append(1)
        audit_result(request, "send", "%1", "ok", message_detail("TOPSECRET text"))
        return {"ok": True}

    c = TestClient(app, cookies={"sid": "x"})
    tok = Csrf(settings.state_dir, settings.allowed_origins).token(s.id)
    return app, c, s, tok, ran, settings


def post(c, tok, origin=ORIGIN, **kw):
    h = {"Origin": origin} if origin else {}
    return c.post("/send", data={"csrf": tok}, headers=h, **kw)


def test_happy_and_no_message_text(tmp_path):
    app, c, s, tok, ran, st = build(tmp_path, unlocked_until=time.time() + 60)
    assert post(c, tok).status_code == 200 and ran
    txt = st.audit_file.read_text()
    assert "TOPSECRET" not in txt and "sha256" in txt
    r = c.post("/send", headers={"Origin": ORIGIN, "X-CSRF-Token": tok})
    assert r.status_code == 200


def test_unauth(tmp_path):
    app, c, s, tok, ran, _ = build(tmp_path, unlocked_until=time.time() + 60)
    c.cookies.clear()
    assert post(c, tok).status_code == 401 and not ran


def test_bad_origin(tmp_path):
    app, c, s, tok, ran, _ = build(tmp_path, unlocked_until=time.time() + 60)
    assert post(c, tok, origin="https://evil.example").status_code == 403
    assert post(c, tok, origin=None).status_code == 403
    assert not ran


def test_missing_or_bad_csrf(tmp_path):
    app, c, s, tok, ran, _ = build(tmp_path, unlocked_until=time.time() + 60)
    assert c.post("/send", headers={"Origin": ORIGIN}).status_code == 403
    assert post(c, "bad").status_code == 403 and not ran


def test_locked_when_require_unlock(tmp_path):
    app, c, s, tok, ran, _ = build(tmp_path, require_unlock=True)
    assert post(c, tok).status_code == 403 and not ran
    s.unlocked_until = time.time() + 60
    assert post(c, tok).status_code == 200 and ran


def test_default_needs_no_unlock(tmp_path):
    app, c, s, tok, ran, st = build(tmp_path)
    assert not st.require_unlock and s.unlocked_until == 0
    assert post(c, tok).status_code == 200 and ran


def test_default_still_refuses_session_origin_csrf(tmp_path):
    app, c, s, tok, ran, _ = build(tmp_path)
    assert post(c, tok, origin="https://evil.example").status_code == 403
    assert post(c, tok, origin=None).status_code == 403
    assert post(c, "bad").status_code == 403
    assert c.post("/send", headers={"Origin": ORIGIN}).status_code == 403
    c.cookies.clear()
    assert post(c, tok).status_code == 401
    assert not ran


def test_tier_too_high(tmp_path):
    app, c, s, tok, ran, _ = build(tmp_path, ceiling=Tier.SAFE)
    assert post(c, tok).status_code == 403 and not ran


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores perms")
def test_audit_failure_refuses(tmp_path):
    app, c, s, tok, ran, st = build(tmp_path, unlocked_until=time.time() + 60)
    st.state_dir.chmod(0o500)
    try:
        assert post(c, tok).status_code == 503 and not ran
    finally:
        st.state_dir.chmod(0o700)
