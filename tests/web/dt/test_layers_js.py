"""static/js/layers.js htmx:confirm: hx-confirm on dt shell markup asks with pm7.confirm (never the native popup);
inside main.x-cl it is left to classic dialog.js, so the two never handle the same event. Runs in node."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pm.web import themes

LAYERS = themes.HERE / "dt" / "static" / "js" / "layers.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

# A stub document records the listeners; pm7.confirm is replaced after load by a stub that records the question
# and answers ANSWER.
HARNESS = r"""
const L = [];
globalThis.window = { pm7: {}, addEventListener() {} };
globalThis.requestAnimationFrame = () => 0;
globalThis.document = { addEventListener: (t, f, o) => L.push([t, f, o]), getElementById: () => null,
                        contains: () => true };
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
const conf = L.filter((x) => x[0] === 'htmx:confirm');
let ANSWER = false;
const asked = [];
window.pm7.confirm = (o) => { asked.push(o); return Promise.resolve(ANSWER); };
async function fire(inClassic, question) {
  const ev = { detail: { question: question, issueRequest(v) { ev.issued = v; },
                         elt: { closest: (s) => (s === 'main.x-cl' && inClassic ? {} : null), matches: () => true,
                                querySelector: () => ({ textContent: ' Reload pm ', classList: { contains: () => false } }) } },
               preventDefault() { ev.prevented = true; }, stopImmediatePropagation() { ev.stopped = true; } };
  conf[0][1](ev);
  await new Promise((r) => setTimeout(r, 0));
  return { prevented: !!ev.prevented, stopped: !!ev.stopped, issued: ev.issued === true };
}
"""


def run(code: str):
    out = subprocess.run(["node", "-e", HARNESS + code, str(LAYERS)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_one_capture_listener():
    assert run("console.log(JSON.stringify(conf.map((x) => x[2])));") == [True]


def test_shell_markup_asks_in_page_and_classic_never_sees_it():
    got = run("(async () => { const no = await fire(false, 'Reload?'); ANSWER = true; const yes = await fire(false, 'R?');"
              " console.log(JSON.stringify([no, yes, asked])); })();")
    assert got[0] == {"prevented": True, "stopped": True, "issued": False}  # Cancel: no request
    assert got[1] == {"prevented": True, "stopped": True, "issued": True}  # OK: issueRequest(true)
    assert got[2][0] == {"title": "Reload?", "ok": "Reload pm", "danger": False}


def test_classic_content_and_plain_requests_are_left_alone():
    got = run("(async () => console.log(JSON.stringify([await fire(true, 'Delete?'), await fire(false, null)])))();")
    assert got == [{"prevented": False, "stopped": False, "issued": False}] * 2


# pm7.menu items against a small fake DOM: a keep item (the chat text size's Larger / Smaller) runs with the menu
# host and leaves the menu open; a plain item closes it first; an item enabled by a keep item's run then works.
MENU = r"""
function E(tag) {
  const n = { tagName: tag, kids: [], attrs: {}, on: {}, hidden: true, disabled: false, parentNode: null,
    offsetWidth: 10, offsetHeight: 10, textContent: '',
    style: { p: {}, setProperty(k, v) { this.p[k] = v; }, removeProperty(k) { delete this.p[k]; } },
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    removeAttribute(k) { delete this.attrs[k]; }, hasAttribute(k) { return k in this.attrs; },
    appendChild(c) { this.kids.push(c); c.parentNode = this; return c; },
    append(...c) { c.forEach((x) => this.appendChild(x)); },
    replaceChildren(...c) { this.kids = []; c.forEach((x) => this.appendChild(x)); },
    addEventListener(t, f) { (this.on[t] = this.on[t] || []).push(f); },
    contains(x) { while (x) { if (x === this) return true; x = x.parentNode; } return false; },
    querySelectorAll() { return []; }, getClientRects() { return [1]; }, focus() {},
    getBoundingClientRect() { return { left: 0, right: 10, top: 0, bottom: 10 }; } };
  return n;
}
const host = E('div'), home = E('div'), anchor = E('button');
home.appendChild(host);
globalThis.window = { pm7: { el: (tag, cls, text) => { const n = E(tag); n.textContent = text || ''; return n; },
  icon: () => null, phone: () => false }, addEventListener() {}, innerHeight: 800 };
globalThis.requestAnimationFrame = () => 0;
globalThis.document = { addEventListener() {}, getElementById: (id) => (id === 'x-menu' ? host : null),
  contains: () => true, documentElement: { clientWidth: 1000 }, body: E('body'), activeElement: null };
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
const pm7 = window.pm7, out = [], ran = [];
const click = (i) => host.kids[i].on.click.forEach((f) => f({ preventDefault() {} }));
"""


def run_menu(code: str):
    res = subprocess.run(["node", "-e", MENU + code + "\nconsole.log(JSON.stringify(out));", str(LAYERS)],
                         capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def test_menu_keep_items_stay_open_and_get_the_host():
    got = run_menu("pm7.menu(anchor, [{label: 'Larger', keep: true, run: (e, h) => { ran.push(h === host);"
                   " h.kids[1].disabled = false; }}, {label: 'Reset', disabled: true, run: () => ran.push('reset')}]);"
                   "out.push(host.hidden); click(0); click(0); out.push(host.hidden, ran.slice());"
                   "click(1); out.push(host.hidden, ran);")
    assert got == [False, False, [True, True], True, [True, True, "reset"]]
