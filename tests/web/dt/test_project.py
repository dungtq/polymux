"""dt project view (PRD §6.4; pm.web.dt.project, templates/project.html + project/_*.html + _project_live.html,
css/project.css, js/project.js): the view model (sidebar with need-you badges, head actions, Git ▾ items, tiles, tabs
with counts, Agents bands, Plans, Docs, first tab and remembered sidebar), the page and the live fragment through the
app with the real dt theme, plus the M2 follow-ups (menus.details, Inbox empty_nav, session fallback line)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from pm.core.models import AgentState, GitStatus, Project, StackStatus
from pm.infra import snapshot as services
from pm.projects.plans import Plan
from pm.web import themes
from pm.web.deps import project_sidebar
from pm.web.dt import DtTheme, inbox, menus, ntfy, project, session

from .conftest import FAIL, IDLE, NOW, RUN, WAIT, agent, make_env
from .kit import WORK, Doc, one

THEME = themes.HERE / "dt"
SETTINGS = SimpleNamespace(idle_after_s=28800, terminal_enabled=False, push_remote="gitdev")

GIT = GitStatus("main", dirty=3, ahead=1, behind=0, has_remote=True)


def proj(name="alpha", git=GIT, stack=None, path="/dev/alpha"):
    return Project(name, path, git=git, stack=stack, agent_ids=())


def snap_of(agents=(RUN, WAIT, FAIL, IDLE)):
    by = {p: tuple(a.id for a in agents if a.project == p) for p in ("alpha", "beta", "gamma")}
    return services.Snapshot(ts=1.0, agents=tuple(agents),
                             projects=tuple(Project(p, f"/dev/{p}", agent_ids=by[p]) for p in by))


class Req:
    def __init__(self, q=None, cookies=None):
        self.query_params, self.cookies = q or {}, cookies or {}


def ctx(**kw):
    snap = snap_of()
    base = {"project": proj(), "sidebar": project_sidebar(snap, "alpha"), "all_agents": snap.agents,
            "agents": [RUN, WAIT], "idle_agents": [IDLE], "pinned_agents": [], "inbox_meta": {}, "now": NOW,
            "ended": [], "ended_total": 0, "ended_ntfy": {}, "stop_agents": [RUN, WAIT, IDLE],
            "backlog": {"open_n": 4, "tracker": True}, "todo": {"open_n": 2, "total": 5},
            "plans": {"total": 3, "shown": [Plan("first", 10, NOW - 120), Plan("PLAN", 5, NOW - 60, "root")]},
            "ps": SimpleNamespace(hidden=[]), "first_key": "readme", "has_features": True, "has_claude": False,
            "has_agents": False}
    return {**base, **kw}


@pytest.fixture(autouse=True)
def _no_snapshot(monkeypatch):
    monkeypatch.setattr(services, "published", lambda settings: None)


def build(**kw):
    return project.build(ctx(**kw), SETTINGS, now=NOW)


# ---------------------------------------------------------------- view model
def test_sidebar_projects_with_agents_and_need_badge():
    v = build()
    assert [p.name for p in v.side] == ["alpha", "beta", "gamma"] and v.side_total == 3
    a, b, g = v.side
    assert a.cur and a.n == 3 and a.need == 1 and a.running == 1 and a.open
    assert [x.id for x in a.agents] == ["claude-bbbbb2", "claude-aaaaa1", "claude-ddddd4"]  # needs you first
    assert b.need == 1 and b.open and not b.cur  # a failed agent: open, badge
    assert g.n == 0 and not g.open and g.agents == []
    assert "codex-ccccc3" in b.q and "beta" in b.q  # an agent match keeps its project row


def test_sidebar_lists_five_agents_of_other_projects():
    many = [agent(f"claude-{i:06x}", "beta", AgentState.IDLE, since=NOW - 9000) for i in range(7)]
    snap = snap_of(many)
    v = build(sidebar=project_sidebar(snap, "alpha"), all_agents=snap.agents)
    b = v.side[1]
    assert len(b.agents) == project.SIDE_AGENTS and b.more == 2 and not b.open  # all idle: folded


def test_head_stats_and_actions():
    v = build()
    texts = [s if isinstance(s, str) else s["text"] for s in v.stats]
    assert texts[:3] == ["3 agents", "1 running", "1 needs you"] and texts[-1] == "/dev/alpha"
    assert {"text": "main · 3 dirty · ↑1 to push", "cls": "warn"} in v.stats
    labels = [a["label"] for a in v.actions]
    assert labels == ["Launch agent", "Git", "Settings", "Stop agents"]  # no compose stack: no Restart stack
    assert v.actions[2]["attrs"] == {"data-pj-settings": "/p/alpha/settings/panel"} and v.actions[2]["icon"] == "gear"
    assert sum(bool(a.get("pri")) for a in v.actions) == 1 and v.actions[0]["href"] == "/p/alpha/launch"
    assert v.actions[0]["attrs"]["data-pj-launch"] == "alpha"
    stop = v.actions[-1]
    assert stop["danger"] and stop["attrs"]["data-pj-stop-n"] == "3" and "disabled" not in stop["attrs"]


def test_restart_stack_and_stop_disabled():
    v = build(project=proj(stack=StackStatus(1, 2)), stop_agents=[])
    labels = [a["label"] for a in v.actions]
    assert "Restart stack" in labels
    assert "disabled" in v.actions[-1]["attrs"]
    v = build(project=proj(stack=StackStatus(1, 2)), ps=SimpleNamespace(hidden=["stack"]))
    assert "Restart stack" not in [a["label"] for a in v.actions]


def test_git_menu_items_ask_first():
    items = {i["id"]: i for i in project.git_menu("alpha", GIT, "gitdev") if not i.get("sep")}
    assert [k for k in items] == ["fetch", "pull", "push", "tab", "remotes"]
    assert (items["fetch"]["label"], items["push"]["label"]) == ("Fetch from gitdev", "Push to gitdev")
    assert all(i["event"] == "pj:git" for i in items.values())  # js/project.js plans, confirms, then posts
    assert not any("post" in i for i in items.values())  # no direct POST from the menu: the plan comes first
    off = {i["id"]: i for i in project.git_menu("alpha", GitStatus("main"), "origin") if not i.get("sep")}
    assert off["push"]["disabled"] and off["push"]["why"] == "no origin remote" and "event" not in off["push"]
    assert off["remotes"]["event"] == "pj:git" and off["remotes"]["note"] == "add origin"  # the way to add it
    assert project.git_menu("alpha", None, "origin")[0]["disabled"]
    v = build()
    menu = json.loads(v.actions[1]["attrs"]["data-menu"])
    assert [i.get("id") for i in menu if not i.get("sep")] == ["fetch", "pull", "push", "tab", "remotes"]
    assert v.remote == "gitdev" and {i.get("id"): i for i in menu}["push"]["label"] == "Push to gitdev"
    assert project.git_parts(GitStatus("main"), "origin")[1] == "clean · no origin remote"
    assert project.git_parts(GitStatus("main", has_remote=True), "origin")[1] == "clean · in sync"


def test_tiles_and_tabs():
    v = build(project=proj(stack=StackStatus(1, 2)))
    t = {x.key: x for x in v.tiles}
    assert list(t) == ["agents", "git", "todo", "issues", "stack", "vms"]
    assert (t["agents"].value, t["agents"].cls) == ("3", "warn") and "1 need you" in t["agents"].sub
    assert t["git"].value == "main" and t["git"].cls == "warn"
    assert (t["todo"].value, t["todo"].small) == ("2", "open of 5")
    assert (t["stack"].value, t["stack"].cls) == ("1/2", "warn")
    keys = [x["key"] for x in v.tabs]
    assert keys == ["agents", "tmux", "files", "git", "stack", "vms", "todo", "issues", "plans", "docs"]
    counts = {x["key"]: x["count"] for x in v.tabs}
    assert counts["agents"] == 3 and counts["git"] == 3 and counts["todo"] == 2 and counts["issues"] == 4
    assert counts["plans"] == 3 and counts["files"] is None
    assert all(x["controls"] == "pj-t-" + x["key"] for x in v.tabs)
    assert [x["key"] for x in v.tabs if x["cur"]] == ["agents"]
    hid = build(ps=SimpleNamespace(hidden=["browse", "git", "backlog"]))
    assert [x["key"] for x in hid.tabs] == ["agents", "tmux", "stack", "vms", "todo", "plans", "docs"]
    assert "tmux" not in [x["key"] for x in build(ps=SimpleNamespace(hidden=["live"])).tabs]


def test_first_tab_and_remembered_sidebar():
    v = build(request=Req({"tab": "plans"}, {project.NAV_COOKIE: "1"}))
    assert v.cur_tab == "plans" and v.navc and [x["key"] for x in v.tabs if x["cur"]] == ["plans"]
    v = build(request=Req({"tab": "nope"}, {project.NAV_COOKIE: "0"}))
    assert v.cur_tab == "agents" and not v.navc


def test_agents_bands_use_inbox_rows_refreshing_the_tab():
    v = build(pinned_agents=[FAIL])
    assert [(g.key, [r.id for r in g.rows]) for g in v.groups] == [
        ("pinned", ["codex-ccccc3"]), ("needs", ["claude-bbbbb2"]), ("running", ["claude-aaaaa1"]),
        ("idle", ["claude-ddddd4"])]
    for g in v.groups:
        for r in g.rows:
            posts = [i for i in r.menu if i.get("post")]
            assert posts and all(i["refresh"] == "#pj-agents" for i in posts)


def test_plans_and_docs():
    v = build()
    assert [p.name for p in v.plans] == ["first", "PLAN"] and v.plans_total == 3
    assert v.plans[0].reader == "/p/alpha/plans/first?embed=1" and v.plans[0].href == "/p/alpha/plans/first"
    assert v.plans[1].reader == "/p/alpha/plans/PLAN?embed=1&src=root" and v.plans[1].age == "1m"
    # no rendered first document in the context: README and CHANGELOG are missing, FEATURES exists and loads first
    assert [(d["key"], d["cur"], d["miss"], d["list"]) for d in v.docs.items] == [
        ("readme", False, True, False), ("changelog", False, True, False), ("features", True, False, False),
        ("skills", False, False, True)]
    assert v.docs.lazy and v.docs.html == ""


DOC = '<div class="x-cl"><article class="pj-doc md"><h1>x</h1></article></div>'
NOTE = '<div class="p-g pj-note"><div class="p-empty"><span>No CHANGELOG here.</span></div></div>'


def test_docs_default_readme_else_first_existing():
    d = build(first_key="readme", first_html=DOC).docs  # README found (no CHANGELOG): shown at once
    assert [x["key"] for x in d.items if x["cur"]] == ["readme"] and d.html == DOC and not d.lazy
    d = build(first_key="changelog", first_html=DOC).docs  # CHANGELOG found, README unknown: try README first
    assert [x["key"] for x in d.items if x["cur"]] == ["readme"] and d.lazy and not any(x["miss"] for x in d.items)
    d = build(first_key="changelog", first_html=NOTE, has_features=False).docs  # nothing at all: the empty box
    assert not d.lazy and d.html == "" and d.items[0]["cur"]
    assert [x["key"] for x in d.items if not x["miss"]] == ["skills"]  # Skills is a list, never the first doc
    d = build(ps=SimpleNamespace(hidden=["skills"])).docs
    assert "skills" not in [x["key"] for x in d.items]
    d = build(first_key="changelog", first_html=NOTE, has_claude=True).docs  # neither: the first optional one
    assert [x["key"] for x in d.items if x["cur"]] == ["features"] and d.lazy


def test_issues_tile_and_tab_agree_with_the_list():
    def issues(backlog):
        v = build(backlog=backlog)
        t = {x.key: x for x in v.tiles}["issues"]
        return (t.value, t.small, t.sub), {x["key"]: x["count"] for x in v.tabs}["issues"]
    assert issues({"open_n": 4, "tracker": True}) == (("4", "open", "tracker"), 4)
    # README A18: BACKLOG.md as a file: the tile counts the file and says so; the tab (tracker only) has no count
    assert issues({"open_n": 3, "tracker": False, "exists": True}) == (
        ("3", "in file", "BACKLOG.md"), None)
    assert issues({"open_n": 0, "tracker": False, "exists": False}) == (("–", "", "none open"), None)


def test_live_fragment_context_builds_without_page_parts():
    keep = ("project", "agents", "idle_agents", "pinned_agents", "inbox_meta", "now", "ended", "ended_total",
            "ended_ntfy", "all_agents", "csrf")
    v = project.build({k: v for k, v in ctx().items() if k in keep}, SETTINGS, now=NOW)
    assert v.side == [] and v.groups and v.plans == [] and v.cur_tab == "agents"


def test_global_never_raises(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("x")

    monkeypatch.setattr(project, "build", boom)
    assert project.bind(lambda: SETTINGS)({}) is None  # pass_context: called with the context


# ---------------------------------------------------------------- M2 follow-ups
def test_details_item_is_shared():
    d = menus.details()
    assert d == {"id": "det", "label": "Details", "icon": "down", "key": "X", "det": True}
    src = (Path(inbox.__file__).read_text(), Path(ntfy.__file__).read_text())
    assert all("menus.details()" in s and '"Details"' not in s for s in src)


@pytest.mark.parametrize(("url", "nav"), [("/?view=needs", True), ("/", True), ("/p/alpha/launch", False),
                                          (None, False)])
def test_inbox_empty_nav(monkeypatch, url, nav):
    monkeypatch.setattr(inbox, "_empty", lambda *_a: ("Nothing.", url, "link"))
    ib = inbox.build({"sort_url": "/", "agents": [], "sections": [], "inbox_meta": {}}, SETTINGS, now=NOW)
    assert ib.empty_nav is nav
    tpl = (THEME / "templates" / "_inbox.html").read_text()
    assert "ib.empty_nav" in tpl and 'startswith("/?")' not in tpl


# ---------------------------------------------------------------- through the app (real dt theme)
def test_project_page_frame(app_env):
    r = app_env.c.get("/p/alpha")
    assert r.status_code == 200, r.text[:300]
    doc = Doc(r.text)
    assert doc.one("main")["class"].split() == ["x-main"]  # a ported page
    lay = doc.one("div", id="pj-lay")
    assert lay["data-pj"] == "alpha" and lay["data-pj-base"] == "/p/alpha" and "navc" not in lay["class"]
    doc.one("nav", id="pj-nav")
    assert doc.one("input", data_p_filter="pj-nav")["data-p-filter-rows"] == "#pj-nl"
    assert doc.one("a", class_="pj-nn", aria_current="page")["href"] == "/p/alpha"
    assert {a["data-pj-ag"] for a in doc.find("a", data_pj_ag=True)} == {RUN.id, WAIT.id, IDLE.id}
    assert "pj-head" in doc.one("div", id="pj-head")["class"] and '<h1>alpha</h1>' in r.text
    assert doc.one("a", data_pj_launch="alpha", class_="pri")["href"] == "/p/alpha/launch"
    assert doc.one("button", data_pj_git="alpha")["aria-haspopup"] == "menu"
    assert len(doc.find("button", data_pj_goto=True)) == 6
    tabs = [t["data-p-tab"] for t in doc.find(role="tab", data_p_tab=True)
            if t.get("aria-controls", "").startswith("pj-t-")]  # partials may have tabs of their own
    secs = [s for s in doc.find("section", class_="pj-tab") if s.get("id", "").startswith("pj-t-")]
    assert [s["data-pj-tab"] for s in secs] == tabs and all(s["data-pj-base"] == "/p/alpha" for s in secs)
    assert [s["data-pj-tab"] for s in secs if "hidden" not in s] == ["agents"]
    assert all(s["id"] == "pj-t-" + s["data-pj-tab"] and s["role"] == "tabpanel" for s in secs)
    box = doc.one("div", id="pj-agents")
    assert "data-i-rows" in box and box["hx-get"] == "/p/alpha/live" and "every" not in box["hx-trigger"]
    assert {d["data-row-id"] for d in doc.find(class_="p-gr", data_row_id=True)} >= {RUN.id, WAIT.id, IDLE.id}
    assert "hidden" in doc.one("aside", id="pj-reader")
    for f in ("css/inbox.css", "css/project.css", "js/inbox.js", "js/project.js"):
        assert f"/static/t/dt/{f}" in r.text


def test_project_page_first_tab_and_collapsed(app_env):
    app_env.c.cookies.set(project.NAV_COOKIE, "1")
    doc = Doc(app_env.c.get("/p/alpha?tab=docs").text)
    assert "navc" in doc.one("div", id="pj-lay")["class"]
    assert [s["data-pj-tab"] for s in doc.find("section", class_="pj-tab") if "hidden" not in s] == ["docs"]
    assert doc.one("button", data_pj_doc="/p/alpha/view/readme")["aria-pressed"] in ("true", "false")


def test_project_live_fragment(app_env):
    r = app_env.c.get("/p/alpha/live", headers={"HX-Request": "true"})
    assert r.status_code == 200
    doc = Doc(r.text)
    assert doc.one("div", data_p_table="pj-agents")["data-list"] == "pj-agents"
    assert "pj-lay" not in r.text and "<html" not in r.text


def test_session_fallback_line(app_env, monkeypatch):
    monkeypatch.setitem(session.MODES, "live", lambda *_a: 1 / 0)
    r = app_env.c.get(f"/a/{RUN.id}")
    assert r.status_code == 200 and "The session tab could not be built" in r.text
    for name in ("agent.html", "builder.html", "agent_ended.html"):
        assert "could not be built" in (THEME / "templates" / name).read_text()


# ---------------------------------------------------------------- static rules of the page files
def test_project_js_rules():
    js = (THEME / "static" / "js" / "project.js").read_text()
    assert "addEventListener('keydown'" not in js and "setInterval" not in js and "fetch(" not in js.replace(
        "pm7.fetch(", "")
    assert "pm7.keys.register" in js and "'mod+shift+B'" in js and "pm7:pj-tab" in js
    assert "pm7.confirm" in js and "typed: 'PANIC'" in js and "/git/plan?action=" in js
    assert len(js.splitlines()) <= 500 and max(map(len, js.splitlines())) <= 120


def test_project_css_uses_layout_tokens():
    css = (THEME / "static" / "css" / "project.css").read_text()
    for tok in ("--pj-cap", "--reader-min", "--reader-w", "--side-float-max"):
        assert f"var({tok})" in css
    assert re.search(r"@container pj \(max-width:760px\)", css)
    assert len(css.splitlines()) <= 500


# ---------------------------------------------------------------- M5 G1: settings, remotes, links, Skills, tmux, deep links
def test_link_groups_linked_projects_and_plugin_links():
    from pm.web.plugins import ExtLink
    v = build(linked=["beta"], ext_groups=[("docs", [ExtLink("plans", "https://docs.example/plans", "Plans in docs")])])
    assert [g.title for g in v.links] == ["Linked", "docs"]
    assert v.links[0].links[0] == {"label": "beta", "url": "/p/beta?tab=files", "desc": "Browse beta (read-only)",
                                   "icon": "", "ext": False}
    assert v.links[1].links[0]["url"] == "https://docs.example/plans" and v.links[1].links[0]["ext"]
    assert build().links == []


def test_tmux_sessions_are_the_tmux_pages_rows():
    a = agent("claude-aaaaa1", "alpha", AgentState.WAITING, since=NOW - 30)
    v = build(sessions=[WORK, one("$4", "keeper", 4)], all_agents=[a])
    ss = v.sessions
    assert [r.name for r in ss.rows] == ["work", "keeper"] and ss.closable == 1  # keeper is protected
    work = ss.rows[0]
    assert work.state == "waiting" and work.agents[0].id == a.id and work.windows == 2 and work.clients == 2
    assert work.project == "alpha" and work.src == "/tmux/s1/live"
    posts = [i for i in work.menu if i.get("post")]
    assert posts and all(i["refresh"] == "#pj-agents" for i in posts)  # the live fragment re-renders #pj-tmux
    assert [c.get("label") for c in ss.cols if c.get("label")] == ["Session", "Windows", "Clients", "Agents", "Active"]
    assert not any(c.get("href") for c in ss.cols)
    assert {x["key"]: x["count"] for x in v.tabs}["tmux"] == 2
    assert build(ps=SimpleNamespace(hidden=["live"])).sessions is None


@pytest.fixture
def tmux_env(settings, snap, monkeypatch, tmp_path):
    root = tmp_path / "dev" / "alpha"
    root.mkdir(parents=True)
    (root / "README.md").write_text("# Alpha\n")
    sk = root / ".claude" / "skills" / "deploy"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text("---\nname: deploy\ndescription: Ship it.\n---\n# Deploy\n")
    snap.value = services.Snapshot(ts=1.0, sessions=(WORK,), agents=(RUN,), projects=(
        Project("alpha", str(root), sessions=("$1",), agent_ids=(RUN.id,), pane_ids=("%1",)),
        Project("beta", str(tmp_path / "b"))))

    async def fake_snapshot(*_a, **_k):
        return snap.value

    monkeypatch.setattr(services, "snapshot_async", fake_snapshot)
    return make_env(settings, DtTheme())


def test_page_head_settings_links_and_tmux_tab(tmux_env, monkeypatch):
    from pm.web import plugins
    from pm.web.plugins import ExtLink
    monkeypatch.setattr(plugins, "project_link_groups",
                        lambda *_a: [("docs", [ExtLink("files", "https://docs.example/f", "Files in docs", "files")])])
    r = tmux_env.c.get("/p/alpha")
    assert r.status_code == 200, r.text[:300]
    doc = Doc(r.text)
    assert doc.one("div", id="pj-lay")["data-pj-remote"] == "origin"  # pm.toml push_remote (js/project.js)
    gear = doc.one("button", data_pj_settings="/p/alpha/settings/panel")
    assert gear["title"] == "Project settings of alpha"
    ext = doc.one("a", href="https://docs.example/f")
    assert ext["target"] == "_blank" and "noopener" in ext["rel"] and ext["title"] == "Files in docs"
    sec = doc.one("section", id="pj-t-tmux")
    assert "hidden" in sec and doc.one("div", id="pj-tmux")["data-tm-rows"] == ""
    assert doc.one("div", data_p_table="pj-tmux")["class"].split() == ["p-g", "tm-list"]
    assert doc.find("button", aria_controls="p-det-pj-tmux-$1")  # the shared row macro, this table's details
    close = doc.one("button", data_pj_closeall="alpha")
    assert close["data-pj-closeall-n"] == "1"
    for f in ("js/pjsettings.js", "js/tmux.js", "css/tmux.css"):
        assert f"/static/t/dt/{f}" in r.text
    live = tmux_env.c.get("/p/alpha/live", headers={"HX-Request": "true"}).text
    oob = Doc(live).one("div", id="pj-tmux")
    assert oob["hx-swap-oob"] == "true" and 'data-row-id="$1"' in live


def test_tmux_tab_hidden_by_live_setting(tmux_env):
    from pm.projects import settings as pset
    pset.save_hidden(tmux_env.s, "alpha", ["live", "skills"])
    r = tmux_env.c.get("/p/alpha")
    assert 'id="pj-t-tmux"' not in r.text and 'id="pj-tmux"' not in r.text and "/view/skills" not in r.text
    live = tmux_env.c.get("/p/alpha/live", headers={"HX-Request": "true"}).text
    assert "hx-swap-oob" not in live  # no target on the page: no out-of-band part


def test_settings_panel_is_the_dt_override_and_saves(tmux_env):
    r = tmux_env.c.get("/p/alpha/settings/panel")
    assert r.status_code == 200
    doc = Doc(r.text)
    root = doc.one("div", id="pj-settings")
    assert root["data-pjs"] == "/p/alpha" and "style=" not in r.text and "<script" not in r.text
    form = doc.one("form", data_pjs_form="settings")
    assert form["action"] == "/p/alpha/settings" and "hx-post" not in form
    assert {i["value"] for i in doc.find("input", name="show")} >= {"browse", "live", "skills"}
    doc.one("div", id="git-remotes")  # the remotes panel inside
    assert doc.find("form", data_pjs_form="links") or "No other projects." in r.text  # discover() reads dev_root
    r = tmux_env.post("/p/alpha/settings", show=["browse", "git", "plans"], default_branch="main", vms="lab-*")
    assert r.status_code == 200
    assert Doc(r.text).one("p", data_pjs_flash=True)["data-pjs-flash"] == "ok"
    r = tmux_env.c.get("/p/alpha")
    assert 'id="pj-t-tmux"' not in r.text and 'id="pj-t-files"' in r.text  # live hidden now
    r = tmux_env.post("/p/alpha/settings", default_branch="bad branch!")
    assert Doc(r.text).one("p", data_pjs_flash=True)["data-pjs-flash"] == "bad"


def test_remotes_panel_is_the_dt_override(tmux_env, monkeypatch):
    from pm.git import remote as gitremote
    url = "git@git.example.com:you/alpha.git"
    monkeypatch.setattr(gitremote, "remotes", lambda _p: [gitremote.RemoteInfo("gitdev", url, url),
                                                           gitremote.RemoteInfo("origin", url, url)])
    r = tmux_env.c.get("/p/alpha/git/remotes")
    assert r.status_code == 200
    doc = Doc(r.text)
    box = doc.one("div", id="git-remotes")
    assert box["hx-get"] == "/p/alpha/git/remotes" and box["hx-trigger"] == "pm7:refresh"
    # the push remote (pm.toml push_remote, default origin) first, marked default
    assert [x["data-row-id"] for x in doc.find("div", data_row_id=True)] == ["origin", "gitdev"]
    chip = r.text.index('title="pm pushes only to this remote">default</span>')
    assert r.text.index('data-row-id="origin"') < chip < r.text.index('data-row-id="gitdev"')
    rm = doc.one("button", data_pjr_rm="gitdev")
    assert rm["aria-label"] == "Remove remote gitdev" and rm["title"] == "Remove remote gitdev"
    assert doc.one("form", data_pjs_form="add")["action"] == "/p/alpha/git/remote/add"
    assert len(doc.find("form", data_pjs_form="set-url")) == 2
    r = tmux_env.post("/p/alpha/git/remote/remove", name="origin", confirm="")  # the push remote wants the typed name
    assert Doc(r.text).one("p", data_pjs_flash=True)["data-pjs-flash"] == "bad"


def test_remotes_panel_without_remotes_has_no_empty_table(tmux_env, monkeypatch):
    """M3: no remote = one muted line, no table (its hidden phone header left an empty band with two rules)."""
    from pm.git import remote as gitremote
    monkeypatch.setattr(gitremote, "remotes", lambda _p: [])
    r = tmux_env.c.get("/p/alpha/git/remotes")
    assert r.status_code == 200 and 'role="table"' not in r.text and 'class="p-gh"' not in r.text
    assert Doc(r.text).one("p", class_="pjr-none") and "No remotes yet." in r.text
    assert Doc(r.text).one("form", data_pjs_form="add")  # the add form stays


def test_workspace_links_use_the_row_width():
    """D3: linked project names get the panel's full row (one per row) and wrap on phones."""
    src = (THEME / "templates" / "_project_settings.html").read_text()
    assert 'class="pjs-grid pjs-links"' in src
    css = (THEME / "static" / "css" / "project.css").read_text()
    assert ".pjs-grid.pjs-links{grid-template-columns:minmax(0,1fr)}" in css
    j = css.index(".pjs-links .p-chk>.ell{white-space:normal}")
    assert css.rindex("@media (max-width:699px){", 0, j) > css.index(".pjs-grid.pjs-links")  # phones only
    assert ".pj-fah .x-ib:focus-visible{outline-offset:-2px}" in css  # M2: the reader's Close ring stays inside


def test_browse_hash_loads_the_files_once_and_offers_the_root():
    """M4: #browse=<path> loads the listing once (a first show loads the hash's path itself); a path that is not there
    (404) or no answer shows a status line with a link back to the project root."""
    pj = (THEME / "static" / "js" / "project.js").read_text()
    i = pj.index("if (p.has('browse'))")
    block = pj[i:pj.index("return true;", i)]
    assert "had = !!f && f.hasAttribute('data-loaded')" in block and "&& had) openFiles(" in block
    assert "'htmx:responseError', 'htmx:sendError'" in pj and "'Back to the project root'" in pj
    assert "a.href = filesUrl('');" in pj and "a.setAttribute('data-pj-in', '');" in pj


def test_skills_view_is_the_dt_override(tmux_env):
    r = tmux_env.c.get("/p/alpha/view/skills", headers={"HX-Request": "true"})
    assert r.status_code == 200
    doc = Doc(r.text)
    assert [b["data-pjk-scope"] for b in doc.find("button", data_pjk_scope=True)] == ["local", "global"]
    row = doc.find("div", data_pj_skill=True)[0]  # local first (global = the real home's skills)
    assert row["data-pj-skill"].startswith("/p/alpha/skill/local/") and row["data-pj-skill-t"] == "deploy"
    assert doc.one("div", data_p_table="pj-sk-local")["data-key-enter"] == "event:pj:skill"
    page = Doc(tmux_env.c.get("/p/alpha?tab=docs").text)
    assert "data-pj-list" in page.one("button", data_pj_doc="/p/alpha/view/skills")
    nav = tmux_env.c.get("/p/alpha/view/skills")  # a browser visit: the page with the hash js/project.js reads
    assert nav.status_code == 303 and nav.headers["location"] == "/p/alpha#view=skills"


def test_panels_labels_and_key_menu():
    from pm.projects import settings as pset
    p = project.panels({"pub": None})
    assert set(p.labels) == set(pset.COMPONENTS) and p.labels["live"] == "tmux tab"
    gen = p.key_menu[0]
    assert gen["post"] == "/settings/ssh/keygen" and gen["refresh"] == "#git-remotes" and gen["confirm"]
    p = project.panels({"pub": SimpleNamespace(line="ssh-ed25519 AAAA pm")})
    assert p.key_menu[0]["copy"] == "ssh-ed25519 AAAA pm" and p.key_menu[-1]["href"] == "/settings?s=ssh"


def test_pjsettings_js_and_deep_links_rules():
    js = (THEME / "static" / "js" / "pjsettings.js").read_text()
    pj = (THEME / "static" / "js" / "project.js").read_text()
    for src in (js, pj):
        assert "setInterval" not in src and "addEventListener('keydown'" not in src
        assert "fetch(" not in src.replace("pm7.fetch(", "") and "confirm(" not in src.replace("pm7.confirm(", "")
        assert len(src.splitlines()) <= 500 and max(map(len, src.splitlines())) <= 120
    assert "typed: rn" in js and "pm7.fail" in js and "data-pjs-flash" in js and "pm7.pj.hash" in js
    for key in ("'browse'", "'view'", "'skill'", "'issues'", "'backlog'", "'issue'"):
        assert key in pj
    assert "pm7.backlog.reader(name(), issue)" in pj and "typed: n," in pj and "/tmux/close-all" in pj


def test_scripts_take_the_remote_name_from_the_server():
    """pm.toml push_remote: project.js / pjsettings.js read #pj-lay[data-pj-remote], pjgit.js the status JSON."""
    js = {f: (THEME / "static" / "js" / f).read_text() for f in ("project.js", "pjsettings.js", "pjgit.js")}
    assert not any("gitdev" in t for t in js.values())
    assert "data-pj-remote" in js["project.js"] and "data-pj-remote" in js["pjsettings.js"]
    assert "S.view.remote" in js["pjgit.js"]
