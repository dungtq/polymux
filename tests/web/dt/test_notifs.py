"""dt Notifications page and bell (PRD.md §6.7, LAYOUT.md §4-5, README A2): ntfy.html / notifications.html, the list
fragment _ntfy.html (the core's GET /ntfy/panel, also the bell's data) with its view model pm.web.dt.ntfy, and the
bell button in shell/_top.html, rendered by the app with the real dt templates over a stubbed ntfy feed. Plus the
static rules of css/notifs.css and js/notifs.js (period names follow the core's TABS; no own keydown or polling)."""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from html import unescape
from pathlib import Path

import jinja2
import pytest

from pm.core.models import NtfyItem
from pm.notify import feed as ntfy_feed
from pm.web import ntfypanel, themes
from pm.web.dt import DtTheme, ntfy, shell
from pm.web.dt.stores import TabState

from .conftest import ORIGIN, make_env, make_snap

THEME = themes.HERE / "dt"
TPL = THEME / "templates"
CSS = THEME / "static" / "css" / "notifs.css"
JS = THEME / "static" / "js" / "notifs.js"
DAY = 86400


# ---------------------------------------------------------------- app with the real dt templates, stub feed
@pytest.fixture
def feed(monkeypatch):
    state = {"items": [], "ok": True}
    monkeypatch.setattr(ntfy_feed, "_snapshot", lambda settings: (list(state["items"]), state["ok"]))
    return state


@pytest.fixture
def nenv(settings, snap, feed):
    class RealDt(DtTheme):
        def themes(self):
            return [themes.Theme(name="dt", templates=TPL, static=THEME / "static", label="dt")]

    (settings.dev_root / "alpha").mkdir()
    snap.value = make_snap(projects=())
    return make_env(settings, RealDt())


def _items(dev: Path) -> list[NtfyItem]:
    p, now = str(dev / "alpha"), time.time()
    return [
        NtfyItem("r1", now - 60, "pm recovered -- h: disk /home", "back to normal", ("pm", p), project=p),
        NtfyItem("a1", now - 240, "pm alert -- h: disk /home 95%", "disk high", ("pm", p), project=p, priority=4),
        NtfyItem("g1", now - 300, "alpha is waiting (agent-claude)", "done, tests pass", (p, "waiting"), project=p,
                 click=ORIGIN + "/a/claude-aaa111"),
        NtfyItem("g2", now - 400, "alpha is waiting (agent-claude)", "done, tests pass", (p, "waiting"), project=p,
                 click=ORIGIN + "/a/claude-aaa111"),
        NtfyItem("y1", now - DAY - 60, "codex-bbb222 - Yesterday's job", "ok", (p,), project=p, priority=5),
        NtfyItem("e1", now - 9 * DAY, "devin-ccc333 - Old one", "old", (p,), project=p),
    ]


def _rows(html: str) -> list[tuple[str, set[str]]]:
    return [(rid, set(cls.split())) for cls, rid in
            re.findall(r'<div class="p-gr ([^"]*)" role="row" data-row-id="([^"]+)"', html)]


def _menu(html: str, rid: str) -> list[dict]:
    i = html.index(f'data-row-id="{rid}"')
    m = re.search(r"data-menu='([^']*)'", html[i:])
    return json.loads(unescape(m.group(1)))


# ---------------------------------------------------------------- the list (GET /ntfy/panel)
def test_panel_is_one_table_with_day_bands_in_order(nenv, feed):
    feed["items"] = _items(nenv.s.dev_root)
    html = nenv.c.get("/ntfy/panel?scope=all").text
    assert html.count('role="table"') == 1 and 'id="n-list"' in html and 'data-list="notifs"' in html
    groups = re.findall(r'data-p-grp="([^"]+)"', html)
    assert groups == ["today", "yesterday", "earlier"]  # no pins yet: no Pinned band
    assert 'data-key-e="menu:read"' in html and 'data-key-p="menu:pin"' in html
    rows = dict(_rows(html))
    assert len(rows) == 5  # g1 + g2 fold into one row
    sys_rows = [r for r, c in rows.items() if "n-system" in c]
    assert len(sys_rows) == 2 and all("n-agent" in c for r, c in rows.items() if r not in sys_rows)
    assert all({"n-pp", "n-p-all"} <= c for c in rows.values())  # every scope-all row carries its periods
    assert sum("n-p-today" in c for c in rows.values()) == 3
    ids = Counter(re.findall(r'\sid="([^"]+)"', html))
    assert not [i for i, n in ids.items() if n > 1], ids
    assert "style=" not in html and "<script" not in html


def test_rows_show_ptag_repeats_and_recovery(nenv, feed):
    feed["items"] = _items(nenv.s.dev_root)
    html = nenv.c.get("/ntfy/panel?scope=all").text
    assert re.search(r'class="chip n-pri warn" title="Priority 4">P4<', html)
    assert re.search(r'class="chip n-pri bad" title="Priority 5">P5<', html)
    assert "P3<" not in html  # the default priority has no tag
    assert re.search(r'class="n-rep"[^>]*>×2<', html)
    assert re.search(r'class="n-rec ok">recovered after 3m<', html)
    # the details hold the message and the facts; the message never goes into the filter text
    assert "done, tests pass" in html and "Recovered" in html
    assert not re.search(r'data-p-q="[^"]*tests pass', html)


def test_row_menu_items_post_through_the_core_routes(nenv, feed):
    feed["items"] = _items(nenv.s.dev_root)
    html = nenv.c.get("/ntfy/panel?scope=all").text
    rid = next(r for r, c in _rows(html) if "n-system" in c and "n-p-today" in c)
    items = {i["id"]: i for i in _menu(html, rid) if "id" in i}
    assert set(items) >= {"det", "read", "pin", "mute"}
    read = items["read"]
    assert read["post"] in ("/inbox/read", "/inbox/mark-unread") and read["vals"] == {"key": rid}
    assert read["refresh"] == "#n-panel" and read["key"] == "E"
    assert items["pin"]["post"] == "/ntfy/pin" and items["pin"]["key"] == "P"
    mute = items["mute"]
    assert mute["post"] == "/ntfy/mute" and mute["vals"]["title"].startswith("pm ") and mute["confirm"]
    assert mute["note"] == "24 h"


def test_mark_unread_and_pin_show_in_the_list(nenv, feed):
    feed["items"] = _items(nenv.s.dev_root)
    html = nenv.c.get("/ntfy/panel?scope=all").text
    rid = next(r for r, c in _rows(html) if "n-p-today" in c and "n-agent" in c)
    nenv.post("/inbox/read", key=rid)
    assert "un" not in dict(_rows(nenv.c.get("/ntfy/panel?scope=all").text))[rid]
    assert nenv.post("/inbox/mark-unread", key=rid).status_code == 200
    html = nenv.c.get("/ntfy/panel?scope=all").text
    assert "un" in dict(_rows(html))[rid]
    read = next(i for i in _menu(html, rid) if i.get("id") == "read")
    assert read["post"] == "/inbox/read" and read["label"] == "Mark read"
    pin_id = next(i for i in _menu(html, rid) if i.get("id") == "pin")["vals"]["id"]
    assert nenv.post("/ntfy/pin", id=pin_id).status_code == 200
    html = nenv.c.get("/ntfy/panel?scope=all").text
    assert re.findall(r'data-p-grp="([^"]+)"', html)[0] == "pinned"  # Pinned on top
    rows = dict(_rows(html))
    assert f"pin:{pin_id}" in rows and rid in rows  # the pin also stays in its day
    assert next(i for i in _menu(html, f"pin:{pin_id}") if i.get("id") == "pin")["post"] == "/ntfy/unpin"
    assert not any(i.get("id") == "read" for i in _menu(html, f"pin:{pin_id}"))  # a pin has no read state
    assert 'aria-label="Pinned"' in html


def test_day_band_mark_read_button_and_empty_states(nenv, feed):
    feed["items"] = _items(nenv.s.dev_root)
    html = nenv.c.get("/ntfy/panel?scope=all").text
    assert html.count("data-n-dayread=") == 3 and 'data-n-dayread="today"' in html
    assert "data-n-none" in html and "data-p-fnone" in html
    feed["items"], feed["ok"] = [], False
    html = nenv.c.get("/ntfy/panel?scope=all").text
    assert "No messages yet (ntfy is unreachable)" in html and 'class="n-warn"' in html


# ---------------------------------------------------------------- the page (GET /ntfy, /notifications)
def _pg(html: str) -> str:
    return re.search(r'<div class="n-pg"[^>]*>', html).group(0)


def test_ntfy_page_skeleton_and_defaults(nenv, feed):
    feed["items"] = _items(nenv.s.dev_root)
    html = nenv.c.get("/ntfy").text
    pg = _pg(html)
    assert 'data-n-src="all"' in pg and 'data-n-per="week"' in pg and "data-n-un" not in pg
    assert '<main id="main" class="x-main ">' in html  # a ported page: no classic compat class
    order = [html.index(s) for s in ('class="p-head', 'class="p-tools', 'id="n-panel"', 'class="p-keys')]
    assert order == sorted(order)
    tools = html[html.index('class="p-tools'):html.index('id="n-panel"')]
    order = [tools.index(s) for s in ('class="p-fbox', 'class="p-seg', 'class="p-swl', 'select class="sel')]
    assert order == sorted(order)  # filter box first, then segments, switch, select (LAYOUT §4)
    assert re.findall(r'data-p-seg="(\w+)"', tools) == ["all", "agent", "system", "pinned"]
    assert re.search(r'data-p-seg="all" aria-current="true"', tools)
    assert 'data-p-filter-rows="#n-list"' in tools
    panel = re.search(r'<div id="n-panel"[^>]*>', html).group(0)
    assert 'hx-get="/ntfy/panel?scope=all"' in panel and 'hx-swap="innerHTML"' in panel
    assert re.search(r'hx-trigger="pm:inbox from:document throttle:2s, pm:refresh from:document, pm7:refresh, '
                     r'pm7:resync from:document"', panel)
    assert "every " not in panel  # V7-CODE-7: the stream's inbox event, no timed poll
    assert 'id="n-list"' in html  # the first list is server-rendered (L-1)
    assert "data-n-readall" in html and 'id="n-head-stats"' in html


def test_notifications_page_preselects_system(nenv, feed):
    feed["items"] = _items(nenv.s.dev_root)
    html = nenv.c.get("/notifications").text
    pg = _pg(html)
    assert 'data-n-src="system"' in pg and 'data-n-dsrc="system"' in pg
    assert 'data-n-per="week"' in pg and 'data-n-dper="week"' in pg  # one saved view: only Source is preselected
    assert re.search(r'data-p-seg="system" aria-current="true"', html)
    assert re.search(r'<div id="n-panel"[^>]*hx-trigger="load, pm:inbox', html)  # the all-scope list at once
    assert 'aria-current="page"' in html and 'data-nav="notifications"' in html


@pytest.mark.parametrize("query, src, per, un", [
    ("?src=pinned&per=today&un=1", "pinned", "today", True),
    ("?src=agent&per=last_month", "agent", "last_month", False),
    ("?src=bogus&per=never&un=yes", "all", "week", False),
])
def test_page_state_comes_from_the_url(nenv, feed, query, src, per, un):
    html = nenv.c.get("/ntfy" + query).text
    pg = _pg(html)
    assert f'data-n-src="{src}"' in pg and f'data-n-per="{per}"' in pg and ("data-n-un" in pg) is un
    assert re.search(rf'<option value="{per}" selected>', html)
    assert re.search(r'data-p-sw="un"', html) and (f'aria-checked="{"true" if un else "false"}"' in html)


def test_periods_follow_the_core_tabs():
    assert ntfy.PERIODS == ntfypanel.TABS
    keys = [k for k, _ in ntfypanel.TABS]
    css = CSS.read_text(encoding="utf-8")
    for k in keys:
        if k != "all":
            assert f"[data-n-per={k}] .n-row.n-pp:not(.n-p-{k})" in css, k


# ---------------------------------------------------------------- the bell (reads the list: newest 6, every source)
def test_panel_rows_carry_the_bell_data(nenv, feed):
    feed["items"] = _items(nenv.s.dev_root)
    html = nenv.c.get("/ntfy/panel?scope=all").text
    box = re.search(r'<div class="n-in" data-n-scope="all" data-n-total="(\d+)" data-n-unread="(\d+)">', html)
    assert box and box.group(1) == "5" and box.group(2) == "5"
    rows = re.findall(r'<div class="p-gr [^"]*" role="row" data-row-id="[^"]+"[^>]*>', html)
    assert len(rows) == 5 and all('data-n-abs="' in r for r in rows)
    assert sorted(re.findall(r'data-n-pri="(\d)"', html)) == ["4", "5"]  # the default priority 3 has none
    assert re.findall(r'data-n-click="([^"]+)"', html) == [ORIGIN + "/a/claude-aaa111"]  # the agent message's link


def test_bell_js_reads_the_list_newest_six_unread_first():
    js = JS.read_text(encoding="utf-8")
    assert "pm7.fetch('/ntfy/panel?scope=all')" in js and "fetch('/ntfy/bell" not in js  # the core bell: system only
    assert "const BELL_N = 6;" in js and "un.concat(rows.filter((x) => !x.un)).slice(0, BELL_N)" in js
    assert "'/ntfy?open=' + encodeURIComponent(key)" in js and "href: '/ntfy'" in js
    assert "!r.closest('[data-p-grp=pinned]')" in js  # a pin is not a new message
    assert not (TPL / "_bell.html").exists()


def _top(count=None) -> str:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TPL), autoescape=True)
    sh = shell.build(make_snap(), TabState(), "/")
    if count is not None:
        sh = sh.__class__(**{**sh.__dict__, "nav": [n.__class__(**{**n.__dict__, "count": count})
                                                     if n.key == "notifications" else n for n in sh.nav]})
    return env.get_template("shell/_top.html").render(shell=sh, cur_tab=None, csrf="tok", dev_banner=False)


def test_top_bar_bell_is_a_menu_button():
    html = _top()
    b = re.search(r"<button[^>]*data-n-bell[^>]*>", html).group(0)
    assert 'class="x-ib x-bell"' in b and 'aria-haspopup="menu"' in b and 'aria-expanded="false"' in b
    assert 'aria-label="Notifications"' in b and 'title="Notifications"' in b
    assert "notifs." not in html  # base.html loads css/notifs.css and js/notifs.js with the shell (test_theme_static)
    assert 'href="/notifications"' not in html  # no longer a link: the bottom nav's Alerts is the page link
    b = re.search(r"<button[^>]*data-n-bell[^>]*>.*?</button>", _top(3), re.S).group(0)
    assert 'aria-label="Notifications, 3 unread"' in b and '<span class="b" aria-hidden="true">3</span>' in b


# ---------------------------------------------------------------- static rules
def test_notifs_js_uses_the_shared_helpers_only():
    js = JS.read_text(encoding="utf-8")
    assert len(js.splitlines()) <= 500 and max(map(len, js.splitlines())) <= 120
    assert "addEventListener('keydown'" not in js and "setInterval" not in js  # keys: listkeys; live: the poll
    assert not re.search(r"(?<![.\w])fetch\(", js)  # only pm7.fetch (CSRF)
    for hook in ("pm7.list.on('notifs'", "pm7.menus.run(", "pm7.menu(btn", "'/ntfy/panel?scope=all'",
                 "'/inbox/read-system'", "'/inbox/read-all'", "'/inbox/read'", "pm7.store.set('notifs.' + k",
                 "pm7.store.get('notifs.src'"):
        assert hook in js, hook
    assert "pm7.toast(" in js and ".innerHTML = h" in js  # recap html comes from our own server's fragment


def test_notifs_css_is_tokens_only_and_n_prefixed():
    css = re.sub(r"/\*.*?\*/", " ", CSS.read_text(encoding="utf-8"), flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|z-index", css)
    classes = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))
    own = {c for c in classes if c.startswith("n-")}
    assert own and not own & {"n-main", "n-proj", "n-recap", "n-detail", "n-acts", "n-msg", "n-dot", "n-age"}


def test_mark_all_read_only_with_something_unread():
    css = re.sub(r"/\*.*?\*/", " ", CSS.read_text(encoding="utf-8"), flags=re.S)
    assert ".n-pg:not(:has(.n-row.un)) [data-n-readall]" in css  # first paint
    assert ".n-pg[data-n-src=system]:not(:has(.n-row.n-system.un)) [data-n-readall]" in css
    js = JS.read_text(encoding="utf-8")
    assert "if (all) all.hidden = !(st.src === 'system' ? sysUn : un);" in js


def test_saved_view_is_one_for_both_urls():
    """D5: Source / Unread / Period are saved under pm.v7.notifs.* by user changes only; a URL with its own state wins;
    /notifications (data-n-dsrc=system) keeps Source System for that load."""
    js = JS.read_text(encoding="utf-8")
    restore = js[js.index("function restore(p)"):js.index("function apply(p, change, user)")]
    assert "if (SAVED.some((k) => u.searchParams.has(k))) return;" in restore
    assert "p.getAttribute('data-n-dsrc') !== 'system'" in restore and "apply(p, ch)" in restore  # not saved
    assert js.count(", true);") >= 4  # segment click, reset link, Unread switch, period select save


# ---------------------------------------------------------------- view model (no app)
class _Item:
    def __init__(self, **kw):
        self.id, self.title, self.message, self.priority = "m1", "pm alert -- disk", "full", 3
        self.time, self.tags = 1000.0, ("pm",)
        self.__dict__.update(kw)


def _row(**kw):
    r = {"item": _Item(), "owner": None, "title": "pm alert -- disk", "key": "k1", "unread": True, "day": "today",
         "src": "system", "q": "pm alert", "abs": "2026-10-15 10:00", "ago": "5m ago", "proj_name": None,
         "tag_label": "pm", "tag_raw": "pm", "n": 1, "recovered_after_s": None, "click": "", "p": "today week all"}
    r.update(kw)
    return r


def test_row_view_model():
    r = ntfy.row(_row(), pin=False)
    assert r.cls == "n-row n-system un n-pp n-p-today n-p-week n-p-all" and r.who == "pm" and r.age == "5m"
    assert r.meta == "pm · pm" and r.since == 1000 and r.attrs == {"data-n-abs": "2026-10-15 10:00"}
    ids = [i.get("id") for i in r.menu]
    assert ids == ["det", None, "read", "pin", None, "mute"] and r.menu[1] == {"sep": True}
    pin = ntfy.row(_row(unread=False, p=""), pin=True)
    assert pin.cls == "n-row n-system" and pin.pinned and "read" not in [i.get("id") for i in pin.menu]
    long = ntfy.row(_row(title="x" * 80), pin=False)
    assert long.menu_label == "Actions for " + "x" * 39 + "…"


def test_page_state_and_never_raises():
    class Req:
        query_params = {"src": "agent", "per": "nope", "un": "1", "q": "disk"}

    pg = ntfy.page({"nav": "ntfy", "request": Req()})
    assert (pg.src, pg.per, pg.un, pg.q, pg.dsrc) == ("agent", "week", True, "disk", "all")
    assert [x["cur"] for x in pg.segs] == [False, True, False, False] and pg.segs[1]["href"] == "/ntfy?src=agent"
    sys_pg = ntfy.page({"nav": "notifications", "request": None})
    assert (sys_pg.src, sys_pg.dsrc, sys_pg.segs[2]["href"]) == ("system", "system", "/notifications?src=system")
    env = jinja2.Environment()
    env.globals["dt_ntfy"] = ntfy.dt_ntfy
    assert env.from_string('{{ dt_ntfy("nope") is none }}').render() == "True"
