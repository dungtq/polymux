"""tmux Kill: the typed-confirm dialog (dialog.js data-dlg-typed, templates/tmux.html) fills the row's real form."""
from __future__ import annotations

import pytest

from .conftest import tmux_windows

pytestmark = pytest.mark.ui


def _sessions(inst):
    return {w.split(":")[0] for w in tmux_windows(inst)}


@pytest.mark.parametrize("theme,width", [("light", 1300), ("dark", 390)])
def test_typed_confirm_cancel_keeps_session(make_page, inst, theme, width):
    page = make_page(width=width, theme=theme)
    page.goto("/tmux")
    btn = page.locator("[data-kill-open]").first
    expect = btn.get_attribute("data-expect")
    btn.evaluate("b => b.click()")  # the button sits in a folded row on phones
    dlg = page.locator("#kill-dialog")
    dlg.wait_for(state="visible")
    assert dlg.evaluate("d => d.matches('dialog.pmdlg[open]')")
    assert dlg.locator("[data-typed-expect]").inner_text() == expect
    assert dlg.locator("[data-typed-label]").inner_text() == btn.get_attribute("data-label")
    go, field = dlg.locator("[data-typed-go]"), dlg.locator("[data-typed-in]")
    assert go.is_disabled() and field.evaluate("i => i === document.activeElement")
    box = dlg.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1
    foot = dlg.locator(".pmdlg-foot button")
    assert foot.nth(0).inner_text() == "Cancel" and foot.nth(1).inner_text() == "Kill"  # [Cancel][Primary], right
    field.fill(expect + "x")
    assert go.is_disabled()
    field.fill(expect)
    assert go.is_enabled()
    dlg.locator("[data-dlg-close]").click()
    dlg.wait_for(state="hidden")
    assert expect in _sessions(inst)
    btn.evaluate("b => b.click()")
    dlg.wait_for(state="visible")
    assert field.input_value() == "" and go.is_disabled()  # reset on every open
    page.keyboard.press("Escape")
    dlg.wait_for(state="hidden")


def test_typed_confirm_kills(make_page, inst):
    page = make_page()
    page.goto("/tmux")
    btn = page.locator("[data-kill-open]").first
    expect = btn.get_attribute("data-expect")
    assert expect in _sessions(inst)
    btn.evaluate("b => b.click()")
    dlg = page.locator("#kill-dialog")
    dlg.wait_for(state="visible")
    dlg.locator("[data-typed-in]").fill(expect)
    page.keyboard.press("Enter")  # Enter in the field = Kill once the text matches
    dlg.wait_for(state="hidden")
    for _ in range(100):
        if expect not in _sessions(inst):
            break
        page.wait_for_timeout(100)
    assert expect not in _sessions(inst), page.locator("#flash").inner_text()
