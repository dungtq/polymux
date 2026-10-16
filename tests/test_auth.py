import stat

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from pm.core.config import Settings
from pm.security.auth import COOKIE_NAME, Session, install, require_session, verify_token


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    settings = Settings(token_file=tmp_path / "pm" / "token", state_dir=tmp_path)
    clock = Clock()
    app = FastAPI()
    app.state.settings = settings
    install(app, settings, clock)

    @app.get("/secret")
    def secret(s: Session = Depends(require_session)):
        return {"ok": True}

    @app.post("/mutate")
    def mutate(s: Session = Depends(require_session)):
        return {"ok": True}

    client = TestClient(app, base_url="https://testserver", follow_redirects=False)
    token = settings.token_file.read_text().strip()
    return app, client, token, clock, settings


def login(client, token, **kw):
    return client.post("/login", data={"token": token, **kw})


def test_token_file_created_0600(env):
    _, _, token, _, settings = env
    assert len(token) >= 43
    assert stat.S_IMODE(settings.token_file.stat().st_mode) == 0o600


def test_login_ok_and_cookie_flags(env):
    _, c, token, _, settings = env
    r = login(c, token, next="/secret")
    assert r.status_code == 303 and r.headers["location"] == "/secret"
    sc = r.headers["set-cookie"].lower()
    assert COOKIE_NAME in sc and "httponly" in sc and "secure" in sc and "samesite=strict" in sc
    assert f"max-age={settings.cookie_ttl_s}" in sc
    assert c.get("/secret").status_code == 200


def test_login_fail(env):
    _, c, *_ = env
    r = login(c, "nope")
    assert r.status_code == 401 and "set-cookie" not in r.headers
    assert c.get("/secret").status_code == 303


def test_pv_token_rejected(env, tmp_path):
    _, c, token, _, settings = env
    pv_token = "pv-token-" + "x" * 40
    assert not verify_token(pv_token, settings)
    assert login(c, pv_token).status_code == 401
    assert not verify_token("", settings)


def test_unauthenticated_redirect_vs_401(env):
    _, c, *_ = env
    r = c.get("/secret")
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    assert c.post("/mutate").status_code == 401
    assert c.get("/secret", headers={"HX-Request": "true"}).status_code == 401


def test_idle_expiry_and_touch(env):
    _, c, token, clock, settings = env
    login(c, token)
    clock.t += settings.idle_ttl_s - 10
    assert c.get("/secret").status_code == 200  # touches
    clock.t += settings.idle_ttl_s - 10
    assert c.get("/secret").status_code == 200
    clock.t += settings.idle_ttl_s + 1
    assert c.get("/secret").status_code == 303


def test_absolute_expiry(env):
    _, c, token, clock, settings = env
    login(c, token)
    for _ in range(20):
        clock.t += settings.idle_ttl_s - 60
        if c.get("/secret").status_code != 200:
            break
    assert clock.t - 1_000_000.0 <= settings.cookie_ttl_s + settings.idle_ttl_s
    assert c.get("/secret").status_code == 303


def test_forged_cookie(env):
    _, c, *_ = env
    c.cookies.set(COOKIE_NAME, "garbage")
    assert c.get("/secret").status_code == 303


def test_logout_revokes(env):
    app, c, token, *_ = env
    login(c, token)
    saved = c.cookies.get(COOKIE_NAME)
    assert c.post("/logout").status_code == 303
    c.cookies.set(COOKIE_NAME, saved)
    assert c.get("/secret").status_code == 303


def test_rate_limit_backoff(env):
    _, c, token, clock, _ = env
    for _ in range(5):
        assert login(c, "bad").status_code == 401
    r = login(c, token)  # even the right token is blocked during backoff
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    clock.t += 3
    assert login(c, token).status_code == 303


def test_bad_origin_and_open_redirect(env):
    _, c, token, *_ = env
    assert c.post("/login", data={"token": token}, headers={"origin": "https://evil.com"}).status_code == 403
    r = login(c, token, next="//evil.com")
    assert r.headers["location"] == "/"


def test_login_page_renders_and_escapes(env):
    _, c, *_ = env
    r = c.get("/login", params={"next": '/"><script>'})
    assert r.status_code == 200 and "<script>" not in r.text and "<form" in r.text


def test_session_unlocked_until_field(env):
    app, c, token, clock, _ = env
    login(c, token)
    s = next(iter(app.state.sessions._sessions.values()))
    assert s.unlocked_until == 0.0
    s.unlocked_until = clock.t + 600


def test_dev_token_logs_in_and_is_separate(env):
    app, client, token, clock, settings = env
    dev = (settings.token_file.parent / "dev-token")
    assert dev.exists() and stat.S_IMODE(dev.stat().st_mode) == 0o600
    dt = dev.read_text().strip()
    assert dt and dt != token
    assert login(client, dt).status_code == 303
    assert client.get("/secret").status_code == 200
    assert login(client, token).status_code == 303


def test_dev_token_revoked_by_new_value(env):
    app, client, token, clock, settings = env
    dev = settings.token_file.parent / "dev-token"
    old = dev.read_text().strip()
    dev.write_text("another-token-value\n")
    assert login(client, old).status_code == 401


def test_revoke_others_keeps_current_and_saves_once(tmp_path, monkeypatch):
    from pm.security import sessions_store as sstore
    from pm.security.auth import SessionStore

    st = SessionStore(Settings(state_dir=tmp_path), Clock())
    keep, a, b = st.create("me"), st.create("x"), st.create("y")
    st2 = SessionStore(Settings(state_dir=tmp_path), Clock())  # restored: all three wait as pending
    pending_extra = st2.create("z")
    saves = []
    monkeypatch.setattr(st2, "_save", lambda: saves.append(1))
    assert st2.revoke_others(sstore.sid_hash(keep.id)) == 3 and len(saves) == 1
    assert [r["h"] for r in st2.listing()] == [sstore.sid_hash(keep.id)]
    assert st2.get(keep.id) is not None and st2.get(pending_extra.id) is None
    assert st.revoke_others(sstore.sid_hash(keep.id)) == 2 and st.get(a.id) is None and st.get(b.id) is None
    assert st.revoke_others(sstore.sid_hash(keep.id)) == 0
    with pytest.raises(ValueError):
        st.revoke_others("")
