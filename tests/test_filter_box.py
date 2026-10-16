"""Unified list filter box (phase 1: Inbox, Ended, ntfy): macro markup, Inbox ?q= server filter, row data-q
(name-type text only, never message text), filter.js static contract. Unit tests of the filter text live in
tests/agents/test_filter_text.py; the browser behaviour in tests/ui/test_filter_box.py."""
import re
import uuid
from pathlib import Path

import pytest
from helpers import login, make_client, make_settings, ntfy_item
from helpers import ntfy_env as env  # noqa: F401  (fixture)

import pm
import pm.notify.owner
from pm.agents import registry
from pm.core.models import Agent, AgentKind, AgentState, NtfyItem
from pm.infra import snapshot as services
from pm.tmux.ctl import SEP
from pm.web.render import TEMPLATE_DIR
from pm.web.render import env as jenv
from pm.web.routes.pages import router

REAL_REGISTRY = True
SRC = Path(pm.__file__).parent
MSG = "SENTINELBODY"  # an ntfy message text that must never be filterable

OLD_MARKUP = ("filter-clear", "ended-q", "ntfy-q", "Filter</button>", ">Filter<", ">Clear<", "Clear filter</a>")


def _line(sid, sname, wid, widx, wname, pane, cwd):
    return SEP.join([sid, sname, "1", wid, str(widx), wname, "1", pane, "4242", "claude", cwd, "1", "t"]) + "\n"


def _ended(st, label, project, cwd, now):
    ident = str(uuid.uuid4())
    a = Agent(id="claude-9", kind=AgentKind.CLAUDE, pane_id="%9", session_id="$9", pid=9, cwd=cwd, project=project,
              state=AgentState.IDLE, ident=ident, label=label)
    rid = registry.assign(st, [a], now=now)[0].id
    registry.assign(st, [], now=now + 10)  # nothing live any more: the record is ended
    return rid


@pytest.fixture
def box(fake_tmux, tmp_path, monkeypatch):
    """Inbox with 3 live agents (running, waiting, idle), 2 ended sessions; the ntfy feed gives the ended
    'zed' session a message containing MSG. Returns (settings, client, ended ids {label: id})."""
    dev = tmp_path / "dev"
    (dev / "alpha").mkdir(parents=True)
    s = make_settings(fake_tmux, tmp_path)
    monkeypatch.setenv("FAKE_TMUX_OUT",
                       _line("$1", "dev-aaa", "@1", 1, "wq-alpha", "%1", str(dev / "alpha"))
                       + _line("$2", "dev-bbb", "@2", 2, "wq-beta", "%2", str(dev / "alpha"))
                       + _line("$3", "dev-ccc", "@3", 3, "wq-gamma", "%3", str(dev / "alpha")))
    live = [Agent(id=f"claude-{n}", kind=AgentKind.CLAUDE, pane_id=f"%{n}", session_id=f"${n}", pid=n,
                  cwd=str(dev / "alpha"), state=st, state_since=1.0, ident=str(uuid.uuid4()))
            for n, st in ((1, AgentState.RUNNING), (2, AgentState.WAITING), (3, AgentState.IDLE))]
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes: list(live))
    ids = {"zed": _ended(s, "dev-zed-1-claude", "alpha", str(dev / "alpha"), 100),
           "yak": _ended(s, "dev-yak-2-claude", "yakproj", "/yak", 200)}
    item = NtfyItem(id="n1", time=300.0, title="zed finished", message=f"all done {MSG}")
    monkeypatch.setattr(pm.notify.owner, "agent_feed",
                        lambda settings, rids, path=None, unread=None: {ids["zed"]: pm.notify.owner.AgentFeed(item)})
    c = make_client(s, router, static=True, logged_in=True)
    return s, c, ids


def _nbox(html):
    """Number of filter inputs (data-filter, not data-filter-live / data-filter-target)."""
    return len(re.findall(r"<input[^>]* data-filter[ >]", html))


def _keys(html):
    return re.findall(r'<div class="nwrap(?: e-more)?" (?:role="listitem" )?data-key="([^"]+)"', html)


def _dq(html, key):
    return re.search(rf'data-key="{re.escape(key)}" data-q="([^"]*)"', html).group(1)


# ---------------------------------------------------------------- the macro
def test_macro_markup_and_escaping():
    html = jenv.from_string('{% from "_filterbox.html" import filterbox %}{{ filterbox(q) }}').render(q='a"<b>')
    assert html.startswith('<span class="fbox"><input ') and html.endswith("</span>")
    inp = re.search(r"<input [^>]*>", html).group(0)
    assert 'type="search"' in inp and 'name="q"' in inp and "data-filter" in inp
    assert 'placeholder="Filter..."' in inp and 'maxlength="80"' in inp and 'autocomplete="off"' in inp
    assert 'value="a&#34;&lt;b&gt;"' in inp  # q is autoescaped
    # the clear x is the input's next sibling (filter.js reads previousElementSibling) and is the only button
    assert re.search(r'data-filter[^>]*><button type="button" class="fbox-x" aria-label="Clear filter"', html)
    assert html.count("<button") == 1 and html.count("<input") == 1 and "submit" not in html
    assert 'value=""' in jenv.from_string('{% from "_filterbox.html" import filterbox %}{{ filterbox() }}').render()


# ---------------------------------------------------------------- one box per page, no old controls
def test_inbox_has_one_filterbox_and_no_old_controls(box):
    s, c, ids = box
    t = c.get("/").text
    assert _nbox(t) == 1 and t.count('class="fbox"') == 1 and t.count('class="fbox-x"') == 1
    assert t.count('placeholder="Filter..."') == 1
    bar = re.search(r'<form class="filterbar" id="inbox-filter".*?</form>', t, re.S).group(0)
    assert bar.index("<input") < bar.index("sel-show")  # box first, Show select after it
    assert "<button" in bar and bar.count("<button") == 1  # only the in-field x; no Filter/Search submit
    assert 'type="submit"' not in bar
    for old in OLD_MARKUP:
        assert old not in t, old
    assert 'name="q"' in bar and "<input" in bar
    # the poll/SSE swap carries the same single box (the form is part of the swapped list)
    p = c.get("/", headers={"hx-request": "true"}).text
    assert _nbox(p) == 1


def test_filter_js_is_loaded_before_ended_js(box):
    s, c, ids = box
    t = c.get("/").text
    assert "/static/js/filter.js" in t and t.index("/static/js/filter.js") < t.index("/static/js/ended.js")


def test_ntfy_page_has_one_filterbox_and_no_old_controls(env):
    s, c, st, dev = env
    login(s, c)
    st["items"] = [ntfy_item(1, str(dev / "alpha"), title="Build DONE", message=f"x {MSG}")]
    t = c.get("/ntfy").text
    assert _nbox(t) == 1 and t.count('class="fbox"') == 1 and t.count('placeholder="Filter..."') == 1
    bar = re.search(r'<div class="filterbar" id="ntfy-tools-all".*?</div>', t, re.S).group(0)
    assert bar.index("data-filter") < bar.index("sel-show") and bar.count("<button") == 1
    for old in OLD_MARKUP:
        assert old not in t, old
    assert _nbox(t) == 1 and _nbox(c.get("/ntfy/panel?scope=all").text) == 0  # fragment: no box


def test_project_page_has_one_filterbox_outside_the_live_panel_and_no_old_controls(box):
    s, c, ids = box
    t = c.get("/p/alpha").text
    assert _nbox(t) == 1 and t.count('class="fbox"') == 1  # one per page
    at = re.search(r"<input[^>]* data-filter[ >]", t).start()
    live_at = t.index('id="proj-live"')
    assert at < live_at  # above the swapped live panel (Agents + Ended), not inside it
    assert not re.search(r'<span class="ended-tools"[^>]*>(?:(?!</span>).)*data-filter', t, re.S)  # nor in the Ended head
    assert _nbox(t[live_at:]) == 0 and "fbox" not in t[live_at:]
    for old in OLD_MARKUP:
        assert old not in t, old


def test_filterable_rows_carry_data_frow_last_and_the_inbox_form_is_live(box):
    s, c, ids = box
    t = c.get("/").text
    rows = re.findall(r'<div class="nwrap(?: e-more)?"[^>]*>', t)
    assert len(rows) == 5  # 3 live + 2 ended
    assert all(r.endswith(' data-q="' + re.search(r'data-q="([^"]*)"', r).group(1) + '" data-frow>') for r in rows)
    form = re.search(r'<form class="filterbar" id="inbox-filter"[^>]*>', t).group(0)
    assert 'action="/"' in form and "data-filter-live" in form and 'data-filter-target="#inbox"' in form
    assert 'id="inbox"' in t
    # the grid variant (project page Ended) tags its rows too, and no longer holds a box in its head
    mod = jenv.get_template("_agentrow.html").module
    g = mod.ended_list
    rec = type("R", (), dict(id="claude-1", kind="claude", label="dev-x-1-claude", project="x", cwd="/x", ended_at=1.0,
                             last_seen=1.0, first_seen=1.0, resume_id="", window_name="", transcript=""))()
    for grid in (False, True):
        h = str(g([rec], 1, "", {}, mod.PROJECT_COLS if grid else mod.INBOX_COLS))
        assert re.search(r'<div class="nwrap"[^>]* data-q="[^"]*" data-frow>', h) and "data-filter" not in h


# ---------------------------------------------------------------- Inbox ?q= (server) and row data-q
def test_inbox_rows_carry_name_type_data_q_without_message(box):
    s, c, ids = box
    t = c.get("/").text
    keys = _keys(t)
    assert len([k for k in keys if k.startswith("w-")]) == 3 and set(ids.values()) <= {k[2:] for k in keys}
    live = next(k for k in keys if k.startswith("w-") and "wq-alpha" in _dq(t, k))
    q = _dq(t, live)
    assert q == q.lower() and "dev-aaa-1-claude" in q and "wq-alpha" in q and "alpha" in q and live[2:] in q
    z = _dq(t, "e-" + ids["zed"])
    assert "dev-zed-1-claude" in z and "alpha" in z and ids["zed"] in z and "claude" in z
    assert "zed finished" not in z and MSG.lower() not in z and "all done" not in z
    # the ended row still shows the ntfy text to the reader; it is just not part of the filter text
    assert MSG in t
    assert all(MSG.lower() not in m.lower() for m in re.findall(r'data-q="([^"]*)"', t))


def test_inbox_q_filters_active_idle_and_ended(box):
    s, c, ids = box
    # a live agent (waiting) by window name; ended and idle rows drop out
    t = c.get("/", params={"q": "WQ-BETA"}).text
    keys = _keys(t)
    assert len(keys) == 1 and keys[0].startswith("w-") and "wq-beta" in _dq(t, keys[0])
    assert "1 of 3 agents" in t
    # an idle (folded) agent matches on its own
    keys = _keys(c.get("/", params={"q": "wq-gamma"}).text)
    assert len(keys) == 1 and keys[0].startswith("w-")
    # the q box shows the typed text again after a reload
    assert re.search(r'data-filter value="WQ-BETA"', c.get("/", params={"q": "WQ-BETA"}).text)
    # a name only ended sessions have: no live rows, one ended row
    t = c.get("/", params={"q": "yakproj"}).text
    assert _keys(t) == ["e-" + ids["yak"]] and "Ended (1)" in t
    # project name shared by a live agent and an ended session: both kinds match
    t = c.get("/", params={"q": "alpha"}).text
    assert {k[:2] for k in _keys(t)} == {"w-", "e-"} and "e-" + ids["zed"] in _keys(t)
    assert "e-" + ids["yak"] not in _keys(t)


def test_inbox_ended_total_counts_only_matches(box):
    s, c, ids = box
    assert "Ended (2)" in c.get("/").text
    t = c.get("/", params={"q": "zed"}).text
    assert "Ended (1)" in t and "Ended (2)" not in t and _keys(t) == ["e-" + ids["zed"]]
    t = c.get("/", params={"q": "nomatchatall"}).text
    assert "Ended (" not in t and "No agents match" in t and "Clear filter</a>" not in t


def test_message_text_never_matches(box):
    s, c, ids = box
    for needle in (MSG, MSG.lower(), "all done", "zed finished"):
        t = c.get("/", params={"q": needle}).text
        assert _keys(t) == [], needle
        assert "Ended (" not in t and "No agents match" in t


def test_inbox_q_composes_with_view_ended(box):
    s, c, ids = box
    t = c.get("/", params={"q": "yak", "view": "ended"}).text
    assert _keys(t) == ["e-" + ids["yak"]] and "Ended (1)" in t


# ---------------------------------------------------------------- ntfy rows
def test_ntfy_row_data_q_excludes_message(env):
    s, c, st, dev = env
    login(s, c)
    st["items"] = [ntfy_item(1, str(dev / "alpha"), title="Build DONE", message=f"body {MSG}", tags=(str(dev / "alpha"), "deploy"))]
    t = c.get("/ntfy/panel?scope=all").text
    q = re.search(r'data-q="([^"]*)"', t).group(1)
    assert "build done" in q and "alpha" in q  # title, project
    assert MSG.lower() not in q and "body" not in q
    assert MSG in t  # still shown in the detail


# ---------------------------------------------------------------- static contract of the JS
def _js(name):
    return (SRC / "static/js" / name).read_text()


def test_filter_js_contract():
    js = _js("filter.js")
    assert not re.search(r"\bfetch\s*\(", js) and "XMLHttpRequest" not in js  # server round trip goes through htmx
    assert "data-pmq" in js and "setAttribute('data-pmq'" in js and "removeAttribute('data-pmq')" in js
    assert "window.pm.filter" in js and "pm:filter" in js and "input[data-filter]" in js
    assert "replaceSync" in js and "CSSStyleSheet" in js  # strict CSP: CSSOM sheet, no <style>
    assert "eval(" not in js and "innerHTML" not in js and "new Function" not in js
    assert "'Escape'" in js and "'Enter'" in js and ".fbox-x" in js and "previousElementSibling" in js
    assert "htmx.trigger(box, 'pm:refresh')" in js
    # row convention and the server-q contract (no Inbox hard-coding)
    assert "[data-frow][data-q]" in js and '[data-frow][data-key="' in js and ".nwrap" not in js
    assert "form[data-filter-live]" in js and "filterTarget" in js and "inbox" not in js.lower()
    assert len(js.splitlines()) <= 500


def test_ended_js_uses_the_shared_filter_and_has_no_input_listener():
    js = _js("ended.js")
    assert "pm.filter.match" in js and "pm.filter.q()" in js and "'pm:filter'" in js
    assert not re.search(r"addEventListener\(\s*['\"]input['\"]", js)
    assert "-q'" not in js and "data-ended-q" not in js and "data-ntfy-q" not in js


def test_lists_js_has_no_second_inbox_filter_and_base_loads_filter_js():
    lists = re.sub(r"//.*", "", _js("lists.js"))
    assert "inbox-filter" not in lists and "'input'" not in lists and '"input"' not in lists and "hx-get" not in lists
    assert '<script src="/static/js/filter.js" defer></script>' in (TEMPLATE_DIR / "base.html").read_text()


def test_css_hides_expand_rows_through_pmq():
    css = (SRC / "static/css/rows.css").read_text()
    for name in ("ended", "ntfy"):
        assert f"html:not([data-{name}-all]):not([data-pmq])" in css
    assert ".fbox-x" in (SRC / "static/css/lists.css").read_text()


def test_wants_fragment_is_htmx_request_but_not_boosted():
    from types import SimpleNamespace as NS

    from pm.web.listing import wants_fragment
    assert wants_fragment(NS(headers={"hx-request": "true"})) is True
    assert wants_fragment(NS(headers={})) is False
    assert wants_fragment(NS(headers={"hx-request": "true", "hx-boosted": "true"})) is False


def test_filter_js_scoped_box_and_several_live_forms():
    js = _js("filter.js")
    # the page's one query skips scoped boxes; a scoped box (sidebar) filters only inside its scope element
    assert "input[data-filter]:not([data-filter-scope])" in js and "getAttribute('data-filter-scope')" in js
    assert "hasAttribute('data-filter-scope')" in js
    # every live form refreshes its own target; only the one whose action is the page path owns the address bar
    assert "querySelectorAll('form[data-filter-live]')" in js and "window.location.pathname" in js
    assert re.search(r"if \(!ownsPage\(form\)\) return;\s*try \{ history\.replaceState", js)
    assert "a[data-qlink][href]" in js  # Audit's result chips keep the typed q
    assert "form[data-filter-live] select:not([data-nav])" in js  # Client errors kind select re-fetches

