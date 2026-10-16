"""Sidebar width: drag / keyboard / dblclick, persisted, clamped, collapsed, no flash on reload (desktop)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui

W = "()=>new Promise(r=>setTimeout(()=>r(Math.round(document.getElementById('v5side').getBoundingClientRect().width)),250))"
MAINX = "()=>Math.round(document.querySelector('main').getBoundingClientRect().x)"


def _open(page, inst):
    page.goto(f"/p/{inst['project']}")
    page.wait_for_load_state("networkidle")
    return page.locator(".vs-grip")


def _drag(page, grip, dx):
    b = grip.bounding_box()
    x, y = b["x"], b["y"] + 200
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx / 2, y)
    page.mouse.move(x + dx, y)
    page.mouse.up()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_drag_persist_reset(make_page, inst, theme):
    page = make_page(width=1300, theme=theme)
    g = _open(page, inst)
    assert g.get_attribute("role") == "separator" and g.get_attribute("aria-orientation") == "vertical"
    assert page.evaluate(W) == 248
    _drag(page, g, 100)
    assert page.evaluate(W) == 348
    assert g.get_attribute("aria-valuenow") == "348"
    assert abs(page.evaluate(MAINX) - 348) <= 1
    page.reload()
    page.wait_for_load_state("networkidle")
    assert page.evaluate(W) == 348
    g.dblclick()
    assert page.evaluate(W) == 248
    assert page.evaluate("()=>localStorage.getItem('pm.v5.sidew')") is None


def test_clamp_and_keyboard(make_page, inst):
    page = make_page(width=1300)
    g = _open(page, inst)
    _drag(page, g, -400)
    assert page.evaluate(W) == 180
    _drag(page, page.locator(".vs-grip"), 900)
    assert page.evaluate(W) == 520  # 40% of 1300
    g.focus()
    page.keyboard.press("ArrowLeft")
    assert page.evaluate(W) == 504 and g.get_attribute("aria-valuenow") == "504"
    page.keyboard.press("Home")
    assert page.evaluate(W) == 180
    page.keyboard.press("Enter")
    assert page.evaluate(W) == 248


def test_collapsed_hides_handle_and_keeps_width(make_page, inst):
    page = make_page(width=1300)
    g = _open(page, inst)
    g.focus()
    page.keyboard.press("ArrowRight")
    assert page.evaluate(W) == 264
    page.click(".vs-toggle")
    assert page.evaluate(W) == 44 and not g.is_visible()
    page.click(".vs-toggle")
    assert page.evaluate(W) == 264 and g.is_visible()


def test_no_handle_on_phone_and_no_flash(make_page, inst):
    page = make_page(width=390)
    page.goto(f"/p/{inst['project']}")
    assert not page.locator(".vs-grip").is_visible()
    page = make_page(width=1300)
    page.evaluate("()=>localStorage.setItem('pm.v5.sidew','300')")
    page.goto(f"/p/{inst['project']}", wait_until="commit")
    page.wait_for_load_state("domcontentloaded")
    assert page.evaluate("()=>document.documentElement.style.getPropertyValue('--side-user')") == "300px"
