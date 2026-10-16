"""dialog.js pmDialog: a failed request inside a dialog keeps it open (its error is shown there), unique title ids,
the phone sheet layout, and pmConfirm compatibility."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui


def test_failed_request_keeps_dialog_open(page, inst):
    page.goto("/p")
    page.locator("button[data-dlg-open=newproj-dialog]").click()
    dlg = page.locator("#newproj-dialog")
    dlg.wait_for(state="visible")
    name = dlg.locator("input[name=name]")
    assert name.evaluate("i => i === document.activeElement")  # the first field has focus
    name.fill(inst["project"])  # exists already: the server refuses it
    dlg.locator("button[type=submit]").click()
    dlg.locator("#np-flash .flash").wait_for()
    assert dlg.evaluate("d => d.open")  # error visible in the dialog, not lost behind a closed one
    page.keyboard.press("Escape")
    assert not dlg.evaluate("d => d.open")


@pytest.mark.parametrize("theme,width", [("light", 1300), ("dark", 390)])
def test_confirm_layout_and_unique_ids(make_page, theme, width):
    page = make_page(width=width, height=844, theme=theme)
    page.goto("/audit")
    page.evaluate("() => { window.pmConfirm({title: 'First?', body: 'Body text'}); }")
    first = page.locator("dialog[data-pm-confirm][open]")
    first.wait_for()
    t1 = first.get_attribute("aria-labelledby")
    assert t1 and t1 != "pmdlg-t" and page.locator(f"#{t1}").inner_text() == "First?"
    assert page.evaluate("() => document.activeElement.matches('[data-pm-confirm] [data-dlg-close]')")  # the safe button
    box = first.bounding_box()
    if width < 700:  # phone: bottom-aligned sheet with 12px margins
        vw = page.evaluate("() => innerWidth")
        assert abs(box["x"] - 12) <= 1 and abs(box["width"] - (vw - 24)) <= 1 and box["y"] + box["height"] >= 844 - 14
    else:
        assert box["y"] + box["height"] < 844 - 100
    page.keyboard.press("Escape")
    page.evaluate("() => { window.pmConfirm({title: 'Second?'}); }")
    t2 = page.locator("dialog[data-pm-confirm][open]").get_attribute("aria-labelledby")
    assert t2 and t2 != t1
    page.mouse.click(5, 5)  # backdrop
    page.locator("dialog[data-pm-confirm]").wait_for(state="detached")


def test_pmdialog_builds_and_removes(page):
    page.goto("/audit")
    page.evaluate("""() => { window.__c = 'open'; const b = document.createElement('p'); b.id = 'pd-body'; b.textContent = 'Hi';
        window.pmDialog({title: 'Built', content: b, size: 'wide', closeX: true, onClose: () => { window.__c = 'closed'; }}); }""")
    dlg = page.locator("dialog.pmdlg.pmdlg-wide[open]")
    dlg.wait_for()
    assert dlg.locator("#pd-body").inner_text() == "Hi"
    dlg.locator(".pmdlg-x").click()
    page.wait_for_function("() => window.__c === 'closed'")
    assert page.locator("#pd-body").count() == 0
