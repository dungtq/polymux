"""popup.js (pm.popover): one layer stack for menus, popovers and phone sheets. Arrow keys in the account menu, Esc closes
only the topmost layer, outside clicks close, the More sheet toggles on its own button, sheets under 700px."""
from __future__ import annotations

import pytest

from .conftest import chat_frame, show_composer

pytestmark = pytest.mark.ui

FOCUSED = "() => { const a = document.activeElement; return a ? (a.getAttribute('aria-label') || a.textContent).trim() : '' }"


def _items(page):
    return page.locator("#acct-menu .acct-pop [role=menuitem]").evaluate_all(
        "ns => ns.filter(n => n.offsetParent).map(n => (n.getAttribute('aria-label') || n.textContent).trim())")


def test_account_menu_arrow_keys(page):
    page.goto("/audit")
    page.locator("#acct-menu > summary").click()
    page.wait_for_function("() => document.getElementById('acct-menu').open")
    items = _items(page)
    assert len(items) >= 4
    page.wait_for_function(f"() => ({FOCUSED})() === {items[0]!r}")  # focus moved to the first item
    page.keyboard.press("ArrowDown")
    assert page.evaluate(FOCUSED) == items[1]
    page.keyboard.press("End")
    assert page.evaluate(FOCUSED) == items[-1]
    page.keyboard.press("ArrowDown")  # wraps
    assert page.evaluate(FOCUSED) == items[0]
    page.keyboard.press("ArrowUp")
    assert page.evaluate(FOCUSED) == items[-1]
    page.keyboard.press("Home")
    assert page.evaluate(FOCUSED) == items[0]
    page.keyboard.press("Escape")
    assert not page.locator("#acct-menu").evaluate("d => d.open")
    assert page.evaluate("() => document.activeElement.matches('#acct-menu > summary')")  # focus back on the anchor


def test_escape_closes_only_the_topmost_layer(page, inst):
    page.goto(f"/#chat=/a/{inst['agents']['idle']}")
    page.wait_for_selector("body.chat-open")
    page.locator("#acct-menu > summary").click()
    page.wait_for_function("() => document.getElementById('acct-menu').open")
    page.keyboard.press("Escape")  # the menu, not the chat aside under it
    assert not page.locator("#acct-menu").evaluate("d => d.open")
    assert page.evaluate("() => document.body.classList.contains('chat-open')")
    # a modal opened over an open menu takes Esc first; the menu stays until the next Esc
    page.locator("#acct-menu > summary").click()
    page.wait_for_function("() => document.getElementById('acct-menu').open")
    page.evaluate("() => { window.__r = 'pending'; window.pmConfirm({title: 'Layer test'}).then(v => { window.__r = v; }); }")
    page.wait_for_selector("dialog[data-pm-confirm][open]")
    page.keyboard.press("Escape")
    page.wait_for_function("() => window.__r === null")
    assert page.locator("dialog[data-pm-confirm]").count() == 0
    assert page.locator("#acct-menu").evaluate("d => d.open")
    page.keyboard.press("Escape")
    assert not page.locator("#acct-menu").evaluate("d => d.open")
    assert page.evaluate("() => document.body.classList.contains('chat-open')")


def test_outside_click_closes_and_one_menu_at_a_time(page):
    page.goto("/audit")
    page.locator("#acct-menu > summary").click()
    page.wait_for_function("() => document.getElementById('acct-menu').open")
    page.locator("#bell-menu > summary").click()  # opening the bell closes the account menu
    page.wait_for_function("() => document.getElementById('bell-menu').open && !document.getElementById('acct-menu').open")
    page.mouse.click(600, 600)
    page.wait_for_function("() => !document.getElementById('bell-menu').open")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_more_sheet_tap_toggles_and_is_a_bottom_sheet(make_page, theme):
    page = make_page(width=390, height=844, theme=theme)
    page.goto("/audit")
    more, sheet = page.locator(".tabmore"), page.locator("#moresheet")
    more.click()
    sheet.wait_for(state="visible")
    assert more.get_attribute("aria-expanded") == "true" and more.get_attribute("aria-controls") == "moresheet"
    assert sheet.evaluate("s => s.classList.contains('is-sheet')")
    box, nav = sheet.bounding_box(), page.locator("nav.tabbar").bounding_box()
    assert box["x"] <= 0.5 and box["width"] >= 390 - 16  # full width (the classic scrollbar gutter may stay free)
    assert abs((box["y"] + box["height"]) - nav["y"]) <= 2  # sits on the bottom nav, the More button stays tappable
    page.keyboard.press("ArrowDown")  # role=menu: arrows move between the items
    assert page.evaluate("() => document.activeElement.closest('#moresheet') !== null")
    more.click()  # second tap on the same button closes it (not close-then-reopen)
    sheet.wait_for(state="hidden")
    assert more.get_attribute("aria-expanded") == "false"
    more.click()
    sheet.wait_for(state="visible")
    page.mouse.click(200, 200)  # outside tap
    sheet.wait_for(state="hidden")


def test_account_menu_is_a_bottom_sheet_on_phones(make_page):
    page = make_page(width=390, height=844)
    page.goto("/audit")
    page.locator("#acct-menu > summary").click()
    pop = page.locator("#acct-menu .acct-pop")
    pop.wait_for(state="visible")
    assert pop.evaluate("p => p.classList.contains('is-sheet')")
    box = pop.bounding_box()
    assert box["x"] <= 0.5 and box["width"] >= 390 - 16 and box["y"] > 844 / 3
    page.keyboard.press("Escape")
    assert not pop.evaluate("p => p.classList.contains('is-sheet')") and not page.locator("#acct-menu").evaluate("d => d.open")


# ---- outside click and Escape on <details> popovers, across the chat drawer iframe (was test_outclick.py) ----
def test_menu_and_stats_close_on_outside_click_and_escape(make_page, inst):
    page = make_page()
    page.goto(f"/a/{inst['agents']['idle']}", wait_until="networkidle")
    menu = page.locator("#acct-menu")
    menu.locator("summary").click()
    assert menu.evaluate("d=>d.open")
    menu.locator(".acct-pop").click(position={"x": 2, "y": 2})  # inside: stays
    assert menu.evaluate("d=>d.open")
    page.locator("main").click(position={"x": 30, "y": 2})  # outside (x=5 now hits a sidebar link and navigates away)
    assert not menu.evaluate("d=>d.open")
    stats = page.locator("#chat-stats")
    if stats.count():
        stats.locator(":scope > summary").click()
        assert stats.evaluate("d=>d.open")
        menu.locator("summary").click()  # opening another closes this one
        assert not stats.evaluate("d=>d.open") and menu.evaluate("d=>d.open")
        page.keyboard.press("Escape")
        assert not menu.evaluate("d=>d.open")


def test_drawer_click_closes_chat_iframe_popover(make_page, inst):
    page = make_page()
    page.goto(f"/a/{inst['agents']['idle']}?embed=1", wait_until="networkidle")
    stats = page.locator("#chat-stats")
    if not stats.count():
        pytest.skip("no stats popover for this agent")
    stats.locator(":scope > summary").click()
    assert stats.evaluate("d=>d.open")
    page.evaluate("window.postMessage({pm:'outclick'}, location.origin)")  # same-window message is ignored (not parent/child)
    assert stats.evaluate("d=>d.open")
    page.mouse.click(5, 300)
    assert not stats.evaluate("d=>d.open")


def test_parent_click_closes_popover_inside_drawer_iframe(make_page, inst):
    page = make_page()
    page.goto(f"/#chat=/a/{inst['agents']['idle']}")
    page.wait_for_selector("body.chat-open")
    fr = page.frame_locator("#vc-frame")
    show_composer(chat_frame(page))  # the narrow aside starts with the composer hidden
    stats = fr.locator("#chat-stats")
    stats.locator(":scope > summary").click()
    assert stats.evaluate("d=>d.open")
    page.locator("#acct-menu summary").click()  # parent document
    page.wait_for_function("()=>!document.querySelector('#vc-frame').contentDocument.querySelector('#chat-stats').open")
    fr.locator("#chat-stats > summary").click()
    assert fr.locator("#chat-stats").evaluate("d=>d.open")
    page.mouse.click(5, 600)
    page.wait_for_function("()=>!document.querySelector('#vc-frame').contentDocument.querySelector('#chat-stats').open")
