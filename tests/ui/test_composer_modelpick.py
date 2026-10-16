"""Composer model/effort pill + popover (modelpick.js) in a real browser against the dev instance.

Never types into the composer textarea; only the pill menu is used. The Effort POST goes to the dev instance's fake tmux."""
from __future__ import annotations

import json
from urllib.parse import parse_qs

import pytest

from .conftest import show_composer

pytestmark = pytest.mark.ui


def _open(page, inst, **kw):
    page.goto("/a/" + inst["agents"]["running"])
    page.wait_for_selector("[data-composer=live] .mp-pill", state="attached")
    show_composer(page)
    pill = page.locator("[data-composer=live] .mp-pill")
    pill.wait_for(state="visible")
    return pill, page.locator("[data-composer=live] .mp-menu")


def _effort_rows(page):
    return page.locator('[data-composer=live] [data-mp-panel="effort"] button[type=submit][name=effort]')


def test_pill_opens_menu_and_toggles(page, inst):
    pill, menu = _open(page, inst)
    assert menu.is_hidden() and pill.get_attribute("aria-expanded") == "false"
    pill.click()
    assert menu.is_visible() and pill.get_attribute("aria-expanded") == "true"
    assert page.locator('[data-composer=live] [data-mp-panel="main"]').is_visible()
    assert page.locator('[data-composer=live] [data-mp-panel="effort"]').is_hidden()
    pill.click()  # second click closes
    assert menu.is_hidden() and pill.get_attribute("aria-expanded") == "false"


def test_first_open_sits_right_above_the_pill(page, inst):
    pill, menu = _open(page, inst)
    for n in (1, 2):  # the first open used to leave a gap (the menu was in the row's flow while the pill was measured)
        pill.click()
        page.wait_for_timeout(100)
        gap = pill.bounding_box()["y"] - (menu.bounding_box()["y"] + menu.bounding_box()["height"])
        assert 0 <= gap <= 12, (n, gap)
        pill.click()


def test_effort_panel_pick_posts_effort_and_updates_pill(page, inst):
    pill, menu = _open(page, inst)
    pill.click()
    menu.get_by_role("menuitem", name="Effort").click()
    panel = page.locator('[data-composer=live] [data-mp-panel="effort"]')
    panel.wait_for(state="visible")
    assert page.locator('[data-composer=live] [data-mp-panel="main"]').is_hidden()
    rows = _effort_rows(page)
    levels = rows.evaluate_all("els => els.map(e => e.value)")
    assert len(levels) >= 2, levels
    current = page.locator("[data-composer=live] [data-mp-pill-e]").inner_text().strip()
    target = next(v for v in levels if v != current)
    with page.expect_request(lambda r: r.method == "POST" and r.url.endswith(f"/a/{inst['agents']['running']}/effort")) as ri:
        rows.filter(has=page.locator(f"xpath=self::*[@value='{target}']")).click()
    form = parse_qs(ri.value.post_data or "")
    assert form.get("effort") == [target], form
    assert form.get("csrf"), "csrf field must be posted"
    resp = ri.value.response()
    assert resp is not None and resp.ok, resp and resp.status
    # menu closed, pill and the Effort row show the new level, the item is ticked
    menu.wait_for(state="hidden")
    page.wait_for_function("t => document.querySelector('[data-composer=live] [data-mp-pill-e]').textContent.trim() === t", arg=target)
    assert page.locator("[data-composer=live] [data-mp-eff-v]").inner_text().strip() == target
    assert page.locator(f'[data-composer=live] [data-mp-panel="effort"] button[value="{target}"]').get_attribute("aria-checked") == "true"
    assert page.locator('[data-composer=live] [data-mp-panel="effort"] [aria-checked="true"]').count() == 1
    assert page.evaluate("() => document.activeElement.matches('.mp-pill')"), "focus returns to the pill after a pick"


def test_escape_closes_menu_and_refocuses_pill(page, inst):
    pill, menu = _open(page, inst)
    pill.click()
    assert menu.is_visible()
    page.keyboard.press("Escape")
    menu.wait_for(state="hidden")
    assert pill.get_attribute("aria-expanded") == "false"
    assert page.evaluate("() => document.activeElement.matches('.mp-pill')")


def test_escape_in_subpanel_goes_back_then_closes(page, inst):
    pill, menu = _open(page, inst)
    pill.click()
    menu.get_by_role("menuitem", name="Effort").click()
    page.locator('[data-composer=live] [data-mp-panel="effort"]').wait_for(state="visible")
    page.keyboard.press("Escape")
    page.locator('[data-composer=live] [data-mp-panel="main"]').wait_for(state="visible")
    assert menu.is_visible()
    page.keyboard.press("Escape")
    menu.wait_for(state="hidden")
    assert page.evaluate("() => document.activeElement.matches('.mp-pill')")


def test_outside_click_closes_menu(page, inst):
    pill, menu = _open(page, inst)
    pill.click()
    assert menu.is_visible()
    page.mouse.click(5, 300)
    menu.wait_for(state="hidden")


def test_no_native_select_for_model_or_effort(page, inst):
    _open(page, inst)
    assert page.locator("[data-composer=live] select").count() == 0


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_menu_stays_inside_phone_viewport(make_page, inst, theme):
    pg = make_page(width=390, height=844, theme=theme)
    pill, menu = _open(pg, inst)
    vp = pg.viewport_size
    for step in ("main", "effort", "models"):
        if step == "main":
            pill.click()
        else:
            if step == "models":
                pg.keyboard.press("Escape")  # back to main
            menu.get_by_role("menuitem", name="Effort" if step == "effort" else "More models").click()
            pg.locator(f'[data-composer=live] [data-mp-panel="{step}"]').wait_for(state="visible")
        box = menu.bounding_box()
        assert box, step
        assert box["x"] >= 0 and box["y"] >= 0, (step, box)
        assert box["x"] + box["width"] <= vp["width"] + 0.5, (step, box)
        assert box["y"] + box["height"] <= vp["height"] + 0.5, (step, box)
        assert pg.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth"), "page scrolls sideways"
    pg.screenshot(path=f"/tmp/pm-dev/modelpick-{theme}-390.png")


def test_models_panel_pick_posts_model(page, inst):
    """Same contract as Effort: the picked submit button's name/value must be in the POST body."""
    pill, menu = _open(page, inst)
    pill.click()
    menu.get_by_role("menuitem", name="More models").click()
    page.locator('[data-composer=live] [data-mp-panel="models"]').wait_for(state="visible")
    rows = page.locator('[data-composer=live] [data-mp-panel="models"] button[type=submit][name=model]')
    assert rows.count() >= 2
    unticked = rows.locator("xpath=self::*[@aria-checked='false']").first
    want = unticked.get_attribute("value")
    with page.expect_request(lambda r: r.method == "POST" and r.url.endswith(f"/a/{inst['agents']['running']}/model")) as ri:
        unticked.click()
    form = parse_qs(ri.value.post_data or "")
    assert form.get("model") == [want], form
    # #155: the heading pill follows the switch (HX-Trigger pm:composer {pill}, chat.js)
    resp = ri.value.response()
    assert resp is not None and resp.ok, resp and resp.status
    pill_txt = json.loads(resp.headers["hx-trigger"])["pm:composer"]["pill"]
    assert pill_txt, resp.headers["hx-trigger"]
    page.wait_for_function("t => document.getElementById('agent-pill').textContent === t", arg=pill_txt)
    assert page.locator("#agent-pill").get_attribute("aria-label").startswith(pill_txt + ", ")
    assert page.locator("h1.agent-title").get_attribute("data-model") == pill_txt


def test_snippets_menu_is_a_popover_above_the_caret(page, inst):
    """The Send caret's snippets menu is a pm.popover (popup.js): right above the caret from the first open, arrow keys
    move through it, Esc closes it and gives focus back, an item inserts its text (never sends)."""
    _open(page, inst)
    caret = page.locator("[data-composer=live] [data-snip]")
    menu = page.locator("[data-composer=live] .snipmenu")
    caret.click()
    menu.wait_for(state="visible")
    c, m = caret.bounding_box(), menu.bounding_box()
    gap = c["y"] - (m["y"] + m["height"])
    assert 0 <= gap <= 12 and abs((m["x"] + m["width"]) - (c["x"] + c["width"])) <= 2, (c, m)  # top-end
    assert caret.get_attribute("aria-expanded") == "true"
    assert page.evaluate("() => document.activeElement.matches('.snipmenu [role=menuitem]')")
    page.keyboard.press("ArrowDown")
    assert page.evaluate("() => document.activeElement.textContent") == "Check TODO"
    page.keyboard.press("Escape")
    assert menu.is_hidden() and page.evaluate("() => document.activeElement.matches('[data-snip]')")
    seen = []
    page.on("request", lambda r: seen.append(r.url) if r.method == "POST" else None)
    caret.click()
    menu.get_by_role("menuitem", name="Go ahead").click()
    assert menu.is_hidden() and page.locator("[data-composer=live] textarea[data-cmp-input]").input_value() == "Go ahead."
    assert not [u for u in seen if u.endswith("/message")]
    page.locator("[data-composer=live] textarea[data-cmp-input]").fill("")
