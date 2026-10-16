"""Converted confirms show the in-page modal (dialog.js); a native browser popup fails the test (conftest guard)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from .conftest import chat_frame, modal_ok, show_composer

pytestmark = pytest.mark.ui


def _audit(inst, action):
    f = Path(inst["state_dir"]) / "audit.jsonl"
    rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []
    return [r for r in rows if r.get("action") == action]


@pytest.mark.parametrize("theme,width", [("light", 1300), ("dark", 390)])
def test_git_push_confirm_is_a_modal(make_page, inst, theme, width):
    page = make_page(width=width, theme=theme)
    page.goto(f"/p/{inst['project']}")
    page.locator("a.pj-tab[data-pj=git]").click()  # git buttons live in the Git tab (loaded on select)
    push = page.locator("form[action$='/git/push'] button")
    push.wait_for(state="visible")
    push.click()
    dlg = page.locator("dialog[data-pm-confirm][open]")
    dlg.wait_for()
    assert "Push to origin?" in dlg.inner_text()  # the dev instance keeps the default push_remote
    box = dlg.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1
    page.screenshot(path=f"/tmp/pm-dev/ui-out/modal-push-{theme}-{width}.png") if Path("/tmp/pm-dev/ui-out").exists() else None
    page.keyboard.press("Escape")  # Cancel path: nothing sent
    assert dlg.count() == 0
    page.wait_for_timeout(500)
    assert not _audit(inst, "git_push")


def _running(scope, aid):
    """Tell the page this agent runs (the live event the composer listens to): its empty Send turns into Stop."""
    scope.evaluate("id => document.dispatchEvent(new CustomEvent('pm:agent_state', {detail: {id: id, state: 'running'}}))", aid)


def test_interrupt_confirm_ok_sends(make_page, inst):
    page = make_page()
    aid = inst["agents"]["idle"]
    page.goto(f"/a/{aid}")
    page.wait_for_load_state("networkidle")
    _running(page, aid)
    stop = page.locator("[data-composer=live] [data-cmp-send].is-stop")
    assert stop.get_attribute("title") == "Interrupt agent (sends Ctrl-C, then Esc)" and stop.is_enabled()
    stop.click()
    assert "Interrupt" in modal_ok(page)
    page.locator("[data-composer=live] [data-cmp-flash] .flash.ok").first.wait_for()
    assert any(r["result"] == "ok" for r in _audit(inst, "agent_interrupt"))
    page.locator("[data-composer=live] textarea[name=text]").fill("x")  # typing turns it back into Send
    assert page.locator("[data-composer=live] [data-cmp-send].is-stop").count() == 0


def test_interrupt_confirm_inside_drawer(make_page, inst):
    page = make_page()
    page.goto(f"/#chat=/a/{inst['agents']['waiting']}")
    page.wait_for_selector("body.chat-open")
    fr = page.frame_locator("#vc-frame")
    show_composer(chat_frame(page))  # the narrow aside starts with the composer hidden
    fr.locator("#chat-stats > summary").wait_for()  # htmx is up (the actions now sit with the token bar)
    _running(chat_frame(page), inst["agents"]["waiting"])
    fr.locator("[data-composer=live] [data-cmp-send].is-stop").click()
    assert "Interrupt" in modal_ok(fr)


def test_launch_template_replace_is_a_modal(make_page, inst):
    page = make_page()
    page.goto(f"/p/{inst['project']}/launch")
    sel = page.locator("[data-template]")
    if sel.locator("option").count() < 2:
        pytest.skip("no prompt templates in this fixture")
    ta = page.locator("[data-composer=launch] [data-cmp-input]")
    ta.fill("my own text")
    sel.select_option(index=1)
    dlg = page.locator("dialog[data-pm-confirm][open]")
    dlg.wait_for()
    assert "Replace the prompt text" in dlg.inner_text()
    dlg.locator("[data-dlg-close]").click()
    assert ta.input_value() == "my own text" and sel.input_value() == ""
    sel.select_option(index=1)
    modal_ok(page)
    assert ta.input_value() != "my own text"


def test_slash_danger_confirm_modal(make_page, inst):
    page = make_page()
    page.goto(f"/a/{inst['agents']['highctx']}")
    ta = page.locator("[data-composer=live] textarea[name=text]")
    ta.fill("/clear ")
    ta.fill("/clear")
    page.locator("[data-composer=live] button.csend").click()
    dlg = page.locator("dialog[data-pm-confirm][open]")
    dlg.wait_for()
    assert "/clear" in dlg.inner_text()
    dlg.locator("[data-dlg-close]").click()
    page.wait_for_timeout(400)
    assert not [r for r in _audit(inst, "agent_message") if r["result"] == "ok"]
    page.locator("[data-composer=live] button.csend").click()
    modal_ok(page)
    page.wait_for_function("document.querySelector('[data-composer=live] [data-cmp-flash] .flash.ok') || true")
    for _ in range(40):
        if [r for r in _audit(inst, "agent_message") if r["result"] == "ok"]:
            break
        page.wait_for_timeout(250)
    assert [r for r in _audit(inst, "agent_message") if r["result"] == "ok"]
