"""Agents page mutations: review -> apply, guards, audit, safety (always a temp HOME)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pm.agents.inventory import read as agentsinv
from pm.core.config import Settings
from pm.core.models import Tier
from pm.security.audit import AuditError
from pm.web import agentslib
from pm.web.app import create_app
from pm.web.routes import agentspage as routes_agentspage

ORIGIN = "https://pm.example.test"
CANON = "# canonical rules\nline two\n"
SECRET = "sk-FAKEKEY-0123456789abcdef"


def w(p: Path, text: str, mode: int | None = None) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    if mode:
        p.chmod(mode)
    return p


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for dp, dn, fn in sorted(os.walk(root)):
        dn.sort()
        for n in sorted(dn + fn):
            p = Path(dp) / n
            if p.is_symlink():
                h.update(f"{p}|L|{os.readlink(p)}".encode())
            elif p.is_file():
                h.update(f"{p}|F|{p.read_text()}".encode())
            else:
                h.update(f"{p}|D".encode())
    return h.hexdigest()


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    for n in ("cfg", "demo"):
        w(h / f".agents/skills/{n}/SKILL.md", f"---\nname: {n}\ndescription: {n} skill\n---\n\nbody {SECRET if n == 'cfg' else ''}\n")
    (h / ".agents/hooks").mkdir(parents=True)
    w(h / ".agents/hooks/old.sh", "#!/bin/sh\necho hi\n", 0o755)
    w(h / "agents/AGENTS.md", CANON)
    w(h / "agents/agents-index.md", "# index\n")
    for b in ("claude", "codex"):
        w(h / f".local/bin/{b}", "#!/bin/sh\n", 0o755)
    (h / ".claude/skills").mkdir(parents=True)
    w(h / ".claude/CLAUDE.md", CANON)
    (h / ".codex").mkdir()
    w(h / ".codex/AGENTS.md", "# old codex copy\n")
    w(h / ".agents-git/secret", "TOPSECRET\n")
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(agentslib, "home", lambda: h)
    monkeypatch.setattr(agentsinv, "_bin_candidates", lambda spec, home: [home / ".local/bin" / b for b in spec.bins])
    agentsinv.clear_cache()
    yield h
    agentsinv.clear_cache()


@pytest.fixture(autouse=True)
def _home_guard(request, tmp_path):
    yield
    if "home" in request.fixturenames:
        assert str(Path.home()).startswith(str(tmp_path))
        assert str(agentslib.home()).startswith(str(tmp_path))


@pytest.fixture
def app(tmp_path, home):
    assert str(Path.home()).startswith(str(tmp_path)) and str(agentslib.home()).startswith(str(tmp_path))
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


def post(c, url, origin=ORIGIN, csrf=True, **data):
    d = {"csrf": csrf_of(c), **data} if csrf is True else dict(data)
    return c.post(url, data=d, headers={"Origin": origin} if origin else {})


def token_of(html: str) -> str:
    return re.search(r'name="token" value="([0-9a-f]{64})"', html).group(1)


def hidden_of(html: str) -> dict:
    form = html[html.index('action="/agents/apply"'):]
    form = form[: form.index("</form>")]
    out: dict = {}
    for k, v in re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form):
        out.setdefault(k, []).append(v)
    return {k: v if len(v) > 1 else v[0] for k, v in out.items()}


def audit_text(app) -> str:
    f = app.state.settings.audit_file
    return f.read_text() if f.exists() else ""


def review_apply(c, **data):
    r = post(c, "/agents/review", **data)
    assert r.status_code == 200, r.text
    h = hidden_of(r.text)
    return r, post(c, "/agents/apply", **{k: v for k, v in h.items() if k != "csrf"})


TOGGLE = {"action": "skill_toggle", "agent": "claude", "skill": "cfg", "enable": "1", "ctx_agent": "claude", "ctx_tab": "skills"}


def test_anonymous_refused(app):
    c = TestClient(app, base_url=ORIGIN)
    for url in ("/agents/review", "/agents/apply"):
        assert c.post(url, data=TOGGLE, headers={"Origin": ORIGIN}, follow_redirects=False).status_code in (401, 303, 307)
    assert c.get("/agents/claude/edit?kind=skill&name=cfg", follow_redirects=False).status_code in (401, 303, 307)


def test_csrf_and_origin_refused(client, home):
    before = tree_hash(home)
    assert post(client, "/agents/review", csrf=False, **TOGGLE).status_code == 403
    assert post(client, "/agents/review", origin="https://evil.example", **TOGGLE).status_code == 403
    assert post(client, "/agents/apply", origin="https://evil.example", token="0" * 64, **TOGGLE).status_code == 403
    assert post(client, "/agents/apply", csrf=False, token="0" * 64, **TOGGLE).status_code == 403
    assert tree_hash(home) == before


def test_get_is_405(client):
    assert client.get("/agents/review").status_code == 405
    assert client.get("/agents/apply").status_code == 405


def test_review_lists_ops_and_changes_nothing(client, home):
    before = tree_hash(home)
    r = post(client, "/agents/review", **TOGGLE)
    assert r.status_code == 200
    assert "Review changes" in r.text
    assert "~/.claude/skills/cfg" in r.text and "symlink" in r.text
    assert re.search(r'name="token" value="[0-9a-f]{64}"', r.text)
    assert r.text.count('class="primary"') == 1
    assert 'href="/agents/claude?s=skills"' in r.text
    assert tree_hash(home) == before


def test_apply_toggle_audit_and_secret(client, app, home):
    link = home / ".claude/skills/cfg"
    _, r = review_apply(client, **TOGGLE)
    assert r.status_code == 200 and "Applied" in r.text
    assert link.is_symlink() and link.resolve() == (home / ".agents/skills/cfg").resolve()
    log = audit_text(app)
    ours = [json.loads(x) for x in log.splitlines() if "agents_" in x]
    ok = [e for e in ours if e["action"] == "agents_skill_toggle"]
    assert ok and ok[-1]["target"] == "claude/cfg" and ok[-1]["detail"] == {"ops": 1}
    assert SECRET not in log and str(home) not in log
    _, r = review_apply(client, **{**TOGGLE, "enable": "0"})
    assert r.status_code == 200 and not link.exists() and not link.is_symlink()


def test_apply_writes_files_without_body_in_audit(client, app, home):
    body = f"line one\nkey={SECRET}\n"
    _, a = review_apply(client, action="skill_create", name="newone", description="A new skill", body=body,
                        ctx_agent="claude", ctx_tab="skills")
    assert a.status_code == 200
    md = home / ".agents/skills/newone/SKILL.md"
    assert "key=" + SECRET in md.read_text()
    log = audit_text(app)
    assert "newone" in log and SECRET not in log and "line one" not in log
    assert SECRET not in a.text
    _, a = review_apply(client, action="rule_create", name="mine.md", body="# mine\n", ctx_agent="claude", ctx_tab="rules")
    assert a.status_code == 200 and (home / "agents/rules/mine.md").read_text() == "# mine\n"
    _, a = review_apply(client, action="script_create", name="run.sh", body="#!/bin/sh\n", ctx_agent="claude", ctx_tab="scripts")
    assert a.status_code == 200 and os.access(home / ".agents/hooks/run.sh", os.X_OK)


def test_crlf_body_normalised(client, home):
    _, a = review_apply(client, action="rule_create", name="crlf.md", body="a\r\nb\r\n", ctx_agent="claude", ctx_tab="rules")
    assert a.status_code == 200
    assert (home / "agents/rules/crlf.md").read_bytes() == b"a\nb\n"


def test_stale_token_refused(client, home):
    r = post(client, "/agents/review", **TOGGLE)
    h = hidden_of(r.text)
    (home / ".claude/skills/other").mkdir()
    link = home / ".claude/skills/cfg"
    os.symlink(str(home / ".agents/skills/cfg"), link)
    before = tree_hash(home)
    a = post(client, "/agents/apply", **{k: v for k, v in h.items() if k != "csrf"})
    assert a.status_code == 409 and "review" in a.text.lower()
    assert tree_hash(home) == before


def test_wrong_token_refused(client, home):
    before = tree_hash(home)
    a = post(client, "/agents/apply", **{**TOGGLE, "token": "f" * 64})
    assert a.status_code == 409
    a = post(client, "/agents/apply", **TOGGLE)
    assert a.status_code == 409
    assert tree_hash(home) == before


def test_audit_failure_503_no_change(client, app, home, monkeypatch):
    r = post(client, "/agents/review", **TOGGLE)
    h = hidden_of(r.text)
    before = tree_hash(home)

    def boom(*a, **k):
        raise AuditError("nope")

    monkeypatch.setattr(app.state.audit, "write", boom)
    a = client.post("/agents/apply", data=h, headers={"Origin": ORIGIN})
    assert a.status_code == 503
    assert tree_hash(home) == before


def test_unknown_action_and_bad_bool(client, home):
    before = tree_hash(home)
    assert post(client, "/agents/review", action="rm_rf", ctx_agent="claude").status_code == 400
    assert post(client, "/agents/apply", action="", token="x").status_code == 400
    assert post(client, "/agents/review", **{**TOGGLE, "enable": "yes"}).status_code == 400
    assert tree_hash(home) == before


@pytest.mark.parametrize("name", ["../evil", "..", "a/b", "/etc/passwd", ".hidden", "x" * 80])
def test_traversal_refused(client, home, name):
    before = tree_hash(home)
    for act in ("skill_create", "skill_delete"):
        r = post(client, "/agents/review", action=act, name=name, description="d", body="b", confirm=name,
                 ctx_agent="claude", ctx_tab="skills")
        assert r.status_code == 400 and "Review changes" not in r.text
    r = post(client, "/agents/apply", action="skill_create", name=name, description="d", body="b", token="0" * 64,
             ctx_agent="claude", ctx_tab="skills")
    assert r.status_code in (400, 409)
    assert tree_hash(home) == before
    assert client.get("/agents/claude/edit", params={"kind": "skill", "name": name}).status_code == 400


def test_delete_needs_typed_name(client, home):
    before = tree_hash(home)
    r = post(client, "/agents/review", action="skill_delete", name="demo", confirm="", ctx_agent="claude", ctx_tab="skills")
    assert r.status_code == 400 and "exact name" in r.text
    r = post(client, "/agents/review", action="skill_delete", name="demo", confirm="Demo", ctx_agent="claude", ctx_tab="skills")
    assert r.status_code == 400
    assert tree_hash(home) == before
    r, a = review_apply(client, action="skill_delete", name="demo", confirm="demo", ctx_agent="claude", ctx_tab="skills")
    assert 'class="danger"' in r.text and 'class="primary"' not in r.text
    assert a.status_code == 200 and not (home / ".agents/skills/demo").exists()


def test_rules_sync_flow(client, home):
    r, a = review_apply(client, action="rules_sync", source="canonical", targets=["codex"], ctx_agent="codex", ctx_tab="rules")
    assert "backup" in r.text
    assert a.status_code == 200
    assert (home / ".codex/AGENTS.md").is_symlink()


def test_validate_get_renders(client):
    r = client.get("/agents/claude?s=rules&validate=1")
    assert r.status_code == 200
    assert 'aria-label="Validation results"' in r.text and "canonical rules file" in r.text
    assert "Validation results" not in client.get("/agents/claude?s=rules").text


def test_edit_page_prefills_and_escapes(client, home):
    w(home / ".agents/skills/demo/SKILL.md", '---\nname: demo\ndescription: "say: hi"\n---\n\n<b>x</b> & body\n')
    r = client.get("/agents/claude/edit?kind=skill&name=demo")
    assert r.status_code == 200
    assert 'value="say: hi"' in r.text and "&lt;b&gt;x&lt;/b&gt; &amp; body" in r.text and "<b>x</b>" not in r.text
    assert r.text.count('name="confirm"') == 2  # edit form + the topbar stop dialog
    r = client.get("/agents/claude/edit?kind=rule&name=AGENTS.md")
    assert r.status_code == 200 and "canonical rules" in r.text and r.text.count('name="confirm"') == 1
    assert client.get("/agents/claude/edit?kind=script&name=old.sh").status_code == 200
    assert client.get("/agents/claude/edit?kind=bogus&name=x").status_code == 400
    assert client.get("/agents/nope/edit?kind=skill&name=demo").status_code == 404


def test_edit_refuses_symlink_big_and_other_frontmatter(client, home, tmp_path):
    outside = w(tmp_path / "outside.txt", "outside secret\n")
    os.symlink(outside, home / ".agents/hooks/ln.sh")
    r = client.get("/agents/claude/edit?kind=script&name=ln.sh")
    assert r.status_code == 400 and "outside secret" not in r.text
    w(home / ".agents/hooks/big.sh", "x" * (256 * 1024 + 1))
    assert client.get("/agents/claude/edit?kind=script&name=big.sh").status_code == 400
    w(home / ".agents/skills/demo/SKILL.md", "---\nname: demo\ndescription: d\nextra: 1\n---\n\nb\n")
    assert client.get("/agents/claude/edit?kind=skill&name=demo").status_code == 400


def test_edit_apply_roundtrip(client, home):
    body = "# changed\n"
    _, a = review_apply(client, action="rule_edit", name="AGENTS.md", body=body, ctx_agent="claude", ctx_tab="rules")
    assert a.status_code == 200 and (home / "agents/AGENTS.md").read_text() == body


def test_mcp_tab_note_and_no_mutation_form(client):
    html = client.get("/agents/claude?s=mcp").text
    assert "Per-agent MCP on/off is not supported yet" in html
    main = html[html.index('aria-label="MCP servers"'):html.index("</section>")]
    assert "/agents/review" not in html and "<form" not in main


def test_skills_tab_row_actions(client, home):
    html = client.get("/agents/claude?s=skills").text
    rows = re.findall(r'<div class="lrow arow-s.*?</div>', html, re.DOTALL)
    assert rows
    for row in rows:
        assert row.count('class="ag-act"') <= 1
    assert 'class="ag-act">Enable</button>' in html
    assert html.count("Edit cfg") >= 1 and 'aria-label="Delete cfg"' in html
    codex = client.get("/agents/windsurf?s=skills").text
    assert 'class="ag-act"' not in codex and "Cannot be switched here" in codex
    assert SECRET not in html


def test_markup_rules(client):
    css = (Path(routes_agentspage.__file__).parents[2] / "static/css/agents-page.css").read_text()
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css) and "box-shadow:0" not in css
    for url in ("/agents/claude?s=skills", "/agents/claude?s=rules&validate=1", "/agents/claude?s=scripts",
                "/agents/claude/edit?kind=skill&name=cfg"):
        html = client.get(url).text
        assert "<script" not in html.replace('src="/static', "") or all(
            "src=" in t for t in re.findall(r"<script[^>]*>", html))
        assert not re.search(r"<script(?![^>]*src=)[^>]*>", html) and " style=" not in html
        assert re.search(r'<button[^>]*onclick', html) is None
    r = post(client, "/agents/review", **TOGGLE)
    assert not re.search(r"<script(?![^>]*src=)[^>]*>", r.text) and " style=" not in r.text
