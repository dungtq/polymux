"""Global keyboard shortcuts with real key events: / , Ctrl+K, Ctrl+P (search) and Ctrl+Shift+P (palette).

Also guards that no page throws or logs a console error while loading (one broken script silently
kills every shortcut that comes after it)."""
from __future__ import annotations

import pytest

from .conftest import chat_frame, show_composer

pytestmark = pytest.mark.ui

SEARCH_FOCUSED = "#gsearch .gs-in"


def _watch(page):
    errs: list[str] = []
    page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    page.on("console", lambda m: errs.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    return errs


def _focused(page, sel):
    return page.locator(sel).evaluate("e => e === document.activeElement")


def _blur(page):
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.locator("body").evaluate("() => { const o=document.getElementById('gsearch'); o && o.classList.remove('open'); }")


@pytest.mark.parametrize("path", ["/", "/p", "/p/fixproj", "/tmux", "AGENT", "BUILDER", "/backlog", "/settings"])
def test_no_load_errors_and_shortcuts(page, inst, path):
    if path == "AGENT":
        path = f"/a/{inst['agents']['running']}"
    if path == "BUILDER":
        path = f"/a/{inst['agents']['running']}/builder"
    errs = _watch(page)
    page.goto(path)
    page.wait_for_load_state("networkidle")
    assert not errs, f"{path}: {errs}"
    _blur(page)
    for combo in ("/", "Control+k", "Control+p", "Meta+k"):
        _blur(page)
        page.keyboard.press(combo)
        assert _focused(page, SEARCH_FOCUSED), f"{path}: {combo} did not focus search"
        page.keyboard.press("Escape")
        page.keyboard.press("Escape")
    _blur(page)
    _blur(page)
    for combo in ("Control+Shift+P", "Meta+Shift+P"):
        page.keyboard.press(combo)
        page.wait_for_selector("#palette[open]")
        assert _focused(page, "#palette .pal-in"), f"{path}: {combo} palette input not focused"
        page.keyboard.press("Escape")
        page.wait_for_function("document.querySelector('#palette[open]') === null")
    assert not errs, f"{path}: {errs}"


def test_shortcuts_from_composer(page, inst):
    """Ctrl+K / Ctrl+Shift+P also work while the agent composer has focus; "/" types a slash there."""
    errs = _watch(page)
    page.goto(f"/a/{inst['agents']['running']}")
    page.wait_for_load_state("networkidle")
    ta = page.locator("[data-composer=live] textarea[name=text]")
    ta.click()
    page.keyboard.press("Control+k")
    assert _focused(page, SEARCH_FOCUSED)
    page.keyboard.press("Escape")
    ta.click()
    page.keyboard.press("Control+Shift+P")
    page.wait_for_selector("#palette[open]")
    assert _focused(page, "#palette .pal-in")
    page.keyboard.press("Escape")
    page.wait_for_function("document.querySelector('#palette[open]') === null")
    ta.click()
    page.keyboard.press("/")
    assert ta.input_value().endswith("/")
    assert not errs, errs


@pytest.mark.parametrize("base", ["/", "/p/fixproj", "/backlog"])
def test_shortcuts_with_chat_aside_focus_in_iframe(make_page, inst, base):
    """Chat aside open (#vc-frame, embed mode): focus inside the iframe must still reach search / palette."""
    page = make_page(width=1400)
    errs = _watch(page)
    page.goto(f"{base}#chat=/a/{inst['agents']['running']}")
    page.wait_for_selector("body.chat-open")
    fr = page.frame_locator("#vc-frame")
    fr.locator(".agent-head").wait_for(state="attached")
    ta = fr.locator("[data-composer=live] textarea[name=text]")
    show_composer(chat_frame(page))  # the narrow aside starts with the composer hidden
    ta.wait_for()
    page.wait_for_load_state("networkidle")
    for target in ("composer", "body"):
        for combo, kind in (("Control+k", "s"), ("Control+p", "s"), ("Meta+k", "s"), ("/", "s"),
                            ("Control+Shift+P", "p"), ("Meta+Shift+P", "p")):
            if target == "composer":
                ta.focus()
                if combo == "/":
                    continue  # a slash typed in the composer is text, not a shortcut
            else:
                fr.locator("body").click(position={"x": 2, "y": 2}, force=True)
                page.evaluate("document.querySelector('#vc-frame').focus()")
            page.keyboard.press(combo)
            if kind == "s":
                for _ in range(50):
                    if page.evaluate("document.activeElement.classList.contains('gs-in')"):
                        break
                    page.wait_for_timeout(100)
                assert _focused(page, SEARCH_FOCUSED), f"{base} {target} {combo}"
            else:
                page.wait_for_selector("#palette[open]")
                assert _focused(page, "#palette .pal-in"), f"{base} {target} {combo}"
                page.keyboard.press("Control+Shift+P")  # toggles closed (Escape would also close the aside, drawer.js)
                page.wait_for_function("document.querySelector('#palette[open]') === null")
            _blur(page)
    assert not errs, errs
