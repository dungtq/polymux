"""dt Inbox (pm.web.dt.inbox, templates/inbox.html + _inbox.html + parts/_agentrow.html, js/inbox.js): the view model
re-buckets the core's sections, counts the views, filters (view, unread), groups by project, builds sort links and
row menus; the fragment renders with the parts' contract (cells, poll URL first, recap box kept across morphs).
Plain Jinja on the real dt templates (no app, no IO)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

from pm.core.models import Agent, AgentKind, AgentState, TmuxPane, TmuxSession, TmuxWindow
from pm.infra import snapshot as services
from pm.web import themes
from pm.web.dt import inbox

THEME = themes.HERE / "dt"
NOW = 1_000_000.0


def ag(id_, state=AgentState.RUNNING, project="alpha", since=NOW - 90, rss=10.0, pane="%1", sid="$1", cpu=0.0):
    return Agent(id=id_, kind=AgentKind(id_.rsplit("-", 1)[0]), pane_id=pane, session_id=sid, pid=1, cwd="/x",
                 project=project, state=state, state_since=since, rss_mb=rss, cpu_pct=cpu, label=f"dev-{project}-1")


def meta(a, **kw):
    return {"key": f"{a.id}:{a.state.value}", "q": "", "err": "", "pinned": False, "unread": False, "ctx_pct": None,
            "unread_n": 0, "last_src": "", "last_text": "", "last_ts": 0.0, **kw}


RUN, WAIT, FAIL = ag("claude-aaaaa1"), ag("claude-bbbbb2", AgentState.WAITING), ag("codex-ccccc3", AgentState.FAILED)
IDLE = ag("claude-ddddd4", AgentState.IDLE, "beta", since=NOW - 5000, rss=500, pane="%9")
FIN = ag("claude-eeeee5", AgentState.IDLE, since=NOW - 60, rss=50)
PIN = ag("devin-fffff6", AgentState.WAITING, "beta")
ALL = [RUN, WAIT, FAIL, IDLE, FIN, PIN]


def sec(key, agents):
    return {"key": key, "title": key, "agents": list(agents), "total": len(agents)}


def ctx(sort_url="/?sort=newest", **kw):
    m = {a.id: meta(a) for a in ALL}
    m[PIN.id]["pinned"] = True
    m[WAIT.id].update(q="Edit app.py?", unread=True, model="opus-4", options=[{"n": 1, "label": "Yes"},
                      {"n": 2, "label": "Yes, and don't ask again"}, {"n": 3, "label": "No"}])
    m[RUN.id].update(model="sonnet-4-5", options=[{"n": 1, "label": "stale"}])  # not waiting: no answers
    m[FAIL.id].update(err="pytest: 3 failed")
    m[IDLE.id].update(unread_n=2, last_src="ntfy", last_text="Done; pushed", ctx_pct=86)
    base = {"sort_url": sort_url, "agents": ALL, "inbox_meta": m, "q": "", "launch_href": "/p/alpha/launch",
            "sections": [sec("pinned", [PIN]), sec("running", [RUN]), sec("waiting", [WAIT, FAIL]),
                         sec("finished", [FIN]), sec("idle", [IDLE])],
            "ended": [], "ended_total": 0, "ended_ntfy": {}, "errors": []}
    return {**base, **kw}


SETTINGS = SimpleNamespace(idle_after_s=28800, terminal_enabled=True)


@pytest.fixture(autouse=True)
def _snap(monkeypatch):
    win = TmuxWindow("@7", "$1", 2, "agent", panes=(TmuxPane("%1", "@7", "$1", 1, "claude", "/x"),))
    snap = services.Snapshot(ts=1.0, sessions=(TmuxSession("$1", "dev-alpha", protected=False, windows=(win,)),))
    monkeypatch.setattr(services, "published", lambda settings: snap)


def build(**kw):
    return inbox.build(ctx(**kw), SETTINGS, now=NOW)


def ids(ib):
    return {g.key: [r.id for r in g.rows] for g in ib.groups}


# ---------------------------------------------------------------- view model
def test_bands_pinned_needs_running_idle():
    ib = build()
    assert ids(ib) == {"pinned": [PIN.id], "needs": [WAIT.id, FAIL.id], "running": [RUN.id],
                       "idle": [FIN.id, IDLE.id]}  # finished + idle merged, newest first
    assert [g.cls for g in ib.groups] == ["", "warn", "", ""]


def test_view_counts_and_stats():
    ib = build()
    c = {v["key"]: v["count"] for v in ib.views}
    assert c == {"all": 6, "needs": 3, "running": 1, "idle": 2, "ended": 0}
    assert [v["key"] for v in ib.views if v["cur"]] == ["all"]
    assert ib.stats[:2] == ["6 agents", "1 running"] and ib.stats[2] == {"text": "3 need you", "cls": "warn"}
    assert ib.unread == 2 and ib.stats[-1] == "2 unread"


def test_needs_view_keeps_the_pinned_waiting_agent():
    ib = build(sort_url="/?view=needs&sort=newest")
    assert ids(ib) == {"pinned": [PIN.id], "needs": [WAIT.id, FAIL.id]}
    assert ib.shown == ib.total == 3


def test_stale_failure_is_idle():
    old = ag("codex-0ld001", AgentState.FAILED, since=NOW - 40000)
    assert inbox.klass(old, NOW, 28800) == "idle" and inbox.klass(FAIL, NOW, 28800) == "needs"


def test_unread_only_and_empty_line():
    ib = build(sort_url="/?un=1&sort=newest")
    assert sorted(r.id for g in ib.groups for r in g.rows) == sorted([WAIT.id, IDLE.id])
    assert ib.more_url is None
    ib = build(sort_url="/?un=1&view=running&sort=newest")
    assert not ib.groups and ib.empty == "No unread agents." and ib.empty_url == "/?view=running&sort=newest"


def test_group_by_project():
    ib = build(sort_url="/?grp=project&sort=newest")
    assert [(g.key, g.icon) for g in ib.groups] == [("p:beta", "folder"), ("p:alpha", "folder")]
    assert ids(ib)["p:alpha"] == [WAIT.id, FAIL.id, RUN.id, FIN.id]


def test_form_defaults_are_dropped_from_urls():
    ib = build(sort_url="/?un=0&grp=state&q=ab&sort=name")
    assert ib.poll_url == "/?q=ab&sort=name" and ib.ctl_url == "/?q=ab&sort=name"


def test_header_sort_links_and_order():
    cols = {c.get("label"): c for c in build(sort_url="/?sort=name&q=x").cols}
    assert cols["Agent"]["href"] == "/?sort=-name&q=x"  # click again reverses
    assert cols["Project"]["href"] == "/?sort=project&q=x"
    assert cols["Age"]["href"] == "/?sort=newest&q=x" and cols["CPU"]["href"] == "/?sort=cpu&q=x"
    assert cols["RAM"]["href"] == "/?sort=ram&q=x"
    assert build(sort_url="/?sort=name").sort == "name"
    assert build(sort_url="/?sort=-name").sort == "-name"
    assert build(sort_url="/?sort=newest").sort == "age" and build(sort_url="/?sort=oldest").sort == "-age"
    assert build(sort_url="/?sort=cpu").sort == "-cpu"
    assert {c["cls"] for c in build().cols if c.get("label") in ("tmux", "CPU", "RAM")} == {"hide-n i-w"}


def test_ram_sort_is_the_cores():
    # the core sorts each section (sort=ram: most RAM first); dt only orders its merged Idle band the same way
    ib = build(sort_url="/?sort=ram")
    assert ids(ib)["idle"] == [IDLE.id, FIN.id] and ib.sort == "-ram"
    cols = {c.get("label"): c for c in ib.cols}
    assert cols["RAM"]["href"] == "/?sort=-ram" and cols["Agent"]["href"] == "/?sort=name"
    assert build(sort_url="/?sort=-ram").sort == "ram"
    assert ids(build(sort_url="/?sort=-ram"))["idle"] == [FIN.id, IDLE.id]
    old = build(sort_url="/?sort=newest&rs=ram")  # the former dt-side parameter is dropped
    assert "rs=" not in old.poll_url and ids(old)["idle"] == [FIN.id, IDLE.id]


def test_row_fields():
    rows = {r.id: r for g in build().groups for r in g.rows}
    w, f, i, r = rows[WAIT.id], rows[FAIL.id], rows[IDLE.id], rows[RUN.id]
    assert w.q == "Edit app.py?" and w.unread and w.word.startswith("Needs you")
    assert f.err == "pytest: 3 failed" and f.state == "failed"
    assert i.src == "ntfy" and i.last == "Done; pushed" and i.unread_n == 2 and i.ctx_pct == 86
    assert r.since == int(NOW - 90) and r.age == "1m" and r.tmux == "dev-alpha:2"
    assert r.term == "/term/s1?w=w7" and r.sid == "$1"
    assert r.ram == "10M" and r.cpu == "–"
    assert w.model == "opus-4" and r.model == "sonnet-4-5" and i.model == ""
    assert w.options == [(1, "Yes"), (2, "Yes, and don't ask again"), (3, "No")] and r.options == []


def _menu(r):
    return {it.get("id"): it for it in r.menu if not it.get("sep")}


def test_row_menu():
    rows = {r.id: r for g in build().groups for r in g.rows}
    m = _menu(rows[RUN.id])
    assert m["pin"]["post"] == f"/a/{RUN.id}/pin" and m["pin"]["vals"] == {"pinned": "1"} and m["pin"]["key"] == "P"
    assert m["read"]["post"] == "/inbox/mark-unread" and m["read"]["vals"] == {"key": f"{RUN.id}:running"}
    assert m["copy"]["copy"] == RUN.id and m["det"]["det"] is True and m["tab"]["event"] == "i:tab"
    assert m["stop"]["post"] == f"/a/{RUN.id}/interrupt"
    assert m["kill"]["post"] == "/tmux/window/w7/kill" and m["kill"]["typed"] == RUN.id and m["kill"]["danger"]
    # V7-SEC-6: the label and the question name the window and its agent; the typed value is the agent id named
    assert m["kill"]["label"] == "Kill window dev-alpha:2…"
    assert m["kill"]["confirm"] == f"Kill window dev-alpha:2 of {RUN.id}?" and RUN.id in m["kill"]["confirm"]
    mw = _menu(rows[WAIT.id])
    assert mw["read"]["post"] == "/inbox/read" and mw["ans2"]["vals"] == {"key": "2"}
    assert [mw[f"ans{n}"]["label"] for n in (1, 2, 3)] == ["1 · Yes", "2 · Yes, and don't ask again", "3 · No"]
    assert _menu(rows[PIN.id])["ans1"]["label"] == "Answer 1"  # no options read: plain numbers
    assert _menu(rows[PIN.id])["pin"]["vals"] == {"pinned": "0"}
    mi = _menu(rows[IDLE.id])
    assert mi["stop"]["post"] == f"/a/{IDLE.id}/exit" and mi["stop"]["confirm"]
    assert mi["kill"]["disabled"] and mi["kill"]["why"] == "tmux window unknown" and "ans1" not in mi  # pane %9


def test_kill_disabled_on_a_protected_session(monkeypatch):
    win = TmuxWindow("@7", "$1", 2, "agent", panes=(TmuxPane("%1", "@7", "$1", 1, "claude", "/x"),))
    snap = services.Snapshot(ts=1.0, sessions=(TmuxSession("$1", "dev-alpha", windows=(win,)),))
    monkeypatch.setattr(services, "published", lambda settings: snap)
    rows = {r.id: r for g in build().groups for r in g.rows}
    k = _menu(rows[RUN.id])["kill"]
    assert k["disabled"] and k["why"] == "protected tmux session" and "post" not in k


def test_no_terminal_when_disabled():
    r = inbox.row(RUN, meta(RUN), services.published(None), False, NOW)
    assert r.term == "" and "term" not in _menu(r)


def _rec(i, resume=True):
    return SimpleNamespace(id=f"claude-e{i:05d}", kind="claude", project="alpha", cwd="/x", first_seen=NOW - 4000,
                           last_seen=NOW - 100, ended_at=NOW - 100, resume_id="u" if resume else None)


def test_ended_view_first_eight_then_all():
    recs = [_rec(i, resume=i != 1) for i in range(12)]
    ib = build(sort_url="/?view=ended&sort=newest", ended=recs, ended_total=30,
               ended_ntfy={recs[0].id: SimpleNamespace(message="All  done", title="t")})
    assert len(ib.ended) == 8 and ib.shown == 8 and ib.total == 30 and not ib.groups
    assert ib.more_url == "/?view=ended&sort=newest&eall=1"
    e0, e1 = ib.ended[0], ib.ended[1]
    assert e0.last == "All done" and e0.ran == "1h 5m" and e0.ended == "1m" and e0.resume and not e1.resume
    assert {it.get("id") for it in e0.menu} >= {"resume", "copy", "open"} and "resume" not in {
        it.get("id") for it in e1.menu}
    ib = build(sort_url="/?view=ended&eall=1&sort=newest", ended=recs, ended_total=30)
    assert len(ib.ended) == 12 and ib.more_url is None


def test_bind_never_raises(monkeypatch):
    g = inbox.bind(lambda: SETTINGS)
    monkeypatch.setattr(inbox, "build", lambda *a, **k: 1 / 0)
    env = jinja2.Environment()
    env.globals["dt_inbox"] = g
    assert env.from_string("{{ dt_inbox() is none }}").render() == "True"


# ---------------------------------------------------------------- templates
@pytest.fixture
def env():
    e = jinja2.Environment(loader=jinja2.FileSystemLoader(THEME / "templates"), autoescape=True,
                           undefined=jinja2.StrictUndefined)
    e.globals["dt_inbox"] = inbox.bind(lambda: SETTINGS)
    return e


def render(env, **kw):
    import time as _t

    c = ctx(**kw)
    real = inbox.build
    inbox.build = lambda cx, s, now=None: real(cx, s, now=NOW)  # fixed clock
    try:
        return env.get_template("_inbox.html").render(**c)
    finally:
        inbox.build = real
        _t.time()


def test_fragment_structure(env):
    html = render(env)
    assert html.startswith('<i hidden data-poll="/?sort=newest" data-key="i-poll"></i>')
    assert 'class="p-g i-list"' in html and 'data-list="inbox"' in html
    assert 'data-key-enter="event:i:open"' in html and 'data-key-e="menu:read"' in html
    assert 'data-key-p="menu:pin"' in html and 'data-key-o="menu:tab"' in html
    assert ' style=' not in html and "<script" not in html and " onclick" not in html
    assert html.count('class="p-gr i-row') == 6 and html.count("data-p-det-for=") == 6
    assert f'data-i-recap="/a/{RUN.id}/recap-row"' in html and 'data-keep' in html
    assert f'data-key="rc-{RUN.id}"' in html
    assert 'class="chip i-ctx"' in html and "ctx 86%" in html
    assert 'class="i-q"' in html and 'class="i-err"' in html and '<span class="i-src"' in html
    assert 'data-i-term="$1"' in html and 'href="/term/s1?w=w7"' in html
    assert f'data-i-rk="{WAIT.id}:waiting" data-i-un="1"' in html
    assert 'data-i-run="ans1"' in html and 'data-i-readall' in html
    assert ">1 · Yes</button>" in html and ">3 · No</button>" in html  # labelled answers (core options)
    assert re.search(r"<dt>Model</dt><dd>opus-4</dd>", html) and "<dt>Model</dt><dd>–</dd>" in html
    assert re.search(r'class="i-errs" role="status" hidden', html)
    for raw in re.findall(r"data-menu='([^']*)'", html):
        assert isinstance(json.loads(raw.replace("&#39;", "'")), list)


def test_rows_have_only_cells(env):
    html = render(env)
    rows = re.findall(r'<div class="p-gr i-row[^>]*>\n(.*?)\n</div>', html, re.S)
    assert len(rows) == 6
    for row in rows:
        tops = re.findall(r"^\s*(<\w+[^>]*>)", row, re.M)  # one cell per line; 9 tracks + .gc-m (phone line 2)
        assert len(tops) == 10 and all('role="cell"' in t for t in tops), tops


def test_fragment_views(env):
    html = render(env, sort_url="/?view=ended&sort=newest", ended=[_rec(1)], ended_total=1)
    assert 'class="p-g i-end"' in html and 'data-i-run="resume"' in html and "i-ctl" not in html
    html = render(env, sort_url="/?q=zz&sort=newest", q="zz", sections=[], agents=[])
    assert "No agent matches “zz”." in html and 'data-i-nav>Clear the filter' in html


def test_page_keeps_the_live_contract():
    src = (THEME / "templates" / "inbox.html").read_text()
    assert re.search(r'<div id="inbox"[^>]*hx-trigger="pm:agent_state from:document, pm:agent_added from:document, '
                     r'pm:agent_removed from:document, pm:inbox from:document, pm:refresh, pm7:refresh"'
                     r'[^>]*data-nodiff>\{% include "_inbox.html" %\}</div>', src)
    assert "every " not in src.split("-#}", 1)[1]  # V7-CODE-7: the stream, no timed poll
    assert "{% block mainclass %}{% endblock %}" in src and "css/inbox.css" in src and "js/inbox.js" in src


def test_inbox_js_rules():
    js = (THEME / "static" / "js" / "inbox.js").read_text()
    assert "keydown" not in js and "fetch(" not in js.replace("pm7.fetch(", "")
    assert "setInterval" not in js and "MutationObserver" not in js
    assert "pm7.menus.run" in js and "nodiff:swapped" in js and "pm7.table.restore" in js
