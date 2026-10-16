"""projects.apisend: port allowlist, path/URL checks, header stripping, no redirects, capped and masked reply."""
import asyncio
import dataclasses

import httpx
import pytest

from pm.core.config import Settings
from pm.core.errors import Denied, Invalid, PmError
from pm.projects import apisend

S = dataclasses.replace(Settings(), builder_api_ports=(5000, 8093, 8094, 0, 70000))


@pytest.fixture
def seen(monkeypatch):
    got = []

    def handler(req: httpx.Request):
        got.append(req)
        if req.url.path == "/redir":
            return httpx.Response(302, headers={"Location": "http://evil.example/"})
        if req.url.path == "/big":
            return httpx.Response(200, content=b"a" * (apisend.MAX_REPLY + 10))
        return httpx.Response(200, headers={"Set-Cookie": "s=1", "X-Ok": "1"},
                              json={"ok": True, "token": "password=hunter2secret"})

    monkeypatch.setattr(apisend, "_transport", httpx.MockTransport(handler))
    return got


def _send(settings, req):
    return asyncio.run(apisend.send(settings, req))


def _req(**kw):
    return {"port": 5000, "method": "GET", "path": "/x", "query": "", "headers": {}, "body": "", **kw}


def test_allowed_ports():
    assert apisend.allowed_ports(S) == (5000,)
    assert apisend.allowed_ports(Settings()) == ()


def test_send_ok_strips_and_masks(seen):
    out = _send(S, _req(method="post", path="/x/y", query="a=1&b=c@d",
                               headers={"Cookie": "pm=1", "Authorization": "Bearer t", "X-CSRF-Token": "c",
                                        "Host": "evil", "X-Mine": "v", "Content-Type": "application/json"},
                               body='{"a":1}'))
    r = seen[0]
    assert str(r.url) == "http://127.0.0.1:5000/x/y?a=1&b=c@d" and r.method == "POST" and r.content == b'{"a":1}'
    assert "cookie" not in r.headers and "authorization" not in r.headers and "x-csrf-token" not in r.headers
    assert r.headers["host"] == "127.0.0.1:5000" and r.headers["x-mine"] == "v"
    assert out["status"] == 200 and not out["truncated"]
    names = [k.lower() for k, _v in out["headers"]]
    assert "set-cookie" not in names and "x-ok" in names
    assert "hunter2secret" not in out["body"] and '"ok"' in out["body"]


def test_redirect_not_followed_and_cap(seen):
    out = _send(S, _req(path="/redir"))
    assert out["status"] == 302 and len(seen) == 1
    out = _send(S, _req(path="/big"))
    assert out["truncated"] and len(out["body"]) == apisend.MAX_REPLY


@pytest.mark.parametrize("path", ["//evil.example/x", "http://evil/x", "/a@b", "/a\\b", "x", "", "/a b", "/a#b",
                                  "/a?b=1", "/x/http://y", "/a\r\nb", None, 5])
def test_bad_paths(seen, path):
    with pytest.raises(Invalid):
        _send(S, _req(path=path))
    assert not seen


@pytest.mark.parametrize("port", [8093, 8094, 4000, 0, 70000, "abc", None, True])
def test_bad_ports(seen, port):
    with pytest.raises((Denied, Invalid)):
        _send(S, _req(port=port))
    assert not seen


def test_bad_input(seen):
    for req in (None, [], _req(method="TRACE"), _req(method="CONNECT"), _req(headers={"Bad Name": "x"}),
                _req(headers={"X": "a\r\nInjected: 1"}), _req(headers=["x"]), _req(query="a=1#frag"),
                _req(body="x" * (apisend.MAX_BODY + 1))):
        with pytest.raises(Invalid):
            _send(S, req)
    assert not seen
    with pytest.raises(Denied):
        _send(Settings(), _req())  # nothing enabled by default


def test_unreachable(monkeypatch):
    def boom(req):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(apisend, "_transport", httpx.MockTransport(boom))
    with pytest.raises(PmError) as e:
        _send(S, _req())
    assert e.value.status == 502


def test_build_url_and_audit_detail():
    assert str(apisend.build_url(5000, "/a", "q=1")) == "http://127.0.0.1:5000/a?q=1"
    d = apisend.audit_detail(_req(path="/p", query="secret=1", body="secret", headers={"A": "secret"}))
    assert d == {"method": "GET", "port": "5000", "path": "/p"}
    assert apisend.audit_detail(None) == {"method": "", "port": "", "path": ""}


def test_parse_request_size_first():
    assert apisend.parse_request(b'{"a": 1}', "8") == {"a": 1}
    assert apisend.parse_request(b"nojson", None) is None
    for raw, cl in ((b"{}", str(apisend.MAX_REQUEST + 1)), (b"{}", "abc"), (b" " * (apisend.MAX_REQUEST + 1), None)):
        with pytest.raises(Invalid) as e:
            apisend.parse_request(raw, cl)
        assert e.value.status == 413
