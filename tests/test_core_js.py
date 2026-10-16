"""static/js/core.js (window.pm helpers) run in node, plus a static check that the feature scripts use them."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "pm" / "static"
CORE = STATIC / "js" / "core.js"
# core.js itself; login-passkey.js runs on the login page, which does not extend base.html and so never
# loads core.js.
RAW_FETCH_OK = {"core.js", "login-passkey.js"}

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

HARNESS = r"""
const calls = [];
globalThis.window = { fetch: (u, o) => { calls.push([u, o]); return 'R'; } };
globalThis.Headers = class { constructor(h) { this.m = {}; if (h && h.m) Object.assign(this.m, h.m); }
  set(k, v) { this.m[k] = v; } };
let meta = { content: 'TOK' }, input = { value: 'INP' };
globalThis.document = { querySelector: (s) => s === 'meta[name="csrf-token"]' ? meta : (s === 'input[name="csrf"]' ? input : null) };
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
const pm = window.pm;
"""


def run(code: str):
    out = subprocess.run(["node", "-e", HARNESS + code, str(CORE)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@node
def test_esc():
    assert run(r"""console.log(JSON.stringify([pm.esc(`<a href="x">&'</a>`), pm.esc(5), pm.esc(null)]));""") == [
        "&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;", "5", "null"]


@node
def test_debounce():
    res = run(r"""
const got = [];
const f = pm.debounce(function (a) { got.push([this && this.n, a]); }, 20);
const ctx = { n: 7, f };
ctx.f(1); ctx.f(2); ctx.f(3);
setTimeout(() => { ctx.f(4); }, 60);
setTimeout(() => console.log(JSON.stringify(got)), 120);
""")
    assert res == [[7, 3], [7, 4]]


@node
def test_csrf_meta_then_input_fallback():
    assert run(r"""
const a = pm.csrf(); meta = null; const b = pm.csrf(); input = null; const c = pm.csrf();
console.log(JSON.stringify([a, b, c]));""") == ["TOK", "INP", ""]


@node
def test_fetch_defaults_and_passthrough():
    res = run(r"""
const sig = { s: 1 };
pm.fetch('/a');
pm.fetch('/b', { cache: 'no-store', signal: sig, keepalive: true, method: 'POST', body: 'x=1', headers: { A: '1' } });
pm.fetch('/c', { credentials: 'omit' });
const o = calls.map(([u, o]) => [u, o, o.signal === sig]);
console.log(JSON.stringify(o));""")
    assert res[0] == ["/a", {"credentials": "same-origin"}, False]
    assert res[1] == ["/b", {"cache": "no-store", "signal": {"s": 1}, "keepalive": True, "method": "POST",
                             "body": "x=1", "headers": {"A": "1"}, "credentials": "same-origin"}, True]
    assert res[2] == ["/c", {"credentials": "omit"}, False]


@node
def test_fetch_csrf_transports():
    res = run(r"""
const mk = () => { const m = new Map([['k', 'v']]); return { set: (k, v) => m.set(k, v), m }; };
const h = { A: '1' };
pm.fetch('/n', { headers: h });
pm.fetch('/h', { csrf: 'header', headers: h });
const b1 = mk(); pm.fetch('/b', { csrf: 'body', body: b1 });
const b2 = mk(); pm.fetch('/both', { csrf: 'both', body: b2, headers: new Headers() });
pm.fetch('/s', { csrf: 'body', body: 'a=1' });
console.log(JSON.stringify([
  calls[0][1], calls[1][1], h, [...b1.m], 'csrf' in calls[2][1], calls[2][1].headers === undefined,
  [...b2.m], calls[3][1].headers.m, calls[4][1].body]));""")
    assert res[0] == {"headers": {"A": "1"}, "credentials": "same-origin"}
    assert res[1] == {"headers": {"A": "1", "X-CSRF-Token": "TOK"}, "credentials": "same-origin"}
    assert res[2] == {"A": "1"}  # caller's headers object is not mutated
    assert res[3] == [["k", "v"], ["csrf", "TOK"]] and res[4] is False and res[5] is True
    assert res[6] == [["k", "v"], ["csrf", "TOK"]] and res[7] == {"X-CSRF-Token": "TOK"}
    assert res[8] == "a=1"  # string bodies are never rewritten


def _scripts():
    yield STATIC / "pm.js"
    yield from sorted((STATIC / "js").glob("*.js"))


def test_no_csrf_meta_lookups_outside_core():
    pat = re.compile(r"""meta\[name=["']?csrf-token""")
    bad = [p.name for p in _scripts() if p.name not in RAW_FETCH_OK and pat.search(p.read_text())]
    assert bad == []


def test_no_raw_fetch_outside_core():
    pat = re.compile(r"(?<![.\w])fetch\(")
    bad = [f"{p.name}:{i}" for p in _scripts() if p.name not in RAW_FETCH_OK
           for i, line in enumerate(p.read_text().splitlines(), 1) if pat.search(line)]
    assert bad == []


def test_core_has_no_eval_and_documents_click_rule():
    s = CORE.read_text()
    assert "eval(" not in s and "new Function" not in s
    assert "Do NOT merge them" in s


# --- pm.notify / pm.store ------------------------------------------------
# A tiny DOM: elements parse innerHTML into flat children (enough for flash fragments), simple selectors only.
# setTimeout is faked after core.js loads: `timers` records [fn, ms]; fire() runs and clears them.
DOM = r"""
class El {
  constructor(id, cls, attrs) { this.id = id || ''; this.className = cls || ''; this.attrs = attrs || ''; this.kids = []; this.parentNode = null; this._h = ''; }
  set innerHTML(h) {
    this.kids.forEach(k => { k.parentNode = null; }); this.kids = []; this._h = h;
    const re = /<(\w+)([^>]*)>([^<]*)/g; let m;
    while ((m = re.exec(h))) {
      const c = /class="([^"]*)"/.exec(m[2]), k = new El('', c ? c[1] : '', m[2]);
      k.tag = m[1]; k.text = m[3]; k.parentNode = this; this.kids.push(k);
    }
  }
  get innerHTML() { return this._h; }
  set textContent(t) { this.innerHTML = ''; this._h = t; }
  matches(sel) {
    return sel.split(',').some(s => {
      s = s.trim();
      if (s[0] === '#') return this.id === s.slice(1);
      if (s[0] === '.') return s.slice(1).split('.').every(c => this.className.split(' ').includes(c));
      if (s[0] === '[') return this.attrs.includes(s.slice(1, -1));
      return this.tag === s;
    });
  }
  querySelector(sel) { return this.kids.find(k => k.matches(sel)) || null; }
  contains(n) { return this.kids.includes(n); }
}
const listeners = {};
let flashEl = new El('flash', 'flash-inline');
globalThis.window = {};
globalThis.document = {
  getElementById: (id) => (id === 'flash' ? flashEl : null),
  addEventListener: (t, f) => { (listeners[t] = listeners[t] || []).push(f); },
};
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
const pm = window.pm;
let timers = [];
globalThis.setTimeout = (fn, ms) => { timers.push([fn, ms]); return timers.length; };
const fire = () => { const t = timers; timers = []; t.forEach(([fn]) => fn()); };
const swap = (el) => listeners['htmx:afterSwap'].forEach(f => f({ target: el, detail: { target: el } }));
const out = (v) => console.log(JSON.stringify(v));
"""


def run_dom(code: str):
    out = subprocess.run(["node", "-e", DOM + code, str(CORE)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@node
def test_notify_text_escapes_and_ok_clears_after_ttl():
    res = run_dom(r"""
const box = pm.notify('Effort set to <high> & "x".');
const html = flashEl.innerHTML, ms = timers.map(t => t[1]);
fire();
out([box === flashEl, html, ms, flashEl.innerHTML, flashEl.kids.length]);""")
    assert res == [True, '<p class="flash ok">Effort set to &lt;high&gt; &amp; &quot;x&quot;.</p>', [4000], "", 0]


@node
def test_notify_err_stays_and_custom_ttl_target():
    res = run_dom(r"""
pm.notify('Nope', { kind: 'err' });
const errTimers = timers.length, errHtml = flashEl.innerHTML;
const other = new El('', 'flash-inline');
pm.notify('Done', { ttl: 1500, target: other });
const ms = timers.map(t => t[1]);
pm.notify('Kept', { ttl: 0 });
const keep0 = timers.length;
fire();
out([errTimers, errHtml, ms, keep0, other.innerHTML, flashEl.innerHTML]);""")
    assert res == [0, '<p class="flash err">Nope</p>', [1500], 1, "", '<p class="flash ok">Kept</p>']


@node
def test_notify_html_inserts_server_fragment_and_keeps_actions():
    res = run_dom(r"""
const r = [];
pm.notify('<p class="flash ok">Forked.</p>', { html: true }); r.push([flashEl.innerHTML, timers.length]); timers = [];
pm.notify('<p class="flash ok">Forked.</p> <a href="/a/x">open</a>', { html: true }); r.push(timers.length);
pm.notify('<p class="flash ok" data-image-path="/u/1.png">File attached.</p>', { html: true }); r.push(timers.length);
pm.notify('<p class="flash err">Refused.</p>', { html: true }); r.push(timers.length);
pm.notify('{"detail":"unknown effort"}', { html: true }); r.push(timers.length);
out(r);""")
    assert res == [['<p class="flash ok">Forked.</p>', 1], 0, 0, 0, 0]


@node
def test_notify_only_clears_the_same_notice_and_no_slot_is_null():
    res = run_dom(r"""
pm.notify('First');
const first = timers; timers = [];
pm.notify('Second');
first.forEach(([fn]) => fn());  // the first notice's timer: Second is a new node, so it stays
const after = flashEl.innerHTML;
fire();
const cleared = flashEl.innerHTML;
flashEl = null;
out([after, cleared, pm.notify('x')]);""")
    assert res == ['<p class="flash ok">Second</p>', "", None]


@node
def test_htmx_swap_into_a_flash_slot_auto_hides_ok_only():
    res = run_dom(r"""
const r = [];
flashEl.innerHTML = '<p class="flash ok">Effort set to high.</p>'; swap(flashEl);
r.push(timers.map(t => t[1])); fire(); r.push(flashEl.innerHTML);
const cmp = new El('', '', 'data-cmp-flash'); cmp.innerHTML = '<p class="flash ok">Model set to x.</p>'; swap(cmp);
r.push(timers.length); fire(); r.push(cmp.innerHTML);
flashEl.innerHTML = '<p class="flash err">Agent gone.</p>'; swap(flashEl); r.push(timers.length);
const other = new El('chat', 'chat'); other.innerHTML = '<p class="flash ok">x</p>'; swap(other); r.push(timers.length);
out(r);""")
    assert res == [[4000], "", 1, "", 0, 0]


@node
def test_core_loads_without_document_listeners():
    # the plain harness document has no addEventListener: core.js must still load (the afterSwap hook is guarded)
    assert run("console.log(JSON.stringify([typeof pm.notify, typeof pm.store.get]));") == ["function", "function"]


@node
def test_store_roundtrip_and_throwing_storage():
    res = run_dom(r"""
const m = new Map();
window.localStorage = { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: k => m.delete(k) };
const r = [pm.store.get('k'), pm.store.get('k', 'd'), pm.store.set('k', 5), pm.store.get('k'),
  pm.store.setJSON('j', { a: [1] }), pm.store.getJSON('j'), pm.store.remove('k'), pm.store.get('k', 'gone')];
m.set('bad', '{nope'); r.push(pm.store.getJSON('bad', 'dflt'), pm.store.getJSON('missing', 7));
const cyc = {}; cyc.c = cyc; r.push(pm.store.setJSON('cyc', cyc));
const boom = () => { throw new Error('denied'); };
window.localStorage = { getItem: boom, setItem: boom, removeItem: boom };
r.push(pm.store.get('k', 'd'), pm.store.set('k', 1), pm.store.remove('k'), pm.store.getJSON('j', []), pm.store.setJSON('j', 1));
Object.defineProperty(window, 'localStorage', { get: boom });
r.push(pm.store.get('k'), pm.store.set('k', 1));
out(r);""")
    assert res == [None, "d", True, "5", True, {"a": [1]}, True, "gone", "dflt", 7, False,
                   "d", False, False, [], False, None, False]


def test_feature_scripts_use_pm_notify_not_a_local_flash():
    for name in ("continuepop.js", "newchat.js", "agentmode.js", "issue-save.js"):
        s = (STATIC / "js" / name).read_text()
        assert not re.search(r"function\s+flash\s*\(", s), name
        assert "pm.notify(" in s, name


@node
def test_badges_text_set_and_99_cap():
    res = run_dom(r"""
const a = new El('', 'vs-ub'), b = new El('', 'vs-ub'); a.nodeType = b.nodeType = 1;
const r = [pm.badges.text(0), pm.badges.text(7), pm.badges.text(99), pm.badges.text(100), pm.badges.text('12'),
  pm.badges.text(null), pm.badges.text(-3)];
pm.badges.set(a, 5); r.push([a.innerHTML, a.hidden]);  // the harness El has a textContent setter only
pm.badges.set([a, b], 250); r.push([a.innerHTML, b.innerHTML, b.hidden]);
pm.badges.set(a, 0); r.push([a.innerHTML, a.hidden]);
pm.badges.set(null, 3); pm.badges.set([], 3);
out(r);""")
    assert res == ["", "7", "99", "99+", "12", "", "", ["5", False], ["99+", "99+", False], ["", True]]


@node
def test_badges_one_subscriber_block_added_on_first_use():
    res = run_dom(r"""
const before = Object.keys(listeners).sort();
const ivs = []; globalThis.setInterval = (fn, ms) => { ivs.push(ms); return 1; };
let calls = 0;
pm.badges.on(() => { calls += 1; }); pm.badges.on(() => { throw new Error('bad subscriber'); }); pm.badges.on(() => { calls += 10; });
pm.badges.on('not a function');
const after = Object.keys(listeners).sort(), r = [listeners['pm:inbox'].length, ivs];
listeners['pm:inbox'].forEach(f => f()); listeners['pm:refresh'].forEach(f => f());
r.push(timers.map(t => t[1])); fire(); r.push(calls);
listeners['htmx:afterRequest'].forEach(f => f({ detail: { requestConfig: { path: '/inbox/unread' } } })); r.push(timers.length);
listeners['htmx:afterRequest'].forEach(f => f({ detail: { requestConfig: { path: '/inbox/read' } } })); r.push(timers.length);
out([before, after, r]);""")
    before, after, r = res
    assert before == ["htmx:afterSwap"]  # nothing for badges until a script subscribes
    assert after == sorted(["htmx:afterSwap", "htmx:afterRequest", "visibilitychange", "pm:agent_state", "pm:agent_added",
                            "pm:agent_removed", "pm:inbox", "pm:refresh"])
    assert r == [1, [60000], [400], 11, 0, 1]  # one listener set, one minute timer, one debounced run for both events


def test_inbox_and_side_use_the_shared_badges():
    js = STATIC / "js"
    inbox, side = (js / "inbox.js").read_text(), (js / "side.js").read_text()
    for src in (inbox, side):
        assert "pm.badges.on(" in src and "pm.badges.set(" in src
        assert "visibilitychange" not in src and "setInterval" not in src and "'pm:agent_state'" not in src
    assert '"99+"' not in inbox and "99+" in (js / "core.js").read_text()


# --- pm.openset / pm.copy ------------------------------------------------
@node
def test_openset_keeps_the_saved_id_map_format():
    res = run_dom(r"""
const m = new Map([['pm.rowx.open', '{"a1":1,"b2":1}'], ['bad', '[1,2]']]);
window.localStorage = { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: k => m.delete(k) };
const s = pm.openset('pm.rowx.open'), r = [s.has('a1'), s.has('zz')];
r.push(s.toggle('a1'), s.toggle('c3'), s.set('d4', true), s.set('b2', false), m.get('pm.rowx.open'));
const bad = pm.openset('bad'); r.push(bad.has('0'), bad.toggle('x'), m.get('bad'));
const fresh = pm.openset('none'); r.push(fresh.has('a'), m.has('none'));
out(r);""")
    assert res == [True, False, False, True, True, False, '{"c3":1,"d4":1}', False, True, '{"x":1}', False, False]


COPY = r"""
const made = [], focused = [];
let execOk = true, prev = { focus: (o) => focused.push(['prev', o]) };
document.activeElement = prev;
document.body = { appendChild: (t) => made.push(['add', t.className, t.style.cssText, t.value]), removeChild: () => made.push(['rm']) };
document.createElement = () => ({ style: {}, setAttribute() {}, focus() {}, select() {}, setSelectionRange() {} });
document.execCommand = (c) => { made.push(['exec', c]); if (execOk === 'throw') throw new Error('x'); return execOk; };
"""


@node
def test_copy_uses_the_clipboard_api_in_a_secure_context():
    res = run_dom(COPY + r"""
const got = [];
window.isSecureContext = true;
window.navigator = { clipboard: { writeText: (t) => { got.push(t); return Promise.resolve(); } } };
pm.copy('hi').then((ok) => {
  window.navigator.clipboard.writeText = () => Promise.reject(new Error('denied'));
  return pm.copy(5).then((ok2) => out([ok, got, ok2, made.map(x => x[0]), made[0][1], made[0][2], made[0][3], focused.length]));
});""")
    ok, got, ok2, steps, cls, css, val, nfocus = res
    assert ok is True and got == ["hi"]
    assert ok2 is True and steps == ["add", "exec", "rm"]  # refused: falls back to the textarea
    assert cls == "copy-scratch" and "font-size:16px" in css and val == "5" and nfocus == 1


@node
def test_copy_fallback_without_clipboard_and_failures_resolve_false():
    res = run_dom(COPY + r"""
window.isSecureContext = false;
window.navigator = { clipboard: { writeText: () => { throw new Error('must not be called'); } } };
const r = [];
pm.copy('a').then((ok) => { r.push(ok); execOk = false; return pm.copy('b'); })
  .then((ok) => { r.push(ok); execOk = 'throw'; return pm.copy(null); })
  .then((ok) => { r.push(ok, made.filter(x => x[0] === 'rm').length, made[made.length - 2]); out(r); });""")
    assert res == [True, False, False, 3, ["exec", "copy"]]


def test_copy_and_open_state_only_in_core():
    clip = re.compile(r"clipboard\.writeText|execCommand\(\s*['\"]copy")
    bad = [p.name for p in _scripts() if p.name != "core.js" and clip.search(p.read_text())]
    assert bad == []
    js = STATIC / "js"
    for name, key in (("rowx.js", "pm.rowx.open"), ("srow.js", "pm.srow.open"), ("backlog.js", "pm.backlog.open")):
        s = (js / name).read_text()
        assert "JSON.parse(localStorage" not in s and "localStorage" not in s.split("*/", 1)[1], name
        assert f"pm.openset('{key}')" in s or f'pm.openset("{key}")' in s, name
    for name in ("copy.js", "chat.js", "canvas.js", "qchat-menu.js"):
        assert "pm.copy(" in (js / name).read_text(), name
    core = CORE.read_text()
    assert "pm.copy = function" in core and "pm.openset = function" in core
