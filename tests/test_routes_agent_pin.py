"""POST /a/<id>/pin: guards, audit, and the pinned row moving to the Pinned group (Inbox, project page)."""

import re

from helpers import login
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.agents import pinned
from pm.web.routes import control as routes_control


def _setup(env):  # noqa: F811
    s, c = env
    object.__setattr__(s, "tier_ceiling", 1)
    login(s, c)
    tok = re.search(r'name="csrf" value="([^"]+)"', c.get("/").text).group(1)
    return s, c, tok, {"Origin": s.allowed_origins[0]}


def _group(html: str, start: str, end: str) -> str:
    return html.split(start, 1)[1].split(end, 1)[0] if start in html else ""


def test_guards(env):  # noqa: F811
    s, c, tok, h = _setup(env)
    assert c.post("/a/claude-12/pin", data={"pinned": "1"}, headers=h).status_code == 403  # no CSRF
    assert c.post("/a/claude-12/pin", data={"csrf": tok, "pinned": "1"}).status_code == 403  # no Origin
    for bad in ("Claude-12", "x", "claude-12-"):
        assert c.post(f"/a/{bad}/pin", data={"csrf": tok, "pinned": "1"}, headers=h).status_code in (400, 404)
    assert pinned.load(s) == set()
    assert "agent_pin" in s.audit_file.read_text()  # the refused one is audited too


def test_pin_moves_row_to_pinned_group_and_back(env):  # noqa: F811
    s, c, tok, h = _setup(env)
    inbox = c.get("/").text
    assert 'data-key="g-pinned"' not in inbox and 'action="/a/claude-12/pin"' in inbox
    assert 'aria-pressed="false" aria-label="Pin claude-12"' in inbox
    r = c.post("/a/claude-12/pin", data={"csrf": tok, "pinned": "1"}, headers={**h, "HX-Request": "true"})
    assert r.status_code == 200 and r.headers["hx-trigger"] == "pm:refresh" and r.text == ""
    assert pinned.load(s) == {"claude-12"}
    for t in (c.get("/").text, c.get("/p/alpha/live", headers={"HX-Request": "true"}).text):
        grp = _group(t, 'data-key="g-pinned"', "</div>\n</div>")
        assert 'data-key="w-claude-12"' in grp and 'aria-pressed="true" aria-label="Unpin claude-12"' in grp
        assert 'name="pinned" value="0"' in grp
        # Pinned sits inside the one list of active rows, before them (one parent: the live morph keeps the row)
        lst = t.split('role="list" data-key="l-', 1)[1]
        assert lst.index('data-key="g-pinned"') < lst.index('data-key="w-claude-12"')
    r = c.post("/a/claude-12/pin", data={"csrf": tok, "pinned": "0"}, headers=h, follow_redirects=False)
    assert r.status_code == 303 and pinned.load(s) == set()
    assert 'data-key="g-pinned"' not in c.get("/").text
    audit = s.audit_file.read_text()
    assert audit.count('"agent_pin"') >= 2 and '"pinned":true' in audit and '"pinned":false' in audit


def test_store_error_is_audited(env, monkeypatch):  # noqa: F811
    s, c, tok, h = _setup(env)

    def boom(*a, **k):
        raise OSError("busy")
    monkeypatch.setattr(routes_control.pinned, "set_pin", boom)
    assert c.post("/a/claude-12/pin", data={"csrf": tok, "pinned": "1"}, headers=h).status_code == 502
    assert '"error"' in s.audit_file.read_text()


def test_audit_failure_503(env, monkeypatch):  # noqa: F811
    s, c, tok, h = _setup(env)
    from pm.security.audit import AuditError

    def boom(*a, **k):
        raise AuditError("x")
    monkeypatch.setattr(routes_control.controlctl, "audit_result", boom)
    assert c.post("/a/claude-12/pin", data={"csrf": tok, "pinned": "1"}, headers=h).status_code == 503
