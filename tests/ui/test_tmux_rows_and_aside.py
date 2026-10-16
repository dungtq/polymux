"""2026-10-10 UI wave: tmux row expand/collapse, ntfy without project chip on a project page, room above Interrupt/Exit,
the chat aside kept by ONE shared handler on every navigation, centered quick chat, Agents/Servers in the sidebar."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui


def _aside(inst):
    return "#chat=/a/" + inst["agents"]["running"]


def _open(page, inst, path):
    page.goto(path + _aside(inst))
    page.wait_for_selector("body.chat-open")


def _still_open(page):
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector("body.chat-open")
    assert page.evaluate("() => location.hash.startsWith('#chat=')")
    page.wait_for_function("() => document.getElementById('vc-frame').contentWindow.location.href.includes('embed=1')")


# ---- 1. tmux rows -----------------------------------------------------------------------------------------------
def test_tmux_row_expand_collapse_and_slots(page, inst):
    page.goto("/p/" + inst["project"])
    row = page.locator("#proj-live .srow").first
    row.wait_for()
    tg = row.locator(".srow-tg")
    assert tg.get_attribute("aria-expanded") == "false"
    assert not row.locator(".c-wins").is_visible() and row.locator(".c-nw").is_visible()
    tg.click()
    assert tg.get_attribute("aria-expanded") == "true" and row.locator(".c-wins").is_visible()
    # survives a live refresh and a reload
    page.evaluate("() => window.htmx.trigger(document.getElementById('proj-live'), 'pm:refresh')")
    page.wait_for_timeout(1200)
    assert page.locator("#proj-live .srow").first.locator(".c-wins").is_visible()
    page.reload()
    page.locator("#proj-live .srow").first.wait_for()
    assert page.locator("#proj-live .srow").first.locator(".c-wins").is_visible()
    page.locator("#proj-live .srow .srow-tg").first.click()
    assert not page.locator("#proj-live .srow").first.locator(".c-wins").is_visible()
    # clean places for the Close / Close-all buttons
    assert page.locator("#proj-live [data-slot='tmux-section-actions']").count() == 1
    assert page.locator("#proj-live .srow [data-slot='tmux-row-actions']").count() >= 1


@pytest.mark.parametrize("width", [1500, 800, 390])
def test_collapsed_tmux_row_is_one_row_even_with_many_windows(page, inst, width):
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("/p/" + inst["project"])
    page.locator("#proj-live .srow").first.wait_for()
    # give the first session 18 windows (DOM only), collapsed: its height must equal a one-window row
    page.evaluate("""() => { const w = document.querySelector('#proj-live .srow .c-wins'); const one = w.firstElementChild;
      for (let i = 0; i < 17; i++) w.appendChild(one.cloneNode(true)); }""")
    hs = page.evaluate("() => [...document.querySelectorAll('#proj-live .srow:not([data-open])')].map(r => r.getBoundingClientRect().height)")
    assert len(hs) >= 2 and max(hs) - min(hs) <= 2 and max(hs) <= 50, hs
    row = page.locator("#proj-live .srow").first
    row.locator(".srow-tg").click()   # expanded still lists the windows below / beside the name
    assert row.locator(".c-wins").is_visible() and row.locator(".swin").count() == 18
    row.locator(".srow-tg").click()


# ---- 3. Exit padding (Interrupt is the Send/Stop slot now) -------------------------------------------------------------------------------
def test_exit_has_room_below_composer(page, inst):
    page.goto("/a/" + inst["agents"]["running"])
    page.wait_for_selector("[data-composer=live] .mrow .mb-act")
    cbox = page.locator("[data-composer=live] .cbox").bounding_box()
    act = page.locator("[data-composer=live] .mrow .mb-act button").first.bounding_box()
    assert act["y"] - (cbox["y"] + cbox["height"]) >= 8, (cbox, act)


# ---- 4. aside kept on every navigation --------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/", "/tmux", "/p/fixproj", "/backlog"])
def test_aside_survives_selects_sorts_and_tab_links(page, inst, path):
    _open(page, inst, path)
    sel = page.locator("main select[data-nav]").first
    if sel.count():
        opts = sel.evaluate("s => [...s.options].map(o => o.value)")
        with page.expect_navigation():
            sel.select_option(opts[-1] if sel.input_value() != opts[-1] else opts[0])
        _still_open(page)
    link = page.locator("main a[href*='sort=']:visible").first
    if link.count():
        link.click()
        page.wait_for_load_state("networkidle")
        _still_open(page)
    # every top tab / sidebar link that leads to a drawer page keeps it
    for href in ("/tmux", "/"):
        a = page.locator(f".tabbar a[href='{href}']").first
        if a.is_visible():
            a.click()
            page.wait_for_url(lambda u, h=href: u.split("#")[0].rstrip("/").endswith(h.rstrip("/")))
            _still_open(page)


def test_aside_survives_route_through_a_page_without_drawer(page, inst):
    """Projects list has no drawer by design: leaving it ignores the aside, the next drawer page (within seconds) restores it."""
    _open(page, inst, "/")
    page.locator(".tabbar a[href='/p']").first.click()
    page.wait_for_url("**/p")
    assert page.locator("#v5chat").count() == 0
    page.locator(".tabbar a[href='/tmux']").first.click()
    page.wait_for_url("**/tmux**")
    _still_open(page)


def test_aside_survives_live_filter_and_history_push(page, inst):
    _open(page, inst, "/")
    page.locator("#inbox-filter input[data-filter]").fill("zz")
    page.wait_for_timeout(500)
    assert page.evaluate("() => location.search.includes('q=zz') && location.hash.startsWith('#chat=')")
    page.evaluate("() => history.pushState(null, '', '/?x=1')")  # htmx push-url style
    assert page.evaluate("() => location.search === '?x=1' && location.hash.startsWith('#chat=')")


def test_closing_the_aside_does_not_carry_it(page, inst):
    _open(page, inst, "/")
    page.locator("#vc-close").click()
    page.wait_for_function("() => !document.body.classList.contains('chat-open')")
    page.locator(".tabbar a[href='/tmux']").first.click()
    page.wait_for_url("**/tmux**")
    assert not page.evaluate("() => document.body.classList.contains('chat-open')")


# ---- 5. quick chat ------------------------------------------------------------------------------------------------
def test_quick_chat_default_small_centered_then_expand(page):
    page.goto("/audit")
    page.evaluate("() => localStorage.removeItem('pm.qchat.v2')")
    page.locator("[data-qchat-open]").click()
    box = page.locator("#qchat").bounding_box()
    vp = page.viewport_size
    # default size since 2026-10-11 (CHANGELOG "quick chat size"): about 48% of the width and 80% of the height, max 860x960
    want_w = min(max(420, vp["width"] * 0.48), 860, vp["width"] - 24)
    want_h = min(max(500, vp["height"] * 0.8), 960, vp["height"] - 48)
    assert abs(box["width"] - want_w) <= 2 and abs(box["height"] - want_h) <= 2, (box, want_w, want_h)
    assert box["width"] < vp["width"] - 60 and box["height"] < vp["height"] - 20, box   # still a popup, not the full window
    assert abs((box["x"] + box["width"] / 2) - vp["width"] / 2) < 14 and abs((box["y"] + box["height"] / 2) - vp["height"] / 2) < 4, box
    for sel in ("[data-composer=qchat] [data-cmp-send]", ".qc-model", "[data-composer=qchat] [data-paste-text]",
                "[data-composer=qchat] [data-attach]"):
        assert page.locator("#qchat " + sel).count() == 1
    page.locator("#qchat .qc-max").click()
    big = page.locator("#qchat").bounding_box()
    assert big["width"] > vp["width"] - 60 and big["height"] > vp["height"] - 120, big
    page.locator("#qchat .qc-max").click()
    back = page.locator("#qchat").bounding_box()
    assert abs(back["width"] - box["width"]) <= 2 and abs(back["height"] - box["height"]) <= 2, (back, box)


# ---- 6. sidebar groups ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/audit", "/p/fixproj", "/tmux"])
def test_sidebar_foot_groups_and_tabbar_without_agents_servers(page, path):
    page.goto(path)
    page.wait_for_selector("#v5side .vs-foot")
    heads = page.locator("#v5side .vs-foot .vs-gh").all_text_contents()
    assert heads in (["Manage", "Service", "Account"], ["Manage", "Account"]), heads  # the dev instance has no Reload
    assert page.locator("#v5side .vs-foot a[href='/agents'], #v5side .vs-foot a[href='/servers']").count() == 2
    assert page.locator(".tabbar > a[href='/agents']").count() == 0
    assert not page.locator(".tabbar > a[href='/servers']").is_visible()
    page.locator("#acct-menu > summary").click()
    assert page.locator("#acct-menu .acct-gh").all_text_contents() == heads
    page.keyboard.press("Escape")


def test_sidebar_go_to_links_work(page):
    page.goto("/audit")
    page.locator("#v5side .vs-foot a[href='/servers']").click()
    page.wait_for_url("**/servers")
    assert page.locator("#v5side .vs-foot a[href='/servers'][aria-current='page']").count() == 1
    page.locator("#v5side .vs-foot a[href='/agents']").click()
    page.wait_for_url("**/agents")
    assert page.locator("#v5side .vs-foot a[href='/agents'][aria-current='page']").count() == 1


def test_phone_keeps_servers_in_bottom_nav(make_page):
    pg = make_page(width=390, height=800)
    pg.goto("/audit")
    assert pg.locator(".tabbar a[href='/servers']").is_visible()
