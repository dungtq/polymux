"""Notices auto-hide (core.js pm.notify + the htmx:afterSwap hook) in a real browser against the dev instance.

An ok notice in a flash slot clears after about 4 s; an err notice stays. The Effort pick posts to the dev
instance's fake tmux (as test_composer_modelpick.py does); the composer textarea is never typed into."""
from __future__ import annotations

import time

import pytest

from .conftest import show_composer

pytestmark = pytest.mark.ui

SLOT = "[data-composer=live] [data-cmp-flash]"


def _open(page, inst):
    page.goto("/a/" + inst["agents"]["running"])
    page.wait_for_selector("[data-composer=live] .mp-pill", state="attached")
    show_composer(page)
    page.locator("[data-composer=live] .mp-pill").wait_for(state="visible")


def _gone_after(page, sel: str, timeout: int = 8000) -> float:
    t0 = time.monotonic()
    page.wait_for_function("s => !document.querySelector(s)", arg=sel, timeout=timeout)
    return time.monotonic() - t0


def test_effort_set_notice_disappears(page, inst):
    """BACKLOG bug: 'Effort set to high.' above the composer never went away."""
    _open(page, inst)
    page.locator("[data-composer=live] .mp-pill").click()
    page.locator("[data-composer=live] .mp-menu").get_by_role("menuitem", name="Effort").click()
    rows = page.locator('[data-composer=live] [data-mp-panel="effort"] button[type=submit][name=effort]')
    rows.first.wait_for(state="visible")
    current = page.locator("[data-composer=live] [data-mp-pill-e]").inner_text().strip()
    target = next(v for v in rows.evaluate_all("els => els.map(e => e.value)") if v != current)
    rows.filter(has=page.locator(f"xpath=self::*[@value='{target}']")).click()
    note = page.locator(f"{SLOT} .flash.ok")
    note.wait_for(state="visible")
    assert note.inner_text().strip() == f"Effort set to {target}."
    took = _gone_after(page, f"{SLOT} .flash.ok")
    assert took > 2.5, took  # the 4 s timer, not an immediate clear
    assert page.locator(SLOT).inner_html() == "" and page.locator(SLOT).is_hidden()  # .flash-inline:empty


def test_htmx_swapped_ok_hides_and_err_stays(page, inst):
    _open(page, inst)
    page.evaluate("""() => htmx.swap('[data-composer=live] [data-cmp-flash]', '<p class="flash ok">Saved.</p>', {swapStyle: 'innerHTML'})""")
    page.locator(f"{SLOT} .flash.ok").wait_for(state="visible")
    assert _gone_after(page, f"{SLOT} .flash.ok") > 2.5
    page.evaluate("""() => htmx.swap('[data-composer=live] [data-cmp-flash]', '<p class="flash err">Refused.</p>', {swapStyle: 'innerHTML'})""")
    page.wait_for_timeout(5500)
    assert page.locator(f"{SLOT} .flash.err").inner_text().strip() == "Refused."


def test_notify_err_stays_and_ok_clears(page, inst):
    _open(page, inst)
    page.evaluate("() => pm.notify('Upload failed.', {kind: 'err', target: pm.composer.slot(document.querySelector('[data-composer=live] textarea'))})")
    err = page.locator(f"{SLOT} .flash.err")
    assert err.inner_text().strip() == "Upload failed."
    color = err.evaluate("e => getComputedStyle(e).color")
    page.wait_for_timeout(5500)
    assert err.is_visible()
    failed = page.evaluate("""() => { const p = document.createElement('p'); p.style.color = 'var(--failed)';
        document.body.appendChild(p); const c = getComputedStyle(p).color; p.remove(); return c; }""")
    assert color == failed, (color, failed)
    page.evaluate("() => pm.notify('<b>Done</b>', {ttl: 1000, target: document.querySelector('[data-composer=live] [data-cmp-flash]')})")
    ok = page.locator(f"{SLOT} .flash.ok")
    assert ok.inner_text() == "<b>Done</b>"  # text, not HTML
    assert _gone_after(page, f"{SLOT} .flash.ok", timeout=4000) < 3.5
