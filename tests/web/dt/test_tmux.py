"""dt tmux page (pm.web.dt.tmux, templates/tmux.html + _tmux_list.html + _tmux_session_rows.html, js/tmux.js):
the view model (worst state, lock, segments, sort header, session / window menus with the core's typed names), the
templates (parts contract, live refresh without a timed poll) and the core routes rendering the dt templates
(GET /tmux as a fragment, GET /tmux/{sid}/live)."""

from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

from pm.core.models import Agent, AgentKind, AgentState, Project, TmuxPane, TmuxSession, TmuxWindow
from pm.infra import snapshot as services
from pm.web import themes
from pm.web.dt import DtTheme, tmux

from .conftest import make_env
from .kit import W1, W2, WORK, one, pane

THEME = themes.HERE / "dt"
TPL = THEME / "templates"
NOW = 1_000_000.0
SETTINGS = SimpleNamespace(tmux_socket=None, terminal_enabled=True)


def ag(id_, state, pane, sid):
    return Agent(id=id_, kind=AgentKind(id_.rsplit("-", 1)[0]), pane_id=pane, session_id=sid, pid=1, cwd="/dev/alpha",
                 project="alpha", state=state, state_since=NOW - 30)




PROT, PMS = one("$4", "keeper", 4), one("$5", "pm", 5)
RUN, WAIT = ag("claude-aaaaa1", AgentState.RUNNING, "%1", "$1"), ag("codex-bbbbb2", AgentState.WAITING, "%2", "$1")


def rows_of(s, agents=(RUN, WAIT), project="alpha"):
    by = {a.pane_id: a for a in agents}
    return [{"window": w, "pane": p, "agent": by.get(p.id), "project": project if p.cwd.startswith("/dev") else None,
             "current": w.active, "first": i == 0} for w in s.windows for i, p in enumerate(w.panes)]


def ctx(**kw):
    sessions = [WORK, PROT, PMS]
    info = {"$1": {"windows": 2, "panes": 3, "agents": [RUN, WAIT], "last": NOW - 60},
            "$4": {"windows": 1, "panes": 1, "agents": [], "last": None},
            "$5": {"windows": 1, "panes": 1, "agents": [], "last": None}}
    cols = {k: {"label": k, "href": f"/tmux?sort={k}x", "active": False, "arrow": ""}
            for k in ("main", "age", "agents", "act")}
    base = {"sessions": sessions, "sess_info": info, "sess_rows": {s.id: rows_of(s) for s in sessions},
            "own_sid": "$5", "open_sid": "", "cols": cols, "self_url": "/tmux?sort=name", "q": "",
            "stats": "tmux · 3 sessions · 4 windows · 5 panes · 2 clients · 2 agents", "tmux_filter": "",
            "tmux_counts": {"": 3, "agents": 1, "attached": 1, "protected": 2}, "limit_shown": 3, "limit_total": 3,
            "errors": [], "terminal_enabled": True, "csrf": "t"}
    return {**base, **kw}


@pytest.fixture(autouse=True)
def _snap(monkeypatch):
    snap = services.Snapshot(ts=1.0, projects=(Project("beta", "/dev/beta"), Project("alpha", "/dev/alpha")))
    monkeypatch.setattr(services, "published", lambda settings: snap)


def build(**kw):
    return tmux.build(ctx(**kw), SETTINGS, now=NOW)


def by_id(page, sid):
    return next(r for r in page.rows if r.id == sid)


def item(menu, id_):
    return next(m for m in menu if m.get("id") == id_)


# ---------------------------------------------------------------- view model
def test_session_rows_worst_state_lock_and_counts():
    p = build()
    w, k, own = by_id(p, "$1"), by_id(p, "$4"), by_id(p, "$5")
    assert (w.state, w.lock, w.windows, w.clients, [a.id for a in w.agents], w.more) == (
        "waiting", "", 2, 2, ["codex-bbbbb2"], "+1")  # waiting is worse than running: shown first
    assert w.more_title == "claude-aaaaa1" and w.age == "1m" and w.since == int(NOW - 60)
    assert (k.state, k.lock, k.agents, k.age) == ("", "protected", [], "–")
    assert own.lock == "pm"
    assert w.meta == "2 windows · 2 attached · codex-bbbbb2 +1"
    assert p.stats == ["3 sessions", "4 windows", "5 panes", "2 clients", "2 agents"]
    assert p.folders == ["/dev/alpha", "/dev/beta"]


def test_segments_use_the_core_filter_and_counts():
    p = build(self_url="/tmux?sort=name&q=lo&f=agents&open=s1", tmux_filter="agents")
    assert [(s["label"], s["count"], s["cur"]) for s in p.segs] == [
        ("All", 3, False), ("With agents", 1, True), ("Attached", 1, False), ("Protected", 2, False)]
    assert p.segs[0]["href"] == "/tmux?sort=name&q=lo" and p.segs[3]["href"] == "/tmux?sort=name&q=lo&f=protected"


def test_header_uses_the_core_sort_links_and_order():
    cols, order = tmux.header(ctx()["cols"], "name")
    assert [c.get("label") or c.get("sr") for c in cols] == [
        "State", "Session", "Windows", "Clients", "Agents", "Active", "Actions"]
    assert cols[1]["href"] == "/tmux?sort=mainx" and cols[5]["href"] == "/tmux?sort=actx"
    assert "href" not in cols[3] or cols[3].get("href") is None  # the core cannot sort by clients
    assert order == "name"
    assert tmux.header({}, "-counts")[1] == "windows" and tmux.header({}, "counts")[1] == "-windows"
    assert tmux.header({}, "newest")[1] == "active" and tmux.header({}, "oldest")[1] == "-active"


def test_session_menu():
    w = by_id(build(), "$1")
    ids = [m.get("id") for m in w.menu if not m.get("sep")]
    assert ids == ["term", "det", "neww", "launch", "copy", "kill"]
    assert item(w.menu, "term")["event"] == "tm:term" and w.term == "/term/s1"
    assert item(w.menu, "neww")["post"] == "/tmux/window/new" and item(w.menu, "neww")["vals"] == {"session": "$1"}
    assert item(w.menu, "launch")["event"] == "tm:launch" and w.project == "alpha" and w.launch == "/p/alpha/launch"
    assert item(w.menu, "copy")["copy"] == "tmux attach -t work"
    k = item(w.menu, "kill")
    assert k["post"] == "/tmux/session/s1/kill" and k["typed"] == "work" and k["danger"]
    assert k["vals"] == {"confirm": "work", "force_attached": "1"}  # 2 clients attached: the question says so
    assert "2 attached clients lose it" in k["body"]


def test_kill_session_disabled_with_the_reason():
    p = build()
    assert item(by_id(p, "$4").menu, "kill") == {"id": "kill", "label": "Kill session…", "icon": "trash",
                                                 "danger": True, "disabled": True, "why": "protected session"}
    own = by_id(p, "$5")
    assert item(own.menu, "kill")["why"] == "pm's own session" and item(own.menu, "neww")["disabled"]
    assert item(own.menu, "term")["disabled"] and "pm" in item(own.menu, "term")["why"]  # no terminal for pm*


def test_no_terminal_when_off_and_attach_with_socket():
    p = tmux.build(ctx(terminal_enabled=False), SimpleNamespace(tmux_socket="dev-1"), now=NOW)
    w = by_id(p, "$1")
    assert w.term == "" and item(w.menu, "term")["why"] == "the terminal is off (pm.toml)"
    assert item(w.menu, "copy")["copy"] == "tmux -L dev-1 attach -t work"


def test_open_session_renders_its_windows():
    p = build(open_sid="$1", self_url="/tmux?sort=name&open=s1")
    w = by_id(p, "$1")
    assert w.open and w.wins is not None and [r.id for r in w.wins.rows] == ["%1", "%2", "%3"]
    assert by_id(p, "$4").wins is None and p.segs[0]["href"] == "/tmux?sort=name"


def test_windows_rows_and_menus():
    ws = tmux.windows(WORK, rows_of(WORK), True, SETTINGS)
    r1, r2, r3 = ws.rows
    assert (r1.index, r1.name, r1.first, r1.current, r1.agent.id, r1.term) == (0, "agent", True, True, RUN.id,
                                                                               "/term/s1?w=w1")
    assert (r3.first, r3.path, r3.agent) == (False, "/tmp", None)
    kw, kp = item(r2.menu, "killw"), item(r2.menu, "killp")
    assert (kw["post"], kw["typed"], kw["vals"]) == ("/tmux/window/w2/kill", "logs",
                                                     {"confirm": "logs", "force_attached": "1"})
    assert (kp["post"], kp["typed"], kp["vals"]["confirm"]) == ("/tmux/pane/p2/kill", "%2", "%2")
    assert ws.lock == "" and "can be killed" in ws.note and ws.attach == "tmux attach -t work"
    prot = tmux.windows(PROT, rows_of(PROT), True)
    assert item(prot.rows[0].menu, "killw")["disabled"] and item(prot.rows[0].menu, "killp")["why"] == \
        "protected session"
    assert prot.lock == "protected"


def test_empty_lines():
    assert build(sessions=[], q="zz").empty.startswith("No session matches")
    p = build(sessions=[], tmux_filter="attached", self_url="/tmux?sort=name&f=attached")
    assert p.empty == "No attached sessions." and p.empty_url == "/tmux?sort=name"
    assert build(sessions=[]).empty.startswith("No tmux sessions")


def test_show_all_link():
    p = build(limit_shown=3, limit_total=9, self_url="/tmux?sort=name&n=10")
    assert (p.shown, p.total, p.more_url) == (3, 9, "/tmux?sort=name&n=all")


def test_bind_never_raises():
    g = tmux.bind(lambda: SETTINGS)
    env = jinja2.Environment()
    env.globals["dt_tmux"] = g
    assert env.from_string("{{ dt_tmux() is none }}").render(sessions=[object()]) == "True"
    assert env.from_string("{{ dt_tmux('windows') is none }}").render() == "True"


# ---------------------------------------------------------------- templates
@pytest.fixture
def env():
    e = jinja2.Environment(loader=jinja2.FileSystemLoader(TPL), autoescape=True, undefined=jinja2.StrictUndefined)
    e.globals["dt_tmux"] = tmux.bind(lambda: SETTINGS)
    return e


def _menu(html, rid):
    i = html.index(f'data-row-id="{rid}"')
    return json.loads(unescape(re.search(r"data-menu='([^']*)'", html[i:]).group(1)))


def test_list_fragment_structure(env):
    html = env.get_template("_tmux_list.html").render(**ctx(open_sid="$1"))
    assert html.lstrip().startswith('<div id="tm-list"') and 'hx-swap="outerHTML"' in html
    trig = re.search(r'hx-trigger="([^"]*)"', html).group(1)
    assert "pm:agent_state from:document" in trig and "pm7:refresh" in trig and "every" not in trig
    assert 'hx-get="/tmux?sort=name"' in html
    assert 'data-list="tmux"' in html and 'data-key-enter="det"' in html and 'data-key-o="menu:term"' in html
    assert 'data-p-filter="tmux"' in html and 'hx-target="#tm-list"' in html
    assert re.search(r'data-row-id="\$1"[^>]*data-tm-term="/term/s1"', html)
    assert 'data-p-det-src="/tmux/s1/live"' in html and 'data-list="tmux-win"' in html  # $1 open: windows in place
    assert html.count('class="tm-lock"') == 2 and '<span class="chip">pm</span>' in html
    assert [m.get("id") for m in _menu(html, "$4") if not m.get("sep")][-1] == "kill"
    assert "data-tm-new" in html and 'class="tm-errs"' not in html


def test_rows_have_only_cells(env):
    html = env.get_template("_tmux_list.html").render(**ctx(open_sid="$1"))
    for m in re.finditer(r'<div class="p-gr[^"]*" role="row"[^>]*>(.*?)\n</div>', html, re.S):
        kids = re.findall(r"^\s*<(\w+)([^>]*)>", m.group(1), re.M)
        assert kids and all('role="cell"' in a for _, a in kids), m.group(0)[:200]


def test_windows_fragment(env):
    html = env.get_template("_tmux_session_rows.html").render(session=WORK, rows=rows_of(WORK), sid_url="s1",
                                                              terminal_enabled=True)
    assert 'class="p-g tm-wins"' in html and html.count('role="row" data-row-id="%') == 3
    assert 'href="/term/s1?w=w2" data-tm-term-link' in html and 'data-tm-agent="claude-aaaaa1"' in html
    assert "pane of window 1" in html and "tmux attach -t work" in html
    assert _menu(html, "%2")[2]["typed"] == "logs"


def test_page_has_the_new_session_dialog(env):
    src = (TPL / "tmux.html").read_text(encoding="utf-8")
    assert "{% block mainclass %}{% endblock %}" in src and "css/tmux.css" in src and "js/tmux.js" in src
    assert 'action="/tmux/session/new"' in src and 'name="cwd" list="tm-folders"' in src and "novalidate" in src


def test_tmux_js_rules():
    js = (THEME / "static" / "js" / "tmux.js").read_text(encoding="utf-8")
    assert "pm7.tabs.open('term'" in js and "pm7.launch.open({ project" in js and "pm7.aside.open(" in js
    assert "pm7.fetch(" in js and not re.search(r"(?<!pm7\.)\bfetch\(", js)  # core.js owns fetch and the CSRF token
    assert "setInterval" not in js and "keydown" not in js  # no timed poll, no key listener of its own
    css = (THEME / "static" / "css" / "tmux.css").read_text(encoding="utf-8")
    assert not re.search(r"\.t-", css)  # tm- prefix, never t- (DESIGN §2)


# ---------------------------------------------------------------- the core routes with the real dt templates
@pytest.fixture
def tenv(settings, snap, monkeypatch):
    class RealDt(DtTheme):
        def themes(self):
            return [themes.Theme(name="dt", templates=TPL, static=THEME / "static", label="dt")]

    s = services.Snapshot(ts=1.0, agents=(RUN, WAIT), sessions=(WORK, PROT),
                          projects=(Project("alpha", str(settings.dev_root / "alpha")),))
    snap.value = s

    async def fresh(_settings, max_age_s=0):
        return s

    monkeypatch.setattr(services, "snapshot_async", fresh)
    return make_env(settings, RealDt())


def test_core_routes_render_the_dt_templates(tenv):
    r = tenv.c.get("/tmux?f=agents", headers={"HX-Request": "true"})
    assert r.status_code == 200 and r.text.lstrip().startswith('<div id="tm-list"')
    assert 'data-row-id="$1"' in r.text and 'data-row-id="$4"' not in r.text  # f=agents: keeper has none
    r = tenv.c.get("/tmux/s1/live")
    assert r.status_code == 200 and 'class="p-g tm-wins"' in r.text and "logs" in r.text
    page = tenv.c.get("/tmux")
    assert page.status_code == 200 and 'id="tm-new"' in page.text and 'id="tm-list"' in page.text
