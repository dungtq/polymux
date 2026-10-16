"""A click on an attached image opens the in-page viewer (a <dialog>), not a new tab."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui

PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/"
       "iZk9HQAAAABJRU5ErkJggg==")


@pytest.mark.parametrize("theme,width", [("light", 1300), ("dark", 390)])
def test_image_opens_in_page(make_page, inst, theme, width):
    page = make_page(width=width, theme=theme)
    page.goto(f"/a/{inst['agents']['idle']}")
    page.evaluate("""(src) => { const a = document.createElement('a'); a.className = 'chat-img'; a.id = 't-img';
        a.href = src; a.target = '_blank'; a.innerHTML = '<img alt="shot" src="' + src + '">';
        a.style.cssText = 'position:fixed;top:60px;left:20px;z-index:100'; document.body.appendChild(a); }""", PNG)
    page.click("#t-img")
    assert len(page.context.pages) == 1
    dlg = page.locator("dialog.imgview")
    assert dlg.is_visible()
    page.keyboard.press("Escape")
    assert page.locator("dialog.imgview").count() == 0
    page.click("#t-img")
    page.click("dialog.imgview .imgview-x")
    assert page.locator("dialog.imgview").count() == 0



def test_zoom(make_page, inst):
    page = make_page(width=1300, theme="light")
    page.goto(f"/a/{inst['agents']['idle']}")
    page.evaluate("""(src) => { const a = document.createElement('a'); a.className = 'chat-img'; a.id = 't-img';
        a.href = src; a.innerHTML = '<img alt="shot" src="' + src + '">';
        a.style.cssText = 'position:fixed;top:60px;left:20px;z-index:100'; document.body.appendChild(a); }""", PNG)
    page.click("#t-img")
    img = page.locator("dialog.imgview img")
    assert "scale(1)" in img.get_attribute("style")
    page.click("button[aria-label='Zoom in']")
    assert "scale(1.5)" in img.get_attribute("style")
    page.keyboard.press("0")
    assert "scale(1)" in img.get_attribute("style")
    page.mouse.move(650, 400)
    page.mouse.wheel(0, -300)
    assert "scale(1)" not in img.get_attribute("style")
    page.click("button[aria-label='Fit']")
    assert "scale(1)" in img.get_attribute("style")
    page.mouse.click(5, 5)  # backdrop
    assert page.locator("dialog.imgview").count() == 0
