"""dt session tab frame (PRD §6.3, templates/agent.html, agent_ended.html, builder.html, session/_frame.html,
shell/_classic.html page flag, css/session.css, js/session.js): the real dt theme rendered through the app for a live
agent, ?st= / embed variants, an ended record and the Builder, plus static rules of the page files."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pm.agents import registry
from pm.infra import snapshot as services
from pm.web import themes
from pm.web.dt import DtTheme

from .conftest import agent, make_env, make_snap
from .kit import Doc

AID = "claude-1c70ee"
THEME = themes.HERE / "dt"


@pytest.fixture
def live(settings, snap, monkeypatch):
    snap.value = make_snap([agent(AID, "alpha")])

    async def fake_snapshot(*_a, **_k):
        return snap.value

    monkeypatch.setattr(services, "snapshot_async", fake_snapshot)
    return make_env(settings, DtTheme())


def page(env, url):
    r = env.c.get(url)
    assert r.status_code == 200, (url, r.status_code, r.text[:300])
    return r.text


def scripts(src: str) -> list[str]:
    return [re.sub(r"\?v=\w+$", "", s) for s in re.findall(r'<script src="([^"]+)"', src)]


def menu_items(doc: Doc) -> dict[str, dict]:
    btn = doc.one("button", class_="c-more")
    return {it["id"]: it for it in json.loads(btn["data-menu"]) if not it.get("sep")}


# ---------------------------------------------------------------- live agent page
def test_live_frame_header(live):
    src = page(live, f"/a/{AID}")
    doc = Doc(src)
    assert doc.one("main")["class"].split() == ["x-main"]  # a ported page: no .x-cl on main
    sess = doc.one("section", class_="c-sess")
    assert sess["data-agent"] == AID and sess["data-c-mode"] == "live" and "data-menu-scope" in sess
    doc.one("header", class_="c-hd")
    assert "running" in doc.one("i", data_c_dot=True)["class"]
    assert doc.one("h1", class_="c-nm sfx")["title"] == AID
    doc.one(id="agent-state")  # classic chat.js / slash.js hook
    hook = doc.one("span", class_="agent-head")  # classic agentmode.js hook
    assert hook["data-mode"] == "live" and hook["data-agent"] == AID and "hidden" in hook
    assert doc.one("a", class_="c-bbtn")["href"] == f"/a/{AID}/builder"
    doc.one("button", data_c_toaside=True)
    doc.one("button", data_c_font=True)
    assert {b["data-chat-view"] for b in doc.find("button", data_chat_view=True)} == {"detail", "compact"}


def test_phone_composer_toggle(live):
    # LAYOUT §6: an icon button in the header line (phones only, css/session.css), state in aria-expanded + title
    for url in (f"/a/{AID}", f"/a/{AID}/builder"):
        src = page(live, url)
        b = Doc(src).one("button", data_c_cmp=True)
        assert b["aria-expanded"] == "false" and b["title"] == b["aria-label"] == "Show composer", url
        # the same bordered icon button as Builder next to it (x-btn sm; phone size in css/session.css)
        assert {"x-btn", "sm"} <= set(b["class"].split()), url
        assert re.search(r"data-c-cmp[^>]*>\s*<svg[^>]*><use href=\S+#edit", src), url
    assert not Doc(page(live, f"/a/{AID}?embed=1")).find(data_c_cmp=True)  # the aside's iframe shows its composer


def test_live_tabs_drive_the_classic_radios(live):
    doc = Doc(page(live, f"/a/{AID}"))
    tabs = doc.find(role="tab", data_p_tab=True)
    assert [t["data-p-tab"] for t in tabs] == ["chat", "sub", "pane"]  # no Terminal: terminal off in tests
    assert [t["aria-selected"] for t in tabs] == ["true", "false", "false"]
    for t in tabs:
        assert doc.one(id=t["aria-controls"])["role"] == "tabpanel"
    radios = doc.find("input", name="agent-tab")
    assert {r["id"] for r in radios} == {"tab-chat", "tab-pane", "tab-sub"}
    assert [r["id"] for r in radios if "checked" in r] == ["tab-chat"]
    assert "hidden" not in doc.one(id="c-pn-chat") and "hidden" in doc.one(id="c-pn-pane")


def test_live_chat_column_and_composer(live):
    doc = Doc(page(live, f"/a/{AID}"))
    assert doc.one(id="chatsplit")["class"].split()[:2] == ["c-split", "chatsplit"]
    doc.one(id="mdpane")
    doc.one("div", class_="c-chat", data_ci_scroll=True)  # classic chatlog.js scroller
    for hook in ("ci", "chat", "sa-live", "ci-pending", "pane", "keypad-wrap", "sa-body"):
        doc.one(id=hook)
    comp = doc.one("section", class_="composer", data_composer="live")
    assert "composer--flow" in comp["class"]
    doc.one("template", id="cmp-continue")


def test_live_menu_items(live):
    items = menu_items(Doc(page(live, f"/a/{AID}")))
    assert list(items) == ["newchat", "continue", "switch", "fork", "review", "usage", "btw", "copy", "exit", "exitkill"]
    assert items["newchat"]["click"] == "form[data-newchat] button[type=submit]"
    assert items["fork"]["post"] == f"/a/{AID}/fork" and items["fork"]["confirm"]
    assert items["review"]["href"] == f"/p/alpha/launch?review={AID}"
    assert items["exit"]["post"] == f"/a/{AID}/exit" and "typed" not in items["exit"]
    kill = items["exitkill"]
    assert kill["post"] == f"/a/{AID}/exitkill" and kill["typed"] == AID and kill["danger"]  # V7-SEC-6
    assert items["copy"]["copy"] == AID
    assert {items[k]["event"] for k in ("continue", "switch", "usage", "btw")} == {
        "c:continue", "c:switch", "c:usage", "c:btw"}


@pytest.mark.parametrize(("st", "want"), [("pane", "pane"), ("sub", "sub"), ("term", "chat"), ("nope", "chat")])
def test_live_first_tab_from_query(live, st, want):
    doc = Doc(page(live, f"/a/{AID}?st={st}"))
    assert [t["data-p-tab"] for t in doc.find(role="tab", aria_selected="true", data_p_tab=True)] == [want]
    assert [r["id"] for r in doc.find("input", name="agent-tab") if "checked" in r] == [f"tab-{want}"]
    assert "hidden" not in doc.one(id=f"c-pn-{want}")


def test_embed_has_the_tabs_panels_and_composer(live):
    # README A29: the chat aside's iframe has the same tabs and panels in a compact header (.c-hd.min)
    src = page(live, f"/a/{AID}?embed=1")
    doc = Doc(src)
    assert "min" in doc.one("header", class_="c-hd")["class"]
    tabs = doc.find(role="tab", data_p_tab=True)
    assert [t["data-p-tab"] for t in tabs] == ["chat", "sub", "pane"]
    assert [t["aria-selected"] for t in tabs] == ["true", "false", "false"]
    for t in tabs:  # icon-only at narrow widths: an icon and a title (the label stays in the DOM for readers)
        assert doc.one(id=t["aria-controls"])["role"] == "tabpanel"
        assert t["title"] and f'id="p-tab-{t["data-p-tab"]}"' in src
    assert len(re.findall(r'<button class="p-tab"[^>]*>\s*<svg class="i"', src)) == 3
    assert {r["id"] for r in doc.find("input", name="agent-tab")} == {"tab-chat", "tab-pane", "tab-sub"}
    for hook in ("c-pn-sub", "c-pn-pane", "keypad-wrap", "sa-body"):
        doc.one(id=hook)
    assert doc.find("form", class_="keyform") and doc.find("button", class_="danger")  # keypad, Interrupt
    assert doc.find(data_keys=True)  # the composer's Keys button flips to the Pane tab
    # still hidden in the aside: id, meta, Builder, composer toggle, move-to-aside
    assert not doc.find("h1", class_="c-nm") and not doc.find(class_="c-bbtn") and not doc.find(data_c_toaside=True)
    assert not doc.find(data_c_cmp=True) and not doc.find(class_="c-id") and not doc.find(data_c_meta=True)
    doc.one("section", class_="composer", data_composer="live")
    doc.one("button", class_="c-more")
    doc.one("button", data_c_font=True)
    # the full page has no icons in its tabs
    assert not re.search(r'<button class="p-tab"[^>]*>\s*<svg', page(live, f"/a/{AID}"))


@pytest.mark.parametrize(("st", "want"), [("pane", "pane"), ("sub", "sub"), ("term", "chat"), ("nope", "chat")])
def test_embed_first_tab_from_query(live, st, want):
    doc = Doc(page(live, f"/a/{AID}?embed=1&st={st}"))
    assert [t["data-p-tab"] for t in doc.find(role="tab", aria_selected="true", data_p_tab=True)] == [want]
    assert [r["id"] for r in doc.find("input", name="agent-tab") if "checked" in r] == [f"tab-{want}"]
    assert "hidden" not in doc.one(id=f"c-pn-{want}")


def test_classic_page_scripts_in_order(live):
    src = page(live, f"/a/{AID}")
    s = scripts(src)
    page_js = ["/static/js/agentmode.js", "/static/js/chat-send.js", "/static/js/atfiles.js",
               "/static/js/issue-save.js", "/static/js/snippets.js", "/static/js/modelpick.js",
               "/static/js/newchat.js", "/static/js/continuepop.js", "/static/js/chat.js", "/static/js/chat-md.js",
               "/static/js/chatlog.js", "/static/js/chat-items.js", "/static/js/subagents.js", "/static/js/pane.js",
               "/static/js/canvas.js"]
    idx = [s.index(x) for x in page_js]
    assert idx == sorted(idx)
    assert s.index("/static/js/client-errors.js") < idx[0] and idx[-1] < s.index("/static/t/dt/js/core.js")
    assert s.index("/static/t/dt/js/core.js") < s.index("/static/t/dt/js/session.js")
    assert "/static/js/composer-dock.js" not in s and "/static/js/jumprail.js" not in s
    assert '/static/t/dt/css/session.css' in src and '/static/css/pane.css' in src


def test_no_session_scripts_on_other_pages(live):
    s = scripts(page(live, "/tmux"))
    assert "/static/js/chat.js" not in s and "/static/t/dt/js/session.js" not in s


# ---------------------------------------------------------------- ended record
def test_ended_frame(live, monkeypatch):
    rec = registry.Record(AID, "claude", "0123abcd-0000-4000-8000-000000000000", 0, 100.0, 200.0, "%9", "alpha",
                          "/x", "", "", 300.0)
    monkeypatch.setattr(registry, "resolve", lambda *_a: registry.Resolved(ended=rec))
    src = page(live, f"/a/{AID}")
    doc = Doc(src)
    assert doc.one("section", class_="c-sess")["data-c-mode"] == "ended"
    assert "ended" in doc.one("i", data_c_dot=True)["class"]
    assert doc.one("span", class_="agent-head")["data-mode"] == "ended"
    assert not doc.find(role="tab", data_p_tab=True) and not doc.find(class_="c-bbtn")
    doc.one("section", class_="composer", data_composer="ended")
    items = menu_items(doc)
    assert list(items) == ["resume", "switch", "fork", "copy"]
    assert items["resume"]["post"] == f"/a/{AID}/resume"
    s = scripts(src)
    assert "/static/js/chat-send.js" not in s and "/static/js/canvas.js" in s and "/static/js/pane.js" not in s


# ---------------------------------------------------------------- Builder
def test_builder_frame(live):
    src = page(live, f"/a/{AID}/builder")
    doc = Doc(src)
    assert doc.one("section", class_="c-sess")["data-c-mode"] == "builder"
    b = doc.one("a", class_="c-bbtn")
    assert "on" in b["class"] and b["href"] == f"/a/{AID}" and b["aria-current"] == "page"
    tabs = doc.find("a", role="tab", data_p_tab=True)
    assert [(t["data-p-tab"], t["href"]) for t in tabs] == [
        ("chat", f"/a/{AID}"), ("sub", f"/a/{AID}?st=sub"), ("pane", f"/a/{AID}?st=pane")]
    assert not doc.find("span", class_="agent-head")  # #builder[data-mode] is agentmode.js's hook here
    assert doc.one(id="builder")["data-mode"] == "live"
    for hook in ("bld-msgs", "bld-stage", "bld-size", "bld-zoom", "bld-src", "bld-files", "bt-chat", "bt-prev"):
        doc.one(id=hook)
    s = scripts(src)
    order = ["/static/js/builder.js", "/static/js/builder-screen.js", "/static/js/builder-api.js",
             "/static/js/chat-send.js", "/static/js/chatlog.js", "/static/js/chat-items.js",
             "/static/js/continuepop.js", "/static/js/chat.js", "/static/js/chat-md.js"]
    idx = [s.index(x) for x in order]
    assert idx == sorted(idx)
    assert "/static/js/canvas.js" not in s and "/static/js/pane.js" not in s
    assert "/static/css/builder.css" in src


# ---------------------------------------------------------------- static rules of the page files
def test_page_files_static_rules():
    css = (THEME / "static/css/session.css").read_text()
    js = (THEME / "static/js/session.js").read_text()
    for f, text in (("session.css", css), ("session.js", js)):
        lines = text.splitlines()
        assert len(lines) <= 500, f
        assert all(len(ln) <= 120 for ln in lines), f
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css)  # tokens only (V7-UI-2)
    assert ":root" not in css
    assert "addEventListener('keydown'" not in js  # page keys go through pm7.keys (PARTS §12)
    # the phone composer: collapsed (height 0, still focusable for ⌘L) unless shown or the setting is open; never in
    # the aside's iframe (.embed can be narrower than 700px on a desktop)
    phone = css[css.index("@media (max-width:699px){"):]
    assert "html:not(.cmp-open) .c-sess:not(.embed):not(.cmp-on) .c-comp:has([data-composer])," in phone
    assert ".c-sess.cmp-off:not(.embed) .c-comp:has([data-composer]){height:0;" in phone
    assert ".c-sess:not(.embed):has(.c-comp [data-composer]) .c-hd .c-cmpbtn{display:inline-flex}" in phone
    assert ".c-hd .c-cmpbtn{display:none}" in css[:css.index("@media (max-width:699px){")]
    for hook in ("'cmp-on'", "'cmp-off'", "'cmp-open'", "[data-c-cmp]", "'focusin'", "'aria-controls'"):
        assert hook in js, hook
    # its shown look is Builder's on look (one rule), not a copy of the shell's .x-ib pressed rule (shell.css)
    shown = re.findall(r"[^{}\n]*\.c-cmpbtn\[aria-expanded=true\][^{}]*\{", css)
    assert [r.strip() for r in shown] == [".c-bbtn.on,.c-hd .c-cmpbtn[aria-expanded=true]{"]
    assert "accent-soft);color:var(--accent)" not in css
    assert ".c-hd .c-cmpbtn,.c-hd .c-bbtn{width:var(--ib);height:var(--ib);padding:0}" in phone
    # phone jump buttons (LAYOUT §6): faded until the chat scrolls (.on), opacity so Tab still reaches them; the
    # fade is the motion token (reduced motion: tokens.css), no raw times in the CSS; desktop keeps them as they are
    assert (".c-sess:not(.embed) .c-jump{opacity:0;pointer-events:none;"
            "transition:opacity var(--t-slide) var(--ease)}") in phone
    assert (".c-sess:not(.embed) .c-jump.on,.c-sess:not(.embed) .c-jump:has(:focus-visible)"
            "{opacity:1;pointer-events:auto}") in phone
    assert "opacity" not in css[:css.index("@media (max-width:699px){")].split(".c-jump", 1)[1].split("}", 1)[0]
    assert not re.search(r"\d(ms|s)\b", re.sub(r"/\*.*?\*/", "", css, flags=re.S))
    assert "const JUMP_IDLE_MS = 1500;" in js and "pm7.debounce(" in js and "setTimeout" not in js
    # V7-CODE-7: one ResizeObserver on the chat scroller; the setting arrives as head.js's pm7:look event
    assert "subtree" not in js and "setInterval" not in js and "MutationObserver" not in js
    assert "'pm7:look'" in js
    # the aside's iframe: starts on ?st= or Chat, a focus without a click (aside.js, ⌘L) selects Chat first
    assert "x-embed" in js and "tabBtn('chat')" in js and "'pointerdown'" in js
    assert "localStorage" not in js and "fetch(" not in js  # pm7.store / pm7.fetch only (V7-ISO-6, V7-CODE-6)
    # the chat aside's compact header (README A29): .min is the container, the tabs scroll in one row and are as tall
    # as the header (not the 44px phone tab), the spacer does not push them, Detail / Compact shows (only the full
    # page's header drops it on a narrow main), the Keys button is no longer hidden
    assert ".c-hd.min{container:hd/inline-size}" in css
    assert re.search(r"\.c-hd\.min \.c-tabs\{[^}]*flex:1 1 auto;min-width:0;[^}]*overflow-x:auto", css)
    assert re.search(r"\.c-hd\.min \.c-tabs \[role=tab\]\{height:calc\(var\(--ib\) \+ var\(--s2\) - 1px\)", css)
    assert ".c-hd.min:has(.c-tabs)>.sp{display:none}" in css and "@container hd (max-width:639px)" in css
    assert ".c-hd:not(:has(.c-tabs))>.sp{display:block}" in phone and ".c-hd.min>.sp" not in css
    assert ".c-hd:not(.min) .c-view{display:none}" in css and ".c-hd .c-view{display:none}" not in css
    assert "[data-keys]" not in css
    for name in ("agent.html", "agent_ended.html", "builder.html", "session/_frame.html"):
        tpl = (THEME / "templates" / name).read_text()
        assert not re.search(r"<script(?![^>]*\bsrc=)|\sstyle=|\son[a-z]+=", tpl), name  # CSP (V7-SEC-2)
        assert "jumprail" not in tpl and "composer-dock" not in tpl, name


# ------------------------------------------------- js/session.js in node: chat end pin, composer toggle, jump buttons
SESSION_JS = THEME / "static/js/session.js"
HARNESS = r"""
const L = [], RO = [], DB = [], W = [];
function el(cls, attrs) {
  const c = new Set(cls), a = Object.assign({}, attrs || {});
  return { id: '', value: '', style: { setProperty() {}, removeProperty() {} },
    classList: { contains: (k) => c.has(k), add: (k) => c.add(k), remove: (k) => c.delete(k),
                 toggle: (k, on) => { on = on === undefined ? !c.has(k) : on; if (on) c.add(k); else c.delete(k); } },
    getAttribute: (k) => (k in a ? a[k] : null), setAttribute: (k, v) => { a[k] = String(v); },
    removeAttribute: (k) => { delete a[k]; }, attrs: a, getClientRects: () => [1], focus() {},
    contains: () => false, querySelector: () => null, matches: () => false, closest: () => null };
}
const root = el([]), sess = el(['c-sess'], { 'data-agent': 'claude-1' }), box = el(['c-comp']);
const btn = el(['c-cmpbtn'], { 'aria-expanded': 'false' }), ta = el([]);
box.querySelector = (q) => (q === '[data-cmp-input]' ? ta : null);
const sc = el(['c-chat']), jump = el(['c-jump']), body = el([]), chatTab = el([], { 'aria-selected': 'false' });
let clicks = 0;
chatTab.click = () => { clicks += 1; };
Object.assign(sc, { scrollHeight: 2000, clientHeight: 600, scrollTop: 1400 });
sc.matches = (q) => q === '.c-sess [data-ci-scroll]';
globalThis.window = { location: { href: 'http://h/a/claude-1' }, addEventListener: (t, f) => W.push([t, f]),
  requestAnimationFrame: () => 1,
  ResizeObserver: class { constructor(f) { RO.push(f); } observe(t) { RO.push(t); } },
  pm7: { $$: (q) => (q === '.c-hd [data-c-cmp]' ? [btn] : []), store: { get: (k, d) => d, set() {} },
         ready: (f) => f(), phone: () => true,
         debounce: (f, ms) => { const x = { f, ms, armed: 0 }; DB.push(x); return () => { x.armed += 1; }; } } };
Object.assign(globalThis, { ResizeObserver: window.ResizeObserver, getComputedStyle: () => ({}) });
globalThis.document = { documentElement: root, addEventListener: (t, f, o) => L.push([t, f, o]),
  getElementById: () => null, body, activeElement: body,
  querySelector: (q) => (q.indexOf('[data-ci-scroll]') >= 0 ? sc : q.indexOf('.c-comp') >= 0 ? box
    : q === '.c-sess' ? sess : q === '.c-jump' ? jump : q.indexOf('data-p-tab="chat"') >= 0 ? chatTab : null) };
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
const scroll = (top) => { sc.scrollTop = top; L.filter((x) => x[0] === 'scroll').forEach((x) => x[1]({ target: sc })); };
const resize = (h) => { sc.clientHeight = h; RO[0]([]); };
const look = () => L.filter((x) => x[0] === 'pm7:look').forEach((x) => x[1]({ detail: { area: 'prefs' } }));
"""


def run_session(code: str):
    out = subprocess.run(["node", "-e", HARNESS + code, str(SESSION_JS)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_chat_end_stays_in_view_when_the_column_shrinks():
    # D1: the phone composer showing shrinks .c-chat without scrolling it; at the end, the newest lines stay in view
    got = run_session("""
      RO[0]([]);                                   // first callback: notes the end, moves nothing
      const a = sc.scrollTop; resize(390); const b = sc.scrollTop;
      scroll(500); resize(600); const c = sc.scrollTop;   // scrolled up to read: left where it is
      scroll(1400); resize(0); const d = sc.scrollTop;    // the Chat panel hidden: nothing to pin
      console.log(JSON.stringify([RO[1] === sc, a, b, c, d]));""")
    assert got == [True, 1400, 2000, 500, 1400]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_composer_toggle_controls_its_box_and_follows_the_setting():
    # aria-controls names the .c-comp box (id given by session.js); head.js applying new prefs (pm7:look) re-syncs
    got = run_session("""
      const a = [box.id, btn.attrs['aria-controls'], btn.attrs['aria-expanded'], btn.title];
      root.classList.add('cmp-open'); look();
      const b = [btn.attrs['aria-expanded'], btn.title];
      root.classList.remove('cmp-open'); look(); const c = btn.attrs['aria-expanded'];
      sess.classList.add('cmp-off'); root.classList.add('cmp-open'); look();
      console.log(JSON.stringify([a, b, c, btn.attrs['aria-expanded']]));""")
    assert got[0] == ["c-comp", "c-comp", "false", "Show composer"]
    assert got[1] == ["true", "Hide composer"]
    assert got[2] == "false"  # the setting back to collapsed
    assert got[3] == "false"  # hidden on this page (.cmp-off) wins over the setting


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_jump_buttons_show_while_the_chat_scrolls():
    # LAYOUT §6 (phone): a user scroll adds .on and re-arms the idle timer; the timer firing removes it; a follow
    # scroll (at the end before and after: streaming, the pin) leaves it off; a jump click re-arms it
    got = run_session("""
      const idle = DB.find((x) => x.ms === 1500), on = () => jump.classList.contains('on');
      const a = on();
      scroll(1000); const b = [on(), idle.armed];
      scroll(900); const c = idle.armed;
      idle.f(); const d = on();
      scroll(1400); idle.f();                       // back to the end: a user scroll, then idle
      sc.scrollHeight = 2300; scroll(1700); const e = [on(), idle.armed];   // new lines followed: stays off
      const cl = L.find((x) => x[0] === 'click')[1], jb = el([], { 'data-c-jump': 'top' });
      jb.closest = (q) => (q === '.c-sess' || q === '[data-c-jump]' ? jb : null);
      cl({ target: jb }); const f = [on(), idle.armed, sc.scrollTop];
      console.log(JSON.stringify([a, b, c, d, e, f]));""")
    assert got[0] is False
    assert got[1] == [True, 1]
    assert got[2] == 2  # every scroll re-arms the timer
    assert got[3] is False
    assert got[4] == [False, 3]
    assert got[5][0] is True and got[5][1] > 3 and got[5][2] == 0


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_embed_focus_without_a_click_selects_the_chat_tab():
    # README A29: aside.js puts the cursor into the composer (⌘L) with contentWindow.focus(); on a Pane tab the
    # composer is hidden, so the Chat tab is selected first. Not on the full page, not on a click into the iframe.
    got = run_session("""
      const focus = W.find((x) => x[0] === 'focus')[1], down = L.find((x) => x[0] === 'pointerdown')[1];
      focus(); const a = clicks;                                   // the full page: nothing
      body.classList.add('x-embed');
      focus(); const b = clicks;                                   // the aside's iframe, no click: Chat
      chatTab.attrs['aria-selected'] = 'true'; focus(); const c = clicks;   // already Chat
      chatTab.attrs['aria-selected'] = 'false'; down(); focus(); const d = clicks;   // the user clicked in
      console.log(JSON.stringify([a, b, c, d]));""")
    assert got == [0, 1, 1, 1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_embed_window_refocus_does_not_switch_the_tab():
    # alt-tab back (the whole browser window lost focus: blur with the shell not focused) or a tab shown again
    # (hidden): the next focus is the browser's, not aside.js's; focus moving to the shell (blur, shell focused) is not
    got = run_session("""
      const on = (t) => W.find((x) => x[0] === t)[1], focus = on('focus'), blur = on('blur');
      const vis = L.find((x) => x[0] === 'visibilitychange')[1], down = L.find((x) => x[0] === 'pointerdown')[1];
      body.classList.add('x-embed');
      window.parent = { document: { hasFocus: () => false } };
      blur(); focus(); const a = clicks;                           // alt-tab and back: stays on the Pane tab
      focus(); const b = clicks;                                   // only the first focus after it is skipped
      window.parent = { document: { hasFocus: () => true } };
      blur(); focus(); const c = clicks;                           // focus went to the shell, aside.js gives it back
      document.hidden = true; vis(); document.hidden = false;
      focus(); const d = clicks;                                   // the tab was hidden: skipped
      window.parent = { document: { hasFocus: () => false } };
      blur(); down(); focus(); const e = clicks;                   // a click clears the mark (and is a click)
      console.log(JSON.stringify([a, b, c, d, e]));""")
    assert got == [0, 1, 2, 2, 2]
