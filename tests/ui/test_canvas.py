"""Canvas: open an assistant reply as editable markdown in the side pane, save, restore, 409, send to composer."""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from .conftest import modal_ok

pytestmark = pytest.mark.ui


def _open(page, inst):
    # Canvas sits only on the last reply of a finished turn (the idle agent's last turn has ended)
    page.goto(f"/a/{inst['agents']['idle']}")
    btn = page.locator("#ci .ci-done-last [data-canvas-open]").first
    btn.wait_for(state="attached", timeout=20000)
    btn.click()
    page.wait_for_selector("#mdpane.is-canvas .cv-ed")
    expect(page.locator(".cv-ed")).not_to_have_value("")


def test_canvas_edit_save_restore(page, inst):
    _open(page, inst)
    ed = page.locator(".cv-ed")
    st = page.locator(".cv-state")
    assert st.inner_text() == "saved"
    ed.press("End")
    ed.type(" EDITED")
    assert st.inner_text() == "unsaved edits"
    page.keyboard.press("Control+s")
    expect(st).to_have_text("saved")
    assert page.locator(".cv-hist li").count() >= 2
    # restore the first version (button only on non-current rows)
    page.locator(".cv-hist [data-cv-restore]").first.click()
    expect(ed).not_to_have_value(re.compile("EDITED"))
    # preview renders
    page.locator("[data-cv-m=view]").click()
    expect(page.locator(".cv-pv > *").first).to_be_visible()


def test_canvas_stale_and_send(page, inst):
    _open(page, inst)
    cid = page.evaluate("(() => { const m = JSON.parse(localStorage.getItem('pm.canvas.' + document.getElementById('chatsplit').dataset.agent) || '{}'); return Object.values(m)[0]; })()")
    aid = inst["agents"]["idle"]
    csrf = page.locator("[data-composer=live] [data-cmp-form] input[name=csrf]").input_value()
    # another writer bumps the version behind our back
    page.evaluate("""async ([aid, cid, csrf]) => {
      const g = await (await fetch(`/a/${aid}/canvas/${cid}`)).json();
      const p = new URLSearchParams({csrf, text: 'other writer', base: g.cur});
      await fetch(`/a/${aid}/canvas/${cid}/save`, {method: 'POST', body: p, headers: {'X-CSRF-Token': csrf}});
    }""", [aid, cid, csrf])
    page.locator(".cv-ed").fill("my local edit")
    page.keyboard.press("Control+s")
    page.wait_for_selector(".cv-note:not([hidden])")
    assert page.locator(".cv-state").inner_text() == "out of date"
    assert page.locator(".cv-ed").input_value() == "my local edit"
    page.locator("[data-cv-reload]").click()
    assert "Discard your edits" in modal_ok(page)
    expect(page.locator(".cv-ed")).to_have_value("other writer")
    # send to agent fills the composer
    page.locator("[data-cv-send]").click()
    assert "other writer" in page.locator("[data-composer=live] textarea[name=text]").input_value()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_canvas_phone_overlay(make_page, inst, theme):
    pg = make_page(width=390, height=800, theme=theme)
    _open(pg, inst)
    box = pg.locator("#mdpane").bounding_box()
    assert box["width"] >= 360 and box["x"] <= 1
    assert not pg.evaluate("document.documentElement.scrollWidth > innerWidth + 1")


def test_embed_has_no_canvas_button(page, inst):
    """Aside drawer: no side pane, so the replies carry no Canvas button at all."""
    aid = inst["agents"]["running"]
    page.goto(f"/a/{aid}?embed=1")
    page.locator("#ci .ci-text").first.wait_for(state="attached", timeout=20000)
    assert page.locator("#ci .cv-open").count() == 0
