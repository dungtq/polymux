"""Sidebar toggle, chat drawer open/resize, theme toggle."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui


def test_sidebar_toggle(page, inst):
    page.goto(f"/p/{inst['project']}")
    page.wait_for_selector("#v5side .vs-toggle")
    html = page.locator("html")
    page.evaluate("sessionStorage.removeItem('pm.v5.side')")
    page.reload()
    assert "side-collapsed" not in (html.get_attribute("class") or "")
    page.locator("#v5side .vs-toggle").click()
    assert "side-collapsed" in html.get_attribute("class")
    assert page.locator("#v5side .vs-toggle").get_attribute("aria-expanded") == "false"
    assert page.evaluate("sessionStorage.getItem('pm.v5.side')") == "1"
    page.reload()
    assert "side-collapsed" in html.get_attribute("class")  # persisted
    page.locator("#v5side .vs-toggle").click()
    assert "side-collapsed" not in html.get_attribute("class")
    assert page.locator("#v5side .vs-toggle").get_attribute("aria-expanded") == "true"


def _open_drawer(page, inst):
    page.goto("/")
    aid = inst["agents"]["running"]
    link = page.locator(f"main a[href='/a/{aid}']").first
    link.wait_for()
    link.click()
    page.wait_for_selector("body.chat-open")
    return aid


def test_drawer_open_and_close(page, inst):
    aid = _open_drawer(page, inst)
    assert page.evaluate("location.hash") == f"#chat=/a/{aid}"
    assert page.locator("#vc-full").get_attribute("href") == f"/a/{aid}"
    frame = page.frame_locator("#vc-frame")
    frame.locator(".agent-head").wait_for(state="attached")  # the embedded head is collapsed (the drawer header holds its controls)
    page.locator("#vc-close").click()
    page.wait_for_function("!document.body.classList.contains('chat-open')")
    assert page.evaluate("location.hash") == ""


def test_drawer_resize(page, inst):
    _open_drawer(page, inst)
    page.locator("#vc-frame").wait_for()
    w0 = page.evaluate("document.getElementById('v5chat').getBoundingClientRect().width")
    page.locator("#vc-resize").focus()
    page.keyboard.press("ArrowLeft")  # +40 px
    page.wait_for_function(f"document.getElementById('v5chat').getBoundingClientRect().width > {w0 + 20}")
    w1 = page.evaluate("document.getElementById('v5chat').getBoundingClientRect().width")
    f = float(page.evaluate("localStorage.getItem('pm.v5.chatf')"))  # share of main + drawer, not px
    assert 0.5 < f <= 0.8
    # drag with the mouse
    box = page.locator("#vc-resize").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + 200
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 80, y, steps=5)
    page.mouse.up()
    page.wait_for_function(f"document.getElementById('v5chat').getBoundingClientRect().width < {w1 - 40}")
    page.locator("#vc-resize").dblclick()
    page.wait_for_function("localStorage.getItem('pm.v5.chatf') === null")


_SPLIT = "(() => { const m = document.querySelector('.v5-shell > main').getBoundingClientRect().width,"\
    " c = document.getElementById('v5chat').getBoundingClientRect().width; return c / (m + c); })()"


def test_drawer_split_same_on_every_page(page, inst):
    """50/50 on the Inbox (no sidebar) and on a project page (sidebar); a split dragged on one page is the
    same share on the other (a saved px width squeezed the Inbox drawer)."""
    aid = inst["agents"]["running"]
    page.evaluate("localStorage.removeItem('pm.v5.chatf'); localStorage.setItem('pm.v5.chatw', '280')")
    for path in ("/", f"/p/{inst['project']}"):
        page.goto(f"{path}#chat=/a/{aid}")
        page.wait_for_selector("body.chat-open #v5chat")
        assert abs(page.evaluate(_SPLIT) - 0.5) < 0.01, path
    assert page.evaluate("localStorage.getItem('pm.v5.chatw')") is None  # old px key dropped
    page.locator("#vc-resize").focus()
    for _ in range(4):
        page.keyboard.press("ArrowLeft")
    dragged = page.evaluate(_SPLIT)
    assert dragged > 0.55
    page.goto(f"/#chat=/a/{aid}")
    page.wait_for_selector("body.chat-open #v5chat")
    assert abs(page.evaluate(_SPLIT) - dragged) < 0.01


@pytest.mark.parametrize("start", ["light", "dark"])
def test_theme_toggle(make_page, start):
    page = make_page(theme=start)
    page.goto("/")
    html = page.locator("html")
    assert html.get_attribute("data-theme") == start
    page.locator("#acct-menu > summary").click()
    page.locator("#acct-menu [data-theme-toggle]").click()
    other = "dark" if start == "light" else "light"
    assert html.get_attribute("data-theme") == other
    assert page.evaluate("localStorage.getItem('pm-theme')") == other
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    page.reload()
    assert html.get_attribute("data-theme") == other
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == bg


def test_sidebar_collapse_does_not_leak_to_new_tab(page, inst):
    page.goto(f"/p/{inst['project']}")
    page.wait_for_selector("#v5side .vs-toggle")
    page.locator("#v5side .vs-toggle").click()
    assert "side-collapsed" in page.locator("html").get_attribute("class")
    other = page.context.new_page()
    other.goto(page.url)
    other.wait_for_selector("#v5side .vs-toggle")
    assert "side-collapsed" not in (other.locator("html").get_attribute("class") or "")
    other.close()


def test_sidebar_reset_layout(page, inst):
    page.goto(f"/p/{inst['project']}")
    page.wait_for_selector("#v5side .vs-toggle")
    page.evaluate("localStorage.setItem('pm.v5.sidew','320')")
    page.reload()
    assert page.evaluate("getComputedStyle(document.querySelector('#v5side')).width") == "320px"
    other = page.context.new_page()  # width is a saved preference shared with new tabs
    other.goto(page.url)
    other.wait_for_selector("#v5side")
    assert other.evaluate("getComputedStyle(document.querySelector('#v5side')).width") == "320px"
    other.close()
    page.locator("#v5side .vs-toggle").click()
    page.locator("#v5side .vs-toggle").click()
    page.goto("/settings")  # Reset layout lives in Settings > Appearance, behind the in-page confirm
    page.locator("[data-layout-reset]").click()
    page.locator("dialog[data-pm-confirm] button", has_text="Reset").click()
    assert page.evaluate("localStorage.getItem('pm.v5.sidew')") is None
    page.wait_for_timeout(400)
    assert page.evaluate("Math.round(document.querySelector('#v5side').getBoundingClientRect().width)") == 248


@pytest.mark.parametrize("path,key", [("/", "inbox"), ("/p", "projects"), ("/tmux", "tmux"), ("/servers", "servers"),
                                      ("/agents", "agents"), ("/backlog", "backlog"), ("/audit", "audit")])
def test_section_sidebar_highlights_current(page, path, key):
    page.goto(path)
    page.wait_for_selector("#v5side .vs-list")
    if key in ("servers", "agents"):  # these two live in the sidebar foot group "Manage" now
        assert page.locator("#v5side .vs-list .vs-row.on").count() == 0
        assert page.locator(f"#v5side .vs-foot a[href='{path}'][aria-current='page']").count() == 1
        return
    on = page.locator("#v5side .vs-list .vs-row.on")
    assert on.count() == 1 and on.get_attribute("data-p") == key
    assert on.get_attribute("aria-current") == "page"


def test_sidebar_foot_theme_and_account(page):
    page.goto("/audit")
    page.wait_for_selector("#v5side .vs-foot")
    html = page.locator("html")
    before = html.get_attribute("data-theme")
    page.locator("#v5side .vs-foot [data-theme-toggle]").click()
    assert html.get_attribute("data-theme") != before
    assert page.locator("#v5side .vs-foot a[href='/settings']").count() == 1
    assert page.locator("#v5side .vs-foot form[action='/logout']").count() == 1


def test_topbar_launch_and_stop_reachable_anywhere(page):
    page.goto("/audit")
    page.locator(".topbar .tb-stop").click()
    assert page.locator("#stop-dialog").evaluate("d => d.open")
    page.locator("#stop-dialog [data-dlg-close]").click()
    assert not page.locator("#stop-dialog").evaluate("d => d.open")
    page.locator(".topbar .tb-launch").click()
    page.wait_for_selector("dialog.launchpop[open]")


def test_sidebar_settings_opens_popup_and_project_list_collapses(page):
    page.goto("/audit")
    page.wait_for_selector("#v5side .vs-foot")
    assert page.locator("#v5side").evaluate("a => a.classList.contains('vs-pclosed')")
    assert not page.locator("#v5side .vs-proj").is_visible()
    page.locator("#v5side .vs-pt:not(.vs-it)").click()  # manual toggle still works
    assert page.locator("#v5side .vs-proj").is_visible()
    page.locator("#vs-settings-open").click()
    page.wait_for_selector("#settings-dialog #settings-panel")
    assert page.locator("#settings-dialog").evaluate("d => d.open") and page.url.endswith("/audit")
    assert page.locator("#settings-dialog a[href='/settings']").count() == 1
    page.locator("#settings-dialog [data-dlg-close]").click()
    assert not page.locator("#settings-dialog").evaluate("d => d.open")
    page.goto("/p")
    assert not page.locator("#v5side").evaluate("a => a.classList.contains('vs-pclosed')")


def test_inbox_views_fold_per_page_kind(page):
    page.goto("/")
    page.wait_for_selector("#v5side .vs-it")
    page.evaluate("sessionStorage.removeItem('pm.v5.inboxopen')")
    page.reload()
    assert page.locator("#v5side .vs-sub").first.is_visible()  # open on the Inbox by default
    page.locator("#v5side .vs-it").click()
    assert not page.locator("#v5side .vs-sub").first.is_visible()
    assert page.locator("#v5side .vs-it").get_attribute("aria-expanded") == "false"
    page.reload()
    # (side-init.js applies html.ib-0 pre-paint, side.js drops it once the aside carries the state)
    assert not page.locator("#v5side .vs-sub").first.is_visible()
    page.goto("/audit")  # other tab: folded by default, the Inbox override does not apply
    assert not page.locator("#v5side .vs-sub").first.is_visible()
    page.locator("#v5side .vs-it").click()
    assert page.locator("#v5side .vs-sub").first.is_visible()
    page.reload()
    assert page.locator("#v5side .vs-sub").first.is_visible()
    page.goto("/")  # back on the Inbox: the other-page override does not apply, server default (open)
    assert page.locator("#v5side .vs-sub").first.is_visible()
    page.goto("/audit")
    assert page.locator("#v5side .vs-sub").first.is_visible()  # this tab's override for other pages is kept
