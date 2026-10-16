"""The one Origin check (csrf.origin_ok) in its two modes, against frozen copies of the old checks."""

from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from starlette.datastructures import Headers

from pm.security import auth
from pm.security.csrf import Csrf, origin_ok

ALLOWED = ("https://pm.example.test",)
HOST = "pm.example.test"


def old_strict(headers, allowed):
    """Csrf.origin_ok before the merge (allowed already trimmed in Csrf.__init__)."""
    allowed = tuple(o.rstrip("/") for o in allowed)
    origin = headers.get("origin")
    if origin:
        return origin.rstrip("/") in allowed
    return headers.get("sec-fetch-site") == "same-origin"


def old_lenient(headers, allowed, scheme):
    """auth._origin_ok before the merge."""
    origin = headers.get("origin")
    if origin is None:
        return True
    if origin in allowed:
        return True
    u = urlsplit(origin)
    return u.netloc == headers.get("host") and u.scheme == scheme


# (headers, strict result, lenient result) with request scheme "https"
CASES = [
    ({}, False, True),                                                     # missing, no host
    ({"host": HOST}, False, True),                                         # missing
    ({"sec-fetch-site": "same-origin"}, True, True),
    ({"sec-fetch-site": "cross-site"}, False, True),
    ({"sec-fetch-site": "same-site"}, False, True),
    ({"origin": "", "host": HOST}, False, False),                          # empty is not "missing"
    ({"origin": "", "sec-fetch-site": "same-origin"}, True, False),
    ({"origin": "https://pm.example.test"}, True, True),
    ({"origin": "https://pm.example.test", "sec-fetch-site": "cross-site"}, True, True),
    ({"origin": "https://pm.example.test/"}, True, False),                       # trailing slash: strict trims
    ({"origin": "https://pm.example.test/", "host": HOST}, True, True),          # lenient: host+scheme match
    ({"origin": "https://evil.example"}, False, False),
    ({"origin": "https://evil.example", "sec-fetch-site": "same-origin"}, False, False),
    ({"origin": "https://evil.example", "host": "evil.example"}, False, True),  # same host+scheme
    ({"origin": "http://evil.example", "host": "evil.example"}, False, False),  # scheme mismatch
    ({"origin": "http://pm.example.test", "host": HOST}, False, False),
    ({"origin": "null"}, False, False),
    ({"origin": "null", "host": HOST}, False, False),
    ({"origin": "null", "sec-fetch-site": "same-origin"}, False, False),
    ({"origin": "HTTPS://PM.EXAMPLE.TEST"}, False, False),                # case: exact
    ({"origin": "https://PM.EXAMPLE.TEST", "host": HOST}, False, False),
    ({"origin": "HTTPS://pm.example.test", "host": HOST}, False, True),          # urlsplit lowercases scheme
    ({"origin": "https://pm.example.test:443", "host": HOST}, False, False),
    ({"origin": "https://pm.example.test.evil.example"}, False, False),
]


def _request(headers, scheme="https", allowed=ALLOWED):
    return SimpleNamespace(headers=headers, url=SimpleNamespace(scheme=scheme),
                           app=SimpleNamespace(state=SimpleNamespace(
                               settings=SimpleNamespace(allowed_origins=allowed))))


@pytest.mark.parametrize("raw,strict,lenient", CASES)
def test_strict_mode_csrf(tmp_path, raw, strict, lenient):
    h = Headers(raw)
    assert old_strict(h, ALLOWED) is strict
    assert Csrf(tmp_path, ALLOWED).origin_ok(h) is strict
    assert origin_ok(h, ALLOWED) is strict


@pytest.mark.parametrize("raw,strict,lenient", CASES)
def test_lenient_mode_auth(raw, strict, lenient):
    h = Headers(raw)
    assert old_lenient(h, ALLOWED, "https") is lenient
    assert auth._origin_ok(_request(h)) is lenient


@pytest.mark.parametrize("raw", [c[0] for c in CASES])
@pytest.mark.parametrize("scheme", ["https", "http"])
@pytest.mark.parametrize("allowed", [ALLOWED, ("https://pm.example.test/",), ("http://pm.example.test", "https://x.example.test")])
def test_both_modes_match_old(tmp_path, raw, scheme, allowed):
    h = Headers(raw)
    assert Csrf(tmp_path, allowed).origin_ok(h) is old_strict(h, allowed)
    assert auth._origin_ok(_request(h, scheme, allowed)) is old_lenient(h, allowed, scheme)


def test_strict_ignores_host_unless_given():
    h = Headers({"origin": "https://evil.example", "host": "evil.example"})
    assert origin_ok(h, ALLOWED) is False
    assert origin_ok(h, ALLOWED, host="evil.example", scheme="https") is True
    assert origin_ok(h, ALLOWED, host="evil.example") is False  # both needed


def old_term_history(headers, allowed):
    """routes/term.term_history Origin check before the merge (True = passes)."""
    origin = headers.get("origin")
    return not (origin is not None and origin.rstrip("/") not in tuple(o.rstrip("/") for o in allowed))


def old_term_ws(headers, allowed):
    """routes/term.term_ws Origin check before the merge (True = passes)."""
    origin = (headers.get("origin") or "").rstrip("/")
    return origin in tuple(o.rstrip("/") for o in allowed)


TERM_EXTRA = [
    ({"origin": ""},),
    ({"origin": "/"},),
    ({"origin": "https://pm.example.test//"},),
    ({"origin": " https://pm.example.test"},),
    ({"origin": "https://pm.example.test", "host": "evil.example"},),
]


@pytest.mark.parametrize("raw", [c[0] for c in CASES] + [c[0] for c in TERM_EXTRA])
@pytest.mark.parametrize("allowed", [ALLOWED, ("https://pm.example.test/",), ("http://pm.example.test", "https://x.example.test")])
def test_term_checks_match_old(raw, allowed):
    h = Headers(raw)
    assert origin_ok(h, allowed, allow_missing=True) is old_term_history(h, allowed)
    assert origin_ok(h, allowed, require_present=True) is old_term_ws(h, allowed)


def test_require_present_rejects_missing_and_empty():
    for raw in ({}, {"sec-fetch-site": "same-origin"}, {"origin": ""},
                {"origin": "", "sec-fetch-site": "same-origin"}):
        assert origin_ok(Headers(raw), ALLOWED, require_present=True) is False
    assert origin_ok(Headers({"origin": "https://pm.example.test/"}), ALLOWED, require_present=True) is True
