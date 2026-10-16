"""Agents page > Auth fragment: status, login flow (start / poll / send / cancel), logout; guards and audit.

The domain module ``pm.agents.inventory.auth`` is replaced by recording fakes: nothing here runs an
agent CLI or touches a tmux server.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pm.agents.inventory import auth as agentsauth
from pm.agents.inventory import read as agentsinv
from pm.core.config import Settings
from pm.core.errors import Conflict
from pm.core.models import AgentInfo, Inventory, Tier
from pm.security.audit import AuditError
from pm.web.app import create_app

ORIGIN = "https://pm.example.test"
CODE = "sekrit-CODE-4f9a1b2c3d"
BIN = str(Path("~/.local/bin/claude").expanduser())
URL = "https://claude.ai/oauth/authorize?code=true&client_id=abc"


def _inv() -> Inventory:
    agents = (
        AgentInfo(name="claude", label="Claude", installed=True, binary="~/.local/bin/claude"),
        AgentInfo(name="cline", label="Cline", installed=True, binary="~/.local/bin/cline"),
        AgentInfo(name="codex", label="Codex", installed=False, binary=""),
    )
    return Inventory(agents, (), (), (), (), (), 1.0)


class Fake:
    """Recording stand-in for the domain functions."""

    def __init__(self) -> None:
        self.state = "in"
        self.view = agentsauth.LoginView(active=False)
        self.calls: list[tuple] = []
        self.logout_error: Exception | None = None

    def status(self, name, binary, *, fresh=False, account="", settings=None):
        self.calls.append(("status", name, binary, fresh) if not account else ("status", name, binary, fresh, account))
        return agentsauth.AuthStatus(name=name, supported=True, state=self.state,
                                     account="me@example.com" if self.state == "in" else "",
                                     method="claude.ai" if self.state == "in" else "", plan="max" if self.state == "in" else "")

    def login_view(self, settings, name, *, account=""):
        self.calls.append(("view", name) if not account else ("view", name, account))
        return self.view

    def login_start(self, settings, name, binary, *, account=""):
        self.calls.append(("start", name, binary) if not account else ("start", name, binary, account))
        self.view = agentsauth.LoginView(active=True, window_id="@9", pane_id="%9",
                                         text="Browser didn't open? Use the url below\n" + URL, urls=(URL,))
        return self.view

    def login_send(self, settings, name, text, *, account=""):
        self.calls.append(("send", name, text) if not account else ("send", name, text, account))

    def login_cancel(self, settings, name, *, account=""):
        self.calls.append(("cancel", name) if not account else ("cancel", name, account))
        self.view = agentsauth.LoginView(active=False)

    def logout(self, name, binary, *, account="", settings=None):
        self.calls.append(("logout", name, binary) if not account else ("logout", name, binary, account))
        if self.logout_error:
            raise self.logout_error
        self.state = "out"

    def clear_cache(self, name=None, *, account=""):
        self.calls.append(("clear", name) if not account else ("clear", name, account))

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


@pytest.fixture
def fake(monkeypatch):
    inv = _inv()
    monkeypatch.setattr(agentsinv, "get_inventory", lambda *a, **k: inv)
    monkeypatch.setattr(agentsinv, "find_agent", lambda i, n: next((a for a in i.agents if a.name == n), None))
    f = Fake()
    monkeypatch.setattr(agentsauth, "cached", lambda name, binary, *, account="": None)
    for fn in ("status", "login_view", "login_start", "login_send", "login_cancel", "logout", "clear_cache"):
        monkeypatch.setattr(agentsauth, fn, getattr(f, fn))
    return f


@pytest.fixture
def app(tmp_path, fake):
    s = Settings(state_dir=tmp_path / "state", token_file=tmp_path / "cfg/token",
                 totp_file=tmp_path / "cfg/totp", tier_ceiling=Tier.SAFE,
                 accounts={"claude": ("work", "alt"), "codex": ("team",)})
    return create_app(s)


@pytest.fixture
def client(app):
    c = TestClient(app, base_url=ORIGIN)
    token = app.state.settings.token_file.read_text().strip()
    assert c.post("/login", data={"token": token}, follow_redirects=False).status_code == 303
    return c


def csrf_of(c: TestClient) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', c.get("/agents/claude").text).group(1)


def post(c, url, csrf=True, origin=ORIGIN, **data):
    d = {"csrf": csrf_of(c), **data} if csrf else dict(data)
    return c.post(url, data=d, headers={"Origin": origin} if origin else {})


def audit_text(app) -> str:
    f = app.state.settings.audit_file
    return f.read_text() if f.exists() else ""


def test_login_required(app):
    c = TestClient(app, base_url=ORIGIN)
    r = c.get("/agents/claude/auth", follow_redirects=False)
    assert r.status_code in (303, 307, 401)
    for path in ("login", "send", "cancel", "logout"):
        r = c.post(f"/agents/claude/auth/{path}", data={"code": CODE}, headers={"Origin": ORIGIN}, follow_redirects=False)
        assert r.status_code in (303, 307, 401, 403)


def test_detail_page_lazy_loads_auth(client):
    html = client.get("/agents/claude").text
    assert 'id="ag-auth"' in html and 'hx-get="/agents/claude/auth"' in html and 'hx-trigger="load"' in html


def test_status_logged_in(client, fake):
    r = client.get("/agents/claude/auth")
    assert r.status_code == 200
    t = r.text
    assert t.lstrip().startswith('<div id="ag-auth"')
    assert "Logged in" in t and "me@example.com" in t and "claude.ai" in t and "max" in t
    assert 'action="/agents/claude/auth/logout"' in t and 'hx-confirm="Log out of Claude?"' in t
    assert 'hx-get="/agents/claude/auth?fresh=1"' in t
    assert "/auth/login" not in t and "every 2s" not in t
    assert ("status", "claude", BIN, False) in fake.calls


def test_status_fresh_query(client, fake):
    client.get("/agents/claude/auth?fresh=1")
    assert ("status", "claude", BIN, True) in fake.calls


def test_status_logged_out(client, fake):
    fake.state = "out"
    t = client.get("/agents/claude/auth").text
    assert "Not logged in" in t and 'action="/agents/claude/auth/login"' in t and "Log out" not in t


def test_status_unknown_shows_login(client, fake):
    fake.state = "unknown"
    t = client.get("/agents/claude/auth").text
    assert "Unknown" in t and 'action="/agents/claude/auth/login"' in t


def test_unsupported_kind(client, fake):
    t = client.get("/agents/cline/auth").text
    assert "Not supported" in t and "<form" not in t
    assert "status" not in fake.names()
    assert post(client, "/agents/cline/auth/login").status_code == 400
    assert "start" not in fake.names()


def test_unknown_agent_404(client, fake):
    assert client.get("/agents/nope/auth").status_code == 404
    assert post(client, "/agents/nope/auth/login").status_code == 404
    assert "start" not in fake.names()


def test_csrf_and_origin_required(client, fake):
    assert post(client, "/agents/claude/auth/login", csrf=False).status_code == 403
    assert post(client, "/agents/claude/auth/login", origin="https://evil.example").status_code == 403
    assert post(client, "/agents/claude/auth/logout", csrf=False).status_code == 403
    assert "start" not in fake.names() and "logout" not in fake.names()


def test_login_start_renders_urls_and_form(client, app, fake):
    fake.state = "out"
    r = post(client, "/agents/claude/auth/login")
    assert r.status_code == 200
    t = r.text
    assert ("start", "claude", BIN) in fake.calls
    assert "Signing in" in t
    assert f'href="{URL.replace("&", "&amp;")}"' in t and 'target="_blank" rel="noopener noreferrer"' in t
    assert 'name="code"' in t and 'autocomplete="off"' in t and 'spellcheck="false"' in t
    assert 'action="/agents/claude/auth/send"' in t and 'action="/agents/claude/auth/cancel"' in t
    assert 'id="ag-auth-out"' in t and 'hx-get="/agents/claude/auth?poll=1"' in t and 'hx-trigger="every 2s"' in t
    # the polling block does not contain the code form, so a poll swap cannot wipe a typed code
    out = t[t.index('id="ag-auth-out"'):]
    out = out[: out.index("</div>")]
    assert 'name="code"' not in out
    assert "status" not in fake.names()  # running login: no status probe
    assert '"agents_auth_login"' in audit_text(app)


def test_non_http_urls_not_linked(client, fake):
    fake.view = agentsauth.LoginView(active=True, text="x", urls=("javascript:alert(1)", URL))
    t = client.get("/agents/claude/auth").text
    assert "javascript:" not in t and 'href="https://claude.ai/' in t


def test_poll_running_returns_output_only(client, fake):
    fake.view = agentsauth.LoginView(active=True, text="Paste code here if prompted >", urls=())
    r = client.get("/agents/claude/auth?poll=1")
    assert r.status_code == 200
    t = r.text
    assert t.lstrip().startswith('<div id="ag-auth-out"') and "Paste code here" in t
    assert 'name="code"' not in t and "ag-auth-line" not in t
    assert "HX-Retarget" not in r.headers
    assert "status" not in fake.names()


def test_poll_after_login_finished_swaps_whole_fragment(client, fake):
    fake.view = agentsauth.LoginView(active=True, text="Login successful", dead=True)
    r = client.get("/agents/claude/auth?poll=1")
    assert r.headers.get("HX-Retarget") == "#ag-auth" and r.headers.get("HX-Reswap") == "outerHTML"
    t = r.text
    assert t.lstrip().startswith('<div id="ag-auth"')
    assert "Login finished" in t and "Login successful" in t and ">Close<" in t
    assert "every 2s" not in t and 'name="code"' not in t
    assert "Logged in" in t
    assert ("status", "claude", BIN, True) in fake.calls


def test_poll_after_window_gone(client, fake):
    r = client.get("/agents/claude/auth?poll=1")
    assert r.headers.get("HX-Retarget") == "#ag-auth"
    assert ("status", "claude", BIN, True) in fake.calls


def test_send_audits_length_not_code(client, app, fake):
    fake.view = agentsauth.LoginView(active=True, text="Paste code >")
    r = post(client, "/agents/claude/auth/send", code=CODE)
    assert r.status_code == 200
    assert ("send", "claude", CODE) in fake.calls
    log = audit_text(app)
    assert '"agents_auth_send"' in log and str(len(CODE)) in log
    assert CODE not in log and CODE not in r.text


def test_send_empty_refused(client, app, fake):
    fake.view = agentsauth.LoginView(active=True, text="Paste code >")
    r = post(client, "/agents/claude/auth/send", code="   ")
    assert r.status_code == 200 and "Paste the code first" in r.text
    assert "send" not in fake.names()
    assert '"denied"' in audit_text(app)


def test_cancel(client, app, fake):
    fake.view = agentsauth.LoginView(active=True, text="x")
    r = post(client, "/agents/claude/auth/cancel")
    assert r.status_code == 200
    assert ("cancel", "claude") in fake.calls
    assert ("status", "claude", BIN, True) in fake.calls
    assert "every 2s" not in r.text and '"agents_auth_cancel"' in audit_text(app)


def test_logout(client, app, fake):
    r = post(client, "/agents/claude/auth/logout")
    assert r.status_code == 200
    assert ("logout", "claude", BIN) in fake.calls and ("clear", "claude") in fake.calls
    assert "Not logged in" in r.text
    assert ("status", "claude", BIN, True) in fake.calls
    assert '"agents_auth_logout"' in audit_text(app)


def test_logout_failure_shows_notice(client, app, fake):
    fake.logout_error = Conflict("logout failed")
    r = post(client, "/agents/claude/auth/logout")
    assert r.status_code == 200 and "logout failed" in r.text and "Logged in" in r.text
    assert '"error"' in audit_text(app)


def test_audit_failure_fails_closed(client, app, fake, monkeypatch):
    csrf = csrf_of(client)

    def boom(*a, **k):
        raise AuditError("nope")

    monkeypatch.setattr(app.state.audit, "write", boom)
    r = client.post("/agents/claude/auth/logout", data={"csrf": csrf}, headers={"Origin": ORIGIN})
    assert r.status_code == 503


def test_binary_passed_absolute(client, fake):
    """The inventory shows ``~/.local/bin/claude``; the domain must get the expanded absolute path."""
    client.get("/agents/claude/auth")
    binary = next(c[2] for c in fake.calls if c[0] == "status")
    assert binary.startswith("/") and binary.endswith("/.local/bin/claude")


def test_not_installed_no_login(client, fake):
    html = client.get("/agents/codex/auth").text
    assert "Not installed" in html and "Log in" not in html
    assert not [c for c in fake.calls if c[0] == "status"]
    assert post(client, "/agents/codex/auth/login").status_code == 400


# ---------------------------------------------------------------- /agents/auth.json (bulk, cache only)


def test_auth_json_login_required(app):
    c = TestClient(app, base_url=ORIGIN)
    assert c.get("/agents/auth.json", follow_redirects=False).status_code in (303, 307, 401)


def test_auth_json_from_cache_only(client, fake, monkeypatch):
    r = client.get("/agents/auth.json")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    rows = {a["name"]: a for a in r.json()["agents"]}
    assert list(rows) == ["claude", "cline", "codex"]
    assert rows["claude"] == {"name": "claude", "supported": True, "state": "unknown", "account": "", "method": "",
                              "plan": "", "error": "",
                              "accounts": [{"name": "work", "supported": True, "state": "unknown", "account": "",
                                            "method": "", "plan": "", "error": ""},
                                           {"name": "alt", "supported": True, "state": "unknown", "account": "",
                                            "method": "", "plan": "", "error": ""}]}
    assert rows["cline"]["supported"] is False and rows["codex"]["supported"] is False
    assert "status" not in fake.names()  # nothing cached: no probe either
    seen = []
    monkeypatch.setattr(agentsauth, "cached", lambda name, binary, *, account="": seen.append((name, binary, account)) or fake.status(name, binary))
    rows = {a["name"]: a for a in client.get("/agents/auth.json").json()["agents"]}
    assert seen == [("claude", BIN, ""), ("claude", BIN, "work"), ("claude", BIN, "alt")]
    assert rows["claude"]["state"] == "in" and rows["claude"]["account"] == "me@example.com"


def test_auth_json_fresh_probes_supported(client, fake):
    rows = {a["name"]: a for a in client.get("/agents/auth.json?fresh=1").json()["agents"]}
    assert [c for c in fake.calls if c[0] == "status"] == [
        ("status", "claude", BIN, True), ("status", "claude", BIN, True, "work"), ("status", "claude", BIN, True, "alt")]
    assert [a["name"] for a in rows["claude"]["accounts"]] == ["work", "alt"] and rows["cline"]["accounts"] == []
    assert rows["claude"]["state"] == "in" and rows["claude"]["plan"] == "max"
    assert rows["codex"]["state"] == "unknown"


def test_auth_json_not_caught_by_agent_detail(client):
    assert client.get("/agents/auth.json").headers["content-type"].startswith("application/json")


# ---------------------------------------------------------------- named accounts


def test_panel_one_row_per_account(client, fake):
    t = client.get("/agents/claude/auth").text
    assert t.count('class="ag-auth-line"') == 3
    assert t.index(">default<") < t.index(">work<") < t.index(">alt<")
    assert t.count('action="/agents/claude/auth/logout"') == 3
    assert 'name="account" value="work"' in t and 'hx-get="/agents/claude/auth?fresh=1&amp;account=work"' in t
    assert 'name="account" value="alt"' in t
    assert 'hx-confirm="Log out of Claude (work)?"' in t and 'hx-confirm="Log out of Claude?"' in t
    assert ("status", "claude", BIN, False, "work") in fake.calls and ("status", "claude", BIN, False) in fake.calls


def test_panel_without_accounts_has_auth_label(client):
    t = client.get("/agents/cline/auth").text
    assert ">Auth<" in t and "default" not in t
    assert 'name="account"' not in t


def test_panel_fresh_only_for_the_named_account(client, fake):
    client.get("/agents/claude/auth?fresh=1&account=work")
    st = {c[4] if len(c) > 4 else "": c[3] for c in fake.calls if c[0] == "status"}
    assert st == {"": False, "work": True, "alt": False}


def test_unknown_account_refused(client, app, fake):
    assert client.get("/agents/claude/auth?account=nope").status_code == 400
    assert client.get("/agents/codex/auth?account=work").status_code == 400  # name of another kind
    for path in ("login", "send", "cancel", "logout"):
        assert post(client, f"/agents/claude/auth/{path}", account="nope", code=CODE).status_code == 400
    assert not {"start", "send", "cancel", "logout"} & set(fake.names())
    assert "nope" not in audit_text(app)


def test_login_named_account(client, app, fake):
    fake.state = "out"
    r = post(client, "/agents/claude/auth/login", account="work")
    assert r.status_code == 200
    assert ("start", "claude", BIN, "work") in fake.calls
    t = r.text
    assert 'id="ag-auth-out-work"' in t and 'hx-get="/agents/claude/auth?poll=1&amp;account=work"' in t
    assert 'name="account" value="work"' in t
    log = audit_text(app)
    assert '"agents_auth_login"' in log and "work" in log and "/accounts/" not in log


def test_poll_named_account(client, fake):
    fake.view = agentsauth.LoginView(active=True, text="Paste code >")
    r = client.get("/agents/claude/auth?poll=1&account=alt")
    assert r.text.lstrip().startswith('<div id="ag-auth-out-alt"')
    assert ("view", "claude", "alt") in fake.calls


def test_send_cancel_logout_named_account(client, fake):
    fake.view = agentsauth.LoginView(active=True, text="Paste code >")
    post(client, "/agents/claude/auth/send", account="work", code=CODE)
    post(client, "/agents/claude/auth/cancel", account="work")
    post(client, "/agents/claude/auth/logout", account="alt")
    assert ("send", "claude", CODE, "work") in fake.calls
    assert ("cancel", "claude", "work") in fake.calls
    assert ("logout", "claude", BIN, "alt") in fake.calls and ("clear", "claude", "alt") in fake.calls


def test_default_account_calls_unchanged(client, fake):
    post(client, "/agents/claude/auth/login")
    assert ("start", "claude", BIN) in fake.calls
