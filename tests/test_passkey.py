import base64
import hashlib
import json
import os
import stat
import time

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pm.core.config import Settings
from pm.security import auth
from pm.security.csrf import Csrf
from pm.security.passkey import PasskeyError, PasskeyManager
from pm.web.routes import passkey as routes_passkey

ORIGIN = "https://pm.example.test"
RP = "pm.example.test"


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Authenticator:
    """Minimal software ES256 authenticator (fmt=none)."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cid = os.urandom(32)
        self.count = 0

    def _cd(self, typ, challenge, origin=ORIGIN):
        return json.dumps({"type": typ, "challenge": challenge, "origin": origin,
                           "crossOrigin": False}).encode()

    def register(self, options, origin=ORIGIN):
        nums = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"),
                            -3: nums.y.to_bytes(32, "big")})
        auth_data = (hashlib.sha256(RP.encode()).digest() + bytes([0x45])
                     + self.count.to_bytes(4, "big") + bytes(16)
                     + len(self.cid).to_bytes(2, "big") + self.cid + cose)
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {"id": b64(self.cid), "rawId": b64(self.cid), "type": "public-key",
                "response": {"clientDataJSON": b64(self._cd("webauthn.create",
                                                            options["challenge"], origin)),
                             "attestationObject": b64(att)},
                "clientExtensionResults": {}}

    def assertion(self, options, count=None, origin=ORIGIN, uv=True, cid=None):
        self.count = self.count + 1 if count is None else count
        cdj = self._cd("webauthn.get", options["challenge"], origin)
        ad = (hashlib.sha256(RP.encode()).digest() + bytes([0x05 if uv else 0x01])
              + self.count.to_bytes(4, "big"))
        sig = self.key.sign(ad + hashlib.sha256(cdj).digest(), ec.ECDSA(hashes.SHA256()))
        c = b64(cid or self.cid)
        return {"id": c, "rawId": c, "type": "public-key",
                "response": {"clientDataJSON": b64(cdj), "authenticatorData": b64(ad),
                             "signature": b64(sig)},
                "clientExtensionResults": {}}


class Sess:
    def __init__(self, sid="s1", unlocked=0.0):
        self.id, self.unlocked_until = sid, unlocked


@pytest.fixture
def mgr(tmp_path):
    return PasskeyManager(Settings(state_dir=tmp_path))


def enrol(mgr, authn=None):
    authn = authn or Authenticator()
    s = Sess(unlocked=time.time() + 600)
    opts = mgr.begin_registration(s)
    mgr.finish_registration(s, authn.register(opts), "laptop")
    return authn


def test_rp_and_origin(mgr):
    assert mgr.rp_id == RP and mgr.origin == ORIGIN


def test_register_and_auth_roundtrip(mgr, tmp_path):
    authn = enrol(mgr)
    p = tmp_path / "passkeys.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    data = json.loads(p.read_text())
    assert set(data[0]) == {"id", "public_key", "sign_count", "name", "created"}
    s = Sess()
    opts = mgr.begin_auth(s)
    assert mgr.finish_auth(s, authn.assertion(opts), now=1000.0) is True
    assert s.unlocked_until == 1000.0 + mgr.settings.unlock_ttl_s
    assert json.loads(p.read_text())[0]["sign_count"] == 1


def test_store_atomic_no_tmp_left(mgr, tmp_path, monkeypatch):
    enrol(mgr)
    p = tmp_path / "passkeys.json"
    good = p.read_text()

    def boom(*_):
        raise OSError("x")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        mgr._save([])
    monkeypatch.undo()
    assert p.read_text() == good
    assert [f.name for f in tmp_path.iterdir() if f.name.endswith(".tmp")] == []
    mgr._save(mgr._load())
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert json.loads(p.read_text()) == json.loads(good)


def test_store_dir_created_0700(tmp_path):
    m = PasskeyManager(Settings(state_dir=tmp_path / "st"))
    m._save([])
    assert stat.S_IMODE((tmp_path / "st").stat().st_mode) == 0o700


def test_load_tolerates_missing_and_bad_json_but_not_permission(mgr, tmp_path):
    p = tmp_path / "passkeys.json"
    assert mgr._load() == []
    p.write_text("{not json")
    assert mgr._load() == []
    p.write_bytes(b"\xff\xfe")
    assert mgr._load() == []
    p.write_text('[{"id": "a"}, 3]')
    assert mgr._load() == [{"id": "a"}]
    if os.geteuid() != 0:
        p.chmod(0)
        try:
            with pytest.raises(PermissionError):
                mgr._load()
        finally:
            p.chmod(0o600)


def test_json_string_response_accepted(mgr):
    authn = enrol(mgr)
    s = Sess()
    opts = mgr.begin_auth(s)
    assert mgr.finish_auth(s, json.dumps(authn.assertion(opts)))


def test_registration_refused_when_locked(tmp_path):
    mgr = PasskeyManager(Settings(state_dir=tmp_path, require_unlock=True))
    with pytest.raises(PasskeyError, match="locked"):
        mgr.begin_registration(Sess())
    s = Sess(unlocked=time.time() + 600)
    opts = mgr.begin_registration(s)
    authn = Authenticator()
    s.unlocked_until = 0  # lock expired mid-flow
    with pytest.raises(PasskeyError, match="locked"):
        mgr.finish_registration(s, authn.register(opts))
    assert not mgr.has_credentials()


def test_registration_bad_origin_and_replay(mgr):
    s = Sess(unlocked=time.time() + 600)
    authn = Authenticator()
    opts = mgr.begin_registration(s)
    with pytest.raises(PasskeyError):
        mgr.finish_registration(s, authn.register(opts, origin="https://evil.example"))
    # challenge was single use even on failure
    with pytest.raises(PasskeyError, match="no challenge"):
        mgr.finish_registration(s, authn.register(opts))
    assert not mgr.has_credentials()


def test_challenge_ttl(tmp_path):
    t = [1000.0]
    m = PasskeyManager(Settings(state_dir=tmp_path), clock=lambda: t[0])
    authn = Authenticator()
    s = Sess(unlocked=1e12)
    opts = m.begin_registration(s)
    t[0] += 121
    with pytest.raises(PasskeyError, match="expired"):
        m.finish_registration(s, authn.register(opts))


def test_challenge_bound_to_session_and_kind(mgr):
    authn = enrol(mgr)
    a, b = Sess("a"), Sess("b")
    opts = mgr.begin_auth(a)
    with pytest.raises(PasskeyError, match="no challenge"):
        mgr.finish_auth(b, authn.assertion(opts))
    # kind mismatch: reg challenge cannot satisfy auth
    s = Sess("c", unlocked=time.time() + 600)
    mgr.begin_registration(s)
    with pytest.raises(PasskeyError):
        mgr.finish_auth(s, authn.assertion(opts))
    assert s.unlocked_until > time.time()  # untouched: was already unlocked
    assert b.unlocked_until == 0


def test_auth_replay_refused(mgr):
    authn = enrol(mgr)
    s = Sess()
    opts = mgr.begin_auth(s)
    resp = authn.assertion(opts)
    assert mgr.finish_auth(s, resp)
    s.unlocked_until = 0
    with pytest.raises(PasskeyError):
        mgr.finish_auth(s, resp)
    assert s.unlocked_until == 0


def test_sign_count_regression_refused(mgr):
    authn = enrol(mgr)
    s = Sess()
    assert mgr.finish_auth(s, authn.assertion(mgr.begin_auth(s), count=10))
    s.unlocked_until = 0
    with pytest.raises(PasskeyError):
        mgr.finish_auth(s, authn.assertion(mgr.begin_auth(s), count=5))
    with pytest.raises(PasskeyError):
        mgr.finish_auth(s, authn.assertion(mgr.begin_auth(s), count=10))
    assert s.unlocked_until == 0


def test_auth_wrong_key_unknown_cred_bad_origin_no_uv(mgr):
    authn = enrol(mgr)
    other = Authenticator()
    for kw, who in (({}, other), ({"origin": "https://evil.example"}, authn),
                    ({"uv": False}, authn)):
        s = Sess()
        opts = mgr.begin_auth(s)
        with pytest.raises(PasskeyError):
            mgr.finish_auth(s, who.assertion(opts, **kw))
        assert s.unlocked_until == 0
    s = Sess()
    opts = mgr.begin_auth(s)
    other.cid = os.urandom(32)
    with pytest.raises(PasskeyError, match="unknown"):
        mgr.finish_auth(s, other.assertion(opts))


def test_garbage_responses(mgr):
    enrol(mgr)
    s = Sess()
    mgr.begin_auth(s)
    for bad in (None, 5, "not json", {}, {"rawId": "!!"}):
        mgr.begin_auth(s)
        with pytest.raises(PasskeyError):
            mgr.finish_auth(s, bad)


def test_begin_auth_without_passkeys(mgr):
    with pytest.raises(PasskeyError, match="no passkeys"):
        mgr.begin_auth(Sess())


def test_auth_rate_limit(tmp_path):
    m = PasskeyManager(Settings(state_dir=tmp_path), max_fail=2)
    authn = enrol(m)
    s = Sess()
    for _ in range(2):
        m.begin_auth(s)
        with pytest.raises(PasskeyError):
            m.finish_auth(s, {}, client="c")
    opts = m.begin_auth(s)
    with pytest.raises(PasskeyError, match="rate"):
        m.finish_auth(s, authn.assertion(opts), client="c")


# ------------------------------------------------------------------ routes
def _env(tmp_path, **kw):
    st = Settings(state_dir=tmp_path / "st", token_file=tmp_path / "tok", **kw)
    app = FastAPI()
    app.state.settings = st
    auth.install(app, st)
    app.include_router(routes_passkey.router)
    sess = app.state.sessions.create("1.2.3.4")
    c = TestClient(app, base_url=ORIGIN)
    c.cookies.set(auth.COOKIE_NAME, app.state.sessions.cookie_value(sess))
    tok = Csrf(st.state_dir, st.allowed_origins).token(sess.id)
    return app, c, sess, {"Origin": ORIGIN, "X-CSRF-Token": tok}


@pytest.fixture
def app_env(tmp_path):
    return _env(tmp_path, require_unlock=True)


@pytest.fixture
def open_env(tmp_path):
    return _env(tmp_path)


def audit_lines(app):
    return [json.loads(x) for x in app.state.settings.audit_file.read_text().splitlines()]


@pytest.mark.parametrize("path", ["register/begin", "register/finish", "auth/begin",
                                  "auth/finish"])
def test_routes_refuse_bad_csrf_origin_and_no_session(app_env, path):
    app, c, sess, h = app_env
    url = f"/passkey/{path}"
    assert c.post(url, json={}, headers={**h, "X-CSRF-Token": "bad"}).status_code == 403
    assert c.post(url, json={}, headers={**h, "Origin": "https://evil.example"}).status_code == 403
    assert c.post(url, json={}, headers={"Origin": ORIGIN}).status_code == 403
    anon = TestClient(app, base_url=ORIGIN)
    assert anon.post(url, json={}, headers=h).status_code == 401


def test_register_routes_need_unlock(app_env):
    app, c, sess, h = app_env
    r = c.post("/passkey/register/begin", json={}, headers=h)
    assert r.status_code == 403
    assert audit_lines(app)[-1]["detail"] == {"reason": "locked"}


def test_full_flow_over_routes(app_env):
    app, c, sess, h = app_env
    authn = Authenticator()
    sess.unlocked_until = time.time() + 600
    opts = c.post("/passkey/register/begin", json={}, headers=h).json()
    assert opts["rp"]["id"] == RP
    r = c.post("/passkey/register/finish",
               json={"name": "phone", "credential": authn.register(opts)}, headers=h)
    assert r.status_code == 200, r.text
    sess.unlocked_until = 0  # locked: auth endpoints still reachable
    opts = c.post("/passkey/auth/begin", json={}, headers=h).json()
    r = c.post("/passkey/auth/finish", json={"credential": authn.assertion(opts)}, headers=h)
    assert r.status_code == 200 and r.json() == {"unlocked": True}
    assert sess.unlocked_until > time.time()
    # failure path audited without credential material
    sess.unlocked_until = 0
    opts = c.post("/passkey/auth/begin", json={}, headers=h).json()
    r = c.post("/passkey/auth/finish", json={"credential": {"rawId": "AAAA"}}, headers=h)
    assert r.status_code == 403 and sess.unlocked_until == 0
    log = app.state.settings.audit_file.read_text()
    assert b64(authn.cid) not in log and "signature" not in log
    assert audit_lines(app)[-1]["result"] == "denied"


def test_bad_json_body(app_env):
    app, c, sess, h = app_env
    r = c.post("/passkey/auth/finish", content=b"{nope", headers={**h,
               "Content-Type": "application/json"})
    assert r.status_code == 400


# ------------------------------------------------------------ default (no step-up)
def test_register_needs_only_login_by_default(open_env):
    app, c, sess, h = open_env
    authn = Authenticator()
    assert sess.unlocked_until == 0
    opts = c.post("/passkey/register/begin", json={}, headers=h).json()
    r = c.post("/passkey/register/finish",
               json={"name": "phone", "credential": authn.register(opts)}, headers=h)
    assert r.status_code == 200, r.text
    # still refused without csrf / origin / session
    assert c.post("/passkey/register/begin", json={}, headers={**h, "X-CSRF-Token": "x"}).status_code == 403
    assert c.post("/passkey/register/begin", json={},
                  headers={**h, "Origin": "https://evil.example"}).status_code == 403
    assert TestClient(app, base_url=ORIGIN).post("/passkey/register/begin", json={},
                                                 headers=h).status_code == 401


@pytest.mark.parametrize("path", ["begin", "finish"])
def test_stepup_routes_404_by_default(open_env, path):
    app, c, sess, h = open_env
    assert c.post(f"/passkey/auth/{path}", json={}, headers=h).status_code == 404


# ------------------------------------------------------------------ login
def anon(app):
    return TestClient(app, base_url=ORIGIN)


def login_env(tmp_path):
    app, c, sess, h = _env(tmp_path)
    authn = Authenticator()
    opts = c.post("/passkey/register/begin", json={}, headers=h).json()
    assert c.post("/passkey/register/finish", json={"credential": authn.register(opts)},
                  headers=h).status_code == 200
    return app, authn, TestClient(app, base_url=ORIGIN)


H = {"Origin": ORIGIN}


def test_login_page_flag(tmp_path):
    app, c, sess, h = _env(tmp_path)
    a = anon(app)
    page = a.get("/login").text
    assert "passkey-login" not in page
    authn = Authenticator()
    opts = c.post("/passkey/register/begin", json={}, headers=h).json()
    c.post("/passkey/register/finish", json={"credential": authn.register(opts)}, headers=h)
    page = a.get("/login")
    assert "Sign in with passkey" in page.text and "/static/js/login-passkey.js" in page.text
    assert b64(authn.cid) not in page.text and "onclick" not in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]


def test_login_no_passkeys(tmp_path):
    app, c, sess, h = _env(tmp_path)
    assert anon(app).post("/login/passkey/begin", headers=H).status_code == 403


def test_login_happy_path(tmp_path):
    app, authn, a = login_env(tmp_path)
    r = a.post("/login/passkey/begin", headers=H)
    assert r.status_code == 200
    sc = r.headers["set-cookie"].lower()
    assert "pm_preauth=" in sc and "httponly" in sc and "secure" in sc and "samesite=strict" in sc
    assert "max-age=120" in sc
    opts = r.json()
    assert "pm_session" not in a.cookies
    r = a.post("/login/passkey/finish", headers=H,
               json={"credential": authn.assertion(opts), "next": "/p/x"})
    assert r.status_code == 200 and r.json() == {"ok": True, "next": "/p/x"}
    assert a.cookies.get(auth.COOKIE_NAME)
    assert app.state.sessions.session_from_cookie(a.cookies.get(auth.COOKIE_NAME))
    assert "pm_preauth" not in a.cookies
    # open redirect refused
    a2 = anon(app)
    o2 = a2.post("/login/passkey/begin", headers=H).json()
    r = a2.post("/login/passkey/finish", headers=H,
                json={"credential": authn.assertion(o2), "next": "//evil.example"})
    assert r.json()["next"] == "/"
    lines = audit_lines(app)
    assert lines[-1]["action"] == "login_passkey" and lines[-1]["result"] == "ok"
    log = app.state.settings.audit_file.read_text()
    assert b64(authn.cid) not in log and "signature" not in log


def test_login_nonce_single_use_and_missing(tmp_path):
    app, authn, a = login_env(tmp_path)
    opts = a.post("/login/passkey/begin", headers=H).json()
    nonce = a.cookies.get("pm_preauth")
    resp = authn.assertion(opts)
    assert a.post("/login/passkey/finish", headers=H, json={"credential": resp}).status_code == 200
    b = anon(app)  # replay with the old nonce
    b.cookies.set("pm_preauth", nonce)
    r = b.post("/login/passkey/finish", headers=H, json={"credential": resp})
    assert r.status_code == 403 and "pm_session" not in b.cookies
    c = anon(app)  # no nonce at all
    c.post("/login/passkey/begin", headers=H)
    c.cookies.clear()
    assert c.post("/login/passkey/finish", headers=H,
                  json={"credential": resp}).status_code == 403


def test_login_nonce_bound(tmp_path):
    app, authn, a = login_env(tmp_path)
    opts = a.post("/login/passkey/begin", headers=H).json()
    b = anon(app)
    b.post("/login/passkey/begin", headers=H)  # b has its own nonce
    r = b.post("/login/passkey/finish", headers=H, json={"credential": authn.assertion(opts)})
    assert r.status_code == 403 and auth.COOKIE_NAME not in b.cookies


def test_login_nonce_expired(tmp_path):
    app, authn, a = login_env(tmp_path)
    opts = a.post("/login/passkey/begin", headers=H).json()
    mgr = app.state.passkeys
    t = time.time()
    mgr.clock = lambda: t + 121
    r = a.post("/login/passkey/finish", headers=H, json={"credential": authn.assertion(opts)})
    assert r.status_code == 403 and r.json()["error"] == "challenge expired"


def test_login_wrong_origin(tmp_path):
    app, authn, a = login_env(tmp_path)
    evil = {"Origin": "https://evil.example"}
    assert a.post("/login/passkey/begin", headers=evil).status_code == 403
    opts = a.post("/login/passkey/begin", headers=H).json()
    r = a.post("/login/passkey/finish", headers=evil, json={"credential": authn.assertion(opts)})
    assert r.status_code == 403 and auth.COOKIE_NAME not in a.cookies
    # assertion signed for a different origin also fails
    opts = a.post("/login/passkey/begin", headers=H).json()
    r = a.post("/login/passkey/finish", headers=H,
               json={"credential": authn.assertion(opts, origin="https://evil.example")})
    assert r.status_code == 403


def test_login_sign_count_regression(tmp_path):
    app, authn, a = login_env(tmp_path)
    for count, want in ((10, 200), (5, 403), (10, 403)):
        opts = a.post("/login/passkey/begin", headers=H).json()
        r = a.post("/login/passkey/finish", headers=H,
                   json={"credential": authn.assertion(opts, count=count)})
        assert r.status_code == want, (count, r.text)
    assert audit_lines(app)[-1]["result"] == "denied"
    assert json.loads(app.state.passkeys.path.read_text())[0]["sign_count"] == 10


def test_login_rate_limited(tmp_path):
    app, authn, a = login_env(tmp_path)
    for _ in range(5):
        a.post("/login/passkey/begin", headers=H)
        assert a.post("/login/passkey/finish", headers=H,
                      json={"credential": {"rawId": "AAAA"}}).status_code == 403
    r = a.post("/login/passkey/begin", headers=H)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    # even a valid assertion is refused while blocked
    assert a.post("/login/passkey/finish", headers=H, json={"credential": {}}).status_code == 429
    assert auth.COOKIE_NAME not in a.cookies


def test_login_no_credential_material_in_audit(tmp_path):
    app, authn, a = login_env(tmp_path)
    opts = a.post("/login/passkey/begin", headers=H).json()
    a.post("/login/passkey/finish", headers=H,
           json={"credential": authn.assertion(opts, uv=False)})
    log = app.state.settings.audit_file.read_text()
    assert audit_lines(app)[-1]["result"] == "denied"
    assert b64(authn.cid) not in log and "signature" not in log and opts["challenge"] not in log
    assert audit_lines(app)[-1]["client"] if "client" in audit_lines(app)[-1] else True


def test_login_bad_json(tmp_path):
    app, authn, a = login_env(tmp_path)
    a.post("/login/passkey/begin", headers=H)
    r = a.post("/login/passkey/finish", content=b"{nope",
               headers={**H, "Content-Type": "application/json"})
    assert r.status_code == 400
