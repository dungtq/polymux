"""The dt service reload bar (PARITY.md "Service reload banner"): js/reload.js (the bounded /healthz wait after
Reload pm or a 502/503/504, the .x-lock look, its PARTS.md §13 row; node, fake timers) and the removal-debt notes in
shell/_classic.html for the classic service worker and app badge, which dt does not register a second time."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pm.web import themes

THEME = themes.HERE / "dt"
TPL = THEME / "templates"
JS = THEME / "static" / "js"
node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def test_reload_js_contract():
    src = (JS / "reload.js").read_text(encoding="utf-8").split("*/", 1)[1]  # the code, not the header
    assert "setInterval" not in src and "MutationObserver" not in src  # V7-CODE-7: one bounded setTimeout chain
    assert "MAXWAIT = 90000" in src and "'/healthz'" in src and r"/\/service\/reload$/" in src
    assert "status === 502 || status === 503 || status === 504" in src
    assert "pm7.el('div', 'x-lock x-rl')" in src  # the existing .x-lock look, no new component
    assert not re.search(r"\bfetch\(", src) and "style=" not in src and ".style." not in src
    parts = (THEME.parents[4] / "docs" / "themes" / "dt" / "PARTS.md").read_text(encoding="utf-8")
    sec = parts[parts.index("## 13. Timed polls"):parts.index("## 14.")]
    assert "`/healthz` wait (`js/reload.js`" in sec


RELOAD_HARNESS = r"""
const src = require('fs').readFileSync(process.argv[1], 'utf8'), scen = process.argv[2];
const topAttrs = process.argv[3] ? JSON.parse(process.argv[3]) : null;  // header.x-top data-rl-* (null: embed)
let now = 1000, reloads = 0;
const timers = [], toasts = [], calls = [], health = [], docLs = {};
globalThis.setTimeout = (fn, ms) => { timers.push({ fn: fn, at: now + (ms || 0) }); return timers.length; };
globalThis.clearTimeout = (id) => { if (id && timers[id - 1]) timers[id - 1].fn = null; };
Date.now = () => now;
globalThis.AbortController = class { constructor() { this.signal = {}; } abort() {} };
class N {
  constructor(tag, cls) { this.tag = tag; this.className = cls || ''; this.kids = []; this.attrs = {};
    this.hidden = false; this.textContent = ''; this.ls = {}; }
  setAttribute(k, v) { this.attrs[k] = String(v); } getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  append(...k) { this.kids.push(...k); } appendChild(k) { this.kids.push(k); } prepend(k) { this.kids.unshift(k); }
  addEventListener(t, f) { this.ls[t] = f; } scrollIntoView() {}
  querySelector(sel) { return this.kids.find((k) => k.className.split(' ').includes(sel.slice(1))) || null; }
}
const main = new N('main');
const top = topAttrs && new N('header', 'x-top');
if (top) Object.keys(topAttrs).forEach((k) => top.setAttribute(k, topAttrs[k]));
if (top) top.hasAttribute = (k) => k in top.attrs;
globalThis.document = { addEventListener: (t, f) => { docLs[t] = f; },
  querySelector: (sel) => sel === 'header.x-top' ? top : null,
  getElementById: (id) => id === 'main' ? main : main.kids.find((k) => k.id === id) || null };
globalThis.window = { pm7: {}, location: { reload: () => { reloads++; } } };
window.top = window;
const pm7 = window.pm7;
pm7.el = (tag, cls) => new N(tag, cls);
pm7.icon = () => null;
pm7.toast = (t) => toasts.push(t);
const asked = [];
let answer = true;
pm7.confirm = (o) => { asked.push(o.title); return Promise.resolve(answer); };
pm7.fail = (what, r) => { toasts.push(what + ' ' + (r ? r.status : 'none')); return Promise.resolve(''); };
const readies = [];
pm7.ready = (fn) => readies.push(fn);
let refuse = false;
pm7.fetch = (url) => {
  calls.push(url);
  if (url === '/service/reload' && refuse) return Promise.resolve({ ok: false, status: 403 });
  if (url === '/healthz') return Promise.resolve({ ok: health.length ? health.shift() : true, status: 200 });
  const st = /boom(\d+)/.exec(url);
  const status = st ? Number(st[1]) : 200;
  return Promise.resolve({ ok: status < 300, status: status });
};
eval(src);
readies.forEach((fn) => fn());
async function flush() { for (let i = 0; i < 20; i++) await Promise.resolve(); }
async function advance(ms) {
  const end = now + ms;
  for (;;) {
    const t = timers.filter((x) => x.fn && x.at <= end).sort((a, b) => a.at - b.at)[0];
    if (!t) break;
    now = t.at; const f = t.fn; t.fn = null; f(); await flush();
  }
  now = end;
}
const bar = () => main.kids.find((k) => k.id === 'x-rl');
const text = () => bar() ? (bar().hidden ? '(hidden) ' : '') + bar().kids[0].textContent : '';
const btn = () => bar() ? (bar().kids[1].hidden ? '' : bar().kids[1].textContent) : '';
const hz = () => calls.filter((c) => c === '/healthz').length;
(async () => {
  const out = {};
  if (scen === 'down') {
    await pm7.fetch('/x?boom0'); await pm7.fetch('/x?boom500'); await pm7.fetch('/x?boom404');
    out.ignored = [text(), pm7.reload.state()];
    await pm7.fetch('/x?boom503');
    out.down = [text(), btn(), pm7.reload.state()];
    await pm7.fetch('/x?boom502');  // already waiting: no second chain
    health.push(false, false);
    await advance(1500); await advance(1000);
    out.still = [text(), pm7.reload.state(), hz()];
    await advance(1000);
    out.back = [text(), btn(), pm7.reload.state(), hz(), reloads];
  } else if (scen === 'restart') {
    await pm7.fetch('/service/reload');
    out.wait = [text(), btn(), pm7.reload.state()];
    health.push(true, true, false, true);  // the old process answers first, then it is gone, then back
    await advance(1000); await advance(700); await advance(700); await advance(1000);
    out.done = [text(), pm7.reload.state(), hz(), reloads];
  } else if (scen === 'bounded') {
    await pm7.fetch('/service/reload');
    for (let i = 0; i < 400; i++) health.push(false);
    await advance(120000);
    out.gaveup = [text(), btn(), bar().kids[1].getAttribute('data-act'), pm7.reload.state(), reloads];
    out.calls = hz();
    await advance(60000);
    out.after = hz();
  } else if (scen === 'code') {
    out.first = [text(), btn()];  // from header.x-top at load
    docLs['pm:pm_code']({ detail: { need: 'restart', msg: 'New code: restart pm to load it.', head: '<b>x</b>' } });
    out.live = [text(), btn(), bar().kids[1].getAttribute('data-act'), calls.length];
    answer = false;
    await bar().kids[1].ls.click(); await flush();
    out.no = [asked.length, calls.length, pm7.reload.state()];  // cancelled: nothing posted
    answer = true;
    await bar().kids[1].ls.click(); await flush();
    out.yes = [asked.length, calls.slice(), pm7.reload.state(), text()];
    docLs['pm:pm_code']({ detail: { need: 'restart', msg: 'again' } });
    out.during = text();  // the restart wait owns the bar
  } else if (scen === 'codefail') {
    docLs['pm:pm_code']({ detail: { need: 'restart', msg: 'm' } });
    refuse = true;
    await bar().kids[1].ls.click(); await flush();
    out.failed = [toasts.slice(), pm7.reload.state(), btn()];
  } else if (scen === 'nocode') {
    docLs['pm:pm_code']({ detail: { need: 'restart', msg: 'New code: restart pm to load it.' } });
    out.text = [text(), btn()];
  } else if (scen === 'htmx') {
    docLs['htmx:afterRequest']({ detail: { successful: false, requestConfig: { path: '/service/reload' },
      xhr: { status: 403 } } });
    out.refused = [toasts.slice(), text(), pm7.reload.state()];
    docLs['htmx:afterRequest']({ detail: { successful: false, pathInfo: { requestPath: '/inbox?x=1' },
      xhr: { status: 504 } } });
    out.down = pm7.reload.state();
    docLs['htmx:afterRequest']({ detail: { successful: true, requestConfig: { path: '/service/reload' },
      xhr: { status: 200 } } });
    out.restart = [pm7.reload.state(), text()];
  }
  console.log(JSON.stringify(out));
})();
"""


def _reload(scen: str, top: dict | None = None) -> dict:
    argv = ["node", "-e", RELOAD_HARNESS, str(JS / "reload.js"), scen] + ([json.dumps(top)] if top is not None else [])
    out = subprocess.run(argv, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@node
def test_reload_after_a_5xx_waits_and_offers_reload_now():
    o = _reload("down")
    assert o["ignored"] == ["", ""]  # status 0, 500, 404: nothing shown
    assert o["down"][0].startswith("pm is not answering") and o["down"][1:] == ["Reload now", "down"]
    assert o["still"][1:] == ["down", 2]
    assert o["back"] == ["pm is back. Reload to get the current page.", "Reload now", "", 3, 0]  # never by itself


@node
def test_reload_after_reload_pm_waits_for_the_restart_then_reloads():
    o = _reload("restart")
    assert o["wait"] == ["pm is restarting… this page reloads when it is back.", "", "restart"]
    assert o["done"] == ["pm is back. Reloading…", "", 4, 1]


@node
def test_reload_wait_is_bounded():
    o = _reload("bounded")
    assert o["gaveup"][0].startswith("pm did not come back within 90 s")
    assert o["gaveup"][1:] == ["Retry", "retry", "", 0]
    assert 40 < o["calls"] < 100 and o["after"] == o["calls"]  # the chain stopped


@node
def test_reload_htmx_requests():
    o = _reload("htmx")
    assert o["refused"] == [["Reload pm failed (HTTP 403). pm was not restarted."], "", ""]
    assert o["down"] == "down"
    assert o["restart"][0] == "restart" and o["restart"][1].startswith("pm is restarting")


@node
def test_new_code_bar_restarts_only_after_a_confirmed_click():
    o = _reload("code", {"data-rl-ok": "", "data-rl-unit": "pm.service", "data-rl-need": "restart",
                         "data-rl-msg": "New code: restart pm to load it.", "data-rl-head": "first"})
    assert o["first"] == ["New code: restart pm to load it. Latest commit: first", "Restart pm"]
    assert o["live"] == ["New code: restart pm to load it. Latest commit: <b>x</b>", "Restart pm", "restart", 0]
    assert o["no"] == [1, 0, ""]  # pm7.confirm said no: nothing posted
    assert o["yes"][:3] == [2, ["/service/reload"], "restart"] and o["yes"][3].startswith("pm is restarting")
    assert o["during"].startswith("pm is restarting")


@node
def test_new_code_bar_refused_restart_toasts_and_waits_for_nothing():
    o = _reload("codefail", {"data-rl-ok": "", "data-rl-unit": "pm.service"})
    assert o["failed"] == [["Reload pm failed. pm was not restarted 403"], "", "Restart pm"]


@node
def test_new_code_bar_without_reload_shows_the_command():
    o = _reload("nocode", {"data-rl-unit": "pm-dev.service"})
    assert o["text"] == ["New code: restart pm to load it. Run: systemctl --user restart pm-dev.service", ""]
    assert _reload("nocode")["text"] == ["", ""]  # embed (no header.x-top): no "new code" state


def _top(**glob) -> str:
    import jinja2

    from pm.web.dt import shell
    from pm.web.dt.stores import TabState

    from .conftest import agent, make_snap
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TPL), autoescape=True)
    env.globals.update(glob)
    sh = shell.build(make_snap([agent("claude-aaa111", "alpha")]), TabState(), "/")
    html = env.get_template("shell/_top.html").render(shell=sh, cur_tab=None, csrf="tok", dev_banner=False)
    return html[html.index("<header"):html.index(">", html.index("<header")) + 1], html


def test_top_carries_the_restart_hooks():
    head, html = _top(reload_ok=True, reload_unit="pm.service", pm_code=lambda: None)
    assert "data-rl-ok" in head and 'data-rl-unit="pm.service"' in head and "data-rl-need" not in head
    assert 'action="/service/reload"' in html  # the account menu's Reload pm
    verdict = {"need": "restart", "msg": "New code: restart pm to load it.", "head": 'fix "x" <y>'}
    head, html = _top(reload_ok=False, reload_unit="pm.service", pm_code=lambda: verdict)
    assert "data-rl-ok" not in head and 'action="/service/reload"' not in html  # gated by reload_ok, not dev_banner
    assert 'data-rl-need="restart"' in head and 'data-rl-msg="New code: restart pm to load it."' in head
    assert 'data-rl-head="fix &#34;x&#34; &lt;y&gt;"' in head
    head, _ = _top()  # globals missing (a bare render): no hooks, no crash
    assert "data-rl-ok" not in head and "data-rl-need" not in head


def test_live_js_forwards_pm_code():
    src = (JS / "live.js").read_text(encoding="utf-8")
    assert "'chat_outbox', 'pm_code']" in src


def test_classic_service_worker_and_app_badge_are_removal_debt():
    head = (TPL / "shell" / "_classic.html").read_text(encoding="utf-8")
    debt = head[head.index("Removal debt"):head.index("-#}")]
    assert "/static/pm.js registers the service worker" in debt and "/sw.js" in debt
    assert "setAppBadge" in debt and "js/inbox.js" in debt
    dt_js = "".join(p.read_text(encoding="utf-8") for p in JS.glob("*.js"))
    assert "serviceWorker" not in dt_js and "setAppBadge" not in dt_js  # not registered a second time
