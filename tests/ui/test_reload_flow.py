"""Reload pm: the request shows an in-page message, polls /healthz and reloads the page when pm is back.

The dev instance has no Reload menu item (dev_banner), so the test injects the same form the Menu and the
agent notice use and mocks /service/reload + /healthz; it never restarts anything.
"""
from __future__ import annotations

import pytest

from .conftest import show_composer

pytestmark = pytest.mark.ui

FORM = ("(root)=>{const f=root.createElement('form');f.id='t-rl';f.method='post';f.action='/service/reload';"
        "f.setAttribute('hx-post','/service/reload');f.setAttribute('hx-swap','none');"
        "f.innerHTML='<input type=hidden name=csrf value=x><button type=submit id=t-rl-b>Reload pm</button>';"
        "root.body.prepend(f);(root.defaultView.htmx).process(f);}")


def _mock(page, health_fail: int, reload_status: int = 200):
    calls = {"h": 0}

    def reload(route):
        route.fulfill(status=reload_status, content_type="text/html", body="<p>x</p>")

    def health(route):
        calls["h"] += 1
        if calls["h"] <= health_fail:
            route.abort()
        else:
            route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

    page.route("**/service/reload", reload)
    page.route("**/healthz", health)
    return calls


def test_reload_top_page_waits_then_reloads(page, inst):
    page.goto(f"/p/{inst['project']}")
    page.wait_for_function("() => window.htmx && document.getElementById('reloadbar')")
    _mock(page, health_fail=2)
    page.evaluate("window.__marker=1")
    page.evaluate("(" + FORM + ")(document)")
    page.click("#t-rl-b")
    bar = page.locator("#reloadbar")
    bar.wait_for(state="visible")
    assert "Restarting pm" in bar.inner_text()
    page.wait_for_function("() => window.__marker !== 1", timeout=20000)  # page was reloaded
    page.wait_for_load_state("load")
    assert not page.locator("#reloadbar").is_visible()


def test_reload_request_failure_is_reported(page, inst):
    page.goto(f"/p/{inst['project']}")
    page.wait_for_function("() => window.htmx && document.getElementById('reloadbar')")
    _mock(page, health_fail=0, reload_status=403)
    page.evaluate("(" + FORM + ")(document)")
    page.click("#t-rl-b")
    page.locator("#reloadbar").wait_for(state="visible")
    assert "failed" in page.locator("#reloadbar").inner_text()
    assert page.evaluate("window.__marker === undefined")


def test_reload_inside_the_chat_frame_reloads_the_top_page(page, inst):
    aid = inst["agents"]["running"]
    page.goto("/")
    page.locator(f"main a[href='/a/{aid}']").first.click()
    page.wait_for_selector("body.chat-open")
    frame = page.frame_locator("#vc-frame")
    frame.locator(".agent-head").wait_for(state="attached")  # the embedded head is collapsed (hidden)
    _mock(page, health_fail=1)
    page.evaluate("window.__marker=1")
    fr = next(f for f in page.frames if f != page.main_frame and "/a/" in f.url)
    fr.wait_for_function("() => window.htmx && document.getElementById('reloadbar')")
    fr.evaluate("(" + FORM + ")(document)")
    fr.click("#t-rl-b")
    # agent pages show the notice in the composer row ([data-cmp-reload], reload.js), not in the page-level #reloadbar;
    # the narrow aside starts with the composer hidden
    show_composer(fr)
    frame.locator("[data-cmp-reload]").wait_for(state="visible")
    assert "Restarting pm" in frame.locator("[data-cmp-reload] .pmr-t").inner_text()
    page.wait_for_function("() => window.__marker !== 1", timeout=20000)


def test_skills_table_fixed_layout(make_page, inst):
    pg = make_page(width=390)
    pg.goto(f"/p/{inst['project']}")
    pg.locator("a.pj-tab[data-pj=skills]").click()  # Skills is a docs-panel tab, loaded on select
    t = pg.locator("table.pj-sk").first
    pg.locator("[data-sk-tab=global]").click()
    t.wait_for(state="attached")
    assert t.evaluate("e=>getComputedStyle(e).tableLayout") == "fixed"
    assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    assert t.locator("tbody tr").count() >= 2
