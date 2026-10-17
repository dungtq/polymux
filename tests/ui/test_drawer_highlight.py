"""The row of the chat open in the drawer keeps its highlight across list refreshes and chat switches."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui

ACTIVE = "document.querySelectorAll('main .v5-active').length"


def _hrefs(page):
    return page.evaluate("[...new Set([...document.querySelectorAll('main a[href^=\"/a/\"]')]"
                         ".map(a => a.getAttribute('href')).filter(h => !h.includes('?')))]")


def test_highlight_survives_refresh_and_switch(page, inst):
    page.goto(f"/p/{inst['project']}")
    page.wait_for_selector("main a[href^='/a/']")
    hrefs = _hrefs(page)
    assert len(hrefs) >= 2, hrefs
    for href in hrefs:
        link = page.locator(f"main a[href='{href}']").first
        if not link.is_visible():
            toggle = page.locator("main .ended-toggle")  # ended rows past the first 8 show on Expand
            if toggle.count() and toggle.first.is_visible():
                toggle.first.click()
        link.click()
        page.wait_for_function(f"{ACTIVE} === 1")
        for _ in range(4):  # force the in-place list swap a few times
            page.evaluate("document.body.dispatchEvent(new CustomEvent('pm:refresh', {bubbles: true}))")
            page.wait_for_timeout(1200)
            n = page.evaluate(ACTIVE)
            assert n == 1, f"{href}: {n} highlighted rows after refresh"
        cls = page.evaluate("document.querySelector('main .v5-active').closest('a,[href]')?.getAttribute('href') "
                            "|| document.querySelector('main .v5-active a[href]')?.getAttribute('href')")
        assert cls == href, (cls, href)


def test_stale_frame_message_does_not_move_highlight(page, inst):
    page.goto(f"/p/{inst['project']}")
    page.wait_for_selector("main a[href^='/a/']")
    a, b = _hrefs(page)[:2]
    page.locator(f"main a[href='{a}']").first.click()
    page.wait_for_selector("body.chat-open")
    page.locator(f"main a[href='{b}']").first.click()
    page.wait_for_function(f"location.hash === '#chat={b}'")
    fr = page.locator("#vc-frame").element_handle().content_frame()
    for _ in range(150):  # the frame has left A's page (A's head would satisfy the wait below on its own)
        if fr.url.split("?")[0].endswith(b):
            break
        page.wait_for_timeout(100)
    page.frame_locator("#vc-frame").locator(".agent-head").wait_for(state="attached")  # the embedded head is collapsed (hidden)
    # the page of A answers late, from the shared frame window
    fr.evaluate("id => window.parent.postMessage({pm: 'drawer', act: 'at', id, path: '/a/' + id}, location.origin)",
                             a.split("/")[-1])
    page.wait_for_timeout(300)
    assert page.evaluate("location.hash") == f"#chat={b}"
    assert page.evaluate("document.querySelector('main .v5-active a[href], main a.v5-active')?.getAttribute('href') "
                         "|| document.querySelector('main .v5-active a[href]')?.getAttribute('href')") == b


def test_highlight_follows_the_pane_when_the_agent_id_rotates(page, inst):
    """/clear or New Chat gives the tmux pane a new agent id: the drawer still holds the old one, the row has the new."""
    page.goto(f"/p/{inst['project']}")
    page.wait_for_selector("main a.arow[data-pane]")
    href = page.locator("main a.arow[data-pane]").first.get_attribute("href")
    page.locator(f"main a.arow[href='{href}']").first.click()
    page.wait_for_selector("body.chat-open")
    page.frame_locator("#vc-frame").locator(".agent-head").wait_for(state="attached")  # the embedded head is collapsed (hidden)
    page.wait_for_function("document.querySelectorAll('main .v5-active').length === 1")
    page.evaluate("""h => { const a = document.querySelector('main a.arow[href="' + h + '"]');
        a.setAttribute('href', '/a/claude-ffffff'); a.setAttribute('data-key', 'claude-ffffff'); a.className = a.className.replace('v5-active', ''); }""", href)
    page.wait_for_timeout(500)
    assert page.evaluate("document.querySelectorAll('main .v5-active').length") == 1


@pytest.mark.parametrize("path", ["/", "/p/{project}"])
@pytest.mark.parametrize("key", ["running", "ended_sid", "ended_manual"])
def test_highlight_after_reload_with_hash(page, inst, path, key):
    """Reopening from the URL hash (reload, shared link) highlights the row once the list is there: agents and ended."""
    ag = inst["agents"][key]
    page.goto(path.format(project=inst["project"]) + f"#chat=/a/{ag}")
    page.wait_for_selector("body.chat-open")
    page.wait_for_function(f"{ACTIVE} === 1")
    assert page.evaluate("document.querySelector('main .v5-active').matches('a[href=\"/a/" + ag + "\"], :has(> a[href=\"/a/" + ag + "\"]), :has(a[href=\"/a/" + ag + "\"])')")
    assert page.evaluate("getComputedStyle(document.querySelector('main .v5-active')).backgroundColor") != "rgba(0, 0, 0, 0)"


def test_highlight_prefers_the_visible_link(page, inst):
    """The same chat linked twice (the first inside a hidden block): the visible row is the selected one."""
    ag = inst["agents"]["running"]
    page.goto("/")
    page.wait_for_selector(f"main a[href='/a/{ag}']")
    page.evaluate("""ag => { const a = document.querySelector('main a[href="/a/' + ag + '"]'), w = document.createElement('div');
        w.hidden = true; w.id = 'dup'; const c = a.cloneNode(true); w.appendChild(c); document.querySelector('main').prepend(w); }""", ag)
    page.locator(f"main a[href='/a/{ag}']:visible").first.click()
    page.wait_for_selector("body.chat-open")
    page.wait_for_function(f"{ACTIVE} === 1")
    assert page.evaluate("!document.querySelector('#dup .v5-active') && !document.querySelector('#dup').closest('.v5-active')")
    assert page.evaluate("document.querySelector('main .v5-active').getClientRects().length") > 0
