"""Shared template macros (templates/_macros.html, _limitbar.html, _sortbar.html, _filterbox.html): exact markup,
the 99+ cap, the dot pair side.js rewrites, and that the old copies are gone."""
import re
from pathlib import Path

import pm
from pm.core.models import AgentState
from pm.web.render import TEMPLATE_DIR, env

TPL = TEMPLATE_DIR


def r(src: str, **kw) -> str:
    return env.from_string(src).render(**kw)


def m(call: str, **kw) -> str:
    return r('{% from "_macros.html" import dot, count_pill, empty, notice, page_head, post_btn, sum_tile %}' + call, **kw)


# ---------------------------------------------------------------- dot
def test_dot_is_the_dot_plus_sr_only_pair():
    assert m('{{ dot("running") }}') == ('<span class="dot dot-running" title="running"></span>'
                                         '<span class="sr-only">running</span>')
    assert m('{{ dot("idle", "not installed") }}') == ('<span class="dot dot-idle" title="not installed"></span>'
                                                       '<span class="sr-only">not installed</span>')
    assert m("{{ dot(s) }}", s=AgentState.WAITING).startswith('<span class="dot dot-waiting" title="waiting">')  # enum
    assert m('{{ dot("err", "down", "sdot") }}') == ('<span class="sdot sdot-err" title="down"></span>'
                                                     '<span class="sr-only">down</span>')
    assert "&lt;" in m("{{ dot('idle', w) }}", w="<b>")  # the word is autoescaped


def test_dot_markup_matches_side_js_setdot():
    js = (Path(pm.__file__).parent / "static/js/side.js").read_text()
    assert "el.className = 'dot dot-' + dot; el.title = dot;" in js and "classList.contains('sr-only')" in js


def test_one_dot_macro():
    defs = [p.name for p in TPL.glob("*.html") if re.search(r"\{% macro dot\(", p.read_text())]
    assert defs == ["_macros.html"]
    for name in ("_side.html", "_agents_macros.html", "_servers.html"):
        assert '<span class="dot dot-{{' not in (TPL / name).read_text(), name


# ---------------------------------------------------------------- count_pill
def test_count_pill_caps_at_99_and_hides_zero():
    assert m("{{ count_pill(5) }}") == '<span class="cpill">5</span>'
    assert m("{{ count_pill(99) }}") == '<span class="cpill">99</span>'
    assert m("{{ count_pill(100) }}") == '<span class="cpill">99+</span>'
    assert m("{{ count_pill(0) }}") == '<span class="cpill" hidden></span>'
    assert m("{{ count_pill(none) }}") == '<span class="cpill" hidden></span>'
    assert m('{{ count_pill(3, "vs-ub", "Unread ntfy messages", "ub") }}') == (
        '<span class="cpill vs-ub" data-ub title="Unread ntfy messages">3</span>')
    assert m('{{ count_pill(250, "ntfy-unread", suffix=" unread") }}') == '<span class="cpill ntfy-unread">99+ unread</span>'


def test_count_pill_used_for_side_and_ntfy_badges():
    assert 'count_pill(r.unread, "vs-ub"' in (TPL / "_side.html").read_text()
    assert 'count_pill(unread, "ntfy-unread"' in (TPL / "_ntfy.html").read_text()
    css = (Path(pm.__file__).parent / "static/css/chrome.css").read_text()
    assert ".cpill{" in css and ".cpill[hidden]{display:none}" in css


# ---------------------------------------------------------------- empty, notice, page_head
def test_empty_and_notice():
    assert m('{{ empty("No containers.", "small") }}') == '<p class="muted small">No containers.</p>'
    assert m('{{ empty("Nothing.") }}') == '<p class="muted">Nothing.</p>'
    assert m('{{ notice("err", "boom", "badge bad") }}') == '<p class="notice notice-err badge bad" role="alert">boom</p>'
    assert m('{{ notice("warn", t) }}', t="<x>") == '<p class="notice notice-warn" role="status">&lt;x&gt;</p>'


def test_page_head_plain_short_and_with_actions():
    assert m('{{ page_head("Agents", "3 installed · 4 skills") }}') == (
        '<div class="phead inbox-head"><div class="inbox-title"><h1>Agents</h1>'
        '<p class="inbox-stats muted">3 installed · 4 skills</p></div></div>')
    assert '<p class="inbox-stats muted">2 running</p>' in m('{{ page_head("tmux", "5 sessions · 2 running", short=true) }}')
    assert '<p class="inbox-stats muted">plain</p>' in m('{{ page_head("tmux", "plain", short=true) }}')
    out = m('{% call page_head("P", "s") %}<span class="head-actions">X</span>{% endcall %}')
    assert out.endswith('</p></div><span class="head-actions">X</span></div>')


def test_list_pages_use_page_head():
    for name in ("projects.html", "tmux.html", "ntfy.html", "agents.html", "agents_detail.html"):
        t = (TPL / name).read_text()
        assert "page_head(" in t and '<div class="phead inbox-head">' not in t, name
        assert 'stats.split(" · "' not in t, name


# ---------------------------------------------------------------- post_btn
def test_post_btn():
    out = m('{{ post_btn("TOK", "/x/stop", "Stop", [("pid", 5)], "Stop it?", "small danger") }}')
    assert out == ('<form class="inline" method="post" action="/x/stop" hx-post="/x/stop" hx-target="#flash" '
                   'hx-swap="innerHTML" data-confirm="Stop it?"><input type="hidden" name="csrf" value="TOK">'
                   '<input type="hidden" name="pid" value="5"><button type="submit" class="small danger">Stop</button></form>')
    out = m('{{ post_btn("T", "/y", "Go", cls="", target="#box", form_cls="inline pj") }}')
    assert 'class="inline pj"' in out and 'hx-target="#box"' in out and "<button type=\"submit\">Go</button>" in out
    assert "confirm" not in out


def test_post_btn_replaces_the_copies():
    for name in ("_servers.html", "_resources.html"):
        t = (TPL / name).read_text()
        assert "post_btn(" in t and '<form class="inline" method="post"' not in t, name
    af = r('{% from "_macros.html" import action_form %}{{ action_form("T", "/p/a/git/push", "Git push", confirm="Push?", cls="pj") }}')
    assert 'class="inline pj"' in af and 'data-confirm="Push?"' in af and "hx-confirm" not in af


def test_sum_tile():
    class T:
        key, label, value, status, pct, detail = "load", "Load", "0.5", "warn", 42.4, "per CPU"
    out = m("{{ sum_tile(t, 'sum-load') }}", t=T)
    assert '<div class="sum-tile lvl-warn" id="sum-load" title="per CPU">' in out
    assert '<progress class="srv-bar lvl-warn" max="100" value="42" aria-label="Load"></progress>' in out
    assert '<span class="sum-detail">per CPU</span>' in out
    T.pct, T.detail = None, ""
    out = m("{{ sum_tile(t, 'x') }}", t=T)
    assert "progress" not in out and "sum-detail" not in out and "title=" not in out


# ---------------------------------------------------------------- limitbar / showsel / pager / filtersel
LINKS = [{"key": "10", "label": "10", "active": True}, {"key": "all", "label": "All", "active": False}]


def lb(call: str, **kw) -> str:
    return r('{% from "_limitbar.html" import showsel, limitbar, pager %}' + call, links=LINKS, **kw)


def test_showsel_server_and_browser_variants():
    s = lb("{{ showsel(links) }}")
    assert '<select name="n" data-nav aria-label="Rows to show">' in s and '<option value="10" selected>10</option>' in s
    c = lb('{{ showsel(links, "ntfy-n") }}')
    assert '<select class="ntfy-n" aria-label="Rows shown">' in c and "data-nav" not in c and 'name="n"' not in c


def test_limitbar_form_and_browser_footer():
    f = lb('{{ limitbar(links, 3, 9, "/agents", [("t", "library")]) }}')
    assert '<form class="showbar" method="get" action="/agents">' in f and "showing 3 of 9" in f
    assert '<input type="hidden" name="t" value="library">' in f
    b = lb('{{ limitbar(links, none, none, none, cls="ntfy-foot", id="ntfy-foot-all", sel="ntfy-n", count="ntfy-shown") }}')
    assert '<footer class="showbar ntfy-foot" id="ntfy-foot-all" hx-preserve="true">' in b and "<form" not in b
    assert '<span class="limitcount ntfy-shown muted small" aria-live="polite"></span>' in b
    assert b.index("limitcount") < b.index('<select class="ntfy-n"')


def test_pager_links_disabled_and_htmx():
    p = lb('{{ pager(2, "/audit?page=1", "", "Pages") }}')
    assert '<div class="aud-pager" role="group" aria-label="Pages">' in p
    assert '<a class="aud-chip" href="/audit?page=1">&larr; newer</a>' in p
    assert '<span class="aud-chip off">older &rarr;</span>' in p and "page 2" in p
    h = lb('{{ pager(1, "", "/x?ce_page=2", "Client error pages", "#ce-list") }}')
    assert '<a class="aud-chip" href="#" hx-get="/x?ce_page=2" hx-target="#ce-list" hx-swap="outerHTML">older &rarr;</a>' in h
    assert '<span class="aud-chip off">&larr; newer</span>' in h


def test_one_pager_for_audit_and_client_errors():
    for name in ("_audit_list.html", "_client_errors.html"):
        t = (TPL / name).read_text()
        assert "pager(" in t and 'class="aud-pager"' not in t, name
    assert "aud-pager" not in (TPL / "audit.html").read_text()


def test_filtersel_name():
    out = r('{% from "_sortbar.html" import filtersel %}{{ filtersel([("all", "All"), ("enabled", "Enabled")], "enabled", "show") }}')
    assert '<select name="show" data-nav aria-label="Filter">' in out and '<option value="enabled" selected>' in out
    assert 'name="f"' in r('{% from "_sortbar.html" import filtersel %}{{ filtersel([("", "All")], "") }}')


def test_filterbox_scoped_variant():
    out = r('{% from "_filterbox.html" import filterbox %}{{ filterbox(scope="#v5side", cls="vs-filter", label="Filter agents") }}')
    assert out.startswith('<span class="fbox vs-filter"><input type="search" data-filter data-filter-scope="#v5side" ')
    assert 'name="q"' not in out and 'aria-label="Filter agents"' in out and 'class="fbox-x"' in out
