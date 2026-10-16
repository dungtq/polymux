import re
from pathlib import Path

from helpers import login, ntfy_item
from helpers import ntfy_env as env  # noqa: F401  (fixture)

from pm.core.models import NtfyItem
from pm.web import ntfypanel
from pm.web.render import TEMPLATE_DIR
from pm.web.routes import ntfy as routes_ntfy


def test_auth_required(env):
    s, c, st, dev = env
    assert c.get("/ntfy/panel", follow_redirects=False).status_code in (303, 401)


def _hidden(t):
    return t.count('class="ntfy-row e-more"')


def test_inbox_renders_up_to_50_rows_default_visible_rest_e_more(env):
    s, c, st, dev = env
    login(s, c)
    st["items"] = [ntfy_item(i, str(dev / "alpha")) for i in range(60, 0, -1)]
    r = c.get("/ntfy/panel")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.text.count("<details class=\"ntfy-row") == 50
    assert _hidden(r.text) == 40  # default 10 stay visible
    assert "set-cookie" not in r.headers and 'class="sel sel-show"' not in r.text
    assert 'href="/p/alpha"' in r.text
    assert 'class="small ntfy-toggle" aria-pressed="false">Expand' in r.text
    # old n/all links still work and change nothing
    assert c.get("/ntfy/panel?n=5&all=1").text.count("<details class=\"ntfy-row") == 50


def test_few_rows_have_no_hidden_toggle(env):
    s, c, st, dev = env
    login(s, c)
    st["items"] = [ntfy_item(i, str(dev / "alpha")) for i in range(3)]
    t = c.get("/ntfy/panel").text
    assert _hidden(t) == 0 and "ntfy-toggle" in t and "ntfy-toggle\" aria-pressed=\"false\" hidden" in t


def test_filter_text_is_name_type_only_title_project_owner_never_message(env):
    s, c, st, dev = env
    login(s, c)
    st["items"] = [ntfy_item(1, str(dev / "alpha"), title="Build DONE", message="Deploy <ok>")]
    t = c.get("/ntfy/panel").text
    m = re.search(r'data-q="([^"]*)"', t)
    assert m and "build done" in m.group(1) and "alpha" in m.group(1)
    assert "deploy" not in m.group(1) and "&lt;ok" not in m.group(1)  # message body is not filterable
    assert "Deploy &lt;ok&gt;" in t  # ...but it is still shown in the detail
    assert m.group(1) == m.group(1).lower()


def test_project_scope_gone(env):
    s, c, st, dev = env
    login(s, c)
    assert c.get("/ntfy/panel?scope=project&project=alpha").status_code == 404
    assert c.get("/ntfy/panel?scope=project").status_code == 404
    assert c.get("/ntfy/panel?scope=bogus").status_code == 404


def test_all_scope_and_page(env):
    s, c, st, dev = env
    p = str(dev / "alpha")
    assert c.get("/ntfy", follow_redirects=False).status_code in (303, 401)
    login(s, c)
    st["items"] = [ntfy_item(1, p, title="agent-msg"),
                   NtfyItem("s1", 2.0, "pm alert -- h", "m", tags=("pm",), click="", project=p)]
    t = c.get("/ntfy/panel?scope=all").text
    assert "agent-msg" in t and "pm alert" in t and 'id="ntfy-tools-all"' not in t
    r = c.get("/ntfy")
    assert r.status_code == 200 and 'id="ntfy-all"' in r.text and "agent-msg" in r.text
    assert 'class="filterbar" id="ntfy-tools-all"' in r.text and 'class="ntfy-q"' not in r.text
    assert 'hx-get="/ntfy/panel?scope=all"' in r.text
    bar = re.search(r'<div class="filterbar" id="ntfy-tools-all".*?</div>', r.text, re.S).group(0)
    assert bar.count('<select class="ntfy-n"') == 1 and bar.index("data-filter") < bar.index("sel-show")
    assert bar.count("<input") == 1 and 'placeholder="Filter..."' in bar and "<button" in bar  # box only; no Filter button
    assert re.search(r'data-filter[^>]*>\s*<button type="button" class="fbox-x"', bar)
    foot = re.search(r'<footer class="showbar ntfy-foot".*?</footer>', r.text, re.S).group(0)
    opts = lambda h: re.findall(r'<option value="(\w+)"', h)
    assert opts(bar) == opts(foot) == ["10", "20", "50", "all"]
    assert r.text.count('<select class="ntfy-n"') == 2


def test_states(env):
    s, c, st, dev = env
    login(s, c)
    assert "No ntfy messages" in c.get("/ntfy/panel").text
    st["ok"] = False
    assert "ntfy unreachable" in c.get("/ntfy/panel").text
    st["items"] = [ntfy_item(1, str(dev / "alpha"))]
    assert "showing cached" in c.get("/ntfy/panel").text


def test_escape_mask_noinline(env):
    s, c, st, dev = env
    login(s, c)
    st["items"] = [ntfy_item(1, str(dev / "alpha"), title="<script>alert(1)</script>",
                      message="<img src=x onerror=1>\nline2")]
    t = c.get("/ntfy/panel").text
    assert "<script>" not in t and "&lt;script&gt;" in t and "<img" not in t
    assert not re.search(r"<style|\sstyle=|\sonclick=|<script(?![^>]*\ssrc=)", t)


def test_click_rule():
    f = ntfypanel.safe_click
    base = "https://pm.example.test"
    assert f("https://pm.example.test/a/claude-1", base)
    for bad in ("http://pm.example.test/x", "https://evil.com/x", "javascript:alert(1)",
                "data:text/html,x", "https://pm.example.test@evil.com/", "", "//pm.example.test/x"):
        assert f(bad, base) == ""


def test_click_render(env):
    s, c, st, dev = env
    login(s, c)
    st["items"] = [ntfy_item(1, str(dev / "alpha"), click=f"{s.base_url}/a/x"),
                   ntfy_item(2, str(dev / "alpha"), click="javascript:alert(1)", priority=5)]
    t = c.get("/ntfy/panel").text
    assert f'href="{s.base_url}/a/x"' in t and "javascript:" not in t and "P5" in t


def test_rows_collapsed_tag_title_message_in_details(env):
    s, c, st, dev = env
    login(s, c)
    st["items"] = [ntfy_item(1, str(dev / "alpha"), title="Hello", message="secret body\nline2",
                      tags=(str(dev / "alpha"), "x"), priority=4)]
    t = c.get("/ntfy/panel").text
    assert t.count('<details class="ntfy-row"') == 1 and 'data-id="n:1"' in t
    summ = t[t.index("<summary>"):t.index("</summary>")]
    assert "Hello" in summ and ">alpha<" in summ and "secret body" not in summ
    assert f'title="{dev}/alpha"' in summ
    import re
    assert re.search(r'class="ntfy-stamp muted small" title="[^"]*">\d{4}-\d\d-\d\d \d\d:\d\d:\d\d<', summ)
    det = t[t.index('class="ntfy-detail"'):]
    assert "secret body\nline2" in det and "priority 4" in det
    assert "Expand all" not in t and "Collapse all" not in t and "data-filter" in t and "ntfy-q" not in t
    # htmx runs with allowScriptTags=false, so the JS must come from the base page, not the fragment
    assert "/static/js/ntfy.js" not in t
    assert "/static/js/ntfy.js" in c.get("/").text


def test_raw_tag_fallback(env):
    s, c, st, dev = env
    login(s, c)
    st["items"] = [ntfy_item(1, "/srv/other/deep/proj"), ntfy_item(2, "plain", tags=("plain",))]
    t = c.get("/ntfy/panel").text
    assert ">deep/proj<" in t and 'title="/srv/other/deep/proj"' in t
    assert ">plain<" in t


def test_panel_htmx_attrs_and_js(env):
    s, c, st, dev = env
    login(s, c)
    t = c.get("/ntfy/panel").text
    assert 'class="ntfy-inner" data-poll="/ntfy/panel?scope=inbox"' in t
    assert "hx-trigger" not in t.split("ntfy-head")[0]
    js = c.get("/static/js/ntfy.js")
    assert js.status_code == 200 and "htmx:afterSwap" in js.text


def test_project_page_has_no_panel(env):
    assert 'id="ntfy-project"' not in open(
        TEMPLATE_DIR / "project.html").read()


def test_panel_etag_204_and_changed(env):
    s, c, st, dev = env
    login(s, c)
    st["items"] = [NtfyItem(id="a1", time=1.0, title="first", message="m", priority=3, tags=(), click="", project=None)]
    r = c.get("/ntfy/panel?scope=inbox")
    et = r.headers["etag"]
    assert r.status_code == 200 and et.startswith('"')
    r2 = c.get("/ntfy/panel?scope=inbox", headers={"If-None-Match": et})
    assert r2.status_code == 204 and r2.content == b"" and r2.headers["etag"] == et
    st["items"] = [NtfyItem(id="a2", time=2.0, title="second", message="m", priority=3, tags=(), click="", project=None)] + st["items"]
    r3 = c.get("/ntfy/panel?scope=inbox", headers={"If-None-Match": et})
    assert r3.status_code == 200 and r3.headers["etag"] != et and "second" in r3.text


def test_inbox_and_project_pages_have_no_ntfy_panel(env):
    s, c, st, dev = env
    login(s, c)
    st["items"] = [NtfyItem(id="a1", time=1.0, title="seed-title", message="m", priority=3, tags=(), click="", project=str(dev / "alpha"))]
    html = c.get("/").text
    assert "seed-title" not in html and 'class="ntfy-inner"' not in html
    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and "<style" not in html
    frag = c.get("/", headers={"HX-Request": "true"})
    assert frag.status_code == 200 and "<html" not in frag.text and "seed-title" not in frag.text
    et = frag.headers["etag"]
    assert c.get("/", headers={"HX-Request": "true", "If-None-Match": et}).status_code == 204
    assert 'id="ntfy-project"' not in c.get("/p/alpha").text
    js = c.get("/static/js/nodiff.js")
    assert js.status_code == 200 and "htmx:beforeRequest" in js.text and "If-None-Match" in js.text


def test_ended_js_handles_both_lists_with_separate_state():
    import pathlib
    js = (pathlib.Path(routes_ntfy.__file__).parents[2] / "static/js/ended.js").read_text()
    for k in ("data-' + L.name + '-all", "'ended'", "'ntfy'", "CSSStyleSheet", "pm.filter.match", "pm:filter"):
        assert k in js
    # the query lives in filter.js: ended.js has no input listener, no own input selector, no per-list q attribute
    for gone in (".ntfy-q", ".ended-q", "'input'", '"input"', "-q'", "attrQ"):
        assert gone not in js
    css = (pathlib.Path(routes_ntfy.__file__).parents[2] / "static/css/rows.css").read_text()
    assert "html:not([data-ntfy-all]):not([data-pmq]) .ntfy-list .e-more{display:none}" in css
    assert "data-ntfy-q" not in css and "data-ended-q" not in css


def test_shown_title_drops_linked_agent_id():
    from pm.notify.owner import Owner
    from pm.web.ntfypanel import shown_title
    o = Owner("claude-c92f82", "dev-pm-1", "", True)
    assert shown_title("claude-c92f82 - did it", o) == "did it"
    assert shown_title("claude-c92f82 - did it", None) == "claude-c92f82 - did it"
    assert shown_title("claude-aaaaaa - x", o) == "claude-aaaaaa - x"


def test_ntfy_page_loads_drawer_scripts(client_factory=None):
    import pathlib
    src = (pathlib.Path(routes_ntfy.__file__)).read_text()
    assert 'nav="ntfy"' in src and "drawer=True" in src.split('nav="ntfy"')[1].split("\n")[0]


def test_all_scope_one_list_with_period_tabs_and_footer(env):
    import time
    s, c, st, dev = env
    login(s, c)
    p, now = str(dev / "alpha"), time.time()
    st["items"] = [ntfy_item(1, p, title="fresh", time=now - 5),
                   ntfy_item(2, p, title="ancient", time=now - 800 * 86400, tags=("/x/other",))]
    t = c.get("/ntfy/panel?scope=all").text
    assert "ntfy-tbl" not in t and "ntfy-seg" not in t and t.count('class="ntfy-list"') == 1
    tabs = re.findall(r'<button type="button" class="tab ntfy-tab" role="tab" data-tab="(\w+)" aria-selected="(\w+)">([^<]+)<', t)
    assert tabs == [("today", "true", "Today"), ("week", "false", "This week"), ("last_week", "false", "Last week"),
                    ("month", "false", "This month"),
                    ("last_month", "false", "Last month"), ("year", "false", "This year"), ("all", "false", "All time")]
    assert 'class="tabs ntfy-tabs tabs-seg" role="tablist" aria-label="Period" data-tabs-key="pm.ntfy.tab"' in t  # pm.tabs keeps it
    foot =re.search(r'<footer class="showbar ntfy-foot".*?</footer>', t, re.S).group(0)
    assert 'class="ntfy-n"' in foot and 'ntfy-shown' in foot and 'class="limitcount' in foot
    assert foot.index("limitcount") < foot.index("sel-show")
    assert '<option value="10" selected>' in foot and '<option value="all">All</option>' in foot
    for v in (10, 20, 50):
        assert f'<option value="{v}"' in foot
    assert "ntfy-toggle" not in t and "ntfy-q" not in t and "ntfy-title" not in t and "ntfy-unread" not in t
    assert "ntfy-head" not in t and 'data-unread="' in t and "ntfy-pg" in t
    page = c.get("/ntfy").text
    mr = re.search(r'<form[^>]*id="ntfy-markread".*?</form>', page, re.S).group(0)
    assert " hidden" in mr and 'action="/inbox/read-all"' in mr and 'name="csrf" value="' in mr and "ntfy-mark" in mr
    assert 'class="phead inbox-head"' in page and page.index("head-actions") < page.index("filterbar")
    assert 'data-p="today week month year all"' in t and 'data-p="all"' in t
    ids = re.findall(r'data-id="([^"]+)"', t)
    assert sorted(ids) == ["n:1", "n:2"] and "~" not in "".join(ids)
    assert t.count("ntfy-row e-more") == 0


def test_all_scope_last_month_rows_not_in_month(env):
    import time
    s, c, st, dev = env
    login(s, c)
    p = str(dev / "alpha")
    from pm.core import timefmt
    now = time.time()
    st["items"] = [ntfy_item(1, p, title="old", time=now - 40 * 86400)]
    t = c.get("/ntfy/panel?scope=all").text
    ps = re.search(r'data-p="([^"]*)"', t).group(1).split()
    assert ps == list(timefmt.periods_of(now - 40 * 86400, now))
    assert not ({"month", "last_month"} <= set(ps))  # last month never overlaps this month


def test_all_scope_last_week_rows_not_in_week(env):
    import time
    s, c, st, dev = env
    login(s, c)
    p, now = str(dev / "alpha"), time.time()
    from pm.core import timefmt
    # same instant as one in the previous week, whatever weekday today is
    ts = now - 7 * 86400
    st["items"] = [ntfy_item(1, p, title="prev", time=ts)]
    ps = re.search(r'data-p="([^"]*)"', c.get("/ntfy/panel?scope=all").text).group(1).split()
    assert ps == list(timefmt.periods_of(ts, now)) and "last_week" in ps and "week" not in ps


def test_all_scope_stats_on_page_and_flat_scopes(env):
    import time
    s, c, st, dev = env
    login(s, c)
    p, now = str(dev / "alpha"), time.time()
    st["items"] = [ntfy_item(1, p, title="a", time=now - 5), ntfy_item(2, p, title="b", time=now - 400 * 86400)]
    page = c.get("/ntfy").text
    assert '<p class="inbox-stats muted">' in page
    assert re.search(r'inbox-stats muted">2 · \d+ unread · 1 today<|inbox-stats muted">2 · 1 today<', page)
    for url in ("/ntfy/panel?scope=inbox", "/ntfy/panel?scope=system", "/notifications"):
        t = c.get(url).text
        assert "ntfy-tabs" not in t and "ntfy-foot" not in t and 'class="ntfy-n"' not in t
    assert 'class="small ntfy-toggle"' in c.get("/ntfy/panel?scope=inbox").text
    for url in ("/ntfy/panel?scope=inbox", "/ntfy/panel?scope=system"):
        t = c.get(url).text
        assert "ntfy-title" in t and "ntfy-head" in t and "data-unread=\"" not in t.split("<details")[0]


def test_ended_js_markread_and_count_text():
    import pathlib
    js = (pathlib.Path(routes_ntfy.__file__).parents[2] / "static/js/ended.js").read_text()
    assert "'showing ' + shown + ' of ' + total" in js and "#ntfy-markread" in js and "f.hidden = !(" in js


def test_ended_js_pins_only_text_filter():
    import pathlib
    js = (pathlib.Path(routes_ntfy.__file__).parents[2] / "static/js/ended.js").read_text()
    assert "pins: '.ntfy-pins .ntfy-row[data-q]'" in js and "rows: '.ntfy-list:not(.ntfy-pins) .ntfy-row[data-q]'" in js
    assert "function pinRules()" in js and "concat(pinRules())" in js


def test_ntfy_show_controls_come_from_the_limitbar_macros():
    tpl = TEMPLATE_DIR
    panel, page = (tpl / "_ntfy.html").read_text(), (tpl / "ntfy.html").read_text()
    assert 'limitbar(show_links, none, none, none, cls="ntfy-foot", id="ntfy-foot-all", sel="ntfy-n", count="ntfy-shown")' in panel
    assert '"ntfy-n") }}' in page and "<select" not in page and "<footer" not in panel



# ------------------------------------------------------------------ M2: row fields, mark unread, mute
HDR = {"origin": "https://pm.example.test"}


def _tok(c):
    return re.search(r'name="csrf" value="([^"]+)"', c.get("/").text).group(1)


def _panel_rows(c, monkeypatch, scope="all"):
    got = {}
    real = ntfypanel.get_template

    class Spy:
        def __init__(self, name):
            self.t = real(name)

        def render(self, **kw):
            got.update(kw)
            return self.t.render(**kw)
    monkeypatch.setattr(ntfypanel, "get_template", Spy)
    assert c.get(f"/ntfy/panel?scope={scope}").status_code == 200
    return {r["item"].id: r for r in got["rows"]}


def test_panel_rows_have_day_src_and_recovered_after(env, monkeypatch):
    import time

    s, c, st, dev = env
    login(s, c)
    p, now = str(dev / "alpha"), time.time()
    st["items"] = [
        NtfyItem("r1", now - 60, "pm recovered -- h: disk /home", "back", project=p),
        NtfyItem("a2", now - 600, "pm alert -- h: disk /home 95%", "again", project=p),
        NtfyItem("a1", now - 900, "pm alert -- h: disk /home 93%", "high", project=p),
        ntfy_item(1, p, time=now - 2 * 86400, title="claude-1 - old"),
    ]
    rows = _panel_rows(c, monkeypatch)
    assert rows["r1"]["recovered_after_s"] == 840.0 and rows["r1"]["src"] == "system"
    assert rows["a1"]["recovered_after_s"] is None and rows["1"]["src"] == "agent"
    assert rows["r1"]["day"] == "today" and rows["1"]["day"] == "earlier"


def test_mark_unread_route(env):
    s, c, st, dev = env
    login(s, c)
    tok = _tok(c)
    published = []
    c.app.state.hub.publish = lambda t, d: published.append((t, d))
    assert c.post("/inbox/mark-unread", data={"key": "claude-3:idle"}, headers=HDR).status_code == 403
    assert c.post("/inbox/mark-unread", data={"csrf": tok, "key": "bad key"}, headers=HDR).status_code == 400
    r = c.post("/inbox/mark-unread", data={"csrf": tok, "key": "claude-3:idle"}, headers=HDR)
    assert r.status_code == 200 and r.headers["hx-trigger"] == "pm:refresh"
    from pm.agents import inbox
    assert inbox.load(s.state_dir).marked_unread("claude-3:idle")
    assert published == [("inbox", {"read": 1})] and "inbox_mark_unread" in s.audit_file.read_text()
    assert c.post("/inbox/read", data={"csrf": tok, "key": "claude-3:idle"}, headers=HDR).status_code == 200
    assert not inbox.load(s.state_dir).marked_unread("claude-3:idle")


def test_mute_unmute_routes_hide_the_title_everywhere(env):
    import time

    s, c, st, dev = env
    login(s, c)
    tok = _tok(c)
    p = str(dev / "alpha")
    c.get("/inbox/unread")  # baseline
    st["items"] = [ntfy_item(1, p, title="noisy", time=time.time() + 1), ntfy_item(2, p, title="keep", time=time.time() + 2),
                   NtfyItem("s1", time.time() + 3, "pm alert -- h: cpu 99%", "m", project=p)]
    assert c.get("/inbox/unread").json() == {"unread": 2, "system": 1}
    assert c.post("/ntfy/mute", data={"title": "noisy"}, headers=HDR).status_code == 403
    for bad in ("", "a\nb", "x" * 201):
        assert c.post("/ntfy/mute", data={"csrf": tok, "title": bad}, headers=HDR).status_code == 400
    for title in ("noisy", "pm alert -- h: cpu 99%"):
        r = c.post("/ntfy/mute", data={"csrf": tok, "title": title}, headers=HDR)
        assert r.status_code == 200 and r.headers["hx-trigger"] == "pm:refresh"
    t = c.get("/ntfy/panel?scope=all").text
    assert "noisy" not in t and "keep" in t and "cpu 99%" not in t
    assert c.get("/inbox/unread").json() == {"unread": 1, "system": 0}
    assert 'class="bell-row' not in c.get("/ntfy/bell").text
    assert c.post("/ntfy/unmute", data={"csrf": tok, "title": "noisy"}, headers=HDR).status_code == 200
    assert c.post("/ntfy/unmute", data={"csrf": tok, "title": "noisy"}, headers=HDR).status_code == 404
    assert "noisy" in c.get("/ntfy/panel?scope=all").text
    audit = s.audit_file.read_text()
    assert "ntfy_mute" in audit and "ntfy_unmute" in audit
