"""pm.web.dt.shell: the dt shell view model (docs/themes/dt/SHELL.md §2)."""

import dataclasses
import logging
from types import SimpleNamespace

import pytest
from jinja2 import Environment

from pm.core.config import Settings
from pm.core.models import AgentState
from pm.infra import snapshot as services
from pm.web import render
from pm.web.dt import shell, stores
from pm.web.dt.stores import TabState

from .conftest import agent, make_snap

W, R, F, I = AgentState.WAITING, AgentState.RUNNING, AgentState.FAILED, AgentState.IDLE


def snap4():
    return make_snap([agent("claude-aaa111", "beta", W, 50.0, window="hub"), agent("codex-bbb222", "alpha", R),
                      agent("claude-ccc333", "alpha", F), agent("devin-ddd444", None, I, 0.0)])


def test_field_names_match_the_contract():
    names = {c: [f.name for f in dataclasses.fields(c)] for c in
             (shell.Shell, shell.NavRow, shell.SessGroup, shell.SessRow, shell.Tab)}
    assert names[shell.Shell] == ["nav", "foot", "groups", "tabs", "home", "rev", "need", "running", "total"]
    assert names[shell.NavRow] == ["key", "href", "label", "icon", "count", "hot", "chord", "cur"]
    assert names[shell.SessGroup] == ["project", "href", "dot", "rows"]
    assert names[shell.SessRow] == ["id", "kind", "state", "href", "label", "since", "open", "cur", "title", "age"]
    assert names[shell.Tab] == ["kind", "id", "title", "href", "state", "agent_kind", "cur", "icon"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        shell.EMPTY.rev = 3


def test_nav_and_foot_rows():
    sh = shell.build(snap4(), TabState(), "/")
    assert [(n.key, n.href, n.icon, n.chord) for n in sh.nav] == [
        ("inbox", "/", "inbox", "G I"), ("projects", "/p", "folder", "G P"), ("tmux", "/tmux", "tmux", "G T"),
        ("notifications", "/ntfy", "bell", "G N"), ("backlog", "/backlog", "list", "G B"),
        ("agents", "/agents", "bot", "G A"), ("servers", "/servers", "server", "G S")]
    assert [n.label for n in sh.nav] == ["Inbox", "Projects", "tmux", "Notifications", "Backlog", "Agents", "Servers"]
    assert [(n.key, n.href, n.icon, n.chord) for n in sh.foot] == [
        ("audit", "/audit", "shield", ""), ("settings", "/settings", "sliders", "mod+,")]
    assert [n.key for n in sh.nav if n.cur] == ["inbox"]
    assert sh.nav[3].count is None  # no cheap unread source at render time (SHELL.md §2)


def test_nav_comes_from_the_core_list_with_plugin_items(monkeypatch):
    docs = render.NavItem("docs", "/x/docs", "docs files", icon="files")
    monkeypatch.setitem(render.env.globals, "NAV", (*render.NAV, docs))
    sh = shell.build(snap4(), TabState(), "/x/docs", nav="docs")
    assert [n.key for n in sh.nav][-2:] == ["servers", "docs"]  # after the dt page nav, generic icon, no chord
    last = sh.nav[-1]
    assert (last.href, last.label, last.icon, last.chord) == ("/x/docs", "docs files", "box", "")
    assert sh.nav[-1].cur and (sh.home.title, sh.home.icon) == ("docs files", "box")
    no_backlog = tuple(i for i in render.NAV if i.key != "backlog")
    monkeypatch.setitem(render.env.globals, "NAV", no_backlog)
    assert "backlog" not in [n.key for n in shell.build(snap4(), TabState(), "/").nav]  # a dt slot needs its item


def test_nav_items_core_href_and_label_override():
    main, foot = shell.nav_items([render.NavItem("ntfy", "/n2", "ntfy"), render.NavItem("audit", "/au", "Log"),
                                  render.NavItem("notifications", "/dup", "dup")])
    assert main == [("notifications", "/n2", "Notifications", "bell", "G N")]  # a plugin key taken by dt is dropped
    assert foot == [("audit", "/au", "Log", "shield", "")]


def test_session_age_text():
    assert [shell.age(s, 1000.0) for s in (None, 0, 995.0, 820.0, 1000 - 7300, 1000 - 90000, 2000)] == [
        "", "", "5s", "3m", "2h", "1d", "0s"]
    sh = shell.build(snap4(), TabState(), "/", now=50.0 + 125)
    rows = {r.id: r for g in sh.groups for r in g.rows}
    assert rows["claude-aaa111"].age == "2m" and rows["devin-ddd444"].age == ""


def test_counts():
    sh = shell.build(snap4(), TabState(), "/")
    assert (sh.need, sh.running, sh.total) == (2, 1, 4)
    nav = {n.key: n for n in sh.nav}
    assert (nav["inbox"].count, nav["inbox"].hot) == (2, True)
    assert (nav["projects"].count, nav["projects"].hot) == (2, False)  # projects with a live agent
    assert nav["tmux"].count == 2
    assert nav["agents"].count is None and nav["servers"].count is None
    quiet = shell.build(make_snap([agent("codex-bbb222", "alpha", R)]), TabState(), "/")
    inbox = quiet.nav[0]
    assert (inbox.count, inbox.hot) == (None, False)  # zero shows no pill


def test_groups_by_project_in_project_order():
    sh = shell.build(snap4(), TabState(), "/")
    assert [(g.project, g.href, g.dot) for g in sh.groups] == [
        ("alpha", "/p/alpha", "failed"), ("beta", "/p/beta", "waiting"), ("other", "", "")]
    alpha = sh.groups[0].rows
    assert [r.id for r in alpha] == ["claude-ccc333", "codex-bbb222"]  # by name
    r = sh.groups[1].rows[0]
    assert (r.id, r.kind, r.state, r.href, r.label, r.title, r.since) == (
        "claude-aaa111", "claude", "waiting", "/a/claude-aaa111", "claude-aaa111", "hub", 50)
    # DESIGN §6.1: the row shows the agent id; the window name only goes to title (none here: name = id)
    assert [(x.label, x.title) for x in alpha] == [("claude-ccc333", ""), ("codex-bbb222", "")]
    assert sh.groups[2].rows[0].since is None  # state_since 0 = unknown


def test_open_and_cur_flags_on_an_open_agent_page():
    tabs = TabState(rev=4, tabs=(("agent", "codex-bbb222"), ("term", "$4")), current=("term", "$4"))
    sh = shell.build(snap4(), tabs, "/a/codex-bbb222/builder", nav="inbox")
    assert sh.rev == 4
    assert [(t.kind, t.id, t.cur) for t in sh.tabs] == [("agent", "codex-bbb222", True), ("term", "$4", False)]
    assert not sh.home.cur and not any(n.cur for n in sh.nav + sh.foot)  # stored current is not used
    rows = {r.id: r for g in sh.groups for r in g.rows}
    assert rows["codex-bbb222"].open and rows["codex-bbb222"].cur
    assert not rows["claude-ccc333"].open and not rows["claude-ccc333"].cur


def test_agent_page_not_in_a_tab_shows_the_agent_in_the_home_tab():
    sh = shell.build(snap4(), TabState(tabs=(("agent", "codex-bbb222"),)), "/a/claude-aaa111", nav="inbox")
    assert sh.home.cur and not any(t.cur for t in sh.tabs)
    rows = {r.id: r for g in sh.groups for r in g.rows}
    assert rows["claude-aaa111"].cur and not rows["claude-aaa111"].open
    h = sh.home
    assert (h.title, h.icon, h.kind, h.id, h.href) == ("claude-aaa111", "chat", "home", "", "/a/claude-aaa111")
    assert h.state == "waiting"  # the phone subtitle shows the agent's state (+ project), not the Inbox stats
    gone = shell.build(snap4(), TabState(), "/a/claude-fff999", nav="inbox")
    assert (gone.home.title, gone.home.state) == ("claude-fff999", "ended")
    assert not any(n.cur for n in sh.nav + sh.foot)  # the session row marks the page, not Inbox
    sub = shell.build(snap4(), TabState(), "/a/claude-aaa111/builder", nav="inbox", agent_id="claude-aaa111")
    assert (sub.home.title, sub.home.icon, sub.home.href) == ("claude-aaa111", "chat", "/a/claude-aaa111/builder")


def test_home_tab_title_and_href():
    sh = shell.build(snap4(), TabState(), "/p/alpha", nav="projects")
    assert (sh.home.title, sh.home.href, sh.home.cur, sh.home.icon) == ("Projects", "/p/alpha", True, "folder")
    assert [n.key for n in sh.nav if n.cur] == ["projects"]
    assert shell.build(snap4(), TabState(), "/ntfy", nav="ntfy").home.title == "Notifications"
    assert shell.build(snap4(), TabState(), "/notifications").home.title == "Notifications"
    assert {shell.build(snap4(), TabState(), p).home.state for p in ("/", "/p/alpha", "/launch")} == {""}
    assert shell.build(snap4(), TabState(), "/term/s4", nav="term").home.title == "tmux"
    assert shell.build(snap4(), TabState(), "/settings", nav="settings").foot[1].cur
    odd = shell.build(snap4(), TabState(), "/launch")
    assert (odd.home.title, odd.home.href, odd.home.icon) == ("pm", "/launch", "inbox")
    on_agent = shell.build(snap4(), TabState(tabs=(("agent", "codex-bbb222"),)), "/a/codex-bbb222", nav="inbox")
    assert (on_agent.home.href, on_agent.home.title) == ("/", "Inbox")  # a tab is current: home = its section


def test_tab_states_titles_and_hrefs():
    tabs = TabState(tabs=(("agent", "claude-aaa111"), ("agent", "cursor-agent-0ff123"), ("term", "$4"),
                          ("term", "$9")))
    sh = shell.build(snap4(), tabs, "/term/s4")
    t = sh.tabs
    assert (t[0].title, t[0].state, t[0].agent_kind, t[0].href) == ("claude-aaa111", "waiting", "claude", "/a/claude-aaa111")
    assert (t[1].title, t[1].state, t[1].agent_kind) == ("cursor-agent-0ff123", "ended", "cursor-agent")
    assert (t[2].title, t[2].href, t[2].state, t[2].cur) == ("logs", "/term/s4", "", True)
    assert (t[3].title, t[3].state, t[3].href) == ("$9", "ended", "/term/s9")


def test_no_snapshot_yet():
    sh = shell.build(None, TabState(tabs=(("agent", "claude-aaa111"), ("term", "$4"))), "/")
    assert sh.groups == [] and (sh.need, sh.running, sh.total) == (0, 0, 0)
    assert [t.state for t in sh.tabs] == ["unknown", ""]


def test_page_path_of_a_fragment_request():
    def req(path, headers):
        return SimpleNamespace(url=SimpleNamespace(path=path), headers=headers)
    assert shell.page_path(req("/p/x", {"hx-current-url": "https://h/a/claude-1"})) == "/p/x"
    cur = {"host": "h", "hx-current-url": "https://h/a/claude-1?x=1"}
    assert shell.page_path(req("/dt/tabs", cur)) == "/a/claude-1"
    assert shell.page_path(req("/dt/tabs", {"host": "h", "referer": "https://h/tmux"})) == "/tmux"
    assert shell.page_path(req("/dt/tabs", {"host": "h", "referer": "https://evil/tmux"})) == "/"
    assert shell.page_path(req("/dt/tabs", {"host": "h", "referer": "//evil/x"})) == "/"
    assert shell.page_path(req("/dt/tabs", {"referer": "/tmux"})) == "/tmux"
    assert shell.page_path(req("/dt/tabs", {})) == "/"


def test_page_ref():
    assert shell.page_ref("/a/claude-aaa111/chat") == ("agent", "claude-aaa111")
    assert shell.page_ref("/term/s12") == ("term", "$12")
    assert shell.page_ref("/term/history") is None and shell.page_ref("/agents/cursor-agent") is None
    assert shell.page_ref("/a/claude-aaa111", "cursor-agent") == ("agent", "cursor-agent")
    assert shell.page_ref("/agents", "cursor-agent") is None  # ``agent`` of another page is not an agent tab


# ------------------------------------------------------------------ the Jinja global
@pytest.fixture
def jenv():
    e = Environment(autoescape=True)
    e.globals["dt_shell"] = shell.dt_shell
    return e


def _request(tmp_path, path="/"):
    st = Settings(state_dir=tmp_path / "state", token_file=tmp_path / "t", totp_file=tmp_path / "o")
    return SimpleNamespace(url=SimpleNamespace(path=path), headers={},
                           app=SimpleNamespace(state=SimpleNamespace(settings=st, tracker=None))), st


def test_dt_shell_in_a_template(jenv, tmp_path, monkeypatch):
    monkeypatch.setattr(services, "published", lambda settings: snap4())
    request, st = _request(tmp_path, "/a/codex-bbb222")
    stores.apply(st, stores.open_tab, ("agent", "codex-bbb222"))
    tpl = jenv.from_string("{% set s = dt_shell() %}{{ s.total }}|{{ s.rev }}|"
                           "{% for t in s.tabs %}{{ t.id }}{{ '*' if t.cur }}{% endfor %}")
    assert tpl.render(request=request, nav="inbox") == "4|1|codex-bbb222*"


def test_dt_shell_merges_the_tracker(jenv, tmp_path, monkeypatch):
    monkeypatch.setattr(services, "published", lambda settings: snap4())
    request, _ = _request(tmp_path)
    tracked = dataclasses.replace(snap4().agents[1], state=W, state_since=99.0)
    request.app.state.tracker = SimpleNamespace(agents=[tracked])
    assert jenv.from_string("{{ dt_shell().need }}").render(request=request) == "3"


def test_dt_shell_embed_or_no_request_is_empty(jenv, tmp_path, monkeypatch):
    monkeypatch.setattr(services, "published", lambda settings: snap4())
    request, _ = _request(tmp_path)
    tpl = jenv.from_string("{{ dt_shell().total }}")
    assert tpl.render(request=request, embed=True) == "0"
    assert tpl.render() == "0"


def test_dt_shell_never_raises_and_logs_once(jenv, tmp_path, monkeypatch, caplog):
    def boom(settings):
        raise RuntimeError("snapshot broke")

    monkeypatch.setattr(services, "published", boom)
    monkeypatch.setattr(shell, "_logged", set())
    request, _ = _request(tmp_path)
    tpl = jenv.from_string("{{ dt_shell().total }}{{ dt_shell().groups | length }}")
    with caplog.at_level(logging.ERROR, logger="pm.web.dt.shell"):
        assert tpl.render(request=request) == "00"
        assert tpl.render(request=request) == "00"
    assert sum("dt shell failed" in r.message for r in caplog.records) == 1
