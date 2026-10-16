"""Launch link opens a modal popup; Cancel closes it."""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.ui


def test_launch_popup_opens_and_cancels(page, inst):
    page.goto(f"/p/{inst['project']}")
    page.locator("a.launch-btn").first.click()
    page.wait_for_selector("dialog.launchpop[open]")
    fr = page.frame_locator("dialog.launchpop iframe")
    fr.locator("form[action$='/launch']").wait_for()
    assert page.url.split("#")[0].endswith(f"/p/{inst['project']}")
    fr.locator("[data-launch-cancel]").click()
    page.wait_for_selector("dialog.launchpop:not([open])", state="attached")


def test_launch_from_popup_opens_drawer(page, inst):
    """Launch in the popup: the iframe must never navigate (no 'refused to connect'); the drawer opens the agent."""
    page.goto(f"/p/{inst['project']}")
    page.locator("a.launch-btn").first.click()
    page.wait_for_selector("dialog.launchpop[open]")
    fr = page.frame_locator("dialog.launchpop iframe")
    fr.locator("form[action$='/launch']").wait_for()
    fr.locator("textarea[name=prompt]").fill("hello from popup test")
    fr.locator("[data-launch-open]").click()
    page.wait_for_selector("body.chat-open", timeout=20000)
    assert "#chat=/a/" in page.evaluate("location.hash")
    assert page.locator("dialog.launchpop[open]").count() == 0
    page.wait_for_timeout(1500)
    inner = page.frame_locator("#vc-frame")
    assert inner.locator("body").count() == 1
    assert "refused" not in page.locator("#vc-frame").evaluate("f => f.contentDocument ? f.contentDocument.body.innerText : ''")


def test_launch_prompt_never_launches_on_enter(page, inst):
    """The Launch prompt is the shared composer with Ctrl/Cmd+Enter as its only send key (whatever Settings > Composer
    says): Enter is a newline; Ctrl+Enter presses Launch (open=1). The POST is stubbed: no agent starts."""
    posts = []

    def launch(route):
        posts.append(route.request.post_data or "")
        route.fulfill(status=200, content_type="text/html", body='<p class="flash ok">stub</p>')

    page.route(re.compile(r".*/p/[^/]+/launch$"), lambda r: launch(r) if r.request.method == "POST" else r.continue_())
    page.goto(f"/p/{inst['project']}/launch")
    root = page.locator("[data-composer=launch]")
    assert root.get_attribute("data-send-key") == "mod-enter" and root.locator("[data-cmp-send]").count() == 0
    assert "Ctrl+Enter" in root.locator(".cmp-keyhint").inner_text()
    ta = root.locator("[data-cmp-input]")
    ta.fill("first line")
    ta.press("End")
    ta.press("Enter")
    page.wait_for_timeout(400)
    assert ta.input_value() == "first line\n" and not posts
    ta.type("second")
    ta.press("Control+Enter")
    page.wait_for_function("() => document.getElementById('flash').textContent.includes('stub')")
    assert len(posts) == 1 and "open=1" in posts[0] and "prompt=first%20line%0Asecond" in posts[0]


def test_launch_prompt_slash_follows_the_agent_kind(page, inst):
    page.goto(f"/p/{inst['project']}/launch")
    root = page.locator("[data-composer=launch]")
    kinds = page.locator("form[action$='/launch'] select[name=kind] option").evaluate_all("os => os.map(o => o.value)")
    for k in kinds:
        page.select_option("form[action$='/launch'] select[name=kind]", k)
        want = page.evaluate("k => JSON.stringify(JSON.parse(document.getElementById('launch-data').dataset.json).slash[k] || [])", k)
        assert root.get_attribute("data-slash") == want, k
    ta = root.locator("[data-cmp-input]")
    if root.get_attribute("data-slash") != "[]":
        ta.fill("/")
        page.locator("[data-composer=launch] .slash-list .slash-opt").first.wait_for()
        lb, tb = page.locator("[data-composer=launch] .slash-list").bounding_box(), root.locator(".cbox").bounding_box()
        assert lb["y"] >= tb["y"] + tb["height"] - 2  # under the box
