"""The dt shell's server search (PARITY.md "Global search"): the dt override of the core's GET /search fragment
(templates/_search.html, sprite icons, read by js/search.js), the core route rendering it, and the search.js contract
(pm7.fetch, debounce, from 3 characters, dropped duplicates, Quick chat hits in place)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

from pm.core.models import Project
from pm.infra import snapshot as services
from pm.web import themes
from pm.web.dt import DtTheme

from .conftest import make_env

THEME = themes.HERE / "dt"
TPL = THEME / "templates"
JS = THEME / "static" / "js"


def hit(href, name, sub="", kind="", model="", snippet=False):
    return SimpleNamespace(href=href, name=name, sub=sub, kind=kind, model=model, snippet=snippet)


def _render(groups, q="zeb"):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TPL), autoescape=True)
    return env.get_template("_search.html").render(groups=groups, q=q)


def test_fragment_groups_rows_and_sprite_icons():
    html = _render([
        ("project", "Projects", [hit("/p/zebra", "zebra", "/dev/zebra")]),
        ("ended", "Ended agents", [hit("/a/claude-1", "claude-1", "zebra", "claude", "sonnet-4-5")]),
        ("qchat", "Quick chat", [hit("/?qchat=7", "Zebra <b>q</b>", "4m - <script>x</script>", snippet=True)]),
        ("file", "Files", [hit("/p/zebra/files?path=a%20b.md", "zebra/a b.md:3", "zebra line", snippet=True)]),
    ])
    assert re.findall(r'<ul data-sg="(\w+)" data-title="([^"]+)">', html) == [
        ("project", "Projects"), ("ended", "Ended agents"), ("qchat", "Quick chat"), ("file", "Files")]
    rows = re.findall(r'<li data-href="([^"]+)"( data-snip)?>', html)
    assert rows == [("/p/zebra", ""), ("/a/claude-1", ""), ("/?qchat=7", " data-snip"),
                    ("/p/zebra/files?path=a%20b.md", " data-snip")]
    icons = re.findall(r'<use href="/static/t/dt/icons.svg#([\w-]+)"/>', html)
    assert icons == ["folder", "clock", "sparkle", "file"]
    sprite = (THEME / "static" / "icons.svg").read_text(encoding="utf-8")
    assert all(f'id="{i}"' in sprite for i in icons)
    assert "<path" not in html and "viewBox" not in html  # sprite only, no inline SVG
    assert '<span class="r">claude · sonnet-4-5</span>' in html
    assert "<script>" not in html and "&lt;script&gt;" in html and "&lt;b&gt;" in html  # escaped, masked by the core


def test_fragment_empty():
    assert _render([]).strip() == ""


@pytest.fixture
def senv(settings, snap, monkeypatch):
    class RealDt(DtTheme):
        def themes(self):
            return [themes.Theme(name="dt", templates=TPL, static=THEME / "static", label="dt")]

    s = services.Snapshot(ts=1.0, agents=(), sessions=(),
                          projects=(Project("zebra", str(settings.dev_root / "zebra")),))
    snap.value = s

    async def fresh(_settings, max_age_s=0):
        return s

    monkeypatch.setattr(services, "snapshot_async", fresh)
    return make_env(settings, RealDt())


def test_core_search_route_renders_the_dt_fragment(senv):
    r = senv.c.get("/search?q=zeb")
    assert r.status_code == 200
    assert '<ul data-sg="project" data-title="Projects">' in r.text and 'data-href="/p/zebra"' in r.text
    assert "gs-hit" not in r.text  # not the classic fragment
    assert senv.c.get("/search?q=").text.strip() == ""


def test_search_js_contract():
    src = (JS / "search.js").read_text(encoding="utf-8")
    assert "pm7.fetch('/search?q=' + encodeURIComponent(q))" in src and "pm7.debounce(" in src
    assert "const MIN_Q = 3;" in src and "q.length >= MIN_Q" in src and "scope === 'all'" in src
    assert "new DOMParser().parseFromString(html, 'text/html')" in src  # parsed, never inserted as HTML
    assert not re.search(r"innerHTML|insertAdjacentHTML|outerHTML", src)
    assert not re.search(r"\bfetch\(", src.replace("pm7.fetch(", ""))
    assert "n !== seq" in src  # a late answer for an older query is dropped
    assert "!seen.has(norm(o.href))" in src  # server hits a local row already has are dropped
    assert "pm7.qchat.open(Number(m[1]))" in src  # Quick chat hits open in place
    assert max(len(line) for line in src.splitlines()) <= 120 and len(src.splitlines()) <= 500


# ---------------------------------------------------------------- search.js in node (a stub DOM)
# The page: one open tab "Projects" whose link is /p/fixproj (the project view), the Projects page in the nav, no
# sessions. The server answers one hit, the project fixproj (/p/fixproj). The tab does not match "fixproj", so it is
# not shown, and must not hide the server's hit (D2). The footer's ↵ word follows the selected row (M5).
SEARCH_HARNESS = r"""
const L = [];
const node = (o) => Object.assign({ attrs: {}, kids: [], getAttribute(k) { return this.attrs[k] == null ? null
  : this.attrs[k]; }, setAttribute(k, v) { this.attrs[k] = String(v); }, removeAttribute(k) { delete this.attrs[k]; },
  querySelector(s) { return (this.q || {})[s] || null; }, closest() { return null; }, classList: { contains: () => false },
  append(...k) { this.kids.push(...k); }, appendChild(k) { this.kids.push(k); }, replaceChildren(...k) { this.kids = k; },
  cloneNode() { return this; }, focus() {}, textContent: '' }, o);
const text = (n) => (typeof n === 'string' ? n : (n.text || '') + n.kids.map(text).join(''));
const tabA = node({ attrs: { href: '/p/fixproj', 'aria-selected': 'true' } });
const tab = node({ attrs: { 'data-tab-kind': 'page', 'data-tab-id': 'projects' },
  q: { 'a[role=tab]': tabA, '.nm': { textContent: 'Projects' } } });
const nav = node({ attrs: { href: '/projects' }, q: { '.t': { textContent: 'Projects' } } });
const li = node({ attrs: { 'data-href': '/p/fixproj' }, q: { '.l': { textContent: 'fixproj' },
  '.d': { textContent: '/dev/fixproj' } } });
const ul = node({ attrs: { 'data-sg': 'project', 'data-title': 'Projects' } });
const input = node({ value: '' }), list = node({ id: 'x-pl' }), verb = node({});
const P = node({ open: false });
globalThis.window = { pm7: {}, location: { origin: 'http://pm', assign() {} } };
globalThis.DOMParser = function () { this.parseFromString = () => ({ doc: true }); };
globalThis.document = { addEventListener: (t, f) => L.push([t, f]), getElementById: (id) => (id === 'x-pal' ? P : null) };
const pm7 = window.pm7;
pm7.el = (tag, cls, t) => node({ tag: tag, cls: cls || '', text: t || '' });
pm7.icon = () => null;
pm7.debounce = (f) => f;
pm7.dialog = (p) => { p.open = true; };
pm7.fetch = () => Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve('<ul data-sg>') });
pm7.$ = (s, root) => ({ input: input, '[role=listbox]': list, '[data-pfopen]': verb })[s] || null;
pm7.$$ = (s, root) => ({ '#x-tabs .x-tab[data-tab-kind]': [tab], '.x-nav a[data-nav], .x-sfoot a[data-nav]': [nav],
  'ul[data-sg]': [ul], 'li[data-href]': [li] })[s] || [];
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
const rows = () => list.kids.filter((k) => k.cls && k.cls.indexOf('x-pi') === 0)
  .map((k) => text(k.kids.find((c) => c.cls === 'l')));
(async () => {
  pm7.search.open('all', 'fixproj');
  const before = rows();
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
  console.log(JSON.stringify({ before: before, after: rows(), verb: verb.textContent }));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_search_dedup_counts_only_shown_rows_and_footer_follows_the_row():
    out = subprocess.run(["node", "-e", SEARCH_HARNESS, str(JS / "search.js")], capture_output=True, text=True,
                         check=True)
    got = json.loads(out.stdout)
    assert got["before"] == []  # the tab "Projects" does not match: not shown
    assert got["after"] == ["fixproj"]  # so the server's /p/fixproj hit stays
    assert got["verb"] == "go to the page"  # ↵ on a server hit navigates; not "open in a tab"


def test_search_footer_word_hook():
    html = (TPL / "shell" / "_layers.html").read_text(encoding="utf-8")
    assert "<span data-pfopen>" in html and "open in a tab</span>" not in html  # filled per selected row
    src = (JS / "search.js").read_text(encoding="utf-8")
    assert "verb: 'open in a tab'" in src and "verb: 'run'" in src and "['open in Quick chat'" in src
    assert "new Set(local.concat(pg)" in src  # the de-dup set: rows shown for this query only
