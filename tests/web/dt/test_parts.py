"""dt shared page parts (templates/parts/*.html, PARTS.md): each macro rendered with plain Jinja (StrictUndefined,
autoescape, like test_theme_static.py) and its markup contract checked: roles, aria, classes, data hooks. Plus the key
table's register() API (keys.js) and the filter match rule (filter.js) in node, and the no-own-keydown rule of
listkeys.js."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import jinja2
import pytest

from pm.web import themes

from .kit import ENV, Node, Tree

THEME = themes.HERE / "dt"
TPL = THEME / "templates"
JS = THEME / "static" / "js"
PARTS = ("_head", "_tabs", "_seg", "_filter", "_table", "_menu", "_switch", "_select")



IMPORTS = "".join(f'{{% import "parts/{p}.html" as {p[1:]} %}}' for p in PARTS)


def render(src: str, **ctx) -> str:
    return ENV.from_string(IMPORTS + src).render(**ctx)


def dom(src: str, **ctx) -> Node:
    return Tree(render(src, **ctx)).root


def test_every_part_exists_and_imports():
    for p in PARTS:
        assert (TPL / "parts" / f"{p}.html").is_file(), p
    assert render("") == ""


# ---------------------------------------------------------------- head, tools
def test_head_title_stats_and_actions():
    acts = [{"label": "Mark all read", "icon": "check", "attrs": {"hx-post": "/x?a=1&b", "data-x": '"q"'}},
            {"label": "New", "icon": "plus", "href": "/new", "pri": True}]
    t = dom('{{ head.head("Inbox", stats=["9 agents", {"text": "2 need you", "cls": "warn"}], actions=acts,'
            ' id="ih") }}', acts=acts)
    h = t.one("p-head")
    assert h.attrs["id"] == "ih" and h.kids[0].tag == "h1" and h.kids[0].get_text() == "Inbox"
    st = t.one("p-stats")
    assert st.tag == "p" and st.attrs["id"] == "ih-stats" and st.get_text() == "9 agents · 2 need you"
    assert st.find(lambda n: n.tag == "span" and "warn" in n.cls)[0].get_text() == "2 need you"
    b, a = t.one("p-acts").kids
    assert b.tag == "button" and b.attrs["type"] == "button" and {"x-btn", "p-act"} <= b.cls and "pri" not in b.cls
    assert b.attrs["hx-post"] == "/x?a=1&b" and b.attrs["data-x"] == '"q"'  # xmlattr escapes, parser unescapes
    assert a.tag == "a" and a.attrs["href"] == "/new" and "pri" in a.cls
    assert [n.get_text() for n in t.by_cls("lb")] == ["Mark all read", "New"]  # phone: visually hidden, still named
    assert all(n.attrs.get("aria-hidden") == "true" for n in t.find(lambda n: n.tag == "svg"))


def test_head_minimal_and_caller():
    t = dom('{{ head.head("Audit") }}')
    assert not t.by_cls("p-stats") and not t.by_cls("p-acts")
    t = dom('{% call head.head("Audit", stats="1 entry") %}<form class="f"></form>{% endcall %}')
    assert t.one("p-stats").get_text() == "1 entry" and t.one("p-acts").kids[0].tag == "form"
    assert render('{% call head.tools() %}<i></i>{% endcall %}') == '<div class="p-tools"><i></i></div>'


# ---------------------------------------------------------------- flat tabs
def test_link_tabs_roving_and_counts():
    items = [{"key": "inst", "label": "Installed", "href": "?t=inst", "count": 6},
             {"key": "find", "label": "Findings", "href": "?t=find", "count": 2, "bad": True, "cur": True}]
    t = dom('{{ tabs.tabs(items, "Agents sections") }}', items=items)
    tl = t.one("p-tabs")
    assert tl.attrs["role"] == "tablist" and tl.attrs["aria-label"] == "Agents sections"
    tabs = tl.find(lambda n: n.attrs.get("role") == "tab")
    assert [n.tag for n in tabs] == ["a", "a"]
    assert [(n.attrs["aria-selected"], n.attrs["tabindex"]) for n in tabs] == [("false", "-1"), ("true", "0")]
    assert "aria-current" not in tabs[0].attrs and tabs[1].attrs["aria-current"] == "page"
    assert [(c.get_text(), "bad" in c.cls) for c in t.by_cls("c")] == [("6", False), ("2", True)]
    # the label carries its text for the hidden 600 copy (css/parts.css): labels never move on selection
    assert [(n.attrs["data-t"], n.get_text()) for n in t.by_cls("t")] == [("Installed", "Installed"),
                                                                          ("Findings", "Findings")]


def test_button_tabs_default_first_and_panels():
    items = [{"key": "a", "label": "First", "controls": "pa"}, {"key": "b", "label": "Second", "controls": "pb"}]
    t = dom('{{ tabs.tabs(items, "Demo") }}{% call tabs.panel("pa", "a", cur=true) %}A{% endcall %}'
            '{% call tabs.panel("pb", "b") %}B{% endcall %}', items=items)
    tabs = t.find(lambda n: n.attrs.get("role") == "tab")
    assert all(n.tag == "button" and n.attrs["type"] == "button" for n in tabs)
    assert [n.attrs["aria-controls"] for n in tabs] == ["pa", "pb"]
    assert [n.attrs["aria-selected"] for n in tabs] == ["true", "false"]  # no cur: the first one
    panels = t.find(lambda n: n.attrs.get("role") == "tabpanel")
    assert [(p.attrs["id"], p.attrs["aria-labelledby"], "hidden" in p.attrs) for p in panels] == [
        ("pa", "p-tab-a", False), ("pb", "p-tab-b", True)]
    assert {n.attrs["id"] for n in tabs} == {"p-tab-a", "p-tab-b"}


# ---------------------------------------------------------------- segments
def test_segments_links_current_and_counts():
    items = [{"key": "all", "label": "All", "href": "?v=all", "cur": True, "count": 9},
             {"key": "need", "label": "Needs you", "href": "?v=need", "count": 2, "warn": True},
             {"key": "none", "label": "None", "href": "?v=none"}]
    t = dom('{{ seg.seg(items) }}', items=items)
    nav = t.one("p-seg")
    assert nav.tag == "nav" and nav.attrs["aria-label"] == "Show"
    links = nav.kids
    assert [a.attrs.get("aria-current") for a in links] == ["true", None, None]
    assert [a.attrs["data-p-seg"] for a in links] == ["all", "need", "none"]
    assert not any("hx-get" in a.attrs for a in links)
    assert [(c.get_text(), "warn" in c.cls) for c in t.by_cls("c")] == [("9", False), ("2", True)]
    t = dom('{{ seg.seg(items, target="#list", push=false) }}', items=items)
    a = t.one("p-seg").kids[1]
    assert (a.attrs["hx-get"], a.attrs["hx-target"], a.attrs["hx-swap"]) == ("?v=need", "#list", "outerHTML")
    assert "hx-push-url" not in a.attrs


# ---------------------------------------------------------------- filter box
def test_filter_box_client_mode():
    t = dom('{{ filter.fbox("inbox", value="cla", placeholder="Filter agents", rows="#list") }}')
    box = t.one("p-fbox")
    assert box.attrs["role"] == "search"
    i = box.find(lambda n: n.tag == "input")[0]
    assert (i.attrs["type"], i.attrs["name"], i.attrs["value"], i.attrs["data-p-filter"]) == ("search", "q", "cla",
                                                                                              "inbox")
    assert i.attrs["placeholder"] == "Filter agents" and i.attrs["aria-label"] and i.attrs["autocomplete"] == "off"
    assert i.attrs["data-p-filter-rows"] == "#list" and "hx-get" not in i.attrs
    clr = t.one("p-fclr")
    assert clr.tag == "button" and clr.attrs["type"] == "button" and "data-p-fclr" in clr.attrs
    assert clr.attrs["aria-label"] and clr.attrs["tabindex"] == "-1"


def test_filter_box_server_mode():
    i = dom('{{ filter.fbox("ag", get="/agents/rows", target="#ag", delay=150) }}').find(lambda n: n.tag == "input")[0]
    assert i.attrs["hx-get"] == "/agents/rows" and i.attrs["hx-target"] == "#ag" and i.attrs["hx-swap"] == "outerHTML"
    assert i.attrs["hx-trigger"] == "input changed delay:150ms, search" and i.attrs["hx-sync"] == "this:replace"
    assert i.attrs["hx-replace-url"] == "true" and i.attrs["value"] == "" and "data-p-filter-rows" not in i.attrs


# ---------------------------------------------------------------- grid table
TABLE = """
{% call table.table("inbox", "Agents", cls="i-list", list="inbox", keys={"o": "menu:tab", "p": "[data-pin]"},
                    labels={"enter": "Open in the aside"}, id="il") %}
{{ table.gh([{"sr": "State"}, {"label": "Agent", "key": "id"}, {"label": "tmux", "cls": "hide-n"},
             {"label": "Age", "key": "age", "num": true}, {"label": "Path", "href": "/x?s=path"}], sort="-age",
            sort_url=su, target="#il") }}
{% call table.group("need", "Needs you", count=1, note="answer from the row", icon="warn", cls="warn") %}
{% call table.row("claude-1c70ee", href="/a/claude-1c70ee", kind="claude", cur=true, open=true, q="Claude-1C70EE fixproj") %}
{{ table.dot("waiting", "needs you") }}{% call table.name("claude-1c70ee", sfx=true) %}<span class="chip">2</span>{% endcall %}
{% call table.cell("hide-n mono") %}dev{% endcall %}{{ table.end("2m", since=100) }}{% call table.cell(num=true) %}1{% endcall %}
{% call table.meta() %}fixproj · edit{% endcall %}
{% call table.acts() %}{{ table.det_btn("inbox", "claude-1c70ee", open=true) }}{{ menu.menu([{"id": "tab", "label": "Tab", "href": "/a/x"}]) }}{% endcall %}
{% endcall %}
{% call table.det("inbox", "claude-1c70ee", open=true) %}{{ table.kv([("Model", "sonnet"), ("Folder", "/x", "mono")]) }}{% call table.dacts() %}<button>Open</button>{% endcall %}{% endcall %}
{% endcall %}
{% call table.group("idle", "Idle", count=0, open=false) %}{% endcall %}
{{ table.empty("Nothing matches.", fnone=true) }}
{% endcall %}
{{ table.foot(1, 31, more_href="?all=1", noun="agents") }}{{ table.foot(5, 5) }}
{{ table.hints([("J K", "move"), ("Enter", "aside")]) }}
"""


def _table() -> Node:
    return dom(TABLE, su=lambda k: f"?sort={k}")


def test_table_frame_and_keys():
    g = _table().one("p-g")
    assert g.attrs["role"] == "table" and g.attrs["aria-label"] == "Agents" and "i-list" in g.cls
    assert (g.attrs["data-p-table"], g.attrs["data-list"], g.attrs["id"]) == ("inbox", "inbox", "il")
    assert g.attrs["data-key-o"] == "menu:tab" and g.attrs["data-key-p"] == "[data-pin]"
    assert g.attrs["data-key-enter-label"] == "Open in the aside"
    assert "style" not in g.attrs  # one grid template per table, by class


def test_table_header_sort():
    gh = _table().one("p-gh")
    assert gh.attrs["role"] == "row"
    cols = gh.kids
    assert all(c.attrs["role"] == "columnheader" for c in cols) and len(cols) == 5
    assert [c.attrs.get("aria-sort") for c in cols] == [None, None, None, "descending", None]
    assert cols[0].one("vh").get_text() == "State" and "hide-n" in cols[2].cls and "num" in cols[3].cls
    links = gh.by_cls("p-sort")
    assert [a.attrs["href"] for a in links] == ["?sort=id", "?sort=age", "/x?s=path"]  # sort_url(key) or href
    assert all(a.tag == "a" and a.kids[-1].tag == "i" and "ar" in a.kids[-1].cls for a in links)  # arrow slot
    assert all(a.attrs["hx-get"] == a.attrs["href"] and a.attrs["hx-target"] == "#il" for a in links)
    asc = dom('{{ table.gh([{"label": "A", "key": "a"}], sort="a", sort_url=su) }}', su=str)
    assert asc.one("p-gh").kids[0].attrs["aria-sort"] == "ascending" and "hx-get" not in asc.one("p-sort").attrs


def test_table_groups_fold():
    gg = _table().by_cls("p-gg")
    assert [(g.attrs["role"], g.attrs["data-p-grp"], "fold" in g.cls) for g in gg] == [
        ("rowgroup", "need", False), ("rowgroup", "idle", True)]
    band = gg[0].kids[0]
    assert "p-grp" in band.cls and "warn" in band.cls and band.attrs["role"] == "row"
    cell = band.kids[0]
    assert cell.attrs["role"] == "cell" and "p-grpc" in cell.cls
    fold = cell.kids[0]
    assert fold.tag == "button" and fold.attrs["type"] == "button" and "p-fold" in fold.cls
    assert fold.attrs["aria-expanded"] == "true" and gg[1].one("p-fold").attrs["aria-expanded"] == "false"
    assert fold.one("t").get_text() == "Needs you" and fold.one("c").get_text() == "1"
    assert fold.one("note").get_text() == "· answer from the row"
    assert len(band.kids) == 1  # no actions: the fold cell only


def test_table_group_band_actions():
    src = ('{% call table.group("today", "Today", count=2, actions=acts) %}{% endcall %}'
           '{% call table.group("old", "Older", actions=[]) %}{% endcall %}')
    acts = [{"label": "Mark read", "icon": "check", "title": "Mark today read", "attrs": {"data-n-dayread": "today"}},
            {"label": "Hide", "hidden": True}]
    gg = dom(src, acts=acts).by_cls("p-gg")
    band = gg[0].kids[0]
    assert [k.attrs["role"] for k in band.kids] == ["cell", "cell"] and "p-grpa" in band.kids[1].cls
    b1, b2 = band.kids[1].kids
    assert b1.tag == "button" and b1.attrs["type"] == "button" and {"x-btn", "sm"} <= b1.cls
    assert b1.attrs["data-n-dayread"] == "today" and b1.attrs["title"] == "Mark today read"
    assert "hidden" not in b1.attrs and "hidden" in b2.attrs
    assert b1.get_text().strip() == "Mark read" and b1.kids[0].tag == "svg"
    assert not gg[1].by_cls("p-grpa")  # an empty list renders no cell


def test_segments_style_toggle_buttons():
    css = (THEME / "static" / "css" / "parts.css").read_text(encoding="utf-8")
    assert ".p-seg>:is(a,button){" in css and "button[aria-pressed=true]" in css
    session = (THEME / "static" / "css" / "session.css").read_text(encoding="utf-8")
    assert ".c-view>button" not in session  # the session header's Detail | Compact is the shared .p-seg


def test_table_row_cells_and_details():
    t = _table()
    r = t.one("p-gr")
    assert r.attrs["role"] == "row" and r.attrs["data-row-id"] == "claude-1c70ee" and r.attrs["tabindex"] == "0"
    assert r.attrs["data-href"] == "/a/claude-1c70ee" and r.attrs["data-kind"] == "claude" and "open" in r.cls
    assert r.attrs["data-p-q"] == "claude-1c70ee fixproj"  # lower-cased for the client filter
    assert [k.attrs.get("role") for k in r.kids] == ["cell"] * len(r.kids)  # every direct child is a cell
    gc = ("gc-dot", "gc-nm", "gc-end", "gc-m", "gc-act")
    roles = [next((c for c in gc if c in k.cls), "-") for k in r.kids]
    assert roles == ["gc-dot", "gc-nm", "-", "gc-end", "-", "gc-m", "gc-act"]
    dot = r.one("gc-dot")
    assert dot.kids[0].attrs["aria-hidden"] == "true" and dot.one("vh").get_text() == "needs you"  # never colour only
    assert r.one("gc-nm").kids[0].kids[0].tag == "bdi"  # sfx: truncated from the left (L-4)
    assert r.one("gc-end").attrs["data-since"] == "100"
    btn = r.one("p-detb")
    det = t.one("p-det")
    assert btn.attrs["aria-expanded"] == "true" and "data-p-det-btn" in btn.attrs
    assert btn.attrs["aria-controls"] == det.attrs["id"] == "p-det-inbox-claude-1c70ee"
    assert det.attrs["role"] == "row" and det.attrs["data-p-det-for"] == "claude-1c70ee" and "hidden" not in det.attrs
    detc = det.kids[0]
    assert detc.attrs["role"] == "cell" and "p-detc" in detc.cls
    kv = det.one("p-kv")
    assert kv.tag == "dl" and [n.tag for n in kv.kids] == ["dt", "dd", "dt", "dd"] and "mono" in kv.kids[3].cls
    assert det.one("p-dacts").kids[0].tag == "button"


def test_row_defaults_and_lazy_details():
    t = dom('{% call table.row("$3") %}<span role="cell">x</span>{% endcall %}'
            '{{ table.det("tmux", "$3", src="/tmux/s3/windows") }}')
    r = t.one("p-gr")
    assert r.attrs["tabindex"] == "-1" and "data-href" not in r.attrs and "data-p-q" not in r.attrs
    det = t.one("p-det")
    assert "hidden" in det.attrs and det.attrs["data-p-det-src"] == "/tmux/s3/windows" and det.kids[0].kids == []


def test_name_as_the_rows_own_link():
    t = dom('{% call table.row("claude-1", href="/a/claude-1") %}'
            '{% call table.name("claude-1", href="/a/claude-1", sfx=true, row_link=true) %}{% endcall %}{% endcall %}'
            '{% call table.row("x", href="/x") %}{% call table.name("x", href="/x") %}{% endcall %}{% endcall %}')
    a, b = [n.kids[0].kids[0] for n in t.by_cls("p-gr")]
    assert a.tag == "a" and a.attrs["href"] == "/a/claude-1" and "data-row-link" in a.attrs  # #247: a real link
    assert a.attrs["tabindex"] == "-1" and {"nm", "sfx"} <= a.cls  # never a Tab stop: the row is the list's one
    assert b.tag == "a" and "data-row-link" not in b.attrs and "tabindex" not in b.attrs  # a plain link stays one
    css = (THEME / "static" / "css" / "table.css").read_text(encoding="utf-8")
    assert ".p-gr .gc-nm>a.nm[data-row-link]{color:inherit;text-decoration:none}" in css
    assert ".gc-nm>a.nm:focus-visible{outline-offset:-2px}" in css  # SV: the ring inside the clipped name cell


def test_table_empty_foot_hints():
    t = _table()
    e = t.one("p-empty")
    assert e.attrs["role"] == "row" and "data-p-fnone" in e.attrs and "hidden" in e.attrs
    assert e.kids[0].attrs["role"] == "cell"
    foot = t.one("p-foot")  # 5 of 5: no foot
    assert "Showing 1 of 31 agents" in foot.get_text() and foot.one("p-more").attrs["href"] == "?all=1"
    assert foot.one("p-more").get_text() == "Show all 31"
    hints = t.one("p-keys")
    assert hints.attrs["aria-hidden"] == "true"
    kbds = hints.find(lambda n: n.tag == "kbd")
    assert [k.attrs["data-k"] for k in kbds] == ["J", "K", "Enter"] and all(k.get_text() == "" for k in kbds)
    plain = dom('{% call table.empty() %}No rows. <a href="/x">Add</a>{% endcall %}').one("p-empty")
    assert "hidden" not in plain.attrs and plain.find(lambda n: n.tag == "a")


def test_table_foot_paging_and_notes():
    f = dom('{{ table.foot(text="Showing 51–100 of 900", newer="/l?page=1", older="/l?page=3", '
            'older_label="Load older (800 more)", notes=["Times local."], target="#l", push=false) }}').one("p-foot")
    links = f.find(lambda n: n.tag == "a")
    assert [a.get_text() for a in links] == ["Newer", "Load older (800 more)"]
    assert [a.attrs["hx-get"] for a in links] == ["/l?page=1", "/l?page=3"]
    assert all(a.attrs["hx-target"] == "#l" and "hx-push-url" not in a.attrs for a in links)
    assert f.get_text().count("·") == 3 and f.get_text().startswith("Showing 51–100 of 900")
    plain = dom('{{ table.foot(older="/l?page=2") }}').one("p-foot")  # no target: a plain link, no leading dot
    assert "·" not in plain.get_text() and "hx-get" not in plain.one("p-more").attrs
    assert "p-foot" not in render("{{ table.foot() }}{{ table.foot(3, 3, more_href='/x') }}")


# ---------------------------------------------------------------- ⋯ menu trigger
def test_menu_trigger_only_with_json_payload():
    items = [{"id": "tab", "label": "Open in a tab", "icon": "tabs", "key": "O", "href": "/a/x"}, {"sep": True},
             {"id": "kill", "label": "Kill <b> it's", "danger": True, "post": "/k", "vals": {"id": "a&b"},
              "typed": "x"}]
    src = render('{{ menu.menu(items, label="Actions for x") }}', items=items)
    t = Tree(src).root
    (b,) = t.kids
    assert b.tag == "button" and b.attrs["type"] == "button" and {"x-ib", "sm", "p-mbtn", "keep"} <= b.cls
    assert b.attrs["aria-haspopup"] == "menu" and b.attrs["aria-expanded"] == "false"
    assert b.attrs["aria-label"] == b.attrs["title"] == "Actions for x"
    assert json.loads(b.attrs["data-menu"]) == items
    assert "data-menu='" in src and "<b>" not in src  # single-quoted attribute, HTML-safe JSON
    assert not t.find(lambda n: n.attrs.get("role") in ("menu", "menuitem"))  # never its own popup
    assert "keep" not in Tree(render('{{ menu.menu([], keep=false) }}')).root.kids[0].cls


# ---------------------------------------------------------------- switch, select
def test_switch():
    t = dom('{{ switch.switch("unread", on=true, label="Unread", count=5) }}')
    lab = t.one("p-swl")
    assert lab.tag == "label"
    sw = lab.kids[0]
    assert sw.tag == "button" and sw.attrs["type"] == "button" and sw.attrs["role"] == "switch"
    assert sw.attrs["aria-checked"] == "true" and sw.attrs["data-p-sw"] == "unread" and "sw" in sw.cls
    hid = lab.find(lambda n: n.tag == "input")[0]
    assert (hid.attrs["type"], hid.attrs["name"], hid.attrs["value"]) == ("hidden", "unread", "1")
    assert "Unread" in lab.get_text() and lab.one("c").get_text() == "5"
    t = dom('{{ switch.switch("en", label="Enabled", hide_label=true, disabled=true, why="Not installed",'
            ' attrs={"hx-post": "/x"}) }}')
    lab = t.one("p-swl")
    sw = lab.kids[0]
    assert sw.attrs["aria-checked"] == "false" and "disabled" in sw.attrs and sw.attrs["hx-post"] == "/x"
    assert lab.attrs["title"] == "Not installed" and lab.one("vh").get_text() == "Enabled"
    assert lab.find(lambda n: n.tag == "input")[0].attrs["value"] == "0"


def test_select():
    opts = [("state", "Group: state"), {"value": "project", "text": "Group: project"},
            {"value": "x", "text": "Off", "disabled": True}]
    t = dom('{{ select.select("grp", opts, value="project", label="Group by", attrs={"hx-get": "/i"}) }}', opts=opts)
    s = t.find(lambda n: n.tag == "select")[0]
    assert "sel" in s.cls and s.attrs["name"] == "grp" and s.attrs["aria-label"] == "Group by"
    assert s.attrs["hx-get"] == "/i"
    o = s.kids
    assert [x.attrs["value"] for x in o] == ["state", "project", "x"]
    assert ["selected" in x.attrs for x in o] == [False, True, False] and "disabled" in o[2].attrs


# ---------------------------------------------------------------- static rules for the parts
def test_parts_templates_use_known_icons_and_no_inline_style():
    sprite = set(re.findall(r'<symbol id="([\w-]+)"', (THEME / "static" / "icons.svg").read_text(encoding="utf-8")))
    for p in PARTS:
        text = (TPL / "parts" / f"{p}.html").read_text(encoding="utf-8")
        code = re.sub(r"\{#.*?#\}", " ", text, flags=re.S)
        used = set(re.findall(r"icons\.svg#([\w-]+)|\{\{\s*_ic\s*\}\}([\w-]+)", code))
        assert {a or b for a, b in used} <= sprite, p
        assert not re.search(r"\sstyle\s*=", code), p


def test_listkeys_has_no_keydown_listener_of_its_own():
    src = (JS / "listkeys.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    assert "keydown" not in code and "keys.register" in code
    for f in ("table.js", "filter.js"):
        assert "'keydown'" not in (JS / f).read_text(encoding="utf-8"), f  # Esc / is layers.js / keys.js


# ---------------------------------------------------------------- listkeys.js clicks (node, a stub DOM)
# A list with one row whose name is its row link (a[data-row-link]) and a plain link (a.x) in another cell. The stub's
# closest() matches the few selectors listkeys.js uses; the long control selector matches a row link only when it does
# not exclude [data-row-link].
LISTKEYS_HARNESS = r"""
const L = [], fired = [], went = [];
function match(n, s) {
  if (s === '[data-row-id]') return n.kind === 'row';
  if (s === '[data-list]') return n.kind === 'list';
  if (s === 'a[data-row-link]') return n.kind === 'rowlink';
  if (s === '.p-det') return n.kind === 'det';
  if (s.indexOf('button') >= 0) return n.kind === 'a' || (n.kind === 'rowlink' && s.indexOf(':not([data-row-link])') < 0);
  return false;
}
const mk = (kind, parent, attrs) => ({ kind: kind, parent: parent, attrs: attrs || {}, tabIndex: -1,
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }, hasAttribute(k) { return k in this.attrs; },
  closest(s) { let n = this; while (n) { if (match(n, s)) return n; n = n.parent; } return null; },
  contains(c) { let n = c; while (n) { if (n === this) return true; n = n.parent; } return false; },
  focus() { document.activeElement = this; }, scrollIntoView() {}, getClientRects() { return [1]; },
  querySelector() { return null; },
  dispatchEvent(ev) { fired.push(ev.type + ':' + ev.detail.id + ':' + ev.detail.key); return true; } });
const list = mk('list', null, { 'data-list': 'inbox', 'data-key-enter': 'event:i:open', 'data-key-o': 'event:i:tab' });
const row = mk('row', list, { 'data-row-id': 'claude-1', 'data-href': '/a/claude-1' });
const link = mk('rowlink', row, { href: '/a/claude-1', 'data-row-link': '' });
const other = mk('a', row, { href: '/term/s1' });
const plist = mk('list', null, { 'data-list': 'projects' });
const prow = mk('row', plist, { 'data-row-id': 'alpha', 'data-href': '/p/alpha' });
const plink = mk('rowlink', prow, { href: '/p/alpha', 'data-row-link': '' });
globalThis.CustomEvent = function (t, o) { this.type = t; this.detail = o.detail; };
globalThis.CSS = { escape: (x) => x };
globalThis.window = { pm7: { $$: (s, l) => (l === list ? [row] : l === plist ? [prow] : []), ready() {} },
  location: { assign: (h) => went.push(h) } };
globalThis.document = { addEventListener: (t, f) => L.push([t, f]), activeElement: null, body: {} };
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
function fire(type, target, o) {
  const e = Object.assign({ type: type, target: target, button: 0, detail: 1, metaKey: false, ctrlKey: false,
    shiftKey: false, altKey: false, defaultPrevented: false }, o || {});
  e.preventDefault = () => { e.defaultPrevented = true; };
  L.filter((x) => x[0] === type).forEach((x) => x[1](e));
  return e.defaultPrevented;
}
const r = {
  plain: fire('click', link), focus: document.activeElement === row,
  ctrl: fire('click', link, { ctrlKey: true }), meta: fire('click', link, { metaKey: true }),
  shift: fire('click', link, { shiftKey: true }), second: fire('click', link, { detail: 2 }),
  other: fire('click', other), dbl: fire('dblclick', link), href: fire('click', plink) };
console.log(JSON.stringify({ r: r, fired: fired, went: went }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_listkeys_row_link_plain_click_is_the_row_modified_clicks_are_the_browsers():
    out = subprocess.run(["node", "-e", LISTKEYS_HARNESS, str(JS / "listkeys.js")], capture_output=True, text=True,
                         check=True)
    got = json.loads(out.stdout)
    r = got["r"]
    assert r["plain"] is True and r["focus"] is True  # ↵ action ran (an event), the link not followed, row focused
    assert r["ctrl"] is r["meta"] is r["shift"] is False  # #247: left to the browser (a new tab / window)
    assert r["second"] is True  # a double-click's second click never follows the link
    assert r["other"] is False  # any other link in the row does its own thing
    assert r["href"] is True and got["went"] == ["/p/alpha"]  # enter = href: the row's own navigation
    assert got["fired"] == ["i:open:claude-1:enter", "i:tab:claude-1:o"]  # click = enter once, dblclick = o


# ---------------------------------------------------------------- keys.js register() and filter.js match (node)
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

KEYS_HARNESS = r"""
const L = [];
const pm7 = { isApple: false, $$: () => [], $: () => null, ready() {}, isField: (el) => !!(el && el.field),
              layers: { top: () => null, sync() {}, soft() {} }, store: { set() {} }, toast() {} };
globalThis.window = { pm7: pm7, location: { assign() {} } };
globalThis.document = { addEventListener: (t, f, o) => L.push([t, f, o]), getElementById: () => null,
  querySelector: () => null, documentElement: { getAttribute: () => null, classList: { contains: () => false } } };
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
const kd = L.filter((x) => x[0] === 'keydown').map((x) => x[1]);
function press(o) {
  const e = Object.assign({ key: '', code: '', metaKey: false, ctrlKey: false, altKey: false, shiftKey: false,
    repeat: false, isComposing: false, defaultPrevented: false, target: {} }, o);
  e.preventDefault = () => { e.defaultPrevented = true; };
  kd.forEach((f) => f(e));
  return e.defaultPrevented;
}
const log = [];
let on = false;
const off = pm7.keys.register({ id: 'list', group: 'This list', active: () => on }, [
  { id: 'n', label: 'Next row', keys: ['J'], run: () => { log.push('J'); } },
  { id: 'o', label: () => 'Open ' + (on ? 'aside' : '?'), keys: ['Enter'], run: () => { log.push('Enter'); return false; } },
  { id: 'r', label: 'Read', keys: ['E'], run: () => { log.push('E'); }, active: () => false }]);
"""


def run_keys(code: str):
    out = subprocess.run(["node", "-e", KEYS_HARNESS + code, str(JS / "keys.js")], capture_output=True, text=True,
                         check=True)
    return json.loads(out.stdout)


@needs_node
def test_keys_register_dispatches_only_while_active():
    got = run_keys("""
      const r = [press({ code: 'KeyJ', key: 'j' })];
      on = true;
      r.push(press({ code: 'KeyJ', key: 'j' }), press({ code: 'KeyJ', key: 'j', target: { field: true } }),
             press({ key: 'Enter', code: 'Enter' }), press({ code: 'KeyE', key: 'e' }));
      const inTable = pm7.keys.table.filter((t) => t.scope).map((t) => t.id);
      const cmds = pm7.keys.commands().map((c) => c.id);
      off();
      r.push(press({ code: 'KeyJ', key: 'j' }));
      console.log(JSON.stringify({ r, log, inTable, cmds, after: pm7.keys.table.filter((t) => t.scope).length }));
    """)
    # inactive: nothing; active: J runs + prevented; J in a text field: nothing; Enter runs but returns false (not
    # prevented: the focused control keeps it); E's own active() is false; after unregister nothing
    assert got["r"] == [False, True, False, False, False, False]
    assert got["log"] == ["J", "Enter"]
    assert got["inTable"] == ["n", "o", "r"] and not {"n", "o", "r"} & set(got["cmds"])  # never a > command
    assert got["after"] == 0


@needs_node
def test_keys_labels_for_list_keys():
    got = run_keys("console.log(JSON.stringify(['Enter', 'ArrowRight', 'ArrowLeft', 'J', 'mod+Enter']"
                   ".map(pm7.keys.label)));")
    assert got == ["↵", "→", "←", "J", "Ctrl+↵"]


@needs_node
def test_filter_match_every_word():
    harness = r"""
      globalThis.window = { pm7: { ready() {}, $$: () => [], $: () => null } };
      globalThis.document = { addEventListener() {}, querySelector: () => null, getElementById: () => null };
      eval(require('fs').readFileSync(process.argv[1], 'utf8'));
      const m = window.pm7.filter.match;
      console.log(JSON.stringify([m('claude-1c70ee fixproj dev', 'FIX  cla'), m('claude-1c70ee fixproj', 'codex'),
                                  m('anything', '   ')]));
    """
    out = subprocess.run(["node", "-e", harness, str(JS / "filter.js")], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [True, False, True]


def test_html_escapes_in_values():
    t = render('{{ head.head("<x>", stats=[{"text": "a&b", "cls": "warn"}]) }}')
    assert "&lt;x&gt;" in t and "a&amp;b" in t and "<x>" not in t


# ---------------------------------------------------------------- core.js pm7.why (node): failed POST messages
WHY = r"""
globalThis.document = { readyState: 'complete', querySelector: () => null, addEventListener() {} };
globalThis.window = globalThis;
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
const R = (ct, body, status) => new Response(body, { status: status, headers: { 'Content-Type': ct } });
(async () => {
  const out = [];
  const r = R('application/json', JSON.stringify({ detail: 'unknown agent' }), 400);
  out.push(await pm7.why(r, 'Fork'), await r.json());  // the caller can still read the body
  out.push(await pm7.why(R('application/json', JSON.stringify({ detail: [{ msg: 'field required' }] }), 422)));
  out.push(await pm7.why(R('text/plain; charset=utf-8', 'bad\n  key ' + 'x'.repeat(40) + ' ' + 'y'.repeat(300), 409),
                         'Exit'));
  out.push(await pm7.why(R('text/html', '<h1>Not Found</h1>', 404), 'Open'), await pm7.why(null, 'Open'));
  console.log(JSON.stringify(out));
})();
"""


@needs_node
def test_why_gives_the_servers_message_short_and_masked():
    out = json.loads(subprocess.run(["node", "-e", WHY, str(JS / "core.js")], capture_output=True, text=True,
                                    check=True).stdout)
    assert out[0] == "Fork: unknown agent" and out[1] == {"detail": "unknown agent"}
    assert out[2] == "Failed: field required"
    assert out[3].startswith("Exit: bad key … ") and len(out[3]) <= len("Exit: ") + 160 and "\n" not in out[3]
    assert out[4:] == ["Open (404)", "Open: no answer"]  # an HTML error page is never shown


def test_menu_post_errors_use_the_servers_message():
    src = (JS / "parts.js").read_text(encoding="utf-8")
    assert "pm7.fail(" in src and "'Failed (' + r.status" not in src
