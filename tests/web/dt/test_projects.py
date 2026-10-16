"""dt Projects list and New project dialog (pm.web.dt.projects, templates/projects.html + _projects_list.html +
projects/_new.html, js/projects.js, js/newproject.js): the view model turns the core's projects_page context into
segments with counts, sort headers with aria order, rows (pin, agents state, git, TODO, issues, ntfy, stack, menus),
the empty line and the foot; the dialog's form model and markup match the core's /p/new/plan and /p/new/run fields.
Plain Jinja on the real dt templates (no app, no IO)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

from pm.agents import sorting
from pm.core.models import Agent, AgentKind, AgentState, GitStatus, Project, StackStatus, TodoProgress
from pm.infra import snapshot as services
from pm.projects import newproject
from pm.web import themes
from pm.web.dt import projects

from .kit import render_launch_form

THEME = themes.HERE / "dt"
NOW = 1_000_000.0
SETTINGS = SimpleNamespace(idle_after_s=28800, dev_root="/srv/dev", project_templates=[{"name": "empty"}],
                           git_remote_template="git@git.example:dt/{name}.git", allow_bypass=False)


def ag(id_, project, state=AgentState.RUNNING):
    return Agent(id=id_, kind=AgentKind(id_.rsplit("-", 1)[0]), pane_id="%1", session_id="$1", pid=1, cwd="/x",
                 project=project, state=state, state_since=NOW - 60)


AGENTS = [ag("claude-aaaaa1", "alpha"), ag("codex-bbbbb2", "alpha", AgentState.WAITING),
          ag("claude-ccccc3", "beta", AgentState.IDLE)]
ALPHA = Project("alpha", "/srv/dev/alpha", git=GitStatus("main", dirty=3, ahead=1, behind=2, has_gitdev=True),
                todo=TodoProgress(1, 3), stack=StackStatus(1, 2), agent_ids=("claude-aaaaa1", "codex-bbbbb2"))
BETA = Project("beta", "/srv/dev/beta", git=GitStatus(None), agent_ids=("claude-ccccc3",))
GAMMA = Project("gamma site", "/srv/dev/gamma site")
COLS = sorting.sort_cols("projects", "newest", "/p", {})


def ctx(**kw):
    base = {"projects": [ALPHA, BETA, GAMMA], "cols": COLS, "self_url": "/p?sort=newest", "q": "", "flags": [],
            "activity": {"alpha": NOW - 120, "beta": 0.0}, "ntfy_counts": {"/srv/dev/alpha": 4},
            "stats": "Projects · 3 · 1 dirty · 1 behind · 2 with agents · 4 ntfy", "pins": {"beta"},
            "seg_counts": {"": 3, "dirty": 1, "agents": 2, "todo": 1, "pinned": 1}, "issue_counts": {"alpha": 5},
            "total_all": 3, "limit_shown": 3, "limit_total": 3, "csrf": "tok"}
    return {**base, **kw}


@pytest.fixture(autouse=True)
def _snap(monkeypatch):
    snap = services.Snapshot(ts=1.0, agents=tuple(AGENTS))
    monkeypatch.setattr(services, "published", lambda settings: snap)


def build(**kw):
    return projects.build(ctx(**kw), SETTINGS, now=NOW)


def rows(pg):
    return {r.name: r for r in pg.rows}


# ---------------------------------------------------------------- view model
def test_stats_line():
    assert projects.stats_of("Projects · 3 · 1 dirty · 1 behind · 4 ntfy", 3) == [
        "3 projects", "1 dirty", {"text": "1 behind", "cls": "warn"}, "4 ntfy"]
    assert projects.stats_of("", 1) == ["1 project"]


def test_segments_counts_and_current():
    pg = build()
    assert [(s["key"], s["label"], s["count"], s["cur"]) for s in pg.segs] == [
        ("all", "All", 3, True), ("dirty", "Dirty", 1, False), ("agents", "Has agents", 2, False),
        ("todo", "Has TODO", 1, False), ("pinned", "Pinned", 1, False)]
    assert pg.segs[1]["href"] == "/p?sort=newest&f=dirty" and pg.segs[0]["href"] == "/p?sort=newest"
    pg = build(flags=["todo"], self_url="/p?f=todo&sort=newest&n=all")
    assert [s["key"] for s in pg.segs if s["cur"]] == ["todo"]
    assert pg.segs[0]["href"] == "/p?sort=newest"  # a segment resets the Show limit and keeps the order


@pytest.mark.parametrize("key, order", [("newest", "active"), ("oldest", "-active"), ("name", "name"),
                                        ("-name", "-name"), ("agents", "-agents"), ("-agents", "agents"),
                                        ("todo", "todo"), ("issues", "-issues"), ("ntfy", "-ntfy"),
                                        ("dirty", "-git"), ("-dirty", "git")])
def test_header_order(key, order):
    cols = sorting.sort_cols("projects", key, "/p", {"q": "x"})
    hdr, got = projects.header(cols, key)
    assert got == order
    by = {c.get("key"): c for c in hdr if c.get("key")}
    assert set(by) == {"name", "agents", "todo", "issues", "ntfy", "active", "git"}
    assert all(c["href"].startswith("/p?q=x&sort=") for c in by.values())
    assert [c.get("label") or c.get("sr") for c in hdr] == ["Pinned", "Project", "Agents", "TODO", "Issues", "ntfy",
                                                          "Active", "Git", "Stack", "Actions"]
    assert [c for c in hdr if "hide-n" in c.get("cls", "")] == [hdr[3], hdr[4], hdr[5], hdr[6], hdr[8]]


def test_rows():
    r = rows(build())
    a, b, g = r["alpha"], r["beta"], r["gamma site"]
    assert [x.name for x in build().rows] == ["alpha", "beta", "gamma site"]  # the core's order is kept
    assert (a.state, a.word, a.agents) == ("waiting", "1 needs you", 2)
    assert (b.state, b.word, g.state) == ("idle", "idle", "")
    assert (a.todo_done, a.todo_total, a.issues, a.ntfy, a.active) == (1, 3, 5, 4, "2m")
    assert (a.branch, a.dirty, a.ahead, a.behind, a.stack, a.stack_ok) == ("main", 3, 1, 2, "1/2", False)
    assert a.meta == "main · 3 dirty · 2 agents · TODO 1/3" and g.meta == "no git"
    assert b.branch == "no branch" and b.pinned and not a.pinned and b.active == ""
    assert g.href == "/p/gamma%20site" and g.git_title == "Not a git repository"


def test_row_menu_pin_is_a_guarded_post():
    m = {i["id"]: i for i in rows(build())["alpha"].menu if "id" in i}
    assert m["pin"]["post"] == "/p/alpha/pin" and m["pin"]["vals"] == {"pinned": "1"}
    assert m["pin"]["refresh"] == "#pj" and m["pin"]["key"] == "P"
    assert m["open"]["href"] == "/p/alpha" and m["copy"]["copy"] == "/srv/dev/alpha"
    mb = {i["id"]: i for i in rows(build())["beta"].menu if "id" in i}
    assert mb["pin"]["label"] == "Unpin" and mb["pin"]["vals"] == {"pinned": "0"}
    mg = {i["id"]: i for i in rows(build())["gamma site"].menu if "id" in i}
    assert mg["pin"]["post"] == "/p/gamma%20site/pin"


def test_row_menu_launch_opens_the_dialog():
    """⋯ Launch agent… opens the Launch dialog (event pj:launch -> pm7.launch.open({project})) like the project head
    and the tmux ⋯; the classic /p/<n>/launch page is only the fallback when the dialog is not on the page."""
    m = {i["id"]: i for i in rows(build())["gamma site"].menu if "id" in i}
    assert m["launch"]["event"] == "pj:launch" and "href" not in m["launch"]
    js = (THEME / "static" / "js" / "projects.js").read_text()
    assert "d.addEventListener('pj:launch'" in js and "pm7.launch.open({ project: project })" in js
    assert "'/p/' + encodeURIComponent(project) + '/launch'" in js


def test_empty_and_foot():
    pg = build(projects=[], q="zz", self_url="/p?q=zz&sort=newest", limit_total=0)
    assert pg.empty == "No project matches “zz”." and pg.empty_url == "/p?sort=newest"
    pg = build(projects=[], flags=["dirty"], self_url="/p?f=dirty", limit_total=0)
    assert pg.empty == "No project in “Dirty”." and pg.empty_url == "/p"
    assert build(projects=[], limit_total=0).empty.startswith("No projects yet")
    pg = build(limit_total=12)
    assert pg.shown == 3 and pg.total == 12 and pg.more_url == "/p?sort=newest&n=all"
    assert build().more_url is None


def test_new_form():
    nf = projects.new_form(SimpleNamespace(**{**vars(SETTINGS), "project_templates": [
        {"name": "uv", "description": "Python", "files": {"a.py": "x"}}]}))
    assert nf.root == "/srv/dev" and nf.templates == [{"name": "uv", "description": "Python"}]
    assert "other" not in nf.kinds and "claude" in nf.kinds and "claude" in nf.modes
    assert nf.remote == "git@git.example:dt/<name>.git"


def test_bind_never_raises(monkeypatch):
    g = projects.bind(lambda: SETTINGS)
    monkeypatch.setattr(projects, "build", lambda *a, **k: 1 / 0)
    env = jinja2.Environment()
    env.globals["dt_projects"] = g
    assert env.from_string("{{ dt_projects() is none }}").render() == "True"


# ---------------------------------------------------------------- templates
@pytest.fixture
def env():
    e = jinja2.Environment(loader=jinja2.FileSystemLoader(THEME / "templates"), autoescape=True,
                           undefined=jinja2.StrictUndefined)

    @jinja2.pass_context
    def fixed(c, part="list"):
        return projects.new_form(SETTINGS) if part == "new" else projects.build(c, SETTINGS, now=NOW)

    e.globals["dt_projects"] = fixed
    return e


def test_list_fragment(env):
    html = env.get_template("_projects_list.html").render(**ctx())
    assert html.startswith('<div id="pj" class="pj-page" hx-get="/p?sort=newest"')
    trig = re.search(r'hx-trigger="([^"]+)"', html).group(1)
    assert "pm:agent_state from:document" in trig and "pm:refresh from:document" in trig
    assert "pm7:resync from:document" in trig and "pm7:refresh" in trig and "every " not in trig
    assert 'class="p-g pj-list"' in html and 'data-list="projects"' in html and 'data-key-p="menu:pin"' in html
    assert ' style=' not in html and "<script" not in html and " onclick" not in html
    assert html.count('data-pj-pin') == 3 and 'aria-pressed="true" aria-label="Unpin beta"' in html
    assert 'data-pj-new' in html and 'x-btn p-act pri' in html  # the one green
    assert 'hx-get="/p" hx-trigger="input changed delay:200ms, search" hx-target="#pj"' in html
    assert html.count('hx-target="#pj"') >= 1 + 5 + 7  # filter, segments, sort links
    assert 'aria-sort="ascending"' in html and ">Active<" in html
    assert '<span class="warn">3 dirty</span>' in html and "↓2" in html and "stack 1/2" in html
    for raw in re.findall(r"data-menu='([^']*)'", html):
        assert isinstance(json.loads(raw.replace("&#39;", "'")), list)


def test_row_names_are_row_links(env):  # #247: ⌘ / Ctrl / Shift / middle click open the project in a new tab
    html = env.get_template("_projects_list.html").render(**ctx())
    rows = re.findall(r'<div class="p-gr pj-row[^>]*data-href="([^"]+)"[^>]*>\n(.*?)\n</div>', html, re.S)
    assert len(rows) == 3
    for href, body in rows:
        assert f'<a class="nm" href="{href}" tabindex="-1" data-row-link>' in body


def test_rows_have_only_cells(env):
    html = env.get_template("_projects_list.html").render(**ctx())
    body = re.findall(r'<div class="p-gr pj-row[^>]*>\n(.*?)\n</div>', html, re.S)
    assert len(body) == 3
    for row in body:
        tops = re.findall(r"^\s*(<\w+[^>]*>)", row, re.M)  # 10 tracks + agents dot (phone) + phone line 2
        assert len(tops) == 12 and all('role="cell"' in t for t in tops), tops


def test_page_and_dialog(env):
    src = (THEME / "templates" / "projects.html").read_text()
    assert "{% block mainclass %}{% endblock %}" in src
    for a in ("css/projects.css", "js/projects.js", "js/newproject.js", '"projects/_new.html"'):
        assert a in src
    html = env.get_template("projects/_new.html").render(csrf="tok")
    assert '<dialog id="pj-new" class="x-dlg pj-ndlg"' in html and ' style=' not in html and "<script" not in html
    names = set(re.findall(r'name="([a-z_]+)"', html))
    assert names - {"csrf"} <= newproject.CORE_FIELDS  # every field is one the core's parse_spec reads
    assert {"name", "template", "readme", "git", "remote", "commit", "agent", "model", "effort", "permission_mode",
            "prompt", "confirm_bypass"} <= names
    assert 'data-pj-root="/srv/dev"' in html and "git@git.example:dt/&lt;name&gt;.git" in html
    modes = json.loads(re.search(r"data-pj-modes='([^']*)'", html).group(1).replace("&#39;", "'"))
    assert "claude" in modes and "modes" in modes["claude"]
    for hook in ("data-pj-plan", "data-pj-err", "data-pj-steps", "data-pj-sum", "data-pj-create", "data-pj-open"):
        assert hook in html


def test_gitdev_remote_box_off_when_not_configured(env):
    def box(settings):
        env.globals["dt_projects"] = lambda part="list": projects.new_form(settings)
        html = env.get_template("projects/_new.html").render(csrf="tok")
        return re.search(r'<label class="p-chk[^"]*"[^>]*>\s*<input type="checkbox" name="remote"[^>]*>', html).group(0)
    on = box(SETTINGS)
    assert " checked" in on and "disabled" not in on and "data-pj-never" not in on
    off = box(SimpleNamespace(**{**vars(SETTINGS), "git_remote_template": ""}))
    assert " checked" not in off and " disabled" in off and "data-pj-never" in off and "p-chk off" in off
    js = (THEME / "static" / "js" / "newproject.js").read_text()
    assert "data-pj-never" in js and "function pathCode" in js and "pm7.el('wbr')" in js


def test_js_rules():
    for name in ("projects.js", "newproject.js"):
        js = (THEME / "static" / "js" / name).read_text()
        assert "keydown" not in js and "fetch(" not in js.replace("pm7.fetch(", "")
        assert "setInterval" not in js and "MutationObserver" not in js and "alert(" not in js
        assert all(len(line) <= 120 for line in js.splitlines())
    js = (THEME / "static" / "js" / "newproject.js").read_text()
    for need in ("/p/new/plan?", "'/p/new/run'", "getReader()", "pm7.dialog(", "pm7.debounce(", "x.end",
                 "pm7.toast('Created '"):
        assert need in js, need
    assert "pm7.menus.run(m, 'pin')" in (THEME / "static" / "js" / "projects.js").read_text()


def test_new_project_reads_the_launch_forms_data_attribute():
    """newproject.js takes its model list from the Launch form (GET /launch/form): the attribute it reads must be the
    one _launch_form.html renders, and it must carry `models` (a rename on either side fails here)."""
    import html as htmlmod

    js = (THEME / "static" / "js" / "newproject.js").read_text()
    sel = re.search(r"doc\.querySelector\('\[(data-l-[a-z-]+)\]'\)", js).group(1)
    assert f"getAttribute('{sel}')" in js
    form = re.search(r"<form [^>]*data-l-form[^>]*>", render_launch_form()).group(0)
    raw = re.search(sel + r'="([^"]*)"', form)
    assert raw, f"_launch_form.html renders no {sel} attribute on the form"
    data = json.loads(htmlmod.unescape(raw.group(1)))
    assert data["models"]["claude"][0]["name"] == "opus"
    assert f"getAttribute('{sel}')" in (THEME / "static" / "js" / "launch.js").read_text()
