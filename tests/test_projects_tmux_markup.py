"""v4 markup of /p and /tmux: titles, sort icons, one row per session, global sidebar, CSS-only-ish Cancel."""

import re

from helpers import login, pane_line
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.core.models import Agent, AgentKind, AgentState
from pm.infra import snapshot as services


def test_projects_v4_head_and_header(env):
    s, c = env
    login(s, c)
    html = c.get("/p").text
    assert '<div class="pg-projects">' in html
    assert re.search(r'<h1>Projects</h1><p class="inbox-stats muted">', html)
    assert 'class="hcell c-main' in html and 'class="arr"' in html
    assert "&amp;#8645;" not in html and "&#8645;" not in html
    assert "<script>" not in html and "style=" not in html and " onclick=" not in html


def test_tmux_v4_new_session_ok_cancel_and_row(env, monkeypatch):
    s, c = env
    login(s, c)
    monkeypatch.setenv("FAKE_TMUX_OUT", pane_line("$1", "work", "@1", "%12", "/tmp"))
    ag = Agent(id="claude-1", kind=AgentKind.CLAUDE, pane_id="%12", session_id="$1", pid=1,
               cwd="/tmp", state=AgentState.RUNNING, state_since=1.0, last_active=1.0)
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes, **kw: [ag])
    html = c.get("/tmux").text
    assert '<div class="pg-tmux">' in html and "<h1>tmux</h1>" in html
    assert '<button type="submit" class="primary">OK</button>' in html
    assert "data-newsess-cancel" in html and "/static/js/newsess.js" in html
    assert html.count('<details class="sessrow"') == 1  # one row per session
    assert "chatlink" not in html and ">Open session<" not in html and ">Open chat<" not in html  # actions live on the window rows
    assert 'class="vs-row on"' in html and 'data-p="tmux"' in html and "Filter sessions" not in html  # global section nav
    assert 'name="csrf"' in html and 'action="/tmux/session/new"' in html
    r = c.get("/static/js/newsess.js")
    assert r.status_code == 200 and "data-newsess-cancel" in r.text
    assert "<script>" not in html and "style=" not in html


def test_tmux_expanded_row_uses_windows_table_and_open_param(env, monkeypatch):
    s, c = env
    login(s, c)
    monkeypatch.setenv("FAKE_TMUX_OUT", pane_line("$1", "work", "@1", "%12", "/tmp"))
    ag = Agent(id="claude-1", kind=AgentKind.CLAUDE, pane_id="%12", session_id="$1", pid=1,
               cwd="/tmp", state=AgentState.RUNNING, state_since=1.0, last_active=1.0)
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes, **kw: [ag])
    html = c.get("/tmux").text
    assert 'class="hrow tx-head tx-w"' in html and 'class="lrow tx-row tx-w"' in html  # shared windows table
    assert 'class="tx-agent"' in html and 'href="/a/claude-1"' in html
    assert 'class="pane-line"' not in html and " open" not in html.split('<details class="sessrow"')[1].split(">")[0]
    html = c.get("/tmux?open=s1").text
    assert '<details class="sessrow" open data-focus>' in html and 'id="sess-s1"' in html
    assert 'id="vs-foot-body"' in html and 'aria-label="Sessions"' not in html
    r = c.get("/tmux/s1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/tmux?open=s1"
    assert c.get("/tmux/s9", follow_redirects=False).status_code == 404


def test_project_live_has_three_tabs_and_no_bottom_stack_card(env):
    s, c = env
    login(s, c)
    for h in (c.get("/p/alpha").text, c.get("/p/alpha/live").text):
        assert 'role="tablist"' in h and h.count("data-ptab=") == 3
        for k in ("tmux", "stack", "vm"):
            assert f'data-ptab="{k}"' in h and f'data-ptab-panel="{k}"' in h
        assert 'data-ptab="tmux" aria-selected="true"' in h  # default
        assert 'data-ptab-panel="vm" data-key="ptp-vm" data-keep hidden hx-get="/p/alpha/vms"' in h
        assert "No compose file." in h and "<h2>Stack</h2>" not in h
    page = c.get("/p/alpha").text
    assert page.index('data-ptab-panel="stack"') < page.index('id="pj-right"')
    assert "/static/js/projtabs.js" in page


def test_project_live_stack_tab_absent_when_hidden(env):
    from pm.projects import settings as pset
    s, c = env
    login(s, c)
    pset.save_hidden(s, "alpha", ["stack"])
    for h in (c.get("/p/alpha").text, c.get("/p/alpha/live").text):
        assert 'data-ptab="stack"' not in h and 'data-ptab-panel="stack"' not in h
        assert 'data-ptab="tmux"' in h and 'data-ptab="vm"' in h
