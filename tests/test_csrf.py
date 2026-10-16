import stat

from starlette.datastructures import Headers

from pm.security.csrf import Csrf, RateLimiter


def mk(tmp_path):
    return Csrf(tmp_path, ("https://pm.example.test",))


def test_token_verify(tmp_path):
    c = mk(tmp_path)
    t = c.token("s1")
    assert c.verify("s1", t)
    assert not c.verify("s2", t)
    assert not c.verify("s1", None) and not c.verify("s1", "")
    assert stat.S_IMODE((tmp_path / "csrf.key").stat().st_mode) == 0o600
    assert mk(tmp_path).token("s1") == t  # persisted secret


def test_origin(tmp_path):
    c = mk(tmp_path)
    assert c.origin_ok(Headers({"origin": "https://pm.example.test"}))
    assert not c.origin_ok(Headers({"origin": "https://evil.example"}))
    assert not c.origin_ok(Headers({}))
    assert c.origin_ok(Headers({"sec-fetch-site": "same-origin"}))
    assert not c.origin_ok(Headers({"sec-fetch-site": "cross-site"}))


def test_rate_limiter():
    now = [0.0]
    r = RateLimiter(2, 10, clock=lambda: now[0])
    assert r.allow("a") and r.allow("a") and not r.allow("a")
    assert r.allow("b")
    now[0] = 11
    assert r.allow("a")
