"""Stats headings, Show-N limit bar/cookies, collapsed tmux rows, ntfy placement."""

import re
from types import SimpleNamespace as NS

from helpers import login, pane_line
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.agents import sorting
from pm.core.models import Agent, AgentKind, AgentState, GitStatus, Project
from pm.infra import snapshot as services
from pm.web import render, stats
from pm.web.render import TEMPLATE_DIR


def _a(i, state, ts=1.0):
    return Agent(id=f"claude-{i}", kind=AgentKind.CLAUDE, pane_id=f"%{i}", session_id="$1", pid=i,
                 cwd="/x", state=state, state_since=ts, last_active=ts)


def _many(monkeypatch, n=25):
    st = [AgentState.RUNNING, AgentState.IDLE, AgentState.WAITING]
    ags = [_a(i, st[i % 3], float(i)) for i in range(1, n + 1)]
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes, **kw: ags)
    return ags


# --- stats -------------------------------------------------------------
def test_stats_inbox_hides_zero():
    snap = NS(agents=[_a(1, AgentState.RUNNING), _a(2, AgentState.IDLE), _a(3, AgentState.WAITING)])
    assert stats.stats_inbox(snap, {"/a": 2, "/b": 3}) == (
        "Inbox · 3 agents · 1 running · 1 idle · 1 waiting · 5 ntfy")
    assert stats.stats_inbox(NS(agents=[]), None) == "Inbox · 0 agents"
    assert stats.stats_inbox(NS(agents=[_a(1, AgentState.RUNNING)])) == "Inbox · 1 agent · 1 running"


def test_stats_projects():
    ps = [Project("a", "/a", git=GitStatus("m", dirty=2, behind=1), agent_ids=("x",)),
          Project("b", "/b", git=GitStatus("m")), Project("c", "/c")]
    assert stats.stats_projects(NS(projects=ps), {"/a": 4}) == (
        "Projects · 3 · 1 dirty · 1 behind · 1 with agents · 4 ntfy")
    assert stats.stats_projects(NS(projects=[])) == "Projects · 0"


def test_stats_ntfy_hides_zero():
    assert stats.stats_ntfy(12, 3, 2) == "ntfy · 12 · 3 unread · 2 today"
    assert stats.stats_ntfy(5) == "ntfy · 5"
    assert stats.stats_ntfy(0, 0, 0) == "ntfy · 0"
    assert stats.stats_ntfy(4, 0, 1) == "ntfy · 4 · 1 today"


def test_stats_tmux():
    from pm.core.models import TmuxPane, TmuxSession, TmuxWindow
    p = TmuxPane("%1", "@1", "$1", 1, "sh", "/")
    ss = [TmuxSession("$1", "a", attached=2, windows=(TmuxWindow("@1", "$1", 0, "w", panes=(p, p)),)),
          TmuxSession("$2", "b")]
    snap = NS(sessions=ss, agents=[_a(1, AgentState.IDLE)])
    assert stats.stats_tmux(snap) == "tmux · 2 sessions · 1 window · 2 panes · 2 clients · 1 agents"


# --- limit logic -------------------------------------------------------
def test_choose_limit():
    c = sorting.choose_limit
    assert c("inbox", None, None) == ("10", False)
    assert c("inbox", "20", None) == ("20", True)
    assert c("inbox", "all", "50") == ("all", True)  # explicit wins
    assert c("inbox", "7", "50") == ("10", False)  # invalid -> default, not persisted
    assert c("inbox", None, "50") == ("50", False)
    assert c("inbox", None, "bogus") == ("10", False)
    assert c("inbox", None, None, 20) == ("20", False)
    assert sorting.apply_limit(range(30), "all") == list(range(30))
    assert sorting.apply_limit(range(30), "20") == list(range(20))


def test_limit_links_keep_query():
    ls = sorting.limit_links("20", "/", {"sort": "cpu", "n": "10"})
    assert [x["key"] for x in ls] == ["10", "20", "50", "all"]
    assert ls[1]["active"] and not ls[0]["active"]
    assert "sort=cpu" in ls[3]["href"] and "n=all" in ls[3]["href"]


# --- pages -------------------------------------------------------------
def test_inbox_limit_per_section_after_sort(env, monkeypatch):
    s, c = env
    login(s, c)
    _many(monkeypatch, 40)  # 13 waiting, 13 running, 14 idle
    html = c.get("/").text
    assert "showing 30 of 40" in html and "Idle (10 of 14)" in html  # default 10 per bucket; v4: one merged table, no Running header
    r = c.get("/?n=10&sort=newest")
    assert "pm_limit_inbox=10" in r.headers["set-cookie"] and "Secure" in r.headers["set-cookie"]
    assert "Idle (10 of 14)" in r.text and '<option value="all">All</option>' in r.text
    top = r.text.split('<div class="nlist" role="list" data-key="l-active">')[1].split('<section class="inbox-sec sec-idle"')[0]
    ids = re.findall(r'class="nrow row-running[^"]*" href="/a/(claude-\d+)"', top)
    assert ids == [f"claude-{i}" for i in range(39, 9, -3)]
    hx = re.search(r'id="inbox" hx-get="([^"]+)"', r.text).group(1).replace("&amp;", "&")
    assert "n=10" in hx and "sort=newest" in hx
    r = c.get("/?n=all")
    assert r.text.count('nrow row-running') == 13 and "showing 40 of 40" in r.text and '<option value="all" selected>All</option>' in r.text


def test_limit_cookie_remembered_and_invalid(env, monkeypatch):
    s, c = env
    login(s, c)
    _many(monkeypatch, 25)
    assert "set-cookie" not in c.get("/?n=bogus").headers or "pm_limit" not in c.get("/?n=bogus").headers["set-cookie"]
    c.cookies.set("pm_limit_inbox", "20")
    assert "showing 20 of 25" not in c.get("/").text  # per section: 8/9/8 all fit in 20
    c.cookies.set("pm_limit_inbox", "all")
    r = c.get("/?n=bogus")
    assert r.text.count('nrow row-running') == 8 and "Idle (9)" in r.text
    # per-page cookies are independent
    assert "pm_limit_projects" not in c.get("/p").headers.get("set-cookie", "")
    assert "pm_limit_projects=50" in c.get("/p?n=50").headers["set-cookie"]
    assert "pm_limit_tmux=all" in c.get("/tmux?n=all").headers["set-cookie"]


def test_headings_have_stats_title_short(env):
    s, c = env
    login(s, c)
    for path, _head, title in (("/", "Inbox · 1 agent", "Inbox"), ("/p", "Projects · ", "Projects"),
                              ("/tmux", "tmux · 1 session", "tmux")):
        html = c.get(path).text
        if True:  # v4: plain title + muted stats line
            assert f"<h1>{title}</h1>" in html and 'class="inbox-stats muted"' in html, path
            assert not re.search(r'inbox-stats muted">(Inbox|Projects|tmux) ', html), path
        assert f"<title>{title} - pm</title>" in html


def test_header_title_left_controls_right(env):
    s, c = env
    login(s, c)
    for path, ctl in (("/p", 'class="filterbar pctl"'), ("/tmux", 'id="tmux-filter"')):
        html = c.get(path).text
        assert '<div class="phead inbox-head">' in html and html.index("<h1>") < html.index(ctl), path
        assert 'name="n" data-nav' in html and "sortchip" not in html and "sortlabel" not in html, path
        assert html.count('name="n" data-nav') == 2 and 'class="showbar"' in html, path  # top select + bottom form
        assert html.index(ctl) < html.index('class="hrow'), path
        assert html.rindex('name="n" data-nav') > html.index('class="hrow'), path  # bottom Show below the table


def test_inbox_layout_order(env):
    s, c = env
    login(s, c)
    html = c.get("/").text
    i = html.index
    assert i("<h1>") < i("launch-btn") < i('id="inbox-filter"') < i('class="nhead has-pin"')
    assert i("stop-btn") < i('id="inbox-filter"')
    assert html.index('name="n" data-nav') < i('class="nhead has-pin"') < html.rindex('name="n" data-nav')
    f = html[i('id="inbox-filter"'):i("</form>", i('id="inbox-filter"'))]
    assert "launch-btn" not in f and "stop-btn" not in f and 'name="n" data-nav' in f


def test_inbox_has_no_ntfy_panel(env):
    s, c = env
    login(s, c)
    html = c.get("/").text
    assert 'id="ntfy-inbox"' not in html and "sortchip" not in html


def test_tmux_collapsed_by_default_and_form_first(env, monkeypatch):
    s, c = env
    login(s, c)
    monkeypatch.setenv("FAKE_TMUX_OUT", pane_line("$1", "work", "@1", "%12", "/tmp") + pane_line("$2", "two", "@2", "%13", "/tmp"))
    html = c.get("/tmux").text
    assert html.count('<details class="sessrow">') == 2 and 'class="sessrow" open' not in html
    assert html.index('action="/tmux/session/new"') < html.index("sessrow")
    assert "data-details-toggle=\"open\"" in html and "/static/js/lists.js" in html
    assert "showing 2 of 2" in html and 'class="hrow srowhead"' in html and "sort=-name" in html
    assert "<script>" not in html and " onclick=" not in html and "style=" not in html


def test_projects_limit_and_showing(env):
    s, c = env
    login(s, c)
    r = c.get("/p?n=20")
    assert "showing 1 of 1" in r.text
    assert '<option value="20" selected>20</option>' in r.text
    t = c.get("/p?sort=name").text
    assert '<option value="all">All</option>' in t and 'name="sort" value="name"' in t


def test_lists_js_served_no_inline(env):
    s, c = env
    r = c.get("/static/js/lists.js")
    assert r.status_code == 200 and "details.sessrow" in r.text


def test_tmux_unprotected_badge_not_protected_text(env, monkeypatch):
    s, c = env
    login(s, c)
    monkeypatch.setenv("FAKE_TMUX_OUT", pane_line("$1", "work", "@1", "%12", "/tmp"))
    html = c.get("/tmux").text
    assert '<span class="badge">protected</span>' not in html
    assert ("unprotected" in html) or 'title="protected"' in html


def test_agent_row_never_shows_program_and_pills_the_name():
    import dataclasses
    from pathlib import Path

    import jinja2

    import pm
    from pm.agents.registry import Record
    root = TEMPLATE_DIR
    env_ = jinja2.Environment(loader=jinja2.FileSystemLoader(root))
    env_.filters["ago"] = lambda v: "x"
    env_.filters["dt"] = lambda v: "t"
    env_.filters["ts"] = lambda v: "t"
    env_.filters["agent_q"] = render.agent_q
    env_.globals["last_src"] = render.last_src
    tpl = env_.from_string('{% from "_agentrow.html" import ended_list, INBOX_COLS, PROJECT_COLS %}'
                           '{{ ended_list([r], 1, "", {}, PROJECT_COLS if g else INBOX_COLS) }}')
    r = Record("claude-1", "claude", None, 1, 1.0, 2.0, "%1", "proj", "/x", "claude", "", 3.0)
    for g in (True, False):  # project-page grid row and inbox row
        assert 'class="kind"' not in tpl.render(r=r, g=g)
        out = tpl.render(r=dataclasses.replace(r, label="build"), g=g)
        assert 'class="kind"' not in out
        assert 'class="aname"' in out and ">build</strong>" not in out and 'class="kpill kp-claude"' in out  # id, never the label
    css = (render.STATIC_DIR / "css/lists.css").read_text()
    assert ".kpill" in css and "border-radius:var(--r-pill)" in css and "1px solid var(--pill)" in css
    assert "#" not in css.split(".kpill{")[1].split("}")[0]
    t = (root / "_tmux_session_rows.html").read_text()
    assert 'class="kind"' not in t and "agent_name(a)" in t and "kpill" not in t


def test_aname_strips_program_suffix():
    from pathlib import Path

    import jinja2

    import pm
    from pm.web.render import env as _env
    tpl = _env.from_string('{% from "_macros.html" import aname %}{{ aname(l, k) | trim }}')
    for label, kind, want in [("dev-pm-7-claude", "claude", "dev-pm-7"), ("devbox-2-devin", "devin", "devbox-2"),
                              ("dev-pm-7-claude-2", "claude", "dev-pm-7-2"),
                              ("my-claude-3-claude", "claude", "my-claude-3"), ("claude-7f3a9c", "claude", "claude-7f3a9c"),
                              ("build", "claude", "build")]:
        assert tpl.render(l=label, k=kind) == want


def test_inbox_ended_history_rows_use_pill_without_trailing_text():
    from pathlib import Path

    import pm
    root = Path(pm.__file__).parent
    t = TEMPLATE_DIR
    inbox = (t / "_inbox.html").read_text()
    row = (t / "_agentrow.html").read_text()  # the shared agent row (Inbox and project page)
    assert "agent_row(" in inbox and "kpill" not in inbox
    assert "agent_name(a, rx=True)" in row and "tslot(" in row and "agent_name(r, model=model, rx=True)" in row
    an = (t / "_agentname.html").read_text()
    assert 'class="aname"' in an and 'kpill kp-model' in an and "pill_model" in an
    h = (t / "_history.html").read_text()
    assert "hist-k" not in h and "agent_name(h)" in h and "apill" not in h
    assert ".kind" not in (root / "static/js/drawer.js").read_text()
    assert ".nrow .n-main strong.aname" in (root / "static/css/lists.css").read_text()


def _spy(monkeypatch, module):
    """Capture the template context a page route hands to ``html`` (theme-only keys classic does not render)."""
    seen = {}
    real = module.html

    def spy(request, name, **ctx):
        seen.update(ctx)
        return real(request, name, **ctx)
    monkeypatch.setattr(module, "html", spy)
    return seen


def test_projects_segment_and_issue_counts(env, monkeypatch, tmp_path):
    from pm.projects.tracker import mdimport
    from pm.web.routes import pages
    s, c = env
    login(s, c)
    alpha = tmp_path / "dev" / "alpha"
    (alpha / "BACKLOG.md").write_text("## Inbox\n\n- [ ] one\n- [ ] two\n- [x] three\n")
    mdimport.import_project(s, "alpha", alpha, files=("backlog",))
    seen = _spy(monkeypatch, pages)
    base = c.get("/p").text
    assert seen["seg_counts"] == {"": 1, "dirty": 0, "agents": 1, "todo": 1, "pinned": 0}
    assert seen["issue_counts"] == {"alpha": 2}
    assert c.get("/p?q=nomatch").status_code == 200 and seen["seg_counts"][""] == 0
    r = c.get("/p?sort=issues")
    assert r.status_code == 200 and "pm_sort_projects=issues" in r.headers["set-cookie"]
    assert "sort=-issues" not in base  # classic has no Issues column


def test_tmux_segments(env, monkeypatch):
    from pm.web.routes import tmuxpages
    s, c = env
    login(s, c)
    monkeypatch.setenv("FAKE_TMUX_OUT", pane_line("$1", "work", "@1", "%12", "/tmp") + pane_line("$2", "two", "@2", "%13", "/tmp"))
    seen = _spy(monkeypatch, tmuxpages)
    html = c.get("/tmux").text
    assert seen["tmux_filter"] == "" and seen["tmux_counts"][""] == 2 and seen["tmux_counts"]["agents"] == 1
    assert [o[0] for o in seen["tmux_filter_opts"]] == ["", "agents", "attached", "protected"]
    assert "f=agents" not in html
    html = c.get("/tmux?f=agents").text
    assert [x.id for x in seen["sessions"]] == ["$1"] and seen["tmux_filter"] == "agents"
    assert seen["tmux_counts"][""] == 2 and "showing 1 of 1" in html  # counts are per segment, not filtered
    c.get("/tmux?f=bogus")
    assert seen["tmux_filter"] == "" and len(seen["sessions"]) == 2
