"""Ticking a backlog / TODO item with the chat drawer open swaps the list in place: no page reload,
the drawer (its iframe and #chat= hash) stays open. One dev instance per module, so each case ticks
different fixture items."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui

CHAT = "#chat=/a/devfix"


def _no_reload(pg):
    assert pg.evaluate("()=>window.__noReload") == 1, "page reloaded"


@pytest.mark.parametrize("theme,bl,todo", [("light", "narrow width screenshots", "Check the chat view"),
                                           ("dark", "add a dark theme check", "Write the fixture tests")])
def test_project_tick_keeps_drawer(make_page, inst, theme, bl, todo):
    pg = make_page(width=1400, theme=theme)
    pg.goto("/p/fixproj" + CHAT)
    pg.wait_for_selector("body.chat-open")
    pg.evaluate("()=>{window.__noReload = 1}")
    # the project cards show open items only: a ticked item leaves the view (the Backlog card keeps it in the DOM,
    # hidden, for the open/done toggle), so check the rendered text, not textContent
    for card, text in (("#backlog-card", bl), ("#todo-card", todo)):
        pg.click("[data-pj-pane=pj-p-" + ("backlog" if card.startswith("#backlog") else "todo") + "]")   # the cards live in right-panel tabs
        pg.locator(card + " .bl-item", has_text=text).locator(".bl-box").click()
        pg.wait_for_function("([c,t])=>!document.querySelector(c).innerText.includes(t)", arg=[card, text])
        _no_reload(pg)
        assert pg.evaluate("()=>document.body.classList.contains('chat-open')")
        assert pg.evaluate("()=>location.hash") == CHAT
    pg.screenshot(path=f"/tmp/pm-dev/backlog-drawer-{theme}.png")


def test_backlog_page_ticks_in_place(make_page, inst):
    pg = make_page(width=1400)
    pg.goto("/backlog")
    pg.evaluate("()=>{window.__noReload = 1}")
    grp = pg.locator("details.bl-group[data-project='fixproj']")
    if not grp.evaluate("d=>d.open"):
        grp.locator("summary").click()
    grp.locator(".bl-item", has_text="Review the plans page").locator(".bl-box").click()
    pg.wait_for_selector("details.bl-group[data-project='fixproj'] .bl-item.done:has-text('Review the plans page')")
    _no_reload(pg)
    assert grp.evaluate("d=>d.open")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_project_add_keeps_drawer(make_page, inst, theme):
    """Quick-add posts in place: no reload, the drawer iframe and #chat= hash stay, the new item shows."""
    pg = make_page(width=1400, theme=theme)
    chat = "#chat=/a/" + inst["agents"]["running"]
    pg.goto("/p/fixproj" + chat)
    pg.wait_for_selector("body.chat-open")
    pg.frame_locator("#vc-frame").locator(".agent-head").wait_for(state="attached")  # the embedded head is collapsed (hidden)
    pg.click("[data-pj-pane=pj-p-backlog]")
    pg.evaluate("()=>{window.__noReload = 1; window.__frame = document.getElementById('vc-frame').contentWindow}")
    text = f"ui added item {theme}"
    pg.fill("#backlog-card .bl-add input[name=text]", text)
    pg.click("#backlog-card .bl-add button[type=submit]")
    pg.wait_for_function("t=>document.querySelector('#backlog-card').textContent.includes(t)", arg=text)
    _no_reload(pg)
    assert pg.evaluate("()=>document.body.classList.contains('chat-open')")
    assert pg.evaluate("()=>location.hash") == chat
    assert pg.evaluate("()=>window.__frame === document.getElementById('vc-frame').contentWindow")
    assert pg.input_value("#backlog-card .bl-add input[name=text]") == ""
    pg.screenshot(path=f"/tmp/pm-dev/backlog-add-drawer-{theme}.png")
