import re

from helpers import login, ntfy_item
from helpers import ntfy_env as env  # noqa: F401  (fixture)

from pm.web.routes import ntfy as routes_ntfy

HDR = {"origin": "https://pm.example.test"}


def _tok(c):
    return re.search(r'name="csrf" value="([^"]+)"', c.get("/").text).group(1)


def _setup(env, n=3):  # noqa: F811
    s, c, st, dev = env
    login(s, c)
    st["items"] = [ntfy_item(i, str(dev / "alpha")) for i in range(n, 0, -1)]
    return s, c, st, dev, _tok(c)


def test_guards_and_errors(env):  # noqa: F811
    s, c, st, dev, tok = _setup(env)
    assert c.post("/ntfy/pin", data={"id": "1"}, headers=HDR).status_code == 403
    assert c.post("/ntfy/unpin", data={"id": "1"}, headers=HDR).status_code == 403
    for bad in ("", "a b", "../x", "x" * 101):
        assert c.post("/ntfy/pin", data={"csrf": tok, "id": bad}, headers=HDR).status_code == 400
    assert c.post("/ntfy/pin", data={"csrf": tok, "id": "nope"}, headers=HDR).status_code == 404
    assert c.post("/ntfy/unpin", data={"csrf": tok, "id": "nope"}, headers=HDR).status_code == 404


def test_pin_unpin_panel(env):  # noqa: F811
    s, c, st, dev, tok = _setup(env)
    assert "ntfy-pins" not in c.get("/ntfy/panel?scope=all").text
    r = c.post("/ntfy/pin", data={"csrf": tok, "id": "3"}, headers=HDR)  # "3" is the newest: the list row's message
    assert r.status_code == 200 and r.headers["hx-trigger"] == "pm:refresh"
    t = c.get("/ntfy/panel?scope=all").text
    pinned, rest = t.split('class="tabs ntfy-tabs tabs-seg"')
    assert 'data-key="pin:3"' in pinned and 'data-id="pin:3"' in pinned and "data-p=" not in pinned
    assert 'action="/ntfy/unpin"' in pinned and 'value="3"' in pinned
    assert rest.count('action="/ntfy/unpin"') == 1 and rest.count('action="/ntfy/pin"') == 2  # the list row offers Unpin too
    for scope in ("inbox", "system"):
        assert "ntfy-pins" not in c.get(f"/ntfy/panel?scope={scope}").text
    st["items"] = []  # the message left the feed: the pin stays and can be removed
    assert 'data-key="pin:3"' in c.get("/ntfy/panel?scope=all").text
    assert c.post("/ntfy/unpin", data={"csrf": tok, "id": "3"}, headers=HDR).status_code == 200
    assert "ntfy-pins" not in c.get("/ntfy/panel?scope=all").text
    audit = s.audit_file.read_text()
    assert "ntfy_pin" in audit and "ntfy_unpin" in audit


def test_pins_not_counted(env):  # noqa: F811
    s, c, st, dev, tok = _setup(env)
    before = c.get("/ntfy").text
    c.post("/ntfy/pin", data={"csrf": tok, "id": "2"}, headers=HDR)
    after = c.get("/ntfy").text
    stats = lambda t: re.search(r'inbox-stats muted">([^<]*)<', t).group(1)  # noqa: E731
    assert stats(before) == stats(after)


def test_audit_failure_503(env, monkeypatch):  # noqa: F811
    s, c, st, dev, tok = _setup(env)
    from pm.security.audit import AuditError

    def boom(*a, **k):
        raise AuditError("x")
    monkeypatch.setattr(routes_ntfy, "audit_result", boom)
    assert c.post("/ntfy/pin", data={"csrf": tok, "id": "2"}, headers=HDR).status_code == 503


def test_store_oserror_500_audited(env, monkeypatch):  # noqa: F811
    s, c, st, dev, tok = _setup(env)

    def boom(*a, **k):
        raise OSError("locked")
    monkeypatch.setattr(routes_ntfy.ntfy_pins, "pin", boom)
    monkeypatch.setattr(routes_ntfy.ntfy_pins, "unpin", boom)
    assert c.post("/ntfy/pin", data={"csrf": tok, "id": "2"}, headers=HDR).status_code == 500
    assert c.post("/ntfy/unpin", data={"csrf": tok, "id": "2"}, headers=HDR).status_code == 500
    audit = s.audit_file.read_text()
    assert "ntfy_pin" in audit and "ntfy_unpin" in audit and "error" in audit
