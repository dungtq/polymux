import re
from pathlib import Path

from fastapi.testclient import TestClient
from helpers import make_client, make_settings

from pm.web.app import create_app
from pm.web.routes.pages import router

CSS = (Path(__file__).resolve().parents[1] / "src/pm/static/css/base.css").read_text()


def _settings(fake_tmux, tmp_path, **kw):
    return make_settings(fake_tmux, tmp_path, state="st", **kw)


def _page(fake_tmux, tmp_path, **kw):
    s = _settings(fake_tmux, tmp_path, **kw)
    (tmp_path / "dev").mkdir(exist_ok=True)
    return make_client(s, router, static=True, logged_in=True).get("/audit").text


def test_nav_markup(fake_tmux, tmp_path):
    html = _page(fake_tmux, tmp_path)
    assert html.count("<nav") == 2  # section sidebar + tabbar
    assert 'class="ext"' not in html and 'href="https://ntfy' not in html  # ntfy is an in-app page, no external link
    assert '/static/css/chat.css' in html and '/static/css/lists.css' in html
    assert "<style" not in html and not re.search(r"\sstyle=", html)
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)


def test_css_nav_rules():
    base = re.search(r"\.tabbar \{[^}]*\}", CSS).group(0)
    assert "position: fixed" in base and "bottom: 0" in base
    m = re.search(r"@media \(min-width: 700px\)[^{]*\{(.*?)\n\}\n", CSS[CSS.index("nav: phone"):], re.S)
    assert m and re.search(r"\.tabbar \{[^}]*position: static", m.group(1))


def test_v4_header_and_more_sheet(fake_tmux, tmp_path):
    html = _page(fake_tmux, tmp_path)
    for css in ("chrome", "rows", "data", "agent"):
        assert f'/static/css/{css}.css' in html
    assert 'class="toplink" href="/backlog"' in html and 'class="toplink" href="/audit"' in html
    assert 'aria-label="Log out"' in html and 'action="/logout"' in html
    assert html.index('class="toplink" href="/ntfy"') < html.index('class="toplink" href="/audit"')
    assert html.index('href="/ntfy" role="menuitem"') < html.index('href="/audit" role="menuitem"')
    assert html.index('href="/ntfy" class="t-desk') < html.index('href="/audit" class="t-desk')
    assert html.index('<a class="vs-row" href="/ntfy"') < html.index('href="/audit"', html.index('<a class="vs-row" href="/ntfy"'))
    assert 'id="moresheet"' in html and 'class="tabmore on"' in html  # /audit sits under More
    assert 'href="/audit" class="t-desk on" aria-current="page"' in html
    assert "/static/js/more.js" in html


def test_header_icons_and_theme_toggle(fake_tmux, tmp_path):
    html = _page(fake_tmux, tmp_path)
    assert 'id="clock"' not in html and "clock.js" not in html
    hdr = html.split('<header class="topbar">')[1].split("</header>")[0]
    assert "data-theme-toggle" in hdr and 'aria-label="Toggle dark / light mode"' in hdr
    for label in ("Settings", "Log out"):
        seg = hdr.split(f'aria-label="{label}"')[1].split("</a>" if label == "Settings" else "</button>")[0]
        assert 'aria-hidden="true"' in seg and 'stroke="currentColor"' in seg and "<svg" in seg
    js = (Path(__file__).parent.parent / "src/pm/static/js/theme.js").read_text()
    assert "data-theme-toggle" in js and "pm-theme" in js


def test_section_sidebar_launch_stop_and_foot(fake_tmux, tmp_path):
    html = _page(fake_tmux, tmp_path)  # /audit: no contextual list, so the section nav sidebar shows
    side = html.split('id="v5side"')[1].split("</aside>")[0]
    assert 'class="v5-shell has-side"' in html and "/static/js/side.js" in html
    for label in ("Inbox", "Projects", "tmux", "Backlog", "Audit", ">Active<", ">Idle<", ">Ended<"):
        assert label in side
    # Agents / Servers sit in the foot group "Manage", not in the section list; same three groups as the Menu dropdown
    lst = side.split('id="v5menu"')[1].split("</nav>")[0]
    assert 'href="/agents"' not in lst and 'href="/servers"' not in lst
    foot = side[side.index("vs-foot"):]
    assert foot.index('aria-label="Manage"') < foot.index('aria-label="Service"') < foot.index('aria-label="Account"')
    assert 'href="/agents"' in foot and 'href="/servers"' in foot
    pop = html.split('class="acct-pop pop"')[1].split("</details>")[0]
    assert pop.index('aria-label="Manage"') < pop.index('aria-label="Service"') < pop.index('aria-label="Account"')
    assert 'href="/agents"' in pop and 'href="/servers"' in pop and "data-theme-toggle" in pop
    assert side.count('aria-current="page"') == 1 and 'href="/audit" aria-current="page"' in side
    assert "vs-foot" in side and "data-theme-toggle" in side and 'href="/settings"' in side
    assert 'action="/logout"' in side and 'action="/service/reload"' in side
    hdr = html.split('<header class="topbar">')[1].split("</header>")[0]
    assert 'href="/launch"' in hdr and 'data-dlg-open="stop-dialog"' in hdr
    assert 'id="stop-dialog"' in html and 'action="/panic"' in html and 'name="scope" value="all"' in html


def test_launch_redirect(fake_tmux, tmp_path):
    s = _settings(fake_tmux, tmp_path)
    (tmp_path / "dev").mkdir(exist_ok=True)
    app = create_app(s)
    c = TestClient(app, base_url="https://testserver")
    assert "/login" in c.get("/launch", follow_redirects=False).headers.get("location", "/login")
    c.post("/login", data={"token": s.token_file.read_text().strip()}, follow_redirects=False)
    r = c.get("/launch?embed=1", follow_redirects=False)
    loc = r.headers["location"]
    assert r.status_code == 303 and loc.startswith("/p") and ("embed=1" in loc or loc == "/p")


def test_sidebar_settings_popup_and_project_list_collapsed(fake_tmux, tmp_path):
    html = _page(fake_tmux, tmp_path)  # /audit: not a project page
    side = html.split('id="v5side"')[1].split("</aside>")[0]
    assert 'href="/settings" id="vs-settings-open" data-dlg-open="settings-dialog"' in side
    dlg = html.split('<dialog id="settings-dialog"')[1].split("</dialog>")[0]
    assert 'hx-get="/settings/panel"' in dlg and "Open full settings page" in dlg and "pmdlg-wide" in html
    assert 'id="settings-dialog"' not in html.split('id="v5side"')[1].split("</aside>")[0]  # dialog sits after the aside, not in base.html
    # collapsed on the server, with the toggle saying so (no flash)
    assert " vs-pclosed" in html.split('id="v5side"')[0].rsplit("<aside", 1)[1]


def test_sidebar_inbox_views_current_row():
    from pm.web.render import env as _env
    tpl = _env.get_template("_side.html")
    base = dict(nav="inbox", sidebar=None, side_projects=None, csrf="", locked=False, require_unlock=False, dev_banner=True)
    for v in ("active", "idle", "ended"):
        h = tpl.render(inbox_view=v, **base)
        assert h.count('aria-current="page"') == 1 and f'href="/?view={v}" aria-current="page"' in h
        assert 'href="/" aria-current' not in h
    h = tpl.render(inbox_view="", **base)
    assert 'href="/" class="vs-row on"' in h or 'class="vs-row on" href="/"' in h
    assert h.count('aria-current="page"') == 1 and 'href="/?view=idle"' in h
    # on /settings itself the link is a plain page link
    assert "settings-dialog" not in tpl.render(**{**base, "nav": "settings"})


def _nav_hrefs(seg, pat):
    return re.findall(pat, seg)


def test_every_nav_place_follows_the_one_nav_list(fake_tmux, tmp_path):
    from pm.web.render import NAV
    html = _page(fake_tmux, tmp_path)  # /audit
    want = lambda flag: [i.href for i in NAV if flag(i)]  # noqa: E731
    hdr = html.split('<header class="topbar">')[1].split("</header>")[0]
    assert _nav_hrefs(hdr, r'<a class="toplink" href="([^"]+)"') == want(lambda i: i.top)
    bar = html.split('<nav class="tabbar"')[1].split("<button")[0]
    assert _nav_hrefs(bar, r'<a href="([^"]+)"') == want(lambda i: i.tab is not None)
    for i in NAV:  # the phone / desktop-only classes ride along with the list
        if i.tab:
            assert f'href="{i.href}" class="{i.tab} ' in bar
    more = html.split('id="moresheet"')[1].split("</div>")[0]
    assert _nav_hrefs(more, r'<a class="pop-item" href="([^"]+)"') == want(lambda i: i.more)
    pop = html.split('class="acct-pop pop"')[1].split('aria-label="Service"')[0]
    assert _nav_hrefs(pop, r'href="([^"]+)"') == want(lambda i: i.menu)
    side = html.split('id="v5menu"')[1].split("</nav>")[0]
    assert _nav_hrefs(side, r'<a class="vs-row[^"]*" href="([^"]+)"[^>]*data-p=') == want(lambda i: i.side)
    # icons come from the _svg macro: every top / menu entry has one drawn
    for i in NAV:
        if i.top or i.menu:
            seg = (hdr if i.top else pop).split(f'href="{i.href}"')[1].split("</a>")[0]
            assert '<svg class="hi"' in seg and ("<path" in seg or "<rect" in seg), i.key


def test_more_tab_is_on_for_the_more_entries_only():
    from pm.web.render import NAV, env
    tpl = env.get_template("base.html")
    ctx = dict(csrf="", locked=False, require_unlock=False, dev_banner=True, embed=True, sidebar=None)
    more = {i.key for i in NAV if i.more}
    assert more == {"agents", "backlog", "ntfy", "audit", "settings"}
    for i in NAV:
        h = tpl.render(nav=i.key, **ctx)
        assert ('class="tabmore on"' in h) == (i.key in more), i.key
        bar = h.split('<nav class="tabbar"')[1].split("</nav>")[0]
        assert bar.count('aria-current="page"') == (i.tab is not None) + i.more, i.key
