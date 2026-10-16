"""static/js/tabs.js (pm.tabs) run in node after core.js (pm.store) against a tiny fake DOM: select, roving tabindex,
panels, persist + restore, arrow keys, live refresh, radio tabs."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pm.web.render import TEMPLATE_DIR

JS = Path(__file__).resolve().parents[1] / "src" / "pm" / "static" / "js"

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

# A fake DOM: elements with attributes, children and parent; selectors are comma lists of compound simple selectors
# (tag, #id, .class, [attr], [attr=v], [attr="v"]); no combinators (tabs.js uses none).
HARNESS = r"""
const L = {}, byId = {}, store = new Map();
function parseSel(s) {
  const out = []; const re = /([a-zA-Z]+)|#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:=("?)([^\]"]*)\5)?\]/g; let m;
  while ((m = re.exec(s))) out.push(m);
  return out;
}
function match(el, sel) {
  return sel.split(',').some(s => parseSel(s.trim()).every(m => {
    if (m[1]) return el.tagName === m[1].toUpperCase();
    if (m[2]) return el.id === m[2];
    if (m[3]) return (el.getAttribute('class') || '').split(' ').includes(m[3]);
    if (m[6] !== undefined && m[4]) return el.getAttribute(m[4]) === m[6];
    return el.hasAttribute(m[4]);
  }));
}
class El {
  constructor(tag, attrs) {
    this.tagName = tag.toUpperCase(); this.attrs = {}; this.kids = []; this.parentNode = null; this.hidden = false;
    this.tabIndex = 0; this.scrollWidth = 0; this.clientWidth = 0; this.scrollLeft = 0; this.offsetLeft = 0; this.offsetWidth = 0;
    this.clicks = 0; this.events = [];
    Object.keys(attrs || {}).forEach(k => this.setAttribute(k, attrs[k]));
  }
  get id() { return this.attrs.id || ''; }
  get htmlFor() { return this.attrs.for || ''; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'id') byId[v] = this; }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  hasAttribute(k) { return k in this.attrs; }
  add(...kids) { kids.forEach(k => { k.parentNode = this; this.kids.push(k); }); return this; }
  all() { return this.kids.flatMap(k => [k, ...k.all()]); }
  matches(s) { return match(this, s); }
  closest(s) { let n = this; while (n && n.matches) { if (n.matches(s)) return n; n = n.parentNode; } return null; }
  querySelectorAll(s) { return this.all().filter(n => n.matches(s)); }
  querySelector(s) { return this.querySelectorAll(s)[0] || null; }
  getClientRects() { return this.hidden ? [] : [1]; }
  focus() { document.activeElement = this; }
  click() { this.clicks++; fireDoc('click', { target: this }); }
  dispatchEvent(e) { let n = this; while (n) { (n.events || []).push(e.type); n = n.parentNode; } fireDoc(e.type, Object.assign({ target: this }, e)); }
}
const root = new El('html');
function fireDoc(t, e) {
  const ev = Object.assign({ defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }, e);
  (L[t] || []).forEach(f => f(ev)); return ev;
}
globalThis.CustomEvent = class { constructor(type, o) { this.type = type; this.detail = o.detail; } };
globalThis.CSS = { escape: s => s };
globalThis.window = { localStorage: { getItem: k => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k) } };
globalThis.document = { activeElement: null,
  addEventListener(t, f) { (L[t] = L[t] || []).push(f); },
  querySelector: s => root.querySelector(s), querySelectorAll: s => root.querySelectorAll(s),
  getElementById: id => (byId[id] && byId[id].closest('html') ? byId[id] : null) };
const fs = require('fs');
function boot() { eval(fs.readFileSync(process.argv[1], 'utf8')); eval(fs.readFileSync(process.argv[2], 'utf8')); }
const sel = el => el.getAttribute('aria-selected');
const key = (t, k) => fireDoc('keydown', { key: k, target: t });
const got = [];
document.addEventListener('pm:tab', e => got.push([e.target.id, e.detail.value, !!e.detail.restored]));
function bar(id, keyName, names, cur) {
  const list = new El('div', Object.assign({ id, role: 'tablist' }, keyName ? { 'data-tabs-key': keyName } : {}));
  names.forEach(n => {
    const b = new El('button', { role: 'tab', 'data-tab': n, 'aria-selected': n === cur ? 'true' : 'false', 'aria-controls': id + '-' + n });
    list.add(b); root.add(new El('section', { id: id + '-' + n }));
  });
  root.add(list); return list;
}
const out = v => console.log(JSON.stringify(v));
"""


def run(code: str):
    p = subprocess.run(["node", "-e", HARNESS + code, str(JS / "core.js"), str(JS / "tabs.js")],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@node
def test_select_marks_one_roves_tabindex_shows_panel_and_fires():
    res = run(r"""
const list = bar('nt', '', ['a', 'b', 'c'], 'a'); boot();
const [a, b, c] = list.kids;
const r0 = [a.tabIndex, b.tabIndex, c.tabIndex];  // initial roving tabindex from the markup's current tab
window.pm.tabs.select(b);
out([r0, list.kids.map(sel), [a.tabIndex, b.tabIndex, c.tabIndex],
     ['nt-a', 'nt-b', 'nt-c'].map(i => document.getElementById(i).hidden), got, store.size]);""")
    r0, sels, tix, hidden, events, stored = res
    assert r0 == [0, -1, -1]
    assert sels == ["false", "true", "false"] and tix == [-1, 0, -1]
    assert hidden == [True, False, True]
    assert events == [["nt", "b", False]] and stored == 0  # no data-tabs-key: nothing persisted


@node
def test_keyed_list_click_persists_and_restore_prefers_memory_then_store():
    res = run(r"""
store.set('pm.k', 'c');
const list = bar('kl', 'pm.k', ['a', 'b', 'c'], 'a'); boot();
const r = [list.kids.map(sel)];          // load: the saved tab wins over the markup
const ev = fireDoc('click', { target: list.kids[1] });
r.push(list.kids.map(sel), store.get('pm.k'), ev.defaultPrevented);
list.kids.forEach((t, i) => t.setAttribute('aria-selected', i === 0 ? 'true' : 'false'));  // a live morph resets the markup
store.set('pm.k', 'a');                  // storage changed by another tab: this page's own last choice still wins
fireDoc('nodiff:swapped', {});
r.push(list.kids.map(sel), got.slice(-1)[0]);
out(r);""")
    assert res[0] == ["false", "false", "true"]
    assert res[1:4] == [["false", "true", "false"], "b", False]
    assert res[4] == ["false", "true", "false"] and res[5] == ["kl", "b", True]


@node
def test_restore_skips_hidden_and_unknown_values():
    res = run(r"""
store.set('pm.h', 'gone');
const list = bar('hl', 'pm.h', ['a', 'b'], 'b'); list.kids[1].hidden = true; boot();
out(list.kids.map(sel));""")
    assert res == ["true", "false"]  # stored value unknown, markup's current is hidden: first visible


@node
def test_arrow_home_end_move_and_click():
    res = run(r"""
const list = bar('ak', 'pm.a', ['a', 'b', 'c'], 'a'); boot();
const [a, b, c] = list.kids, r = [];
let e = key(a, 'ArrowRight'); r.push(document.activeElement === b, sel(b), e.defaultPrevented);
key(b, 'End'); r.push(sel(c));
key(c, 'ArrowRight'); r.push(sel(a));     // wraps
key(a, 'ArrowLeft'); r.push(sel(c));
key(c, 'Home'); r.push(sel(a));
e = key(a, 'ArrowDown'); r.push(e.defaultPrevented);
out(r);""")
    assert res == [True, "true", True, "true", "true", "true", "true", False]


@node
def test_radio_label_tabs_follow_change_and_take_no_tabindex():
    res = run(r"""
const r1 = new El('input', { type: 'radio', id: 'tab-chat' }), r2 = new El('input', { type: 'radio', id: 'tab-pane' });
r1.type = 'radio'; r2.type = 'radio'; r1.checked = true; r2.checked = false;
const list = new El('div', { role: 'tablist' });
const l1 = new El('label', { for: 'tab-chat', role: 'tab', 'aria-selected': 'true' }), l2 = new El('label', { for: 'tab-pane', role: 'tab', 'aria-selected': 'false' });
l1.tabIndex = l2.tabIndex = 99;
list.add(l1, l2); root.add(r1, r2, list); boot();
r1.checked = false; r2.checked = true; fireDoc('change', { target: r2 });
const k = key(l2, 'ArrowLeft');
out([sel(l1), sel(l2), l1.tabIndex, l2.tabIndex, k.defaultPrevented]);""")
    assert res == ["false", "true", 99, 99, False]  # the radios own the keys


def test_tabs_js_loads_after_core_and_is_not_in_core():
    base = (TEMPLATE_DIR / "base.html").read_text()
    i, j = base.index('src="/static/js/core.js"'), base.index('src="/static/js/tabs.js"')
    assert i < j and "pm.tabs" not in (JS / "core.js").read_text()
    assert base[i:j].count("<script") == 1  # right after core.js
