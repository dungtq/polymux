import re
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import pm
from pm.core.config import Settings
from pm.core.models import (
    AgentInfo,
    Finding,
    Inventory,
    McpServerInfo,
    RuleInfo,
    ScriptInfo,
    SkillInfo,
    Tier,
)
from pm.web.app import create_app
from pm.web.routes import agentspage as routes_agentspage

NAMES = ["claude", "codex", "devin", "cursor", "cline", "copilot", "windsurf", "antigravity", "gemini", "aider", "opencode"]


def _fake() -> Inventory:
    agents = []
    for i, n in enumerate(NAMES):
        inst = n not in ("opencode",)
        agents.append(AgentInfo(
            name=n, label=n.capitalize() if n != "antigravity" else "Antigravity (agy)", installed=inst,
            version=f"1.{i}" if inst else "", binary=f"~/.local/bin/{n}" if inst else "",
            config_dir=f"~/.{n}" if inst else "", rules_file=f"~/.{n}/AGENTS.md" if inst else "",
            skills_dir=f"~/.{n}/skills" if n in ("claude", "codex") else "",
            mcp_config="~/.cursor/mcp.json" if n == "cursor" else "",
            skills_enabled=3 if n in ("claude", "codex") else 0, mcp_count=2 if n == "cursor" else 0,
        ))
    skills = [SkillInfo(f"shared-{i:02d}", f"Shared skill number {i}", f"~/.agents/skills/shared-{i:02d}", "shared",
                        "", ("claude", "codex", "devin")[: 1 + i % 3]) for i in range(30)]
    skills.append(SkillInfo("claude-only", "Lives in claude", "~/.claude/skills/claude-only", "agent", "claude", ("claude",)))
    skills.append(SkillInfo("<script>x</script>", "evil <b>name</b>", "~/.agents/skills/evil", "shared", "", ("claude",)))
    rules = [
        RuleInfo("CLAUDE.md", "claude", "~/.claude/CLAUDE.md", "claude rules", "symlink", "~/agents/AGENTS.md", 40, "ok"),
        RuleInfo("cfg.md", "cline", "~/Cline/Rules/cfg.md", "drifted copy", "copy", "", 12, "drift"),
        RuleInfo("broken.md", "codex", "~/.codex/broken.md", "", "symlink", "~/missing", 0, "broken"),
        RuleInfo("AGENTS.md", "", "~/agents/AGENTS.md", "canonical rules", "file", "", 100, "ok"),
    ]
    scripts = [
        ScriptInfo("ntfy-stop.sh", "~/.claude/hooks/ntfy-stop.sh", "hook", "notify on stop", ("claude", "devin")),
        ScriptInfo("sync.sh", "~/bin/sync.sh", "script", "sync things", ("codex",)),
    ]
    mcp = [
        McpServerInfo("exa", "cursor", "stdio", "npx -y exa-mcp-server", "", ("EXA_API_KEY",), "~/.cursor/mcp.json"),
        McpServerInfo("remote", "cursor", "http", "", "mcp.example.com", (), "~/.cursor/mcp.json", None),
    ]
    findings = [Finding("warn", "Cline rules differ between two dirs"), Finding("info", "skill foo is not linked")]
    return Inventory(tuple(agents), tuple(skills), tuple(rules), tuple(scripts), tuple(mcp), tuple(findings), 1.0)


@pytest.fixture
def agentsinv(monkeypatch):
    try:
        from pm.agents.inventory import read as mod
    except ImportError:
        mod = types.ModuleType("pm.agents.inventory.read")
        monkeypatch.setitem(sys.modules, "pm.agents.inventory.read", mod)
        monkeypatch.setattr("pm.agents.inventory.read", mod, raising=False)
    inv = _fake()
    monkeypatch.setattr(mod, "get_inventory", lambda *a, **k: inv, raising=False)
    monkeypatch.setattr(mod, "find_agent", lambda i, n: next((a for a in i.agents if a.name == n), None), raising=False)
    return inv


@pytest.fixture
def app(tmp_path, agentsinv):
    s = Settings(state_dir=tmp_path / "state", token_file=tmp_path / "cfg/token",
                 totp_file=tmp_path / "cfg/totp", tier_ceiling=Tier.SAFE)
    a = create_app(s)
    if not any(getattr(r, "path", "") == "/agents" for r in a.routes):
        a.include_router(routes_agentspage.router)
    return a


@pytest.fixture
def client(app):
    c = TestClient(app, base_url="https://testserver")
    token = app.state.settings.token_file.read_text().strip()
    assert c.post("/login", data={"token": token}, follow_redirects=False).status_code == 303
    return c


def _rows(html: str, cls: str) -> list[str]:
    return re.findall(r'<(?:div|a) class="lrow ' + cls + r'[^"]*"[^>]*>', html)


def test_login_required(app):
    c = TestClient(app, base_url="https://testserver")
    for url in ("/agents", "/agents/claude"):
        r = c.get(url, follow_redirects=False)
        assert r.status_code in (303, 307, 401)
        assert "Installed" not in r.text


def test_default_section_is_installed(client):
    r = client.get("/agents")
    assert r.status_code == 200
    assert 'class="pg-agents"' in r.text
    assert 'aria-label="Installed agents"' in r.text
    assert 'aria-label="Skills, rules and scripts"' not in r.text
    assert 'aria-label="MCP servers"' not in r.text
    assert "10 installed" in r.text


def test_sections_render_one_at_a_time(client):
    inst = client.get("/agents?t=installed").text
    lib = client.get("/agents?t=library").text
    mcp = client.get("/agents?t=mcp").text
    assert 'aria-label="Installed agents"' in inst and 'aria-label="Skills, rules and scripts"' not in inst
    assert 'aria-label="Skills, rules and scripts"' in lib and 'aria-label="Installed agents"' not in lib
    assert 'aria-label="MCP servers"' in mcp and 'aria-label="Installed agents"' not in mcp
    assert client.get("/agents?t=bogus").text.count('aria-label="Installed agents"') == 1
    assert "Skills, rules &amp; scripts" in inst and "MCP servers" in inst


def test_installed_rows_findings_and_links(client):
    html = client.get("/agents").text
    assert len(_rows(html, "arow-i")) == 11
    assert 'href="/agents/claude"' in html and 'href="/agents/opencode"' in html
    assert "not installed" in html
    assert "ag-find ag-warn" in html and "Cline rules differ" in html
    assert "skill foo is not linked" in html


def test_agents_tab_is_current(client):
    for url in ("/agents", "/agents?t=library", "/agents/claude"):
        html = client.get(url).text
        assert re.search(r'href="/agents"[^>]*aria-current="page"', html)


def test_sidebar_absent_on_overview_present_on_detail(client):
    assert "v5-side" in client.get("/agents").text  # section sidebar on every top-level page
    html = client.get("/agents/claude").text
    assert "v5-side" in html
    assert re.search(r'<a[^>]*href="/agents/claude"[^>]*aria-current="page"', html) or re.search(
        r'<a[^>]*aria-current="page"[^>]*href="/agents/claude"', html)
    for n in NAMES:
        assert f'href="/agents/{n}"' in html
    assert html.count('href="/agents/devin"') >= 1
    assert "All agents" in html


def test_unknown_agent_404(client):
    assert client.get("/agents/nope").status_code == 404


@pytest.mark.parametrize("tab,needle", [
    ("skills", "shared-00"), ("rules", "CLAUDE.md"), ("mcp", "No MCP config found"), ("scripts", "ntfy-stop.sh"),
])
def test_detail_tabs_render(client, tab, needle):
    r = client.get(f"/agents/claude?s={tab}")
    assert r.status_code == 200
    assert needle in r.text
    for t in ("Skills (", "Rules (", "MCP (", "Scripts ("):
        assert t in r.text
    assert f'aria-label="{ {"skills": "Skills", "rules": "Rules", "mcp": "MCP servers", "scripts": "Scripts"}[tab] }"' in r.text


def test_detail_default_tab_and_header(client):
    html = client.get("/agents/cursor").text
    assert 'aria-label="Skills"' in html
    assert "~/.cursor/mcp.json" in html and "Config dir" in html and "Skills dir" in html
    mcp = client.get("/agents/cursor?s=mcp").text
    assert "EXA_API_KEY" in mcp and "Written to" in mcp and "mcp.example.com" in mcp
    assert "no MCP config found" not in html
    assert "no MCP config found" in client.get("/agents/claude").text


def test_detail_skills_show_filter_and_state(client):
    allr = _rows(client.get("/agents/devin?s=skills").text, "arow-s")
    on = _rows(client.get("/agents/devin?s=skills&show=enabled").text, "arow-s")
    off = _rows(client.get("/agents/devin?s=skills&show=disabled").text, "arow-s")
    assert len(on) + len(off) == len(allr) and on and off
    assert all("ag-off" not in r for r in on) and all("ag-off" in r for r in off)
    q = client.get("/agents/claude?s=skills&q=claude-only").text
    assert len(_rows(q, "arow-s")) == 1
    assert "claude-only" not in client.get("/agents/devin?s=skills").text


def test_detail_rules_status(client):
    html = client.get("/agents/cline?s=rules").text
    assert "vr-warn" in html and "drift" in html
    assert "broken" in client.get("/agents/codex?s=rules").text


def test_library_default_and_limit(client):
    html = client.get("/agents?t=library").text
    assert len(_rows(html, "arow-l")) == 20
    assert "showing 20 of 38" in html
    assert len(_rows(client.get("/agents?t=library&n=50").text, "arow-l")) == 38
    assert len(_rows(client.get("/agents?t=library&n=all").text, "arow-l")) == 38
    assert "showing 20 of 38" in client.get("/agents?t=library&n=7").text


def test_library_filters(client):
    def rows(q):
        return len(_rows(client.get("/agents?t=library&n=all&" + q).text, "arow-l"))
    assert rows("type=skill") == 32
    assert rows("type=rule") == 4
    assert rows("type=script") == 2
    assert rows("type=bogus") == 38
    assert rows("q=ntfy-stop") == 1
    assert rows("q=nothing-matches-this") == 0
    assert 0 < rows("a=devin") < 38
    assert rows("a=devin&type=script") == 1
    assert rows("a=claude&type=rule") == 1
    html = client.get("/agents?t=library&q=zzzz").text
    assert "Nothing matches" in html and "showing 0 of 0" in html


def test_library_sort_and_reverse(client):
    def names(sort):
        html = client.get("/agents?t=library&n=all&type=skill&sort=" + sort).text
        return re.findall(r'<span class="c-nm" title="([^"]*)">', html)
    asc, desc = names("name"), names("-name")
    assert asc == sorted(asc, key=str.lower) and desc == asc[::-1]
    html = client.get("/agents?t=library&sort=name").text
    assert 'aria-sort="ascending"' in html and "sort=-name" in html
    assert 'aria-sort="descending"' in client.get("/agents?t=library&sort=-name").text
    assert client.get("/agents?t=library&sort=junk").status_code == 200


def test_installed_sort(client):
    html = client.get("/agents?sort=-skills").text
    first = re.search(r'class="lrow arow-i[^"]*"[^>]*href="/agents/([a-z]+)"', html).group(1)
    assert first in ("claude", "codex")
    asc = client.get("/agents?sort=skills").text
    assert 'aria-sort="ascending"' in asc


def test_mcp_section(client):
    html = client.get("/agents?t=mcp").text
    assert len(_rows(html, "arow-m")) == 2
    assert "EXA_API_KEY" in html and "mcp.example.com" in html
    assert "9 of 10 installed agents have no MCP servers configured" in html
    assert 'aria-label="MCP coverage"' in html and html.count('class="ag-cr"') == 2
    rev = client.get("/agents?t=mcp&sort=-name").text
    assert rev.index('title="remote"') < rev.index('title="exa"')


def test_filters_use_selects_and_noscript_fallback(client):
    html = client.get("/agents?t=library").text
    assert html.count("data-nav") >= 3 and "<noscript>" in html
    assert 'class="showbar"' in html and 'name="n"' in html


def test_no_inline_script_or_style(client):
    for url in ("/agents", "/agents?t=library", "/agents?t=mcp", "/agents/claude", "/agents/cursor?s=mcp"):
        r = client.get(url)
        body = r.text
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", body)
        assert " style=" not in body
        assert "script-src 'self'" in r.headers["content-security-policy"]


def test_escaping(client):
    html = client.get("/agents/claude?s=skills").text
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html
    assert "evil <b>name</b>" not in html
    lib = client.get("/agents?t=library&n=all&q=evil").text
    assert "&lt;script&gt;" in lib and "<script>x" not in lib


def test_env_values_not_representable(client, agentsinv):
    html = client.get("/agents?t=mcp").text + client.get("/agents/cursor?s=mcp").text
    assert "EXA_API_KEY" in html
    assert not re.search(r"EXA_API_KEY\s*=", html)
    assert "sk-test-FAKEKEY" not in html and "FAKEKEY" not in html


def test_css_has_no_hex_colours():
    css = (Path(routes_agentspage.__file__).parents[2] / "static/css/agents-page.css").read_text()
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert "box-shadow:0" not in css


def test_detail_split_keeps_global_sidebar(client):
    html = client.get("/agents/claude").text
    assert 'class="ag-split"' in html and 'class="ag-left"' in html and 'class="ag-right"' in html
    # same Agents page: title, tab bar, Installed table (selected row) + Findings on the left, detail on the right
    assert "<h1>Agents</h1>" in html and 'class="tabs ag-tabs tabs-seg"' in html and 'class="list ag-table"' in html
    assert 'id="ag-fh"' in html and 'class="ag-sec"' in html.replace('<section class="ag-sec"', 'class="ag-sec"')
    assert html.count("ag-sel") == 1 and 'arow-i ag-sel' in html and 'aria-current="page" title="Open Claude' in html
    assert "v5-side" in html and 'class="ag-close" href="/agents"' in html
    assert "isort=" in client.get("/agents/claude?isort=-skills").text
    edit = client.get("/agents/claude/edit?kind=rule&name=nope")
    assert 'class="ag-split"' in edit.text and "v5-side" in edit.text


HX = {"HX-Request": "true"}


def test_library_filter_box_is_live(client):
    html = client.get("/agents?t=library&q=claude").text
    form = re.search(r'<form class="pctl ag-ctl"[^>]*>.*?</form>', html, re.S).group(0)
    assert 'action="/agents"' in form and 'data-filter-live data-filter-target="#ag-lib"' in form
    assert '<span class="fbox"><input type="search" name="q" data-filter value="claude"' in form
    sec = html.split('aria-label="Skills, rules and scripts"', 1)[1].split("</section>", 1)[0]
    assert "psearch" not in sec and ">Search<" not in sec and 'placeholder="Search' not in sec
    assert form.index("data-filter") < form.index('name="type"') < form.index('name="n" data-nav')
    assert re.search(r'<div id="ag-lib" hx-get="/agents\?[^"]*q=claude[^"]*" hx-trigger="pm:refresh"', html)
    frag = client.get("/agents?t=library&q=claude", headers=HX).text
    assert "<form class=\"pctl" not in frag and "<html" not in frag.lower() and 'class="showbar"' in frag
    assert len(_rows(frag, "arow-l")) == len(_rows(html, "arow-l")) > 0
    assert "Nothing matches" in client.get("/agents?t=library&q=zzzz-none", headers=HX).text


def test_skills_filter_select_and_live_box(client):
    html = client.get("/agents/devin?s=skills&show=enabled").text
    form = re.search(r'<form class="pctl ag-ctl"[^>]*>.*?</form>', html, re.S).group(0)
    assert 'data-filter-live data-filter-target="#ag-skills"' in form and 'action="/agents/devin"' in form
    assert '<select name="show" data-nav aria-label="Filter">' in form and "sel-show" not in form  # a filter, not Show
    assert '<option value="enabled" selected>' in form and form.index("data-filter") < form.index('name="show"')
    frag = client.get("/agents/devin?s=skills&show=enabled", headers=HX).text
    assert "<form class=\"pctl" not in frag and "ag-vsum" in frag
    assert len(_rows(frag, "arow-s")) == len(_rows(html, "arow-s")) > 0


def test_filter_box_is_flexible():
    """Agents library / Skills: the filter box grows with the row (capped), never a fixed width; phones: own line."""
    css = (Path(routes_agentspage.__file__).parents[2] / "static/css/agents-page.css").read_text()
    rule = re.search(r"^\.pg-agents \.ag-ctl \.fbox\{([^}]*)\}", css, re.M).group(1)
    assert "flex:1 1" in rule and "width:auto" in rule and "min-width:0" in rule and "max-width:" in rule
    assert ".pg-agents .ag-ctl .fbox{flex:1 1 100%;width:auto;min-width:0}" in css


# ---------------------------------------------------------------- Findings view (?t=findings, M4)


def test_findings_view_classic_unchanged(client, monkeypatch):
    """?t=findings is not in the classic tab row; classic renders it exactly as the Installed section."""
    inst = client.get("/agents?t=installed")
    fnd = client.get("/agents?t=findings")
    assert fnd.status_code == 200
    assert fnd.text == inst.text
    assert "t=findings" not in inst.text


def test_findings_view_context(client, monkeypatch):
    from pm.agents.inventory import read as mod

    inv = _fake()
    inv = Inventory(inv.agents, inv.skills, inv.rules, inv.scripts, inv.mcp, (
        Finding("warn", "key in args", "security", "cursor", (("action", "mcp_env"), ("config", "~/.cursor/mcp.json"),
                                                              ("server", "exa"))),
        Finding("warn", "drift", "rules", "cline"), Finding("info", "old style"), Finding("info", "odd", "weird"),
    ), 1.0)
    monkeypatch.setattr(mod, "get_inventory", lambda *a, **k: inv)
    seen = {}
    real = routes_agentspage.html

    def spy(request, name, **ctx):
        seen.clear()
        seen.update(ctx)
        return real(request, name, **ctx)

    monkeypatch.setattr(routes_agentspage, "html", spy)
    client.get("/agents?t=findings")
    assert seen["view"] == "findings" and seen["section"] == "installed"
    assert seen["finding_kinds"] == [("security", "Security", 1), ("rules", "Rules", 1), ("notes", "Notes", 2)]
    assert seen["finding_counts"]["skills"] == 0 and seen["fixable"] == 1
    assert [len(g[2]) for g in seen["finding_groups"]] == [1, 1, 2]
    assert [k for k, _, _ in seen["tab_items"]] == ["installed", "library", "mcp", "usage"]
    client.get("/agents?t=mcp")
    assert seen["view"] == "mcp" and "finding_kinds" not in seen
