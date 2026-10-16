"""Command palette (Cmd/Ctrl+Shift+P): opens, filters, keyboard runs a command, existing search keys unchanged."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui


def _open(page):
    page.goto("/p")
    page.wait_for_load_state("networkidle")
    page.keyboard.press("Control+Shift+P")
    page.wait_for_selector("#palette[open]")


def test_open_filter_close(page):
    _open(page)
    assert page.locator("#palette .pal-in").evaluate("e => e === document.activeElement")
    assert page.locator("#palette .pal-it").count() >= 8
    page.fill("#palette .pal-in", "audit")
    assert page.locator("#palette .pal-it").count() == 1
    page.fill("#palette .pal-in", "zzzzqq")
    assert page.locator("#palette .pal-empty").is_visible()
    page.keyboard.press("Escape")
    assert page.locator("#palette[open]").count() == 0
    assert page.locator("#gsearch.open").count() == 0  # search did not react to Shift+P


def test_arrows_enter_navigates(page):
    _open(page)
    page.fill("#palette .pal-in", "go to")
    page.keyboard.press("ArrowDown")
    assert page.locator("#palette .pal-it.on").count() == 1
    page.fill("#palette .pal-in", "backlog")
    page.keyboard.press("Enter")
    page.wait_for_url("**/backlog")


def test_stop_uses_existing_dialog_and_theme_toggle(page):
    _open(page)
    page.fill("#palette .pal-in", "stop all")
    page.keyboard.press("Enter")
    page.wait_for_selector("#stop-dialog[open]")  # existing PANIC confirm, nothing posted
    page.keyboard.press("Escape")
    _open(page)
    page.fill("#palette .pal-in", "dark")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('#palette[open]') === null")


def test_search_keys_unchanged(page):
    page.goto("/p")
    page.wait_for_load_state("networkidle")
    page.keyboard.press("Control+K")
    assert page.locator("#gsearch .gs-in").evaluate("e => e === document.activeElement")
    assert page.locator("#palette[open]").count() == 0
