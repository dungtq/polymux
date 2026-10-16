"""v4 markup of the agent page, launch page and terminal page."""

from helpers import login
from helpers import pages_env as env  # noqa: F401  (fixture)
from test_term_ui import render

from pm.web.render import TEMPLATE_DIR


def test_agent_head_v4(env):
    s, c = env
    login(s, c)
    t = c.get("/a/claude-12").text
    assert 'class="agent-head"' in t
    assert 'id="agent-state"' in t
    assert 'agent-meta am-row2' in t
    assert 'class="am-main ico-dir"' in t and 'class="am-host"' in t
    # state badge sits beside the h1, not inside it
    h1 = t[t.index("<h1"):t.index("</h1>")]
    assert "badge" not in h1


def test_launch_page_v4(env):
    s, c = env
    login(s, c)
    t = c.get("/p/alpha/launch").text
    assert "launch-actions" in t and "data-launch-open" in t


def test_term_page_v4_ids_and_status_in_head():
    t = render("sess")
    i = t.index('<header class="term-head">')
    head = t[i:t.index("</header>", i)]
    assert 'id="term-status"' in head
    n0 = t.index('<nav class="term-nav"')
    nav = t[n0:t.index("</nav>", n0)]
    for i in ("term-font-dec", "term-font-inc", "term-reconnect", "term-hist-btn"):
        assert i in nav
    assert "pane-bg" not in t  # colours come from CSS, no inline style


def test_agent_subs_and_hint_markup(env):
    s, c = env
    login(s, c)
    t = c.get("/a/claude-12").text
    assert "agent-subs" not in t
    assert 'id="sa-live"' in t and "subagents?running=1" in t
    assert 'class="send-hint" data-cmp-hint' in t and "data-hint-off" in t and "data-hint-close" in t
    assert 'id="send-hint"' not in t  # the composer's hooks are data attributes, no ids


def test_sa_live_absent_for_codex(env, monkeypatch):
    from dataclasses import replace

    from pm.core.models import AgentKind
    from pm.infra import snapshot as services
    s, c = env
    base = services.agents_mod.detect_agents
    monkeypatch.setattr(services.agents_mod, "detect_agents",
                        lambda panes: [replace(a, kind=AgentKind.CODEX) for a in base(panes)])
    login(s, c)
    assert 'id="sa-live"' not in c.get("/a/claude-12").text


def test_one_source_for_head_rail_and_zoom():
    """agent_head, jump_rail, zoomctl and jumpzoom live once (_agentname.html, _chatchrome.html)."""
    from pathlib import Path
    t = TEMPLATE_DIR
    pages = {n: (t / n).read_text() for n in ("agent.html", "agent_ended.html", "builder.html", "_drawer.html")}
    chrome = (t / "_chatchrome.html").read_text()
    for n, src in pages.items():
        assert "agent_head(" in src, n
        assert "macro jumpzoom" not in src and 'class="jump-rail"' not in src and 'class="zoomctl"' not in src, n
        assert '<h1 class="agent-title"' not in src and "at-pill" not in src and "vc-pill" not in src, n
    for n in ("agent.html", "agent_ended.html"):
        assert "{{ jump_rail() }}" in pages[n] and "{{ jumpzoom() }}" in pages[n], n
    assert 'jump_rail("vc-top", "vc-bottom")' in pages["_drawer.html"] and 'zoomctl("vc-zoom")' in pages["_drawer.html"]
    assert chrome.count('class="jump-rail"') == 1 and chrome.count('class="zoomctl"') == 1
    assert (t / "_agentname.html").read_text().count("macro agent_head(agent, mode, pill=None)") == 1


def test_macros_agent_text_has_no_name_clash():
    """_macros.html's plain label macro is agent_text; agent_name is only the canonical _agentname.html one."""
    import re
    from pathlib import Path
    t = TEMPLATE_DIR
    assert "macro agent_text(a)" in (t / "_macros.html").read_text()
    assert "macro agent_name" not in (t / "_macros.html").read_text()
    for f in t.rglob("*.html"):
        for names in re.findall(r'\{%-? from "_macros\.html" import ([^%]*)%\}', f.read_text()):
            imported = [n.strip().split(" as ")[0].strip() for n in names.replace(" with context", "").split(",")]
            assert "agent_name" not in imported, f.name


def test_agent_page_head_pill_from_python(env, monkeypatch):
    """The heading pill comes from composer_ctx head_pill (agents.head.live_pill), no Jinja filter."""
    from pathlib import Path
    s, c = env
    login(s, c)
    monkeypatch.setattr("pm.agents.head.live_pill", lambda a, home=None: "fable-9")
    t = c.get("/a/claude-12").text
    assert 'data-model="fable-9"' in t and 'id="agent-pill"' in t and ">fable-9</span></h1>" in t
    assert "live_pill" not in (TEMPLATE_DIR / "agent.html").read_text()
    assert '"head_pill": head_pill' in (Path(__file__).resolve().parents[1] / "src/pm/web/chatctx.py").read_text()
