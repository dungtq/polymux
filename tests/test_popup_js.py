"""static/js/popup.js (pm.popover) run in node against a tiny fake DOM, plus static contracts of the popup family
(popup.css tokens only, dialog.js pmDialog, _popup.html macro, outclick.js gone)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pm.web.render import TEMPLATE_DIR

ROOT = Path(__file__).resolve().parents[1] / "src" / "pm"
POPUP = ROOT / "static/js/popup.js"

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

HARNESS = r"""
const L = {};
let openDialog = null;
function mk(tag, rect, size) {
  const attrs = {}, cls = new Set(), kids = [];
  return { tagName: tag, id: '', hidden: true, style: {}, kids,
    offsetWidth: size ? size[0] : 0, offsetHeight: size ? size[1] : 0,
    getBoundingClientRect() { return rect; }, getClientRects() { return [1]; },
    setAttribute(k, v) { attrs[k] = String(v); }, getAttribute(k) { return k in attrs ? attrs[k] : null; },
    hasAttribute(k) { return k in attrs; }, removeAttribute(k) { delete attrs[k]; },
    classList: { toggle(c, on) { if (on) cls.add(c); else cls.delete(c); }, remove(c) { cls.delete(c); }, contains(c) { return cls.has(c); } },
    contains(n) { return n === this || kids.some((k) => k === n || (k.contains && k.contains(n))); },
    querySelector() { return null; }, querySelectorAll() { return []; },
    focus() { document.activeElement = this; } };
}
globalThis.window = { innerHeight: 800, addEventListener() {}, requestAnimationFrame(f) { f(); },
  matchMedia: () => ({ get matches() { return globalThis.PHONE === true; }, addEventListener() {} }) };
window.parent = window;
globalThis.location = { origin: 'https://x' };
globalThis.document = { activeElement: null, documentElement: { clientWidth: 1000 },
  addEventListener(t, f) { (L[t] = L[t] || []).push(f); }, contains() { return true; },
  querySelector(s) { return s === 'dialog[open]' ? openDialog : null; }, querySelectorAll() { return []; } };
function key(k) { const e = { key: k, defaultPrevented: false, isComposing: false, stopped: false,
  preventDefault() { this.defaultPrevented = true; }, stopImmediatePropagation() { this.stopped = true; } };
  (L.keydown || []).forEach((f) => f(e)); return e; }
function down(t) { (L.pointerdown || []).forEach((f) => f({ target: t })); }
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
const pp = window.pm.popover;
const R = (l, t, w, h) => ({ left: l, top: t, right: l + w, bottom: t + h, width: w, height: h });
"""


def run(code: str):
    out = subprocess.run(["node", "-e", HARNESS + code, str(POPUP)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@node
def test_places_below_flips_up_and_clamps():
    res = run(r"""
const a = mk('BUTTON', R(100, 50, 80, 30)), el = mk('DIV', null, [200, 150]);
pp.open(a, el, { placement: 'bottom-start' });
const below = [el.style.position, el.style.top, el.style.left, el.getAttribute('data-placement'), el.hidden];
pp.close(el);
const b = mk('BUTTON', R(100, 700, 80, 30)), el2 = mk('DIV', null, [200, 150]);
pp.open(b, el2, { placement: 'bottom-start' });          // no room below: flips up
const up = [el2.style.top, el2.getAttribute('data-placement')];
pp.close(el2);
const c = mk('BUTTON', R(20, 50, 40, 30)), el3 = mk('DIV', null, [200, 100]);
pp.open(c, el3, { placement: 'bottom-end' });             // right-aligned would leave the screen: clamped to 8px
const end = [el3.style.left];
pp.close(el3);
console.log(JSON.stringify([below, up, end, el3.style.top, el3.hidden]));
""")
    below, up, end, top_after, hidden_after = res
    assert below == ["fixed", "84px", "100px", "bottom-start", False]
    assert up == [f"{700 - 4 - 150}px", "top-start"]
    assert end == ["8px"]
    assert top_after == "" and hidden_after is True  # closing clears the CSSOM position and hides it


@node
def test_aria_and_focus_return():
    res = run(r"""
const a = mk('BUTTON', R(10, 10, 50, 20)), el = mk('DIV', null, [100, 100]);
pp.open(a, el, { role: 'menu' });
const opened = [a.getAttribute('aria-expanded'), a.getAttribute('aria-controls') === el.id && !!el.id, el.getAttribute('role'), pp.isOpen(el)];
document.activeElement = el; el.kids.push(document.activeElement);
key('Escape');
console.log(JSON.stringify([opened, a.getAttribute('aria-expanded'), document.activeElement === a, pp.isOpen(el)]));
""")
    opened, expanded, refocused, still = res
    assert opened == ["true", True, "menu", True]
    assert expanded == "false" and refocused and still is False


@node
def test_escape_closes_only_the_topmost_and_steps_aside_for_a_modal():
    res = run(r"""
const a = mk('BUTTON', R(10, 10, 50, 20)), outer = mk('DIV', null, [300, 300]);
pp.open(a, outer, {});
const b = mk('BUTTON', R(20, 40, 50, 20)), inner = mk('DIV', null, [100, 100]);
outer.kids.push(b);                                       // nested: opened from inside the first layer
pp.open(b, inner, {});
const e1 = key('Escape');
const step1 = [pp.isOpen(outer), pp.isOpen(inner), e1.defaultPrevented, e1.stopped];
openDialog = mk('DIALOG');                                // a modal on top takes Esc itself
const e2 = key('Escape');
const step2 = [pp.isOpen(outer), e2.defaultPrevented];
openDialog = null;
key('Escape');
console.log(JSON.stringify([step1, step2, pp.isOpen(outer)]));
""")
    assert res == [[True, False, True, True], [True, False], False]


@node
def test_escape_skips_a_layer_that_left_the_page():
    res = run(r"""
const a = mk('BUTTON', R(10, 10, 50, 20)), el = mk('DIV', null, [100, 100]);
pp.open(a, el, {});
document.contains = (n) => n !== el;                      // an htmx swap removed the open popover
const e = key('Escape');
console.log(JSON.stringify([e.defaultPrevented, e.stopped, pp.isOpen(el)]));
""")
    assert res == [False, False, False]  # the stale layer is dropped; Esc reaches the drawer / composer


@node
def test_outside_pointerdown_closes_but_the_anchor_counts_as_inside():
    res = run(r"""
const a = mk('BUTTON', R(10, 10, 50, 20)), el = mk('DIV', null, [100, 100]);
pp.open(a, el, {});
down(a);                                                  // the tap that will toggle it: stays open
const onAnchor = pp.isOpen(el);
down(mk('DIV'));                                          // anywhere else
console.log(JSON.stringify([onAnchor, pp.isOpen(el), pp.toggle(a, el, {}), pp.toggle(a, el, {})]));
""")
    assert res == [True, False, True, False]


@node
def test_sheet_under_700px_is_css_placed():
    res = run(r"""
globalThis.PHONE = true;
const a = mk('BUTTON', R(10, 700, 50, 20)), el = mk('DIV', null, [100, 100]);
el.setAttribute('data-sheet', '');
pp.open(a, el, {});
const on = [el.classList.contains('is-sheet'), el.style.top || '', el.style.position || ''];
pp.close(el);
console.log(JSON.stringify([on, el.classList.contains('is-sheet')]));
""")
    assert res == [[True, "", ""], False]


def test_static_contracts():
    js = POPUP.read_text()
    assert len(js.splitlines()) <= 250
    assert "addEventListener('click'" not in js  # never a click handler: capture order in dialog.js / rowx.js stays
    assert re.search(r"""\sstyle\s*=\s*["'\\]""", js) is None and "innerHTML" not in js  # CSSOM only (CSP)
    assert not (ROOT / "static/js/outclick.js").exists()
    css = (ROOT / "static/css/popup.css").read_text()
    assert re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", css) is None  # tokens only
    for sel in (".pop{", ".pop-item{", ".pop.is-sheet{", "dialog.pmdlg{", "dialog.pmdlg::backdrop{background:var(--backdrop)}",
                ".pmdlg-head{", ".pmdlg-foot{", "dialog.pmdlg.pmdlg-wide{", "dialog.pmdlg.pmdlg-full{"):
        assert sel in css, sel
    for t in ("--pop-r", "--pop-shadow", "--z-pop", "--z-sheet"):
        assert f"var({t})" in css
    base = (TEMPLATE_DIR / "base.html").read_text()
    assert base.index("/static/css/nav.css") < base.index("/static/css/popup.css") < base.index("/static/css/filespane.css")
    dlg = (ROOT / "static/js/dialog.js").read_text()
    assert "window.pmDialog" in dlg and "htmx:afterRequest" in dlg and "requestConfig" in dlg
    assert "var ok = el('button', o.danger ? 'small danger' : 'small primary'," in dlg  # OK is never grey
    assert "t.id = 'pmdlg-t';" not in dlg and "'pmdlg-t' + (++seq)" in dlg  # unique title ids, no fixed id
    for old in ("launchpop.js", "plans-pop.js", "imgview.js", "palette.js"):
        assert "e.target === dlg" not in (ROOT / "static/js" / old).read_text(), old  # one backdrop rule, in dialog.js
    assert "kill-dialog" not in (ROOT / "static/pm.js").read_text()


def test_popup_macro_renders_dialog_and_foot():
    from pm.web.render import env

    tpl = env.from_string('{% from "_popup.html" import dialog, dlg_foot %}{% call dialog("x-dlg", "Hello", size="wide", x=true) %}'
                          '<p>Body</p>{% call dlg_foot() %}<button class="small">Go</button>{% endcall %}{% endcall %}')
    html = tpl.render()
    assert html.startswith('<dialog id="x-dlg" class="pmdlg pmdlg-wide" aria-labelledby="x-dlg-title">')
    assert '<p id="x-dlg-title" class="pmdlg-title">Hello</p>' in html and 'class="pmdlg-x" data-dlg-close' in html
    foot = html.split('class="pmdlg-foot"')[1]
    assert foot.index("data-dlg-close") < foot.index(">Go<")  # [Cancel][Primary]


@node
def test_outclick_is_told_to_same_origin_frames_only():
    res = run(r"""
const sent = [];
const same = { location: { origin: 'https://x' }, postMessage(m, o) { sent.push(['same', o]); } };
const opaque = { get location() { throw new Error('SecurityError'); }, postMessage(m, o) { sent.push(['opaque', o]); } };
const other = { location: { origin: 'https://y' }, postMessage(m, o) { sent.push(['other', o]); } };
document.querySelectorAll = (s) => s === 'iframe' ? [{ contentWindow: same }, { contentWindow: opaque }, { contentWindow: other }, { contentWindow: null }] : [];
down(mk('DIV'));
console.log(JSON.stringify(sent));
""")
    assert res == [["same", "https://x"]]  # the sandboxed builder preview (opaque origin) gets no message
