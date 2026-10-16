"""Canvas split: chat | divider | pane at 50/50, drag + keyboard resize, persistence, reset, phone overlay."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui


def _open(page, inst):
    # Canvas sits only on the last reply of a finished turn (the idle agent's last turn has ended)
    page.goto(f"/a/{inst['agents']['idle']}")
    btn = page.locator("#ci .ci-done-last [data-canvas-open]").first
    btn.wait_for(timeout=20000)
    btn.click()
    page.wait_for_selector("#mdpane.is-canvas .cv-ed")


def _w(page):
    return page.evaluate("""() => { const r = s => document.querySelector(s).getBoundingClientRect();
      return {chat: r('.chatmain').width, pane: r('#mdpane').width, div: r('.cs-div').width,
              sp: r('#chatsplit').width, now: +document.querySelector('.cs-div').getAttribute('aria-valuenow')}; }""")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_split_default_and_a11y(make_page, inst, theme):
    pg = make_page(width=1400, height=900, theme=theme)
    _open(pg, inst)
    w = _w(pg)
    assert abs(w["chat"] - w["pane"]) <= 30 and w["now"] == 50  # 50/50 (pane has 12px divider-neutral rounding)
    dv = pg.locator(".cs-div")
    assert dv.get_attribute("role") == "separator" and dv.get_attribute("aria-orientation") == "vertical"
    assert dv.get_attribute("tabindex") == "0"
    assert not pg.evaluate("document.documentElement.scrollWidth > innerWidth + 1")


def test_split_drag_keys_persist_reset(page, inst):
    _open(page, inst)
    dv = page.locator(".cs-div")
    box = dv.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 200)
    page.mouse.down()
    page.mouse.move(box["x"] - 150, box["y"] + 200, steps=6)
    page.mouse.up()
    w = _w(page)
    assert w["now"] < 50 and w["chat"] < w["pane"] and w["chat"] >= 319
    saved = page.evaluate("localStorage.getItem('pm.chatsplit')")
    assert saved and abs(float(saved) - w["now"]) < 1
    # keyboard
    dv.focus()
    before = _w(page)["now"]
    page.keyboard.press("ArrowRight")
    assert _w(page)["now"] == before + 2
    page.keyboard.press("Shift+ArrowRight")
    assert _w(page)["now"] == before + 12
    page.keyboard.press("Home")
    w = _w(page)
    assert w["chat"] >= 319 and w["now"] <= before
    page.keyboard.press("End")
    w = _w(page)
    assert w["pane"] >= 359
    # persisted across reload
    page.reload()
    page.locator("#ci .ci-done-last [data-canvas-open]").first.wait_for(timeout=20000)
    page.locator("#ci .ci-done-last [data-canvas-open]").first.click()
    page.wait_for_selector("#mdpane.is-canvas .cv-ed")
    assert _w(page)["pane"] >= 359 and _w(page)["now"] > 55
    # double-click resets
    page.locator(".cs-div").dblclick()
    w = _w(page)
    assert w["now"] == 50 and abs(w["chat"] - w["pane"]) <= 30
    assert page.evaluate("localStorage.getItem('pm.chatsplit')") is None


def test_split_phone_has_no_divider(make_page, inst):
    pg = make_page(width=390, height=800)
    _open(pg, inst)
    assert not pg.locator(".cs-div").is_visible()
    assert pg.locator("#mdpane").bounding_box()["x"] <= 1
