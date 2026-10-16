"""2026-10-10 backlog round 3: chat aside survives filter/sort/edit-save, composer actions below, skills collapsed,
top-right nav size, quick-chat composer."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui
CHAT = "#chat=/a/devfix"


def _edit_first_item(page, text):
    it = page.locator("#backlog-card .bl-item").first
    it.hover()
    it.locator("[data-bl-edit]").click()
    dlg = page.locator("dialog[data-pm-confirm][open]")
    dlg.locator("input").fill(text)
    dlg.get_by_role("button", name="Save").click()


def test_item_edit_save_keeps_aside_and_columns(page):
    page.goto("/p/fixproj" + CHAT)
    page.wait_for_selector("body.chat-open")
    page.click("[data-pj-pane=pj-p-backlog]")
    page.wait_for_selector("#backlog-card .bl-item")
    page.screenshot(path="/tmp/pm-dev/r3-before.png")
    _edit_first_item(page, "Round3 edited item")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(800)
    page.screenshot(path="/tmp/pm-dev/r3-after.png")
    assert page.evaluate("() => document.body.classList.contains('chat-open') && location.hash.startsWith('#chat=')")


def test_topbar_right_buttons_same_font_as_tabs(page):
    page.goto("/audit")
    sz = page.evaluate("""() => {
      const f = s => parseFloat(getComputedStyle(document.querySelector(s)).fontSize);
      return {tab: f('.tabbar a[href="/audit"]'), l: f('.tb-launch .tl'), s: f('.tb-stop .tl'), c: f('.tb-qchat .tl'), m: f('.acct-sum .tl')};
    }""")
    print(sz)
    assert sz["l"] == sz["s"] == sz["c"] == sz["m"] == sz["tab"], sz


def test_agent_exit_below_composer_on_model_row(page, inst):
    page.goto("/a/" + inst["agents"]["running"])
    page.wait_for_selector("[data-composer=live] .mrow .mb-act")
    cbox = page.locator("[data-composer=live] .cbox").bounding_box()
    act = page.locator("[data-composer=live] .mrow .mb-act").bounding_box()
    model = page.locator("[data-composer=live] .mrow .modelbar").bounding_box()
    assert act["y"] >= cbox["y"] + cbox["height"] - 1, "actions must sit below the composer"
    assert abs((act["y"] + act["height"] / 2) - (model["y"] + model["height"] / 2)) < 20, "same row as Model"
    assert page.locator("[data-composer=live] .mrow .mb-act", has_text="Exit").count() == 1
    assert page.locator("[data-composer=live] .cstat", has_text="Exit").count() == 0
    # Interrupt: one place, the Send/Stop slot (no button in the model row, the token row or the icon bar)
    for where in ("[data-composer=live] .mrow", "[data-composer=live] .cstat", "[data-composer=live] .cbar-l"):
        assert page.locator(where, has_text="Interrupt").count() == 0
        assert page.locator(where + " [aria-label^='Interrupt']").count() == 0


def test_skill_tabs_local_first(page):
    page.goto("/p/fixproj")
    page.locator("a.pj-tab[data-pj=skills]").click()  # Skills is a docs-panel tab, loaded on select
    tabs = page.locator("[data-sk-tab]")
    tabs.first.wait_for(state="attached")
    assert tabs.count() == 2 and tabs.first.get_attribute("aria-selected") == "true"
    assert tabs.first.locator(".pj-n").inner_text().strip().isdigit()
    assert page.locator(".pj-skgrp:visible").count() == 1 and page.locator(".pj-skgrp:visible").get_attribute("data-scope") == "local"
    tabs.nth(1).click()
    assert page.locator(".pj-skgrp:visible").get_attribute("data-scope") == "global"
    assert page.locator("table.pj-sk:visible").count() == 1


def test_inbox_filter_and_show_keep_chat_aside(page, inst):
    chat = "#chat=/a/" + inst["agents"]["running"]
    page.goto("/" + chat)
    page.wait_for_selector("body.chat-open")
    sel = page.locator("select[data-nav][name=n]").first
    opts = sel.evaluate("s => [...s.options].map(o => o.value)")
    with page.expect_navigation():
        sel.select_option(opts[-1] if sel.input_value() != opts[-1] else opts[0])
    page.wait_for_selector("body.chat-open")
    assert page.evaluate("() => location.search.length > 0 && location.hash.startsWith('#chat=')")
    page.locator("#inbox-filter input[data-filter]").fill("zz")
    page.wait_for_timeout(400)
    assert page.evaluate("() => location.hash.startsWith('#chat=') && location.search.includes('q=zz')")


def test_sort_header_link_keeps_chat_aside(page, inst):
    chat = "#chat=/a/" + inst["agents"]["running"]
    page.goto("/" + chat)
    page.wait_for_selector("body.chat-open")
    link = page.locator("main a[href*='sort=']:visible").first
    link.wait_for()
    link.click()
    page.wait_for_load_state("networkidle")
    assert page.evaluate("() => location.search.includes('sort=') && location.hash.startsWith('#chat=')")
    page.wait_for_selector("body.chat-open")


def test_backlog_file_edit_is_framed_and_keeps_chat_aside(page, inst):
    """Edit file opens the editor inside the Backlog tab (iframe); Cancel closes it, Save reloads the page; the aside stays."""
    chat = "#chat=/a/" + inst["agents"]["running"]
    page.goto("/p/fixproj" + chat)
    page.wait_for_selector("body.chat-open")
    page.click("[data-pj-pane=pj-p-backlog]")
    page.locator("[data-bl-file]").click()
    fr = page.frame_locator("#pj-p-backlog iframe")
    fr.locator("#bl-edit").wait_for()
    assert page.evaluate("() => location.hash.startsWith('#chat=')")
    page.wait_for_function("() => { const d = document.querySelector('#pj-p-backlog iframe').contentDocument; "
                           "const i = d && d.querySelector('#bl-edit input[name=lease]'); return !!(i && i.value); }")   # lease taken
    fr.locator("button[data-bl-close]").first.click()
    page.wait_for_function("() => document.querySelector('.pj-bledit').hidden")
    page.locator("[data-bl-file]").click()
    fr.locator("#bl-edit").wait_for()
    fr.locator("#bl-edit button[type=submit]").first.click()
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("body.chat-open")
    assert page.evaluate("() => location.hash.startsWith('#chat=') || location.hash === '#backlog'")


def test_item_edit_save_keeps_aside_agent_tables_columns(page, inst):
    page.goto("/p/fixproj" + "#chat=/a/" + inst["agents"]["running"])
    page.wait_for_selector("body.chat-open")
    page.click("[data-pj-pane=pj-p-backlog]")
    page.wait_for_selector("#backlog-card .bl-item")
    _edit_first_item(page, "Round3 edited again")
    page.wait_for_function("() => document.body.classList.contains('chat-open') && location.hash.startsWith('#chat=')")
    edited = page.locator("#backlog-card .bl-item", has_text="Round3 edited again")
    edited.first.wait_for()  # the card re-renders after the save; wait for the new text instead of the old items
    assert edited.count() == 1
    assert page.locator(".pg-project .arow, .pg-project .nrow").count() > 0  # Agents table still rendered


def test_qchat_composer_model_note_and_dropped_text(page):
    page.goto("/audit")
    page.locator("[data-qchat-open]").click()
    assert "claude -p" not in page.locator(".qc-head").inner_text()
    assert "claude -p" in page.locator(".qc-note").inner_text()
    form = page.locator("#qchat [data-cmp-form]").bounding_box()
    send = page.locator("#qchat .cbox [data-cmp-send]").bounding_box()
    assert form["y"] <= send["y"] and send["y"] + send["height"] <= form["y"] + form["height"], "Send is inside the composer"
    model = page.locator("#qchat .qc-model").bounding_box()
    assert model["y"] >= form["y"] + form["height"], "model select comes after the composer"
    # paste a text file from the clipboard
    page.evaluate("""() => {
      const t = document.querySelector('#qchat [data-cmp-input]'); t.focus();
      const dt = new DataTransfer(); dt.items.add(new File(['hello pasted'], 'p.txt', {type: 'text/plain'}));
      t.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
    }""")
    page.wait_for_function("() => document.querySelector('#qchat [data-cmp-input]').value.includes('hello pasted')")
    # drop a text file and an image
    page.evaluate("""() => {
      const dt = new DataTransfer();
      dt.items.add(new File(['dropped body'], 'd.md', {type: 'text/markdown'}));
      dt.items.add(new File([new Uint8Array([137,80,78,71])], 'shot.png', {type: 'image/png'}));
      const p = document.querySelector('#qchat [data-composer]');  // drop onto the composer (composer-attach.js)
      p.dispatchEvent(new DragEvent('dragover', {dataTransfer: dt, bubbles: true, cancelable: true}));
      p.dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true}));
    }""")
    page.wait_for_function("() => document.querySelector('#qchat [data-cmp-input]').value.includes('dropped body')")
    hint = page.locator("#qchat [data-cmp-flash]")
    assert hint.is_visible() and "shot.png" in hint.inner_text()
    assert page.locator("#qchat [data-cmp-input]").input_value().count("```") == 4


@pytest.mark.parametrize("theme,width", [("light", 1400), ("dark", 1400), ("light", 390), ("dark", 390)])
def test_round3_screenshots(make_page, inst, theme, width):
    pg = make_page(width=width, theme=theme)
    out = f"/tmp/pm-dev/r3-{theme}-{width}"
    pg.goto("/a/" + inst["agents"]["running"])
    pg.wait_for_selector("[data-composer=live] .mrow", state="attached")  # phones start with the composer hidden (tap content to show)
    pg.screenshot(path=out + "-agent.png")
    pg.goto("/p/fixproj")
    pg.locator("a.pj-tab[data-pj=skills]").click()
    pg.locator("[data-sk-tab=global]").click()
    pg.screenshot(path=out + "-project.png", full_page=True)
    pg.locator("[data-qchat-open]").click()
    pg.screenshot(path=out + "-qchat.png")
    pg.goto("/audit")
    pg.screenshot(path=out + "-top.png", clip={"x": 0, "y": 0, "width": width, "height": 50})
