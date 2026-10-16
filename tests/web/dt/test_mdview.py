"""The dt markdown reader (user issue #250; DESIGN.md §6.19, §7): shell/_mdview.html in the shell's layer roots (not in
embed pages), and js/mdview.js in node against a small fake DOM: pm7.md.open checks, the GET /a/<id>/md URL, the
real path in the head, relative .md links opened in the same dialog against the shown file's folder, other links
left alone, the 404 text."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from pm.web import themes

from .kit import render_base

JS = themes.HERE / "dt" / "static" / "js" / "mdview.js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def test_reader_dialog_markup():
    page, embed = render_base(), render_base(embed=True)
    dlg = page[page.index('<dialog id="x-md"'):page.index("</dialog>", page.index('<dialog id="x-md"'))]
    for hook in ('class="x-dlg x-md"', 'aria-labelledby="x-md-t"', "data-md-title", 'data-md-zoom="-1"',
                 'data-md-zoom="1"', "data-md-zoom-val", "data-md-copy", 'data-act="close"', "data-md-body"):
        assert hook in dlg, hook
    for b in re.findall(r"<button[^>]*>", dlg):  # V7-UI-6
        assert "aria-label=" in b and "title=" in b, b
    assert 'id="x-md"' not in embed


HARNESS = r"""
const CFG = JSON.parse(process.argv[2]);
const log = [], store = {};
function el(tag, attrs) {
  const a = Object.assign({}, attrs || {});
  const n = { tagName: tag, kids: [], textContent: '', title: '', on: {}, style: { p: {},
      setProperty(k, v) { this.p[k] = v; }, removeProperty(k) { delete this.p[k]; } },
    classList: { has: new Set(), contains(c) { return this.has.has(c); } },
    getAttribute: (k) => (k in a ? a[k] : null), addEventListener: (t, f) => { (n.on[t] = n.on[t] || []).push(f); },
    replaceChildren(...k) { n.kids = k; n.html = ''; }, querySelector: () => null, closest: () => null };
  return n;
}
const body = el('div'), ttl = el('bdi'), val = el('span'), dlg = el('dialog');
ttl.parentNode = el('h2');
let mdv = null;
Object.defineProperty(body, 'innerHTML', { set(h) { body.html = h; const m = /data-path="([^"]*)"/.exec(h);
  mdv = m ? el('div', { 'data-path': m[1] }) : null; } });
body.querySelector = (s) => (s === '.mdv' ? mdv : null);
body.querySelectorAll = () => [];
const parts = { '[data-md-body]': body, '[data-md-title]': ttl, '[data-md-zoom-val]': val };
dlg.querySelector = (s) => parts[s] || null;
globalThis.document = { getElementById: (id) => (id === 'x-md' ? dlg : null) };
globalThis.getComputedStyle = () => ({ fontSize: '14px' });
let answer = CFG.answer || [200, '<div class="mdv" data-path="/home/u/p/docs/a.md"><div class="md"></div></div>'];
globalThis.window = { pm7: {
  ready: (f) => f(), el: (spec, at, text) => { const n = el('p'); n.text = text; n.spec = spec; return n; },
  toast: (t) => log.push(['toast', t]),
  store: { get: (k, dv) => (k in store ? store[k] : dv), set: (k, v) => { store[k] = v; } },
  dialog: (x, o) => { log.push(['dialog', x === dlg]); dlg.open = true; return x; },
  fetch: (u) => { log.push(['fetch', u]); const [st, txt] = answer;
    return Promise.resolve({ ok: st < 400, status: st, text: () => Promise.resolve(txt) }); } } };
const pm7 = window.pm7;
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
function click(href, cls) {
  const a = el('a', { href: href });
  if (cls) a.classList.has.add(cls);
  const t = { closest: (s) => (s === '[data-md-body] a[href]' ? a : null) };
  const e = { target: t, prevented: false, preventDefault() { this.prevented = true; } };
  dlg.on.click.forEach((f) => f(e));
  return e.prevented;
}
const tick = () => new Promise((r) => setTimeout(r, 0));
const out = [];
"""


def run(code: str, **cfg):
    script = HARNESS + "(async () => {" + code + "\nconsole.log(JSON.stringify(out)); })();"
    res = subprocess.run(["node", "-e", script, str(JS), json.dumps(cfg)], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


@needs_node
def test_open_loads_the_file_and_shows_its_real_path():
    got = run("out.push(pm7.md.open('bad id', 'a.md'), pm7.md.open('claude-1c70ee', ''), log.length);"
              "out.push(pm7.md.open('claude-1c70ee', 'docs/a b.md')); await tick();"
              "out.push(log, ttl.textContent, ttl.parentNode.title, pm7.md.current(), body.html.length > 0);")
    assert got[:4] == [False, False, 0, True]
    assert got[4] == [["fetch", "/a/claude-1c70ee/md?path=docs%2Fa%20b.md"], ["dialog", True]]
    assert got[5] == got[6] == got[7] == "/home/u/p/docs/a.md" and got[8] is True


@needs_node
def test_links_inside_the_reader():
    got = run("pm7.md.open('claude-1c70ee', 'docs/a.md'); await tick(); log.length = 0;"
              "out.push(click('../b%20c.md#Sec'), click('/abs/x.md'), click('~/n.md'), click('#only'));"
              "out.push(click('https://example.com/x.md'), click('mailto:a@b'), click('/uploads/x.png', 'chat-img'));"
              "out.push(click('src/x.py'), log);")
    assert got[:7] == [True, True, True, True, False, False, False]
    assert got[7] is True  # a relative non-.md link does not navigate the page
    fetched = [x[1] for x in got[8] if x[0] == "fetch"]
    assert fetched == ["/a/claude-1c70ee/md?path=%2Fhome%2Fu%2Fp%2Fb%20c.md", "/a/claude-1c70ee/md?path=%2Fabs%2Fx.md",
                       "/a/claude-1c70ee/md?path=~%2Fn.md"]
    assert ["toast", "Only .md files open here"] in got[8]


@needs_node
def test_missing_file_says_where_it_looked():
    got = run("pm7.md.open('claude-1c70ee', 'SECURITY.md'); await tick(); await tick();"
              "out.push(body.kids.map((k) => [k.spec, k.text]), ttl.textContent);", answer=[404, "{}"])
    assert got[0][0][0] == "p.x-mdn.err" and "agent's folder, its project or the files its chat named" in got[0][0][1]
    assert got[1] == "SECURITY.md"


@needs_node
def test_text_size_buttons_share_the_session_reader_size():
    got = run("pm7.md.open('claude-1c70ee', 'a.md'); const z = (n) => dlg.on.click.forEach((f) => f({target:"
              " {closest: (s) => (s === '[data-md-zoom]' ? el('b', {'data-md-zoom': n}) : null)}}));"
              "z('1'); z('1'); out.push(store['chat.md'], dlg.style.p['--md-size'], val.textContent);")
    assert got == ["16", "16px", "16px"]
