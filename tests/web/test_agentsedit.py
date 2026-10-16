"""Agents page mutations (routes/agentsedit.py): the JSON answers of /agents/review|apply for a fetch, the classic
HTML for a navigation, the M4 fix actions end to end, and POST /agents/rescan.

Every test runs on a tmp home (HOME and agentslib.home patched); nothing reads or writes the real ~/.claude* or
~/.agents*.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pm.agents import usage
from pm.agents.inventory import read as agentsinv
from pm.core.config import Settings
from pm.core.models import Tier
from pm.security.audit import AuditError
from pm.web import agentslib
from pm.web.app import create_app

ORIGIN = "https://pm.example.test"
KEY = "sk-FAKEKEY-0123456789abcdefXYZ"
FETCH = {"Origin": ORIGIN, "X-Requested-With": "fetch"}
TOGGLE = {"action": "skill_toggle", "agent": "claude", "skill": "cfg", "enable": "1"}


def w(p: Path, text: str, mode: int | None = None) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    if mode:
        p.chmod(mode)
    return p


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    for n in ("cfg", "demo"):
        w(h / f".agents/skills/{n}/SKILL.md", f"---\nname: {n}\ndescription: {n} skill\n---\n\nbody\n")
    (h / ".agents/hooks").mkdir(parents=True)
    w(h / "agents/AGENTS.md", "# canonical\n")
    w(h / "agents/agents-index.md", "# index\n- `~/.gone/x.md` old\n- `~/agents/AGENTS.md`\n")
    for b in ("claude", "codex"):
        w(h / f".local/bin/{b}", "#!/bin/sh\n", 0o755)
    (h / ".claude/skills").mkdir(parents=True)
    w(h / ".claude.json", json.dumps({"mcpServers": {"c7": {"command": "npx", "args": ["c7-mcp", "--api-key", KEY]}}},
                                     indent=2), 0o600)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(agentslib, "home", lambda: h)
    monkeypatch.setattr(agentsinv, "_bin_candidates", lambda spec, home: [home / ".local/bin" / b for b in spec.bins])
    agentsinv.clear_cache()
    yield h
    agentsinv.clear_cache()
    assert str(Path.home()).startswith(str(tmp_path)) and str(agentslib.home()).startswith(str(tmp_path))


@pytest.fixture
def app(tmp_path, home):
    s = Settings(state_dir=tmp_path / "state", token_file=tmp_path / "cfg/token",
                 totp_file=tmp_path / "cfg/totp", tier_ceiling=Tier.SAFE)
    return create_app(s)


@pytest.fixture
def client(app):
    c = TestClient(app, base_url=ORIGIN)
    token = app.state.settings.token_file.read_text().strip()
    assert c.post("/login", data={"token": token}, follow_redirects=False).status_code == 303
    return c


def csrf_of(c: TestClient) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', c.get("/agents/claude").text).group(1)


def jpost(c, url, data, headers=FETCH, **kw):
    return c.post(url, data={"csrf": csrf_of(c), **data}, headers=headers, **kw)


def audit_text(app) -> str:
    f = app.state.settings.audit_file
    return f.read_text() if f.exists() else ""


def review_apply(c, data):
    r = jpost(c, "/agents/review", data)
    assert r.status_code == 200, r.text
    return r.json(), jpost(c, "/agents/apply", {**data, "token": r.json()["token"]})


def test_json_review_apply(client, app, home):
    plan, a = review_apply(client, TOGGLE)  # no ctx_agent / ctx_tab needed for JSON
    assert plan == {"ok": True, "ops": [{"kind": "symlink", "path": "~/.claude/skills/cfg",
                                         "detail": "-> ~/.agents/skills/cfg"}],
                    "token": plan["token"], "destructive": False}
    assert re.fullmatch(r"[0-9a-f]{64}", plan["token"])
    assert a.status_code == 200 and a.json() == {"ok": True, "lines": ["linked ~/.claude/skills/cfg"]}
    assert (home / ".claude/skills/cfg").is_symlink()
    assert "agents_skill_toggle" in audit_text(app)


def test_apply_drops_the_inventory_cache(client, home, monkeypatch):
    from pm.agents.inventory import read as agentsinv

    cleared = []
    monkeypatch.setattr(agentsinv, "clear_cache", lambda: cleared.append(1))
    _, a = review_apply(client, TOGGLE)
    assert a.status_code == 200 and cleared == [1]


def test_json_refusals(client, app, home):
    r = jpost(client, "/agents/review", {**TOGGLE, "skill": "nope"})
    assert r.status_code == 400 and r.json() == {"ok": False, "error": r.json()["error"], "stale": False}
    r = jpost(client, "/agents/review", {"action": "skill_delete", "name": "demo", "confirm": "x"})
    assert r.status_code == 400 and "confirm" in r.json()["error"]
    assert jpost(client, "/agents/review", {**TOGGLE, "action": "skill_delete"}).status_code == 400
    plan = jpost(client, "/agents/review", {"action": "skill_delete", "name": "demo", "confirm": "demo"}).json()
    assert plan["destructive"] is True
    a = jpost(client, "/agents/apply", {**TOGGLE, "token": "f" * 64})
    assert a.status_code == 409 and a.json()["stale"] is True and a.json()["ok"] is False
    assert "agents_skill_toggle" in audit_text(app) and "denied" in audit_text(app)
    assert jpost(client, "/agents/review", {"action": "rm_rf"}).status_code == 400


def test_navigation_keeps_classic_html(client, home):
    data = {**TOGGLE, "ctx_agent": "claude", "ctx_tab": "skills"}
    plain = jpost(client, "/agents/review", data, headers={"Origin": ORIGIN})
    nav = jpost(client, "/agents/review", data, headers={"Origin": ORIGIN, "Sec-Fetch-Mode": "navigate",
                                                         "X-Requested-With": "fetch"})
    assert plain.headers["content-type"].startswith("text/html") and 'action="/agents/apply"' in plain.text
    assert nav.text == plain.text
    a = jpost(client, "/agents/apply", {**data, "token": "f" * 64}, headers={"Origin": ORIGIN})
    assert a.status_code == 409 and a.headers["content-type"].startswith("text/html")


def test_mcp_env_json_never_shows_the_key(client, app, home):
    plan, a = review_apply(client, {"action": "mcp_env", "config": "~/.claude.json", "server": "c7"})
    assert [o["path"] for o in plan["ops"]] == ["~/.claude.json"] and "C7_API_KEY" in plan["ops"][0]["detail"]
    assert a.status_code == 200, a.text
    for text in (json.dumps(plan), a.text, audit_text(app)):
        assert KEY not in text
    assert "~/.claude.json c7" in audit_text(app)
    cfg = json.loads((home / ".claude.json").read_text())
    assert cfg["mcpServers"]["c7"] == {"command": "npx", "args": ["c7-mcp"], "env": {"C7_API_KEY": KEY}}


def test_skills_link_and_index_prune_json(client, home):
    data = {"action": "skills_link", "agent": "claude", "skills": ["cfg", "demo"]}
    plan, a = review_apply(client, data)
    assert [o["kind"] for o in plan["ops"]] == ["symlink", "symlink"] and len(a.json()["lines"]) == 2
    _, a = review_apply(client, {"action": "index_prune", "lines": "~/.gone/x.md"})
    assert a.status_code == 200
    assert (home / "agents/agents-index.md").read_text() == "# index\n- `~/agents/AGENTS.md`\n"


def test_rescan(client, app, monkeypatch):
    calls = []
    monkeypatch.setattr(agentsinv, "clear_cache", lambda: calls.append("inv"))
    monkeypatch.setattr(usage, "clear_cache", lambda: calls.append("usage"))
    assert client.post("/agents/rescan", headers={"Origin": ORIGIN}).status_code == 403  # no CSRF
    assert client.get("/agents/rescan").status_code == 405
    assert calls == []
    r = jpost(client, "/agents/rescan", {})
    assert r.status_code == 200 and r.json() == {"ok": True} and calls == ["inv", "usage"]
    assert "agents_rescan" in audit_text(app)
    r = jpost(client, "/agents/rescan", {}, headers={"Origin": ORIGIN}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/agents"


def test_rescan_audit_failure_fails_closed(client, app, monkeypatch):
    calls = []
    csrf = csrf_of(client)
    monkeypatch.setattr(agentsinv, "clear_cache", lambda: calls.append("inv"))

    def boom(*a, **k):
        raise AuditError("nope")

    monkeypatch.setattr(app.state.audit, "write", boom)
    r = client.post("/agents/rescan", data={"csrf": csrf}, headers=FETCH)
    assert r.status_code == 503 and calls == []
