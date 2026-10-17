"""dt Settings page (pm.web.dt.settings, templates/settings.html + _settings_panel.html + settings/_*.html, the core
fragment overrides _settings_qchat.html / _autoexit*.html, js/settings*.js): the view model (sections, aliases, rows
and ⋯ items, read-only lines), the core routes rendering the real dt templates, the /dt routes it uses (prefs,
close all tabs) and the static rules of its files."""

from __future__ import annotations

import dataclasses
import json
import re
from html import unescape
from pathlib import Path
from types import SimpleNamespace

import pytest
from markupsafe import Markup

from pm.web import themes
from pm.web.dt import DtTheme, prefs, stores
from pm.web.dt import settings as dts

from .conftest import EP, make_env
from .kit import render_base

THEME = themes.HERE / "dt"
TPL, JS = THEME / "templates", THEME / "static" / "js"
HDR = {"Accept": "application/json"}


# ---------------------------------------------------------------- view model
def req(s=None):
    return SimpleNamespace(query_params={"s": s} if s is not None else {})


def test_sections_and_aliases():
    assert dts.sec_of("access") == "access" and dts.sec_of("Security") == "access"
    assert dts.sec_of("you") == "appearance" and dts.sec_of("nope") is None and dts.sec_of("") is None
    groups, secs = dts.nav("ssh")
    assert [g.label for g in groups] == ["You", "Work", "Security", "System"]
    assert [s.id for s in groups[2].secs] == ["access", "ssh", "servers"]
    assert secs["ssh"].cur and not secs["access"].cur and secs["git"].href == "/settings?s=git"


def test_build_list_view_and_rows(settings):
    ctx = {"request": req(), "sessions": [
        {"h": "a" * 64, "created": 1.0, "last_seen": 5.0, "client": "10.0.0.2", "current": False},
        {"h": "b" * 64, "created": 2.0, "last_seen": 9.0, "client": "172.19.0.3", "current": True}],
        "passkeys": [{"id": "k1", "name": "phone", "created": 3.0}], "agents": ("claude", "codex"),
        "prefs": {"launch": {"agent": "codex", "model": "", "mode": "weird"}, "notify": {"finish": False}},
        "notify_rules": (("finish", "Agent failed"), ("waiting", "Agent waiting for input")),
        "servers": [{"id": "s1", "name": "build1", "host": "10.0.0.5", "port": 22, "user": "me", "hostkey_fp": "",
                     "last_test": None},
                    {"id": "s2", "name": "lab", "host": "lab", "port": 2222, "user": "root", "hostkey_fp": "SHA256:x",
                     "last_test": {"reason": "auth"}}], "srv_results": {"auth": "key not accepted"},
        "ui_themes": [("classic", "classic"), ("dt", "dt (keyboard)")], "ui_theme": "dt"}
    p = dts.build(ctx, settings)
    assert p.view == "list" and p.cur == "appearance"  # no ?s=: the phone's list, the first section elsewhere
    assert [r.current for r in p.sessions] == [True, False]  # this session first
    cur, other = p.sessions
    assert cur.menu[0]["vals"] == {"h": "b" * 64, "confirm": "1"} and cur.menu[0]["confirm"] == "Log out of this session?"
    assert "10.0.0.2" in other.menu[0]["confirm"] and other.menu[0]["refresh"] == "#s-sec-access"
    assert p.passkeys[0].menu[0]["vals"] == {"id": "k1"} and "phone" in p.passkeys[0].menu[0]["confirm"]
    s1, s2 = p.servers
    assert (s1.state, s1.pinned, s2.state, s2.addr, s2.word) == ("waiting", False, "failed", "root@lab:2222",
                                                                   "key not accepted")
    ids1 = [m.get("id") for m in s1.menu]
    assert ids1[0] == "pin" and "repin" not in ids1
    test1 = next(m for m in s1.menu if m.get("id") == "test")
    assert test1["disabled"] and test1["why"] == "pin the host key first"
    ids2 = [m.get("id") for m in s2.menu]
    assert "repin" in ids2 and "pin" not in ids2 and ids2[-1] == "remove"
    assert next(m for m in s2.menu if m.get("id") == "test")["why"] == "generate pm's key first"  # no pm key
    assert ("weird", "weird") in p.mode_opts and p.launch["agent"] == "codex"
    assert [n[3] for n in p.notify] == [False, True]
    assert p.ui_opts == (("classic", "classic"), ("dt", "dt (keyboard)")) and p.ui_theme == "dt"
    assert p.acts["delete_history"]["typed"] == "delete" and p.acts["reload"]["post"] == "/service/reload"
    dev = dts.build(ctx, dataclasses.replace(settings, dev_banner=True))
    assert dev.acts["reload"]["disabled"] and not dev.reload_ok  # a dev instance never reloads


def test_build_section_and_system_lines(settings):
    p = dts.build({"request": req("security"), "git_hosts": ("git.example",)}, settings)
    assert p.view == "sec" and p.cur == "access"
    assert (p.git[0].value, p.git[0].help, p.git[1].value) == ("origin", "push_remote", "git.example")
    p = dts.build({"request": req("security")}, dataclasses.replace(settings, push_remote="gitdev"))
    assert p.git[0].value == "gitdev"
    labels = [line.label for line in p.system]
    assert labels == ["Status", "Config file", "State folder", "ntfy topics", "Tier ceiling", "Alert thresholds"]
    assert "none" in p.system[1].value  # no pm.toml in the test settings


def test_server_menu_opens_a_shell_once_pinned_with_a_key(settings):
    srv = [{"id": "s1", "name": "lab", "host": "lab", "port": 22, "user": "me", "hostkey_fp": "SHA256:x"}]
    rows = dts.server_rows(srv, {}, has_key=True)
    sh = next(m for m in rows[0].menu if m.get("id") == "shell")
    assert sh["post"] == "/settings/servers/s1/shell" and sh["label"] == "Open a shell in tmux" and "confirm" not in sh
    ids = [m.get("id") for m in rows[0].menu]
    assert ids.index("test") < ids.index("shell") < ids.index("install")
    off = next(m for m in dts.server_rows(srv, {}, has_key=False)[0].menu if m.get("id") == "shell")
    assert off["disabled"] and off["why"] == "generate pm's key first" and "post" not in off
    srv[0]["hostkey_fp"] = ""
    off = next(m for m in dts.server_rows(srv, {}, has_key=True)[0].menu if m.get("id") == "shell")
    assert off["disabled"] and off["why"] == "pin the host key first"


def test_totp_state_only_with_require_unlock(settings):
    base = {"request": req("access")}
    assert dts.build(base, settings).totp == ""  # require_unlock off: nothing to set up
    assert dts.build({**base, "require_unlock": True, "provisioned": False}, settings).totp == "missing"
    assert dts.build({**base, "require_unlock": True, "provisioned": True}, settings).totp == "ready"


def test_plugins_section_only_when_a_plugin_adds_one(settings):
    p = dts.build({"request": req("plugins")}, settings)
    assert "plugins" not in p.secs and p.cur == "appearance" and p.view == "list"  # unknown without plugins
    p = dts.build({"request": req("plugins"), "plugin_sections": Markup("<section>x</section>")}, settings)
    assert p.cur == "plugins" and p.view == "sec" and p.secs["plugins"].group == "System"
    assert [s.id for s in p.nav[-1].secs] == ["git", "system", "plugins"] and str(p.plugins) == "<section>x</section>"


def test_tabs_count_and_prefs_come_from_the_stores(settings):
    stores.apply(settings, stores.open_tab, ("agent", "claude-aaa111"))
    prefs.apply(settings, {"run_status": "off"})
    p = dts.build({"request": req()}, settings)
    assert p.tabs_open == 1 and "1 session tab" in p.acts["close_tabs"]["confirm"]
    assert p.prefs.get("run_status") == "off"


# ---------------------------------------------------------------- the core routes with the real dt templates
@pytest.fixture
def senv(settings, snap):
    class RealDt(DtTheme):
        def themes(self):
            return [themes.Theme(name="dt", templates=TPL, static=THEME / "static", label="dt")]

    return make_env(settings, RealDt())


def test_plugin_settings_section_renders_in_dt(settings, snap, tmp_path):
    tpl = tmp_path / "plug-tpl"
    tpl.mkdir()
    (tpl / "plug_settings.html").write_text('<p class="plug-body">Plug value {{ plug_v }}</p>')

    class Plug:
        name = "plug"

        def templates_dir(self):
            return tpl

        def settings_sections(self):
            return [("Plug options", "plug_settings.html", lambda request: {"plug_v": "42"})]

    class RealDt(DtTheme):
        def themes(self):
            return [themes.Theme(name="dt", templates=TPL, static=THEME / "static", label="dt")]

    assert 'data-s-go="plugins"' not in make_env(settings, RealDt()).c.get("/settings").text  # none: no entry
    e = make_env(settings, RealDt(), more=[EP("plug", Plug())])
    t = e.c.get("/settings?s=plugins").text
    head = re.search(r'<section class="s-sec" id="s-sec-plugins"[^>]*>', t)
    assert head and " hidden" not in head.group(0) and 'data-s-go="plugins"' in t, t[:3000]
    assert '<h2 class="set-h">Plug options</h2>' in t and '<p class="plug-body">Plug value 42</p>' in t
    frag = e.c.get("/settings/panel?s=plugins").text
    assert "Plug value 42" in frag and 'hx-select="#s-sec-plugins"' in frag


def test_totp_setup_row_in_access(settings, snap):
    class RealDt(DtTheme):
        def themes(self):
            return [themes.Theme(name="dt", templates=TPL, static=THEME / "static", label="dt")]

    e = make_env(dataclasses.replace(settings, require_unlock=True), RealDt())
    t = e.c.get("/settings?s=access").text
    assert 'hx-post="/unlock/provision"' in t and 'hx-target="#s-totp-msg"' in t and 'id="s-totp-msg"' in t
    r = e.post("/unlock/provision", headers={"HX-Request": "true"})
    assert r.status_code == 200 and "otpauth://" in r.text and "shown once" in r.text
    t = e.c.get("/settings?s=access").text
    assert "/unlock/provision" not in t and "set up" in t and "otpauth" not in t  # the secret is never shown again
    off = make_env(settings, RealDt())
    t = off.c.get("/settings?s=access").text
    assert "/unlock/provision" not in t and "Set up TOTP" not in t  # require_unlock off: no TOTP row


def test_settings_page_renders_every_section(senv):
    r = senv.c.get("/settings")
    assert r.status_code == 200
    t = r.text
    for sid in dts.IDS:
        assert f'id="s-sec-{sid}"' in t and f'data-s-go="{sid}"' in t
    assert 's-lay s-v-list' in t and 'aria-current="page"' in t  # list view, Appearance marked
    assert 'data-p-filter="settings"' in t and 'data-p-filter-rows="#s-panel"' in t
    assert 'hx-get="/settings/autoexit"' in t and 'hx-get="/settings/qchat"' in t  # lazy core fragments
    assert 'data-passkey="register"' in t and 'id="passkey-name"' in t  # classic passkey.js keeps working
    assert '/static/t/dt/js/settings.js' in t and '/static/css/settings.css' not in t  # classic CSS gone
    for js in ("theme.js", "fonts.js", "copy.js", "/static/js/servers.js"):
        assert js not in t
    assert "this browser" in t and "all devices" in t and "pm.toml · read-only" in t


def test_section_url_and_panel_fragment(senv):
    r = senv.c.get("/settings?s=servers")
    assert r.status_code == 200 and 's-lay s-v-sec' in r.text
    sec = re.search(r'<section class="s-sec" id="s-sec-servers"[^>]*>', r.text).group(0)
    assert " hidden" not in sec and 'hx-select="#s-sec-servers"' in sec
    frag = senv.c.get("/settings/panel?s=access", headers={"HX-Request": "true"})
    assert frag.status_code == 200 and 'id="s-sec-access"' in frag.text and "<html" not in frag.text


def test_qchat_and_autoexit_fragments(senv):
    q = senv.c.get("/settings/qchat")
    assert q.status_code == 200 and q.text.lstrip().startswith('<div id="s-qchat"')
    assert 'aria-controls="s-qc-mcp"' in q.text and 'name="part" value="history"' in q.text
    menu = re.search(r"data-s-do='([^']*delete-all[^']*)'", q.text)
    assert menu and json.loads(unescape(menu.group(1)))["typed"] == "delete"
    a = senv.c.get("/settings/autoexit")
    assert a.status_code == 200 and 'id="s-autoexit"' in a.text and 'name="hours"' in a.text


def test_core_settings_posts_answer_json_for_the_page(senv):
    r = senv.post("/settings/composer", headers=HDR, paste_lines="x", exts="png")
    assert r.status_code == 200 and r.json()["ok"] is False and "number" in r.json()["notice"]
    r = senv.post("/settings/prefs", headers=HDR, launch_agent="claude", launch_model="", launch_mode="",
                  notify_finish="1", notify_waiting="0")
    assert r.json() == {"ok": True, "notice": "Preferences saved."}


def test_dt_prefs_routes(senv):
    assert senv.c.get("/dt/prefs").json() == {"rev": 0, "prefs": {}}
    r = senv.post("/dt/prefs", headers=HDR, run_status="plain", col_k_claude="#AA0000")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    j = r.json()
    assert j["ok"] is True and j["rev"] == 1 and j["prefs"] == {"run_status": "plain", "col_k_claude": "#aa0000"}
    assert senv.events[-1] == ("ui", {"area": "prefs", "rev": 1})
    bad = senv.post("/dt/prefs", headers=HDR, col_k_claude="red")
    assert bad.status_code == 400 and "colour" in bad.json()["detail"]
    lines = [json.loads(x) for x in senv.s.audit_file.read_text().splitlines()]
    mine = [x["result"] for x in lines if x["action"] == "dt.prefs.save"]
    assert mine == ["attempt", "ok", "attempt", "denied"]
    assert senv.post("/dt/prefs", headers=HDR, run_status="").json()["prefs"] == {"col_k_claude": "#aa0000"}
    page = senv.c.get("/settings?s=colors").text
    assert 'data-s-a="col_k_claude" aria-label="Claude colour" value="#aa0000" data-s-set' in page


def test_display_quick_look_pref_row(senv):
    seg = re.compile(r'<span class="p-seg" role="group" aria-label="Quick look opens files in" data-s-a="peek_open">'
                     r'(.*?)</span>')
    on = re.compile(r'value="(\w+)" aria-pressed="true"')
    m = seg.search(senv.c.get("/settings?s=display").text)
    assert m and on.findall(m.group(1)) == ["new"]
    assert senv.post("/dt/prefs", headers=HDR, peek_open="same").json()["prefs"] == {"peek_open": "same"}
    m = seg.search(senv.c.get("/settings?s=display").text)
    assert m and on.findall(m.group(1)) == ["same"]
    assert senv.post("/dt/prefs", headers=HDR, peek_open="popup").status_code == 400


def test_display_composer_on_phones_pref_row(senv):
    seg = re.compile(r'<span class="p-seg" role="group" aria-label="Composer on phones" data-s-a="cmp_phone">(.*?)</span>')
    on = re.compile(r'value="(\w+)" aria-pressed="true"')
    m = seg.search(senv.c.get("/settings?s=display").text)
    assert m and on.findall(m.group(1)) == ["collapsed"]
    assert senv.post("/dt/prefs", headers=HDR, cmp_phone="open").json()["prefs"] == {"cmp_phone": "open"}
    m = seg.search(senv.c.get("/settings?s=display").text)
    assert m and on.findall(m.group(1)) == ["open"]
    assert senv.post("/dt/prefs", headers=HDR, cmp_phone="shut").status_code == 400
    head = _js("head.js")
    assert "c.toggle('cmp-open', p.cmp_phone === 'open')" in head


def test_composer_text_size_in_composer_and_appearance(senv):
    """Composer text size: this browser's pm.v7.cmp.size (head.js -> --compose-size), in Appearance › Fonts and in
    Composer › Text, outside the all-devices form; settings.js keeps both inputs in step."""
    inp = 'data-s-b="cmp.size"'
    comp = senv.c.get("/settings?s=composer").text
    sec = comp[comp.index('id="s-sec-composer"'):]
    sec = sec[:sec.index("</section>")]
    assert sec.count(inp) == 1 and sec.index(inp) < sec.index('action="/settings/composer"')
    assert "Text size, pasting and attaching" in sec
    app = senv.c.get("/settings?s=appearance").text
    sec = app[app.index('id="s-sec-appearance"'):]
    assert sec[:sec.index("</section>")].count(inp) == 1
    js = _js("settings.js")
    assert "bset(t.getAttribute('data-s-b'), t.value);\n      syncB(lay());" in js
    assert "'cmp.size'" in js and "prop('--compose-size', cs)" in _js("head.js")
    css = (THEME / "static/css/qchat.css").read_text()
    assert "html[data-compose-size] .q-pop .q-box textarea{font-size:var(--compose-size)}" in css


def test_display_sidebar_state_row(senv):
    # Panels (this browser): one sidebar state for all tabs, or per tab (pm7.look.rail in head.js)
    m = re.search(r'<span class="p-seg" role="group" aria-label="Sidebar state" data-s-b="shell.railmode">(.*?)</span>',
                  senv.c.get("/settings?s=display").text)
    assert m and re.findall(r'value="(\w+)"', m.group(1)) == ["all", "tab"]
    js = _js("settings.js")
    assert "'shell.railmode': 'all'" in js  # the default is not stored
    assert "lk.rail(now)" in js  # a mode switch keeps the sidebar as it is


def test_close_all_tabs_route(senv):
    for i in ("claude-aaa111", "codex-bbb222"):
        senv.post("/dt/tabs/open", kind="agent", id=i)
    r = senv.post("/dt/tabs/close-all", headers={"HX-Current-URL": "https://pm.example.test/settings"})
    assert r.status_code == 200 and r.headers["x-dt-closed"] == "2"
    st = stores.load(senv.s)
    assert st.tabs == () and st.current is None and st.closed[-2:] == (("agent", "claude-aaa111"),
                                                                        ("agent", "codex-bbb222"))
    assert senv.events[-1][1]["area"] == "tabs"


# ---------------------------------------------------------------- static rules of the page files
def _js(name):
    return (JS / name).read_text(encoding="utf-8")


def test_page_scripts_use_the_shared_helpers():
    for name in ("settings.js", "settings-forms.js", "settings-servers.js"):
        js = _js(name)
        assert not re.search(r"(?<!pm7\.)\bfetch\(", js) and "setInterval" not in js and "keydown" not in js
        assert "localStorage" not in js and "innerHTML" not in js and "window.pm." not in js
        assert "document.cookie" not in js  # the UI theme is a GET ?ui= (the server sets pm_ui)


def test_head_js_token_map_matches_the_store():
    head = _js("head.js")
    keys = set(re.findall(r"(col_[a-z_]+): '--", head))
    assert keys == set(prefs.COLOURS)
    for k, tok in prefs.COLOURS.items():
        assert f"{k}: '{tok}'" in head


# ---------------------------------------------------------------- the Settings popup (README A23, A27)
CSS = THEME / "static" / "css"
SCRIPTS = ("settings.js", "settings-forms.js", "settings-servers.js", "settings-popup.js")


def _ids(html):
    return re.findall(r'\sid="([^"]+)"', html)


def test_popup_dialog_on_other_pages_not_on_settings(senv):
    for url in ("/p", "/agents"):
        t = senv.c.get(url).text
        assert re.search(r'<dialog id="sp-dlg" class="sp-dlg" aria-labelledby="sp-dlg-t">', t), url
        assert 'data-sp-body' in t and 'data-sp-page' in t and 'data-act="close"' in t
        side = re.search(r'<a href="/settings" data-nav="settings"[^>]*>', t).group(0)
        assert "data-sp-open" in side and 'aria-controls="sp-dlg"' in side  # sidebar foot opens the popup
        acct = re.search(r'<a href="/settings" role="menuitem"[^>]*>', t).group(0)
        assert "data-sp-open" in acct and 'aria-controls="sp-dlg"' in acct  # so does the account menu's Settings
    t = senv.c.get("/settings").text
    assert 'id="sp-dlg"' not in t  # no popup on the page itself: its ids would exist twice (x-peek reuses the frame)
    side = re.search(r'<a href="/settings" data-nav="settings"[^>]*>', t).group(0)
    assert "data-sp-open" not in side
    assert "data-sp-open" not in re.search(r'<a href="/settings" role="menuitem"[^>]*>', t).group(0)
    ids = _ids(t)
    assert len(ids) == len(set(ids))


def test_popup_absent_in_embed():
    h = render_base(embed=True)
    assert "sp-dlg" not in h and "settings-popup.js" not in h and "css/settings.css" not in h
    full = render_base()
    assert 'id="sp-dlg"' in full and "settings-popup.js" in full


def test_settings_assets_load_once(senv):
    for url in ("/settings", "/p"):
        t = senv.c.get(url).text
        for s in SCRIPTS:
            assert len(re.findall(rf'src="/static/t/dt/js/{re.escape(s)}[?"]', t)) == 1, (url, s)
        assert len(re.findall(r'href="/static/t/dt/css/settings\.css[?"]', t)) == 1, url


def test_panel_fragment_is_the_popup_body(senv):
    frag = senv.c.get("/settings/panel?s=colors").text
    assert "<html" not in frag and 'id="s-panel"' in frag and "s-lay s-v-sec" in frag
    sec = re.findall(r'<section class="s-sec" id="s-sec-([\w-]+)"[^>]*>', frag)
    shown = [m.group(1) for m in re.finditer(r'<section class="s-sec" id="s-sec-([\w-]+)"(?![^>]*\shidden)[^>]*>',
                                             frag)]
    assert len(sec) == len(dts.IDS) and shown == ["colors"]
    assert 'data-p-filter="settings"' in frag and 'data-p-filter-rows="#s-panel"' in frag  # the search box too
    assert frag.index('data-p-filter="settings"') < frag.index('id="s-panel"')
    ids = _ids(frag)
    assert len(ids) == len(set(ids))
    assert "s-lay s-v-list" in senv.c.get("/settings/panel").text  # no ?s=: the phone's section list
    page = senv.c.get("/settings").text
    assert page.count('data-p-filter="settings"') == 1  # the page keeps one search box (from the panel)


def test_keys_row_toggles_the_popup():
    keys = _js("keys.js")
    row = re.search(r"\{ id: 'settings',[^}]*\}", keys).group(0)
    assert "layer: spOpen" in row and "run: settings" in row and "'Settings popup'" in row
    assert "pm7.settings.toggle()" in keys and "goPage('settings', '/settings')" in keys
    pop = _js("settings-popup.js")
    assert "pm7.dialog(" in pop and "'/settings/panel'" in pop and "pm7.settings.init(" in pop
    assert "Object.assign(pm7.settings" in pop and "Object.assign(pm7.settings" in _js("settings.js")
    for bad in ("keydown", "Escape", "setInterval", "::backdrop", "window.open", "history."):
        assert bad not in pop, bad  # layers.js owns Esc / backdrop; the popup never changes the URL
    js = _js("settings.js")
    assert "pageLay()" in js and re.search(r"popstate', \(\) => \{\n\s+if \(!pageLay\(\)\) return;", js)


def test_popup_size_tokens():
    tok = (CSS / "tokens.css").read_text()
    assert "--sp-w:1040px" in tok and "--sp-h:760px" in tok
    css = (CSS / "settings.css").read_text()
    assert "var(--sp-w)" in css and "var(--sp-h)" in css and "var(--scrim)" in css
    assert not re.search(r"\b(1040|760)px", css)
