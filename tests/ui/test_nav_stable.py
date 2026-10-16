"""The top nav must not move between pages or when the inbox badge changes (desktop)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui

ROUTES = ["/", "/agents", "/p", "/tmux", "/servers", "/backlog"]
JS = """()=>({links:[...document.querySelectorAll('.tabbar>a')].filter(a=>a.offsetWidth).map(a=>{const b=a.getBoundingClientRect();return [Math.round(b.x*10)/10,Math.round(b.width*10)/10]}),
 right:Math.round(document.querySelector('.topbar>span').getBoundingClientRect().x*10)/10,
 brand:Math.round(document.querySelector('.topbar .brand').getBoundingClientRect().width*10)/10})"""


def test_nav_geometry_same_on_every_page(page):
    seen = {}
    for r in ROUTES:
        page.goto(r)
        page.wait_for_load_state("networkidle")
        seen[r] = page.evaluate(JS)
    first = seen[ROUTES[0]]
    for r, g in seen.items():
        assert g == first, f"nav moved on {r}: {g} != {first}"


def test_inbox_badge_does_not_resize_nav(page):
    page.goto("/")
    page.wait_for_load_state("networkidle")
    base = None
    for n in (0, 3, 12, 120):
        page.evaluate("n=>document.querySelectorAll('[data-inbox-badge]').forEach(b=>{b.textContent=n>99?'99+':n;b.hidden=n<=0})", n)
        g = page.evaluate(JS)
        base = base or g
        assert g == base, f"badge {n} moved nav: {g} != {base}"


def test_inbox_badge_does_not_overlap_label(page):
    page.goto("/")
    page.wait_for_load_state("networkidle")
    for n in (0, 3, 12, 120):
        r = page.evaluate("""n=>{const a=document.querySelector('.tabbar a[href="/"]');
          const b=a.querySelector('[data-inbox-badge]');b.textContent=n>99?'99+':n;b.hidden=n<=0;
          const rg=document.createRange();rg.setStart(a.firstChild,0);rg.setEnd(a.firstChild,a.firstChild.length);
          const l=rg.getBoundingClientRect(),k=b.getBoundingClientRect(),w=a.getBoundingClientRect();
          return {lr:l.right,kl:k.left,kr:k.right,ar:w.right,vis:!b.hidden}}""", n)
        if r["vis"]:
            assert r["kl"] >= r["lr"] + 2, f"badge {n} overlaps label: {r}"
            assert r["kr"] <= r["ar"], f"badge {n} outside link: {r}"
