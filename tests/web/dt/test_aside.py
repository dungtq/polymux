"""The dt chat aside (docs/themes/dt/SHELL.md §4 "Chat aside", DESIGN.md §6.1, LAYOUT.md §2): shell/_aside.html hooks,
base.html loading aside.css / aside.js (aside.js also in embed pages, for the bridge), head.js restoring html.aside
and --aside-pct before paint, and js/aside.js in node against a small fake DOM: open / close / storage, terminal
ids, the phone path, ⌘L / ⌘⇧L / resize keys in the one key table, the Esc soft layer, the postMessage origin and
source checks, and the embed bridge."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pm.web import themes

from .kit import render_base

THEME = themes.HERE / "dt"
JS = THEME / "static" / "js"
ASIDE_TPL = THEME / "templates" / "shell" / "_aside.html"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


# ---------------------------------------------------------------- templates
def test_aside_markup_has_the_hooks():
    html = render_base()
    aside = html[html.index('<aside id="x-aside"'):html.index("</aside>", html.index('<aside id="x-aside"'))]
    for hook in ("data-aside-edge", 'role="separator"', 'aria-valuemin="20"', 'aria-valuemax="50"', "data-aside-dot",
                 "data-aside-state", "data-aside-name", "data-aside-kind", 'data-act="astab"', 'data-act="aside"',
                 "data-aside-body", "data-aside-empty"):
        assert hook in aside, hook
    # icon-only buttons carry aria-label and title (V7-UI-6); Open as tab starts disabled (nothing shown yet)
    for b in re.findall(r"<button[^>]*>", aside):
        assert "aria-label=" in b and "title=" in b, b
    assert re.search(r'data-act="astab"[^>]*disabled', aside)


def test_base_loads_the_aside_assets_also_in_embed():
    page, embed = render_base(), render_base(embed=True)
    assert '<link rel="stylesheet" href="/static/t/dt/css/aside.css">' in page
    css = re.findall(r'href="/static/t/dt/css/([^"]+)"', page)
    assert css.index("aside.css") == css.index("shell.css") + 1
    for html in (page, embed):  # embed pages need aside.js for the Esc / key bridge
        js = re.findall(r'<script src="/static/t/dt/js/([^"]+)"', html)
        assert js.index("aside.js") == js.index("live.js") + 1
    assert 'id="x-aside"' not in embed


# ---------------------------------------------------------------- head.js (before paint)
HEAD = r"""
const [store, width] = JSON.parse(process.argv[2]);
const cls = new Set(), props = {};
globalThis.window = { innerWidth: width, localStorage: { getItem: (k) => (k in store ? store[k] : null) } };
globalThis.document = { documentElement: { classList: { add: (c) => cls.add(c),
    toggle: (c, on) => { if (on) cls.add(c); else cls.delete(c); } }, setAttribute() {}, removeAttribute() {},
  style: { setProperty: (k, v) => { props[k] = v; }, removeProperty: (k) => { delete props[k]; } } },
  addEventListener() {}, querySelector: () => null };
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
console.log(JSON.stringify([[...cls].sort(), props]));
"""


def head(store: dict, width: int):
    out = subprocess.run(["node", "-e", HEAD, str(JS / "head.js"), json.dumps([store, width])],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@needs_node
def test_head_restores_the_aside_on_docked_widths_only():
    on = {"pm.v7.aside.open": "1", "pm.v7.aside.pct": "36"}
    assert head(on, 1512) == [["aside"], {"--aside-pct": "36vw"}]
    assert head(on, 899) == [[], {"--aside-pct": "36vw"}]  # overlay widths: a layer, never restored
    assert head({"pm.v7.aside.open": "0", "pm.v7.aside.pct": "70"}, 2560) == [[], {}]  # out of range: the token


# ---------------------------------------------------------------- aside.js in node (fake DOM)
HARNESS = r"""
const CFG = JSON.parse(process.argv[2]);
const log = [], store = Object.assign({}, CFG.store || {}), listeners = [], winL = [], softs = [], regs = [];
function cl(init) {
  const s = new Set(init || []);
  return { add: (c) => s.add(c), remove: (c) => s.delete(c), contains: (c) => s.has(c),
    toggle: (c, on) => { on = on === undefined ? !s.has(c) : !!on; if (on) s.add(c); else s.delete(c); return on; },
    list: () => [...s].sort() };
}
function el(tag, init) {
  const a = Object.assign({}, init || {});
  const n = { tagName: tag, classList: cl(), hidden: false, textContent: '', title: '', disabled: false, kids: [],
    style: { p: {}, setProperty(k, v) { this.p[k] = v; }, removeProperty(k) { delete this.p[k]; } },
    getAttribute: (k) => (k in a ? a[k] : null), setAttribute: (k, v) => { a[k] = String(v); },
    removeAttribute: (k) => { delete a[k]; }, hasAttribute: (k) => k in a, attrs: a,
    addEventListener: (t, f) => { (n.on[t] = n.on[t] || []).push(f); }, on: {},
    getClientRects: () => [1], focus() { doc.activeElement = n; }, blur() { doc.activeElement = doc.body; },
    querySelector: () => null, closest: () => null, contains: (x) => x === n };
  return n;
}
const R = el('html'), body = el('body');
if (CFG.embed) body.classList.add('x-embed');
(CFG.html || []).forEach((c) => R.classList.add(c));
const parts = { '[data-aside-edge]': el('div'), '[data-aside-dot]': el('i'), '[data-aside-state]': el('span'),
  '[data-aside-name]': el('b'), '[data-aside-kind]': el('span'), '[data-act=astab]': el('button'),
  '[data-aside-body]': el('div') };
const bdi = el('bdi');
parts['[data-aside-name]'].querySelector = (s) => (s === 'bdi' ? bdi : null);
const abody = parts['[data-aside-body]'];
abody.appendChild = (f) => { abody.frame = f; f.remove = () => { if (abody.frame === f) abody.frame = null; }; };
const aside = el('aside');
aside.querySelector = (s) => (s === '[data-aside-body] iframe.x-aframe' ? abody.frame || null : parts[s] || null);
aside.contains = (x) => !!x && (Object.values(parts).includes(x) || x === abody.frame || x === aside);
const curRow = CFG.cur ? el('a', { 'data-sess-id': CFG.cur }) : null;
const doc = globalThis.document = {
  documentElement: R, body: body, activeElement: body,
  getElementById: (id) => (id === 'x-aside' && !CFG.embed ? aside : null),
  querySelector: (s) => (s === '#x-sess a.x-sr.cur[data-sess-id]' ? curRow : null),
  createElement: (t) => el(t), contains: () => true,
  addEventListener: (t, f, o) => listeners.push([t, f, !!o])
};
const parentMsgs = [];
globalThis.window = {
  innerWidth: CFG.width || 1512, location: { origin: 'https://pm.test', assign: (h) => log.push(['go', h]) },
  addEventListener: (t, f) => winL.push([t, f]),
  frameElement: CFG.inAside ? { closest: (s) => (s === '#x-aside' ? {} : null) } : null,
  CSS: { escape: (x) => x }
};
window.parent = CFG.embed ? { postMessage: (m, o) => parentMsgs.push([m, o]) } : window;
const table = [{ id: 'aside', keys: [], run: () => log.push('keys.js setAside') },
               { id: 'search', keys: ['mod+shift+P', 'mod+K'], run: () => log.push('search') }];
window.pm7 = {
  isApple: false, ready: (f) => f(), $$: () => [], emit: (t, x) => log.push([t, x]),
  phone: () => (CFG.width || 1512) < 700, overlay: () => (CFG.width || 1512) < 900,
  toast: (t) => log.push(['toast', t]),
  store: { get: (k, dv) => ('pm.v7.' + k in store ? store['pm.v7.' + k] : dv === undefined ? null : dv),
           set: (k, v) => { store['pm.v7.' + k] = String(v); }, remove: (k) => { delete store['pm.v7.' + k]; } },
  layers: { soft: (x) => softs.push(x), sync() {}, top: () => null },
  keys: CFG.embed ? undefined : { table: table, label: (s) => s,
    register: (scope, rows) => { rows.forEach((r) => regs.push(Object.assign({ scope: scope }, r))); } },
  tabs: { open: (k, i, h) => log.push(['tabs.open', k, i, h]), close: (k, i) => log.push(['tabs.close', k, i]),
          current: () => (CFG.tab ? el('div', { 'data-tab-kind': CFG.tab[0], 'data-tab-id': CFG.tab[1] }) : null) }
};
globalThis.CSS = window.CSS;
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
const A = window.pm7.aside;
const frame = () => (abody.frame ? { src: abody.frame.src, title: abody.frame.title } : null);
const state = () => ({ html: R.classList.list(), store: Object.assign({}, store), frame: frame(), cur: A ? A.current() : null,
  pct: R.style.p['--aside-pct'] || null, chip: parts['[data-aside-kind]'].textContent, name: bdi.textContent });
const msg = (data, origin, src) => winL.filter((x) => x[0] === 'message').forEach((x) => x[1](
  { data: data, origin: origin, source: src }));
const key = (spec, e) => regs.find((r) => r.keys.indexOf(spec) >= 0).run(e || {});
const out = [];
"""


def run(code: str, **cfg):
    out = subprocess.run(["node", "-e", HARNESS + code + "\nconsole.log(JSON.stringify(out));", str(JS / "aside.js"),
                          json.dumps(cfg)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@needs_node
def test_open_close_and_storage():
    got = run("out.push(A.open('claude-1c70ee', {focus: false}), state()); A.close(); out.push(state());"
              "out.push(A.open('bad id/..'), A.open(''), state().html);")
    opened, closed, bad = got[1], got[2], got[3:]
    assert got[0] is True
    assert opened["html"] == ["aside"] and opened["cur"] == "claude-1c70ee" and opened["name"] == "claude-1c70ee"
    assert opened["frame"] == {"src": "/a/claude-1c70ee?embed=1", "title": "Chat with claude-1c70ee"}
    assert opened["store"] == {"pm.v7.aside.open": "1", "pm.v7.aside.last": "claude-1c70ee"}
    assert opened["chip"] == "claude"  # kind from the id when no session row says it
    assert closed["html"] == [] and closed["frame"] is None and closed["cur"] is None
    assert closed["store"]["pm.v7.aside.open"] == "0" and closed["store"]["pm.v7.aside.last"] == "claude-1c70ee"
    assert bad == [False, False, []]


@needs_node
def test_terminal_ids_open_the_term_embed():
    got = run("A.open('$3', {focus: false}); out.push(state());")[0]
    assert got["frame"]["src"] == "/term/s3?embed=1" and got["chip"] == "terminal" and got["cur"] == "$3"


@needs_node
def test_phone_opens_the_tab_instead():
    got = run("out.push(A.open('codex-44aa01'), state(), log);", width=390)
    assert got[0] is True and got[1]["html"] == [] and got[1]["frame"] is None
    assert ["tabs.open", "agent", "codex-44aa01", "/a/codex-44aa01"] in got[2]


@needs_node
def test_keys_layer_and_command_row():
    got = run("out.push(regs.map((r) => [r.id, r.keys, r.scope.group]), softs.map((s) => s.rank));"
              "table[0].run(); out.push(state());", cur="claude-1c70ee")
    assert got[0] == [["aside-l", ["mod+L"], "Panels"], ["aside-move", ["mod+shift+L"], "Panels"],
                      ["aside-size", ["ArrowLeft", "ArrowRight"], "Panels"]]
    assert got[1] == [69]  # below keys.js's rank-70 aside layer: ours closes it (DESIGN §7: aside last)
    assert got[2]["cur"] == "claude-1c70ee"  # keys.js's "aside" command row now runs toggle() (current session)


@needs_node
def test_mod_l_opens_then_closes_and_esc_layer_closes():
    got = run("key('mod+L'); out.push(state().cur); key('mod+L'); out.push(state().html);"
              "key('mod+L'); softs[0].close(); out.push(state().html, softs[0].scrim());",
              store={"pm.v7.aside.last": "codex-44aa01"})
    # no focused row or current session: the last agent; again (composer not loaded yet: it waits) -> stays
    assert got[0] == "codex-44aa01"
    assert got[2] == [] and got[3] is False  # Esc soft layer closes; no scrim when docked


@needs_node
def test_mod_shift_l_moves_the_session_tab():
    got = run("key('mod+shift+L'); out.push(state(), log);", tab=["agent", "claude-1c70ee"])
    assert got[0]["store"] == {"pm.v7.aside.last": "claude-1c70ee", "pm.v7.aside.open": "1"}
    assert ["tabs.close", "agent", "claude-1c70ee"] in got[1]
    back = run("A.open('codex-44aa01', {focus: false}); key('mod+shift+L'); out.push(state(), log);", tab=["home", ""])
    assert back[0]["html"] == [] and ["tabs.open", "agent", "codex-44aa01", "/a/codex-44aa01"] in back[1]


@needs_node
def test_resize_keys_store_the_share():
    got = run("key('ArrowLeft', {code: 'ArrowLeft'}); out.push(state().pct, store['pm.v7.aside.pct']);"
              "for (let i = 0; i < 40; i++) key('ArrowRight', {code: 'ArrowRight'}); out.push(state().pct);",
              store={"pm.v7.aside.pct": "30"})
    assert got == ["31vw", "31", "20vw"]  # clamped at 20 %


@needs_node
def test_messages_only_from_the_aside_frame_and_own_origin():
    got = run("A.open('claude-1c70ee', {focus: false}); const w = abody.frame.contentWindow = {};"
              "msg({pm7: 'esc'}, 'https://evil.test', w); out.push(state().html);"
              "msg({pm7: 'esc'}, 'https://pm.test', {}); out.push(state().html);"
              "msg({pm7: 'key', spec: 'mod+K'}, 'https://pm.test', w); out.push(log.includes('search'));"
              "msg({pm7: 'key', spec: 'mod+W'}, 'https://pm.test', w); msg('esc', 'https://pm.test', w);"
              "out.push(state().html); msg({pm7: 'esc'}, 'https://pm.test', w); out.push(state().html);")
    assert got == [["aside"], ["aside"], True, ["aside"], []]


@needs_node
def test_restore_on_load_without_writing_the_flag():
    got = run("out.push(state());", html=["aside"], store={"pm.v7.aside.last": "claude-1c70ee", "pm.v7.aside.open": "1"})
    assert got[0]["frame"]["src"] == "/a/claude-1c70ee?embed=1" and got[0]["html"] == ["aside"]
    gone = run("out.push(state().html);", html=["aside"], store={"pm.v7.aside.open": "1"})
    assert gone == [[]]  # nothing to show: the restored column is taken back


@needs_node
def test_embed_bridge_only_inside_the_aside_frame():
    code = ("const kd = listeners.filter((x) => x[0] === 'keydown');"
            "kd.forEach((x) => x[1]({ctrlKey: true, metaKey: false, altKey: false, shiftKey: false, code: 'KeyL',"
            " preventDefault() {}}));"
            "kd.forEach((x) => x[1]({ctrlKey: true, metaKey: false, altKey: false, shiftKey: false, code: 'KeyW',"
            " preventDefault() {}}));"
            "softs.forEach((s) => s.open() && s.close()); out.push(softs.map((s) => s.rank), parentMsgs);")
    got = run(code, embed=True, inAside=True)
    assert got[0] == [90]
    assert got[1] == [[{"pm7": "key", "spec": "mod+L"}, "https://pm.test"], [{"pm7": "esc"}, "https://pm.test"]]
    assert run(code, embed=True, inAside=False) == [[], []]  # other embeds (drawer, Builder, term tab): nothing


# ---------------------------------------------------------------- btw, ⌘L on a session tab, .md links (README A9)
BTW_STUB = ("window.pm7.btw = {panel: (id) => el('div', {'data-btw-agent': id}),"
            " ask: (id, q) => log.push(['ask', id, q]), focus: (id) => log.push(['focus', id]), focused: () => false};")


@needs_node
def test_btw_shows_the_panel_in_the_aside_without_storage():
    got = run(BTW_STUB + "out.push(A.btw('claude-1c70ee'), state(), log.slice());"
              "out.push(A.btw('codex-44aa01', 'why?'), log.slice(-2)); A.close(); out.push(state());"
              "out.push(A.btw('$3'), A.btw('bad id'));")
    assert got[0] is True
    st = got[1]
    assert st["html"] == ["aside"] and st["cur"] == "claude-1c70ee"
    assert st["name"] == "btw · claude-1c70ee" and st["chip"] == "no tools"
    assert st["store"] == {}  # a side question is not restored on the next page
    assert ["focus", "claude-1c70ee"] in got[2]
    assert ["pm7:aside", {"open": True, "id": "claude-1c70ee", "btw": True}] in got[2]
    assert got[3] is True and ["ask", "codex-44aa01", "why?"] in got[4]
    assert got[5]["html"] == [] and got[5]["cur"] is None
    assert got[6:] == [False, False]  # terminals and bad ids have no btw


@needs_node
def test_btw_is_not_used_on_phones():
    got = run(BTW_STUB + "out.push(A.btw('claude-1c70ee'), state().html, log);", width=390)
    assert got[0] is False and got[1] == [] and any(x[0] == "toast" for x in got[2] if isinstance(x, list))


@needs_node
def test_mod_l_on_a_session_tab_focuses_its_own_composer():
    code = ("const own = el('textarea'); doc.querySelector = (s) => (s === '#main .c-sess textarea[data-cmp-input]'"
            " ? own : null);"
            "key('mod+L'); out.push(doc.activeElement === own, state().html);"
            "key('mod+L'); out.push(doc.activeElement === own, state().html);")
    got = run(code, store={"pm.v7.aside.last": "codex-44aa01"})
    assert got == [True, [], False, []]  # the aside never opens with the same chat beside its own tab
    # with the aside open, ⌘L keeps its aside meaning (the aside's composer; not loaded yet here: it waits)
    opened = run(code.split("key(")[0] + "A.open('codex-44aa01', {focus: false}); key('mod+L');"
                 "out.push(state().html, doc.activeElement === own);")
    assert opened == [["aside"], False]


@needs_node
def test_md_links_from_the_aside_chat_open_the_reader_dialog():
    # user issue #250: always the centred reader (js/mdview.js), also on that agent's own session page
    code = ("A.open('claude-1c70ee', {focus: false}); const w = abody.frame.contentWindow = {};"
            "window.pm7.md = {open: (i, p) => log.push(['md', i, p])};"
            "doc.getElementById = (id) => (id === 'x-aside' ? aside : id === 'chatsplit' ? el('div',"
            " {'data-agent': 'claude-1c70ee'}) : null);"
            "msg({pm: 'drawer', act: 'md', id: 'claude-1c70ee', path: 'docs/A B.md'}, 'https://pm.test', w);"
            "msg({pm: 'drawer', act: 'md', id: 'claude-1c70ee', path: 'x.md'}, 'https://evil.test', w);"
            "msg({pm: 'drawer', act: 'md', id: 'claude-1c70ee', path: 'y.md'}, 'https://pm.test', {});"
            "out.push(log.filter((x) => x[0] === 'md'), state().html);")
    got = run(code)
    assert got[0] == [["md", "claude-1c70ee", "docs/A B.md"]] and got[1] == ["aside"]


@needs_node
def test_head_state_from_the_open_option_or_a_list_row():
    code = ("const dot = parts['[data-aside-dot]'];"
            "A.open('claude-1c70ee', {focus: false, state: 'ended'}); out.push(dot.classList.list());"
            "A.open('codex-44aa01', {focus: false, state: 'bogus'}); out.push(dot.classList.list());"
            "const rd = el('i'); rd.classList.add('dot'); rd.classList.add('waiting');"
            "const row = el('div'); row.querySelector = (s) => (s === '.dot' ? rd : null);"
            "doc.querySelector = (s) => (s === '.p-gr[data-row-id=\"devin-1\"]' ? row : null);"
            "A.open('devin-1', {focus: false}); out.push(dot.classList.list());")
    assert run(code) == [["ended"], ["unknown"], ["waiting"]]
