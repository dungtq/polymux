"""Exit / Resume / Fork / compose on agent pages of the dev instance (a fresh instance per module)."""
from __future__ import annotations

import pytest

from .conftest import modal_ok, tmux_windows

pytestmark = pytest.mark.ui


def _go(page, inst, key):
    page.goto(f"/a/{inst['agents'][key]}")
    page.wait_for_load_state("networkidle")


def test_exit_switches_to_ended_view(page, inst):
    _go(page, inst, "idle")
    head = page.locator(".agent-head")
    assert head.get_attribute("data-mode") == "live"
    marker = "pm-ui-nonreload"
    page.evaluate(f"window.{marker.replace('-', '_')} = 1")
    page.locator(".agent-actions button[data-dlg-open=exit-dialog]").click()
    page.locator("#exit-dialog form[action$='/exit'] button").click()
    # agentmode.js polls /mode and switches by itself; no page.goto from the test
    page.wait_for_selector(".agent-head[data-mode='ended']", timeout=60000)
    assert page.locator(".kpill.at-ended").inner_text().strip() == "Ended"
    assert page.locator(".ended-meta").is_visible()
    assert page.locator("button[data-dlg-open=exit-dialog]").count() == 0


def test_resume_ended_with_session_goes_live(page, inst):
    _go(page, inst, "ended_sid")
    assert page.locator(".agent-head").get_attribute("data-mode") == "ended"
    btn = page.locator(".agent-actions form[action$='/resume'] button")
    assert btn.is_enabled()
    btn.click()
    modal_ok(page)
    page.wait_for_selector(".agent-head[data-mode='live']", timeout=60000)
    page.wait_for_selector("[data-composer=live] textarea[name=text]")
    assert page.locator("[data-composer=live] textarea[name=text]").is_visible()


def test_ended_head_has_resume_and_fork_no_bottom_row(page, inst):
    _go(page, inst, "ended_manual")  # ended_sid is resumed by the test above (same instance)
    head = page.locator(".agent-head .agent-actions")
    assert head.locator("form[action$='/resume'] button").is_enabled()
    assert head.locator("form[action$='/fork'] button").is_enabled()
    assert page.locator(".ended-foot").count() == 0
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")  # the head stays put on scroll
    assert page.evaluate("()=>document.querySelector('.agent-head').getBoundingClientRect().top < 100")


def test_resume_disabled_with_reason_without_session(page, inst):
    _go(page, inst, "ended_nosid")
    btn = page.locator(".agent-actions").get_by_role("button", name="Resume")
    assert btn.is_disabled()
    why = page.locator("#resume-why")
    assert "No session id" in why.text_content()
    assert page.locator(".agent-actions form[action$='/resume']").count() == 0


def test_fork_opens_new_tmux_window(page, inst):
    _go(page, inst, "running")
    before = tmux_windows(inst)
    page.locator(".agent-actions").get_by_role("button", name="Fork", exact=True).click()
    modal_ok(page)
    for _ in range(100):
        after = tmux_windows(inst)
        if len(after) > len(before):
            break
        page.wait_for_timeout(100)
    assert len(after) > len(before), f"no new window: {before} -> {after}"
    assert page.locator("[data-composer=live] [data-cmp-flash] .flash.err").count() == 0  # the composer's own notice line


def test_compose_and_send_shows_message_in_chat(page, inst):
    _go(page, inst, "waiting")
    text = "hello from the ui suite"
    ta = page.locator("[data-composer=live] textarea[name=text]")
    ta.fill(text)
    page.locator("[data-composer=live] button.csend").click()
    page.locator("#ci-pending .chat-bubble.role-user, #ci .chat-bubble.role-user", has_text=text).first.wait_for()  # outbox bubble, then the item
    assert ta.input_value() == ""
