"""Composer JS contracts (composer.js, composer-attach.js, composer-dock.js, chat-send.js): string-level checks plus node
runs of the pure helpers and of one composer root on a tiny fake DOM (send key, Send <-> Stop, ended labels)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JSDIR = ROOT / "src/pm/static/js"
ATTACH = JSDIR / "composer-attach.js"
SEND = JSDIR / "chat-send.js"
COMPOSER = JSDIR / "composer.js"
DOCK = JSDIR / "composer-dock.js"
node = pytest.mark.skipif(shutil.which("node") is None, reason="node missing")


def test_sizes():
    assert len(COMPOSER.read_text().splitlines()) <= 300
    for f in (ATTACH, DOCK, SEND):
        assert len(f.read_text().splitlines()) <= 500, f.name


def test_no_singletons_left():
    """Everything per root through the [data-cmp-*] hooks: no #msgbar / #flash / #imgform / #slash-card / #send-hint."""
    for name in ("composer.js", "composer-attach.js", "composer-dock.js", "chat-send.js", "slash.js", "atfiles.js",
                 "snippets.js", "modelpick.js", "issue-save.js", "pmreload.js"):
        js = (JSDIR / name).read_text()
        for bad in ("getElementById('msgbar')", "#msgbar", "#imgform", "slash-card'",
                    "getElementById('send-hint')", "getElementById('pm-reload')", "form.send"):
            assert bad not in js, (name, bad)
        # the page's #flash only as the fallback of pm.composer.slot() (an element outside any composer)
        assert js.count("getElementById('flash')") == (1 if name == "composer.js" else 0), name
    assert not (JSDIR / "chat-composer.js").exists() and not (JSDIR / "chat-attach.js").exists()


def test_one_enter_handler():
    """The Enter-to-send handler lives in composer.js only (chat.js and the old composer copy are gone)."""
    for name in ("chat.js", "chat-send.js", "composer-attach.js", "composer-dock.js", "snippets.js"):
        assert "'Enter'" not in (JSDIR / name).read_text(), name
    js = COMPOSER.read_text()
    assert "function sends(key, e, fine)" in js and "key !== 'mod-enter'" in js and "(hover: hover) and (pointer: fine)" in js


def test_paths_hidden_from_textarea():
    js = ATTACH.read_text() + SEND.read_text()
    assert "addPathToMessage" not in js and "removePathFromMessage" not in js
    assert "htmx:configRequest" in js and "e.detail.parameters[ta ? ta.name : 'text']" in js
    assert "removeAttribute('required')" in js
    assert "Still uploading" in js


def test_paste_long_text_becomes_file():
    js = ATTACH.read_text()
    assert "data-paste-lines" in js and "pasted-text.md" in js and "pasted-text.txt" in js
    assert "window.pmComposer" in js


def test_attach_is_per_root_and_works_without_an_agent():
    js = ATTACH.read_text()
    assert "root.getAttribute('data-upload') || (f ? f.getAttribute('action') : '')" in js  # Launch: /p/<name>/image
    assert "pm.fetch(url, { method: 'POST', body: fd, csrf: 'header' })" in js and not re.search(r"(?<![\w.])fetch\(", js)
    assert "[data-cmp-chips]" in js and "[data-cmp-upload] input[type=file]" in js


@node
def test_helpers_in_node():
    code = r"""
const src=require('fs').readFileSync(process.argv[1],'utf8');
const m=/function countLines[\s\S]*?function pastedName[\s\S]*?\n  }\n/.exec(src)[0];
eval(m+';globalThis.r=[countLines("a\\nb\\nc"),pastedName("# t\\nx"),pastedName("plain\\ntext"),pastedName("a\\n- b"),pastedName("x\\n```")]');
console.log(JSON.stringify(r));
"""
    out = subprocess.run(["node", "-e", code, str(ATTACH)], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [3, "pasted-text.md", "pasted-text.txt", "pasted-text.md", "pasted-text.md"]


def test_draft_kept_per_root():
    js = COMPOSER.read_text()
    assert "dk: dk ? 'pm.draft:' + dk : ''" in js and "root.getAttribute('data-draft-key')" in js
    assert "pm.store.set(st.dk, st.ta.value); else pm.store.remove(st.dk)" in js and "localStorage" not in js
    # the form transport ("Resume and send"): the box and the draft clear once it went out
    assert "if (e.detail.successful && st.transport === 'form') ta.value = '';" in js


def test_history_keys_wired():
    js = COMPOSER.read_text()
    assert "HIST_SRC = '#ci .ci-user .chat-copy[data-raw]'" in js
    assert "if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;" in js
    assert "new Event('input', { bubbles: true })" in js
    # bubble phase only: slash.js / atfiles.js own the arrows (capture) while their popup is open
    m = re.search(r"ta\.addEventListener\('keydown', function \(e\) \{[\s\S]*?\n    \}\);", js).group(0)
    assert "}, true)" not in m and "e.defaultPrevented" in m


@node
def test_history_and_send_key_helpers_in_node():
    code = r"""
globalThis.window={};globalThis.document=undefined;
eval(require('fs').readFileSync(process.argv[1],'utf8'));
const c=window.pm.composer, k=(key,o)=>Object.assign({key,isComposing:false,altKey:false,ctrlKey:false,metaKey:false,shiftKey:false},o||{});
console.log(JSON.stringify([
  c.histList(["a","b","b","c"],[],100), c.histList(["a","b"],["b","x"],100), c.histList(["a"," ",""],["x","x"],100),
  c.histList(["1","2","3","4"],[],2), c.histMove(3,3,-1), c.histMove(3,0,-1), c.histMove(3,2,1), c.histMove(3,3,1),
  c.sends('enter',k('Enter'),true), c.sends('enter',k('Enter',{shiftKey:true}),true), c.sends('enter',k('Enter'),false),
  c.sends('enter',k('Enter',{ctrlKey:true}),false), c.sends('mod-enter',k('Enter'),true), c.sends('mod-enter',k('Enter',{ctrlKey:true}),true),
  c.sends('mod-enter',k('Enter',{metaKey:true}),false), c.sends('mod-enter',k('Enter',{ctrlKey:true,shiftKey:true}),true),
  c.sends('enter',k('Enter',{isComposing:true}),true), c.sends('enter',k('a'),true)]));
"""
    out = subprocess.run(["node", "-e", code, str(COMPOSER)], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [["a", "b", "c"], ["a", "b", "x"], ["a", "x"], ["3", "4"], 2, 0, 3, 3,
                                      True, False, False, True, False, True, True, False, False, False]


def test_placeholder_follows_the_pm_composer_event():
    js = COMPOSER.read_text()
    m = re.search(r"root\.addEventListener\('pm:composer', function \(e\) \{[\s\S]*?\n    \}\);", js)
    assert m, "pm:composer listener missing"
    body = m.group(0)
    assert "st.phFull = x.ph" in body and "st.phShort = x.phShort" in body
    assert "ta.setAttribute('data-ph-short', x.phShort)" in body and "setPh(st)" in body
    assert "innerHTML" not in body and "fetch(" not in body


def test_stop_and_retarget_contract():
    js = COMPOSER.read_text()
    assert "pm:agent_state" in js and "x.state === 'running'" in js and "root.getAttribute('data-state')" in js
    assert "if (f.requestSubmit) f.requestSubmit();" in js  # the stop form keeps its hx-confirm (in-page modal)
    assert "SLOTS = ['[data-cmp-flash]', '[data-cmp-slash-card]']" in js and "x.target = q(root, s) || t" in js
    pmjs = (Path(__file__).resolve().parents[1] / "src/pm/static/pm.js").read_text()
    assert "t.matches('#flash, .flash-inline')" in pmjs and "j.detail" in pmjs  # fork's JSON {"detail"}: pm.js, escaped
    assert not re.search(r"\b(alert|confirm|prompt)\(|window\.open|eval\(", js)


# ---- one root on a fake DOM ----
FAKE_DOM = r"""
function cl() { const s = new Set(); return { contains: c => s.has(c), add: c => s.add(c), remove: c => s.delete(c),
  toggle: (c, on) => { on = on === undefined ? !s.has(c) : !!on; if (on) s.add(c); else s.delete(c); return on; } }; }
function el(attrs, q) {
  return { attrs: Object.assign({}, attrs || {}), on: {}, q: q || {}, classList: cl(), style: {}, value: '', disabled: false,
    textContent: '', offsetHeight: 40, clientHeight: 40, scrollHeight: 40, submits: [],
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }, setAttribute(k, v) { this.attrs[k] = String(v); },
    hasAttribute(k) { return k in this.attrs; }, removeAttribute(k) { delete this.attrs[k]; },
    addEventListener(t, f) { (this.on[t] = this.on[t] || []).push(f); },
    fire(t, ev) { ev = Object.assign({ type: t, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }, ev || {});
      (this.on[t] || []).forEach(f => f(ev)); return ev; },
    dispatchEvent(ev) { return this.fire(ev.type, ev); }, querySelector(s) { return this.q[s] || null; },
    querySelectorAll() { return []; }, closest() { return null; }, requestSubmit(b) { this.submits.push(b ? 'send' : 'plain'); },
    setSelectionRange() {}, matches() { return false; } };
}
const docOn = {};
globalThis.Event = function (t) { this.type = t; };
globalThis.window = { pm: { store: { get: () => null, set() {}, remove() {} }, esc: String },
  matchMedia: (m) => ({ matches: /hover/.test(m) }), addEventListener() {} };
globalThis.document = { readyState: 'complete', addEventListener: (t, f) => (docOn[t] = docOn[t] || []).push(f),
  getElementById: () => null, querySelector: () => null, querySelectorAll: () => [], contains: () => true };
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
function make(key, ended) {
  const ta = el({ placeholder: 'p' }), form = el(), stop = el();
  const alt = ended ? el({ 'data-label-empty': 'Fork', 'data-label-text': 'Fork and send' }) : null;
  const send = el(ended ? { 'data-cmp-empty-ok': '', 'data-label-empty': 'Resume', 'data-label-text': 'Resume and send', title: 'R' }
                        : { title: 'Send message (Enter)', 'data-tip': 'Send message (Enter)', 'data-stop-title': 'Interrupt agent (sends Ctrl-C, then Esc)' });
  send.type = 'submit'; send.title = send.attrs.title;
  const root = el({ 'data-composer': ended ? 'ended' : 'live', 'data-agent': 'a-1', 'data-state': 'idle', 'data-send-key': key,
                    'data-send': ended ? 'form' : 'htmx' },
    { '[data-cmp-input]': ta, '[data-cmp-form]': form, '[data-cmp-send]': send, '[data-cmp-alt]': alt, '[data-cmp-stop]': ended ? null : stop });
  window.pm.composer.init(root);
  return { root, ta, form, send, stop, alt };
}
function state(s) { return { stop: s.send.classList.contains('is-stop'), type: s.send.type, dis: s.send.disabled,
  title: s.send.title, label: s.send.textContent, alt: s.alt ? s.alt.textContent : null }; }
function type(s, v) { s.ta.value = v; s.ta.fire('input'); }
const K = { shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, isComposing: false };
const out = [];
const live = make('enter');
out.push(state(live));                                                        // idle, empty: Send off
(docOn['pm:agent_state'] || []).forEach(f => f({ detail: { id: 'a-1', state: 'running' } }));
out.push(state(live));                                                        // running, empty: Stop
type(live, 'hi'); out.push(state(live));                                      // typing: Send again
type(live, ''); out.push(state(live));                                        // empty again: Stop
live.root.fire('click', { target: { closest: s => s === '[data-cmp-send]' ? live.send : null } });
out.push(live.stop.submits.slice());                                          // Stop posts the interrupt form
type(live, 'go');
out.push(live.ta.fire('keydown', Object.assign({ key: 'Enter' }, K)).defaultPrevented);
out.push(live.form.submits.slice());                                          // Enter sent it (fine pointer)
const mod = make('mod-enter');
type(mod, 'x');
out.push(mod.ta.fire('keydown', Object.assign({ key: 'Enter' }, K)).defaultPrevented);
out.push(mod.ta.fire('keydown', Object.assign({ key: 'Enter' }, K, { ctrlKey: true })).defaultPrevented);
out.push(mod.form.submits.slice());
const ended = make('enter', true);
out.push(state(ended)); type(ended, 'more'); out.push(state(ended));
console.log(JSON.stringify(out));
"""


@node
def test_send_stop_swap_and_send_key_on_a_fake_dom():
    out = subprocess.run(["node", "-e", FAKE_DOM, str(COMPOSER)], capture_output=True, text=True, check=True)
    r = json.loads(out.stdout)
    send = "Send message (Enter)"
    stop = "Interrupt agent (sends Ctrl-C, then Esc)"
    assert r[0] == {"stop": False, "type": "submit", "dis": True, "title": send, "label": "", "alt": None}
    assert r[1] == {"stop": True, "type": "button", "dis": False, "title": stop, "label": "", "alt": None}
    assert r[2] == {"stop": False, "type": "submit", "dis": False, "title": send, "label": "", "alt": None}
    assert r[3]["stop"] is True and r[3]["title"] == stop
    assert r[4] == ["plain"]  # the hidden interrupt form, not the message form
    assert r[5] is True and r[6] == ["send"]  # "enter": Enter sends
    assert r[7] is False and r[8] is True and r[9] == ["send"]  # "mod-enter": Enter is a newline, Ctrl+Enter sends
    # ended: Resume / Fork while empty (Resume still works), Resume and send / Fork and send with text
    assert r[10] == {"stop": False, "type": "submit", "dis": False, "title": "R", "label": "Resume", "alt": "Fork"}
    assert r[11]["label"] == "Resume and send" and r[11]["alt"] == "Fork and send" and r[11]["dis"] is False


def test_composer_js_leaves_json_errors_to_pm_js():
    """pm.js turns a JSON error ({"detail": ...}) into the notice; a second converter in composer.js parsed that HTML
    again and replaced the server's reason with a generic "refused" (arch-guard 2026-10-13)."""
    js = (Path(__file__).resolve().parents[1] / "src/pm/static/js/composer.js").read_text()
    assert "JSON.parse" not in js and "refused" not in js


INSERT_STOP = FAKE_DOM.split("const K = {")[0] + r"""
// insert: at the cursor, cut at maxlength, input event; a full box refuses
const ib = make('enter'); ib.ta.closest = () => ib.root; ib.ta.maxLength = 8; ib.ta.value = 'ab'; ib.ta.selectionStart = ib.ta.selectionEnd = 1;
let fired = 0; ib.ta.on.input = [() => fired++]; ib.ta.focus = () => {};
const r1 = window.pm.composer.insert(ib.ta, 'XY'), v1 = ib.ta.value;
ib.ta.selectionStart = ib.ta.selectionEnd = 4;
const r2 = window.pm.composer.insert(ib.ta, '123456'), v2 = ib.ta.value, r3 = window.pm.composer.insert(ib.ta, 'z');
// onStop (quick chat): no stop form, Stop while running() is set and the box is empty; a click calls the hook
const qc = make('enter'); qc.root.closest = () => qc.root; qc.root.attrs = { 'data-composer': 'qchat', 'data-send': 'stream' };
let stops = 0; window.pm.composer.init(qc.root, { onStop: () => stops++ });
const idle = qc.send.classList.contains('is-stop');
window.pm.composer.running(qc.root, true);
const on = qc.send.classList.contains('is-stop');
qc.root.fire('click', { target: { closest: s => s === '[data-cmp-send]' ? qc.send : null } });
window.pm.composer.running(qc.root, false);
console.log(JSON.stringify([r1, v1, r2, v2, r3, fired, idle, on, stops, qc.stop.submits.length,
  qc.send.classList.contains('is-stop'), window.pm.composer.running(qc.root)]));
"""


@node
def test_insert_and_on_stop_on_a_fake_dom():
    """pm.composer.insert (qchat-aside "Use in composer", composer-attach inline text) and the onStop hook (quick chat)."""
    out = subprocess.run(["node", "-e", INSERT_STOP, str(COMPOSER)], capture_output=True, text=True, check=True)
    r = json.loads(out.stdout)
    assert r[0] == {"ok": True, "cut": False} and r[1] == "aXYb"
    assert r[2] == {"ok": True, "cut": True} and r[3] == "aXYb1234" and r[4] == {"ok": False, "cut": False}
    assert r[5] == 2  # one input event per insert (grow, draft, Send state)
    assert r[6:] == [False, True, 1, 0, False, False]  # Stop only while running; its click is the hook, not a form


def test_heading_pill_follows_the_pm_composer_event():
    """The HX-Trigger pm:composer carries the heading pill (web/agentctl.composer_event); chat.js has one
    document listener that sets the text and aria-label of #agent-pill (and the heading's data-model)."""
    js = (JSDIR / "chat.js").read_text()
    assert js.count("'pm:composer'") == 1
    m = re.search(r"document\.addEventListener\('pm:composer', function \(e\) \{[\s\S]*?\n  \}\);", js)
    assert m, "pm:composer listener missing in chat.js"
    body = m.group(0)
    assert "getElementById('agent-pill')" in body and "pl.textContent = x.pill" in body and "pillLabel(pl)" in body
    assert "setAttribute('data-model', x.pill)" in body and "innerHTML" not in body
    assert "setAttribute('aria-label'" in js[js.index("function pillLabel"):]
    py = (ROOT / "src/pm/web/agentctl.py").read_text()
    assert '{"ph": full, "phShort": short, "pill": pill}' in py and "agenthead.live_pill" in py
