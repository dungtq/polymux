"""Settings > Composer "Send with": Ctrl/Cmd+Enter sends and Enter is a newline ("mod-enter"), or Enter sends and
Shift+Enter is a newline ("enter"); titles follow the setting. Dev instance only (its own throwaway agents)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui


def _set(page, key):
    ok = page.evaluate("""k => { const b = new FormData(); b.set('paste_lines', '10'); b.set('exts', 'png,jpg,md,txt');
        b.set('send_key', k); return pm.fetch('/settings/composer', {method: 'POST', body: b, csrf: 'both'}).then(r => r.ok); }""", key)
    assert ok


def _open(page, inst):
    page.goto("/a/" + inst["agents"]["idle"])
    page.wait_for_load_state("networkidle")
    return page.locator("[data-composer=live] textarea[data-cmp-input]")


def _posts(page):
    seen = []
    page.on("request", lambda r: seen.append(r.url) if r.method == "POST" and r.url.endswith("/message") else None)
    return seen


def test_mod_enter_enter_is_a_newline_ctrl_enter_sends(page, inst):
    page.goto("/settings")
    _set(page, "mod-enter")
    ta = _open(page, inst)
    root = page.locator("[data-composer=live]")
    assert root.get_attribute("data-send-key") == "mod-enter"
    assert root.locator("[data-cmp-send]").get_attribute("title") == "Send message (Ctrl+Enter)"
    seen = _posts(page)
    ta.fill("line one")
    ta.press("End")
    ta.press("Enter")
    page.wait_for_timeout(400)
    assert ta.input_value() == "line one\n" and not seen  # Enter only added a newline
    ta.type("line two")
    with page.expect_request(lambda r: r.method == "POST" and r.url.endswith("/message")):
        ta.press("Control+Enter")
    page.wait_for_function("() => document.querySelector('[data-composer=live] textarea[data-cmp-input]').value === ''")


def test_enter_sends_shift_enter_is_a_newline(page, inst):
    page.goto("/settings")
    _set(page, "enter")
    ta = _open(page, inst)
    assert page.locator("[data-composer=live]").get_attribute("data-send-key") == "enter"
    assert page.locator("[data-composer=live] [data-cmp-send]").get_attribute("title") == "Send message (Enter)"
    seen = _posts(page)
    ta.fill("first")
    ta.press("End")
    ta.press("Shift+Enter")
    page.wait_for_timeout(300)
    assert ta.input_value() == "first\n" and not seen
    with page.expect_request(lambda r: r.method == "POST" and r.url.endswith("/message")):
        ta.press("Enter")
