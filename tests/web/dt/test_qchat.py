"""The dt Quick chat panel (PRD §6.12): shell/_qchat.html and its openers (top-bar icon after the brand, the phone
drawer row), the shared SSE reader js/qstream.js (node) that Quick chat and btw both use, and the static contract of
js/qchat.js / js/qchat-menus.js (key Q through the key table, a soft layer, the core's /qchat routes only)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import jinja2
import pytest

from pm.web import themes
from pm.web.dt import shell as dtshell

THEME = themes.HERE / "dt"
TPL = THEME / "templates"
JS = THEME / "static" / "js"
node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _shell():
    main, foot = dtshell.nav_items()
    return dtshell.Shell([dtshell.NavRow(k, h, lab, ic, None, False, ch, k == "inbox") for k, h, lab, ic, ch in main],
                         [dtshell.NavRow(k, h, lab, ic, None, False, ch, False) for k, h, lab, ic, ch in foot], [], [],
                         dtshell.Tab("home", "", "Inbox", "/", "", "", True, "inbox"), 1, 0, 0, 0)


def _base(embed=False) -> str:
    env = jinja2.Environment(loader=jinja2.ChoiceLoader([
        jinja2.DictLoader({"page.html": '{% extends "base.html" %}{% block content %}x{% endblock %}'}),
        jinja2.FileSystemLoader(TPL)]), autoescape=True)
    env.globals.update(dt_shell=lambda: dtshell.EMPTY if embed else _shell())
    return env.get_template("page.html").render(csrf="tok", embed=embed, dev_banner=True, require_unlock=False)


def test_panel_markup_and_hooks():
    h = _base()
    panel = re.search(r'<section id="q-panel".*?</section>', h, re.S).group(0)
    assert re.match(r'<section id="q-panel" class="q-pop" role="dialog" aria-modal="false" aria-labelledby="q-title"'
                    r' data-q-panel hidden>', panel)
    for hook in ("data-q-side", "data-q-title", "data-q-new", "data-q-more", "data-q-dock", "data-q-close",
                 "data-q-search", "data-q-list", "data-q-log", "data-q-form", "data-q-input", "data-q-send",
                 "data-q-note", "data-q-btwchip"):
        assert hook in panel, hook
    assert [m for m in re.findall(r'data-q-chip="(\w+)"', panel)] == ["model", "mode", "mcp", "skills"]
    # icon-only buttons are named (V7-UI-6); the btw chip never is an a[data-kind] (classic colours its text)
    for b in re.findall(r"<button [^>]*>", panel):
        if "q-chip" not in b:
            assert "aria-label=" in b, b
    assert not re.search(r"<a [^>]*data-kind", panel)


def test_openers_after_the_brand_and_in_the_drawer():
    h = _base()
    lead = re.search(r'<div class="x-lead">(.*?)</div>', h, re.S).group(1)
    assert lead.index('class="x-brand"') < lead.index('data-q-open')
    assert re.search(r'<button class="x-ib x-qc" type="button" data-q-open aria-controls="q-panel" '
                     r'aria-pressed="false" aria-label="Quick chat"', lead)
    foot = re.search(r'<div class="x-sfoot">(.*?)</div>', h, re.S).group(1)
    assert re.search(r'<button class="x-sqc" type="button" data-q-open[^>]*>.*Quick chat', foot, re.S)
    assert re.search(r'<a class="x-sla" href="/launch" data-l-open[^>]*>.*Launch an agent', foot, re.S)  # phones
    srcs = re.findall(r'<script src="/static/t/dt/js/([^"]+)"', h)
    assert srcs.index("qstream.js") < srcs.index("btw.js") < srcs.index("qchat.js") < srcs.index("qchat-menus.js")
    assert 'href="/static/t/dt/css/qchat.css"' in h


def test_embed_has_no_panel():
    h = _base(embed=True)
    for gone in ("q-panel", "dt/js/qchat.js", "qstream.js", "dt/css/qchat.css", "data-q-open"):
        assert gone not in h, gone


def test_js_contract():
    q, menus, btw = ((JS / n).read_text() for n in ("qchat.js", "qchat-menus.js", "btw.js"))
    keys = (JS / "keys.js").read_text()
    # key Q and the > command "Quick chat": one row of the shell key table, its run pointed at toggle here
    assert re.search(r"id: 'qchat', group: 'Panels', label: '[^']+', keys: \['Q'\]", keys)
    assert "qchat: 'Quick chat'" in keys and "t.id === 'qchat'" in q and "row.run = () => { toggle(); }" in q
    assert "pm7.layers.soft({ rank: 30" in q  # Esc: after dialogs / menus, before the overlay sidebar (rank 40)
    for src in (q, menus):
        assert not re.search(r"\b(d|document|window)\.addEventListener\('keydown'", src)  # the key table does it
        assert not re.search(r"\bfetch\(", src.replace("pm7.fetch(", ""))  # only pm7.fetch (core.js)
        assert set(re.findall(r"'(/qchat/[a-z-]+)", src)) <= {"/qchat/options", "/qchat/convs", "/qchat/send",
                                                              "/qchat/delete-all"}
    assert "pm7.qstream.read(" in btw and "getReader" not in btw  # one SSE reader (js/qstream.js)
    assert "pm7.qstream.send(" in q
    assert "Open as a tab" not in menus.split("*/", 1)[1]  # not offered: the tab store has no quick chat kind
    # Delete and Delete all cannot be undone: typed confirms naming the object (V7-SEC-6)
    dl = menus[menus.index("async function del("):menus.index("function convMenu")]
    assert dl.count("pm7.confirm(") == 2 and "typed: name" in dl and "typed: 'delete'" in dl
    assert "'/qchat/delete-all'" in dl and "Delete all conversations" in menus


@node
def test_qstream_parse_and_read():
    code = r"""
globalThis.window = { pm7: {} };
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
const S = window.pm7.qstream;
const enc = new TextEncoder();
const chunks = ['event: conv\ndata: {"id": 7, "title": "t"}\n\nevent: del', 'ta\ndata: {"t": "Hi"}\n\n',
  'event: done\ndata: {"html": "<p>Hi</p>"}\n\n'];
let i = 0;
const body = { getReader: () => ({ read: async () => (i < chunks.length ? { done: false, value: enc.encode(chunks[i++]) }
  : { done: true }) }) };
const seen = [];
S.read(body, (e, d) => seen.push([e, d])).then((done) => console.log(JSON.stringify(
  [done, seen, S.parse('event: error\ndata: {"msg": "x"}'), S.parse('data: nope')])));
"""
    out = subprocess.run(["node", "-e", code, str(JS / "qstream.js")], capture_output=True, text=True, check=True)
    done, seen, err, bad = json.loads(out.stdout)
    assert done is True
    assert seen == [["conv", {"id": 7, "title": "t"}], ["delta", {"t": "Hi"}], ["done", {"html": "<p>Hi</p>"}]]
    assert err == {"event": "error", "data": {"msg": "x"}} and bad is None


def test_qchat_deep_link_contract():
    q = (JS / "qchat.js").read_text(encoding="utf-8")
    ready = q[q.index("pm7.ready(() => {"):]
    assert "deepLink();" in ready  # after wire(): the panel's listeners exist before it opens
    assert ready.index("wire();") < ready.index("deepLink();")
    fn = q[q.index("function deepLink()"):q.index("pm7.ready(")]
    assert "searchParams.get('qchat')" in fn and "window.history.replaceState(" in fn
    assert r"/^\d+$/.test(id)" in fn and ".finally(done)" in fn  # the param goes even when the open fails


@node
def test_qchat_deep_link_opens_then_drops_the_param():
    q = (JS / "qchat.js").read_text(encoding="utf-8")
    fn = q[q.index("function deepLink()"):q.index("  pm7.ready(")]
    code = r"""
const fn = process.argv[1];
async function run(href) {
  const seen = [];
  globalThis.window = { location: { href: href },
    history: { state: { k: 1 }, replaceState: (s, t, u) => seen.push(['replace', s, u]) } };
  const open = (id) => { seen.push(['open', id]); return Promise.resolve(); };
  eval(fn + '; deepLink();');
  await new Promise((r) => setTimeout(r, 0));
  return seen;
}
(async () => console.log(JSON.stringify([await run('https://pm.test/?qchat=12&sort=new#x'),
  await run('https://pm.test/p/a?qchat=bad'), await run('https://pm.test/p/a?x=1')])))();
"""
    out = subprocess.run(["node", "-e", code, fn], capture_output=True, text=True, check=True)
    ok, bad, none = json.loads(out.stdout)
    assert ok == [["open", 12], ["replace", {"k": 1}, "/?sort=new#x"]]  # opened first, then the param is gone
    assert bad == [["replace", {"k": 1}, "/p/a"]]  # not a number: not opened, still dropped
    assert none == []
