"""v5 shell: project sidebar, right-hand drawer, embed mode, tmux session page, Agents nav tab."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import make_client, make_settings

from pm.core.models import Agent, AgentKind, AgentState
from pm.infra import snapshot as services
from pm.tmux import ctl as tmuxctl
from pm.tmux.ctl import SEP
from pm.web.app import create_app

CSS = Path(__file__).parent.parent / "src/pm/static/css/shell.css"
JS = Path(__file__).parent.parent / "src/pm/static/js"


def _line(sid, sname, wid, pid, cwd):
    return SEP.join([sid, sname, "1", wid, "0", "main", "1", pid, "4242", "claude", cwd, "1", "t"]) + "\n"


@pytest.fixture
def env(fake_tmux, tmp_path, monkeypatch):
    dev = tmp_path / "dev"
    (dev / "alpha").mkdir(parents=True)
    (dev / "beta").mkdir(parents=True)
    s = make_settings(fake_tmux, tmp_path, terminal_enabled=True)
    monkeypatch.setenv("FAKE_TMUX_OUT", _line("$1", "work", "@1", "%12", str(dev / "alpha")))
    agent = Agent(id="claude-12", kind=AgentKind.CLAUDE, pane_id="%12", session_id="$1", pid=4242,
                  cwd=str(dev / "alpha"), state=AgentState.RUNNING, state_since=1.0)
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes: [agent])
    monkeypatch.setattr(tmuxctl, "capture_pane", lambda settings, pane, lines=200: "hello")
    c = make_client(s, logged_in=True)
    return s, c


def _rows(html):
    return re.findall(r'<a class="vs-row[^"]*" href="([^"]+)"', html)


def test_sidebar_only_on_project_and_launch(env):
    _, c = env
    for path in ("/p/alpha", "/p/alpha/launch"):
        h = c.get(path).text
        assert 'class="v5-side' in h and "v5-shell has-side" in h, path
        assert "/static/js/side-init.js" in h and "/static/js/side.js" in h
    for path in ("/", "/p", "/servers", "/backlog", "/audit", "/settings"):  # section sidebar
        r = c.get(path)
        assert 'class="v5-side vs-nav' in r.text and "/static/js/side-init.js" in r.text, path



def test_sidebar_rows_and_current_server_side(env):
    _, c = env
    h = c.get("/p/beta").text
    assert sorted(r for r in _rows(h) if r.startswith("/p/")) == ["/p/alpha", "/p/beta"]
    cur = re.findall(r'<a class="vs-row vs-prow on" href="([^"]+)" aria-current="page"', h)
    assert cur == ["/p/beta"]
    assert h.count('aria-current="page" data-p=') == 1
    # alpha has the running agent: dot + count rendered by the server
    assert re.search(r'href="/p/alpha"[^>]*>\s*<span class="dot dot-running"', h)
    assert 'class="vs-c">1<' in h


def test_launch_page_marks_its_project(env):
    _, c = env
    h = c.get("/p/alpha/launch").text
    assert re.findall(r'<a class="vs-row vs-prow on" href="([^"]+)"', h) == ["/p/alpha"]


def test_agents_and_servers_left_the_desktop_tabbar(env):
    _, c = env
    h = c.get("/p").text
    nav = h[h.index('<nav class="tabbar"'):]
    order = re.findall(r'<a href="(/[a-z]*)" class="', nav)
    assert order[:2] == ["/", "/p"] and "/agents" not in order  # Agents lives in the sidebar / Menu
    assert 'href="/servers" class="t-phone' in nav  # only the phone bottom nav keeps Servers
    sheet = nav[nav.index('id="moresheet"'):]
    assert 'href="/agents"' in sheet  # phones reach Agents from the More sheet
    side = h.split('id="v5side"')[1].split("</aside>")[0]
    assert 'aria-label="Manage"' in side and 'href="/agents"' in side and 'href="/servers"' in side


def test_drawer_present_where_agent_links_live(env):
    _, c = env
    for path in ("/", "/p/alpha", "/tmux", "/tmux/s1"):
        h = c.get(path).text
        assert 'id="v5chat"' in h and "/static/js/drawer.js" in h, path
    for path in ("/p", "/servers", "/audit", "/settings", "/p/alpha/launch"):
        h = c.get(path).text
        assert 'id="v5chat"' not in h and "drawer.js" not in h, path


def test_rows_stay_real_links(env):
    _, c = env
    assert 'href="/a/claude-12"' in c.get("/").text
    assert 'href="/a/claude-12"' in c.get("/p/alpha").text


def test_embed_agent_page(env):
    _, c = env
    full = c.get("/a/claude-12")
    emb = c.get("/a/claude-12?embed=1")
    assert emb.status_code == 200
    assert '<html lang="en" class="v5-embed">' in emb.text and "v5-embed" not in full.text
    assert 'id="v5chat"' not in emb.text
    assert "frame-ancestors 'self'" in emb.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in full.headers["content-security-policy"]
    # embedded forms are the same forms: CSRF field still there
    assert 'name="csrf"' in emb.text


def test_embed_not_enabled_by_other_values(env):
    _, c = env
    r = c.get("/a/claude-12?embed=0")
    assert "v5-embed" not in r.text and "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_tmux_overview_has_global_sidebar_and_opens_rows(env):
    _, c = env
    h = c.get("/tmux").text
    assert 'class="v5-side vs-nav' in h and 'data-p="tmux"' in h and "vs-foot" in h
    assert 'href="/tmux?open=s1"' not in h and "Filter sessions" not in h  # no sessions sidebar
    assert 'href="/tmux/s1"' in h  # the row's own link still works (redirects)
    h = c.get("/tmux?open=s1").text
    assert re.findall(r'<a class="vs-row on" href="([^"]+)" aria-current="page"', h) == ["/tmux"]
    assert "%12" in h and 'href="/a/claude-12"' in h
    assert 'href="/term/s1?w=w1"' in h and 'href="/term/s1"' not in h  # ids, never names
    assert 'href="/p/alpha"' in h  # path links to the known project


def test_tmux_session_page_redirects_and_rejects_names_and_unknown_ids(env):
    _, c = env
    r = c.get("/tmux/s1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/tmux?open=s1"
    assert c.get("/tmux/s99").status_code == 404
    assert c.get("/tmux/work").status_code == 404
    assert c.get("/tmux/%241").status_code == 404
    r = c.get("/tmux?s=work", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/tmux?open=s1"


def test_session_page_requires_login(env):
    s, _ = env
    anon = TestClient(create_app(s), base_url="https://testserver")
    r = anon.get("/tmux/s1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_no_hex_no_inline_script_in_v5_assets(env):
    _, c = env
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", CSS.read_text())
    for name in ("side-init.js", "side.js", "drawer.js"):
        assert (JS / name).exists()
    for path in ("/p/alpha", "/tmux/s1", "/a/claude-12?embed=1"):
        h = c.get(path).text
        assert not re.search(r"<script(?![^>]*\bsrc=)", h), path
        assert " style=" not in h.split("<main>", 1)[1], path


def test_unknown_project_still_404_without_sidebar_crash(env):
    _, c = env
    assert c.get("/p/nope").status_code == 404


def test_drawer_open_fills_browser_projects_centered_header_sticky(env):
    _, c = env
    css = CSS.read_text()
    assert "body.chat-open .v5-shell:not(.has-side){max-width:none;width:100%;margin:0;border-left:0;border-right:0;background:none}" in css
    assert "body.wide" not in css
    assert 'class="wide"' not in c.get("/p").text
    assert 'class="wide"' not in c.get("/").text
    # header is pinned so sticky sidebar/drawer at --top-h never leave a gap while scrolling
    assert "grid-template-rows:var(--top-h) auto" in css
    assert "position:sticky;top:0;height:var(--top-h)" in css


def test_drawer_state_in_url_hash_contract(env):
    _, c = env
    js = (JS / "drawer.js").read_text()
    init = (JS / "drawer-init.js").read_text()
    # state in the hash, validated by the same regexes, restored on load/popstate, cleared on close
    for tok in ("#chat=", "#term=", "pushState", "location.replace", "popstate", "fromHash", "AGENT.test", "TERM.test"):
        assert tok in js, tok
    assert "chat-open" in init and "document.body" in init and "/a/" in init and "s\\d+" in init
    assert "innerHTML" not in js and "send" not in init
    h = c.get("/p/alpha").text
    assert h.index("drawer-init.js") > h.index("<body")  # runs before the layout paints
    assert h.index("drawer-init.js") < h.index("v5chat")
    assert "drawer-init.js" not in c.get("/a/claude-12?embed=1").text


def test_project_sidebar_has_no_filter_or_fold_controls(env):
    _, c = env
    h = c.get("/p/alpha").text
    side = h.split('id="v5side"')[1].split("</aside>")[0]
    assert "class=\"vs-all\"" not in h  # no head link (the list ends with its own "All projects" row)
    assert "vs-fold-btn" not in side and "vs-filter" not in side and "vs-pbar" not in side and "Menu</span>" not in side
    assert "vs-toggle" in h  # width toggle stays, now on the right of the header
    assert side.index("vs-toggle") > side.index('class="vs-head"')
    assert re.search(r'class="vs-row vs-prow on" href="/p/alpha"', h)
    assert 'href="/p/beta"' not in side  # idle project: not listed by default
    assert re.search(r'class="vs-row vs-prow vs-allp" href="/p"', side) and "All projects" in side  # but reachable
    css, js = CSS.read_text(), (JS / "side.js").read_text()
    assert "vs-fold-btn" not in css and ".vs-fold:" not in css and "vs-fold-btn" not in js and "sidefold" not in js
    # other sidebars keep their own filter
    assert "vs-fold-btn" not in c.get("/tmux/%241").text


def test_inbox_views_fold_wiring(env):
    _, c = env
    h = c.get("/").text
    side = h.split('id="v5side"')[1].split("</aside>")[0]
    assert 'class="vs-pt vs-it" aria-expanded="true" aria-controls="vs-isub"' in side
    assert side.index('id="vs-isub"') < side.index('data-v="active"') < side.index('data-v="ended"')
    init, js, css = (JS / "side-init.js").read_text(), (JS / "side.js").read_text(), CSS.read_text()
    assert "pm.v5.inboxopen" in init and "ib-0" in init and "pm.v5.inboxopen" in js and "vs-iclosed" in js
    assert "sessionStorage" in init and "localStorage.getItem('pm.v5.inboxopen')" not in init
    assert "html.ib-0 .vs-isub{display:none}" in css and "html.ib-1:not(.side-collapsed) .vs-iclosed .vs-isub" in css
    assert " vs-iclosed" not in h.split('id="v5side"')[0].rsplit("<aside", 1)[1]  # open on the Inbox
    for path in ("/tmux", "/backlog", "/p"):  # folded on the server elsewhere, toggle says so
        a = c.get(path).text
        tag = a.split('id="v5side"')[0].rsplit("<aside", 1)[1]
        assert " vs-iclosed" in tag
        assert 'class="vs-pt vs-it" aria-expanded="false"' in a



def test_sidebar_shell_fills_width_no_body_gutters(env):
    css = CSS.read_text()
    assert ".v5-shell.has-side{width:100%;margin:0}" in css
    assert ".v5-shell.has-side>main{flex:1 1 0;min-width:0;max-width:none;margin:0;border:0;" in css
    assert "align-items:flex-start;background:var(--page);min-height" in css
    assert "max-width:960px" in css  # no-sidebar pages stay centred
    assert "max-width:1120px" not in css and "max-width:1760px" not in css
