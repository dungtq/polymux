"""The ended session composer on the dev instance: a real composer (no ghost). Empty box: plain Resume / Fork; with
text: "Resume and send" (POST /a/<id>/resume with text, the page switches to live, same id) and "Fork and send"
(POST /a/<id>/fork with text, the answer in the composer's notice line). The draft survives a reload."""
from __future__ import annotations

from urllib.parse import parse_qs

import pytest

from .conftest import modal_ok

pytestmark = pytest.mark.ui


def _go(page, inst, key):
    aid = inst["agents"][key]
    page.goto(f"/a/{aid}")
    page.wait_for_load_state("networkidle")
    return aid


def _comp(page):
    return page.locator("[data-composer=ended]")


def test_ended_composer_is_real_and_follows_the_box(page, inst):
    aid = _go(page, inst, "ended_manual")
    comp = _comp(page)
    assert comp.is_visible() and page.locator("[data-composer=live]").count() == 0
    assert "msgbar" not in page.request.get(f"/a/{aid}").text()  # the served page (the body's --msgbar-h is set by JS)
    send, alt = comp.locator("[data-cmp-send]"), comp.locator("[data-cmp-alt]")
    assert send.inner_text() == "Resume" and alt.inner_text() == "Fork" and send.is_enabled()
    ta = comp.locator("textarea[data-cmp-input]")
    ta.fill("carry on please")
    assert send.inner_text() == "Resume and send" and alt.inner_text() == "Fork and send"
    assert page.evaluate("k => localStorage.getItem(k)", f"pm.draft:/a/{aid}") == "carry on please"
    page.reload()
    page.wait_for_load_state("networkidle")
    assert _comp(page).locator("textarea[data-cmp-input]").input_value() == "carry on please"  # the draft survives
    _comp(page).locator("textarea[data-cmp-input]").fill("")
    assert _comp(page).locator("[data-cmp-send]").inner_text() == "Resume"


def test_ended_without_session_id_forks(page, inst):
    _go(page, inst, "ended_nosid")
    comp = _comp(page)
    assert comp.get_by_role("button", name="Resume").is_disabled()
    send = comp.locator("[data-cmp-send]")
    assert send.inner_text() == "Fork" and comp.locator("form[data-cmp-form]").get_attribute("action").endswith("/fork")


def test_fork_and_send_posts_the_text(page, inst):
    aid = _go(page, inst, "ended_manual")
    comp = _comp(page)
    comp.locator("textarea[data-cmp-input]").fill("fork with this")
    with page.expect_request(lambda r: r.url.endswith(f"/a/{aid}/fork") and r.method == "POST") as req:
        comp.locator("[data-cmp-alt]").click()
        assert "Fork" in modal_ok(page)
    body = parse_qs(req.value.post_data or "")
    assert body.get("text") == ["fork with this"] and body.get("csrf")
    note = comp.locator("[data-cmp-flash] .flash")
    note.first.wait_for()
    if "ok" in (note.first.get_attribute("class") or ""):
        assert "Forked" in note.first.inner_text()
        assert comp.locator("textarea[data-cmp-input]").input_value() == ""  # sent: the box and the draft clear
        assert page.evaluate("k => localStorage.getItem(k)", f"pm.draft:/a/{aid}") is None
    else:  # the dev instance could not start the fork: the refusal is a readable notice, the text stays
        assert "refused" in note.first.inner_text().lower() or "cannot" in note.first.inner_text().lower()
        assert comp.locator("textarea[data-cmp-input]").input_value() == "fork with this"
    assert page.locator(".agent-head").get_attribute("data-mode") == "ended"  # the fork runs in its own window


def test_resume_and_send_goes_live_with_the_same_id(page, inst):
    aid = _go(page, inst, "ended_sid")
    comp = _comp(page)
    ta = comp.locator("textarea[data-cmp-input]")
    ta.fill("continue with the tests")
    with page.expect_request(lambda r: r.url.endswith(f"/a/{aid}/resume") and r.method == "POST") as req:
        ta.press("Enter")  # the send key (Enter on a desktop pointer) = Resume and send
        assert "Resume" in modal_ok(page)
    body = parse_qs(req.value.post_data or "")
    assert body.get("text") == ["continue with the tests"] and body.get("csrf")
    page.wait_for_selector(".agent-head[data-mode='live']", timeout=60000)  # agentmode.js reloads into the live view
    assert page.url.endswith(f"/a/{aid}")
    live = page.locator("[data-composer=live] textarea[data-cmp-input]")
    live.wait_for()
    assert live.input_value() == ""  # sent with the resume: no stale draft in the live composer
