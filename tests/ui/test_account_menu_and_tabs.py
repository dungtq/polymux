"""2026-10-10 backlog round: account menu, quick chat popup, section icons, Inbox git + drawer kept,
agent actions above the composer, Sub-agents tab, backlog item edit."""
from __future__ import annotations

import pytest

from .conftest import show_composer

pytestmark = pytest.mark.ui
CHAT = "#chat=/a/devfix"


def test_account_menu_groups_theme_settings_reload_logout(page):
    page.goto("/audit")
    assert page.locator(".topbar > span > [data-theme-toggle], .topbar > span > form[action='/logout']").count() == 0
    page.locator("#acct-menu > summary").click()
    pop = page.locator("#acct-menu .acct-pop")
    assert pop.is_visible()
    assert pop.locator("a[href='/settings']").count() == 1 and pop.locator("form[action='/logout']").count() == 1
    html = page.locator("html")
    before = html.get_attribute("data-theme")
    pop.locator("[data-theme-toggle]").click()
    assert html.get_attribute("data-theme") != before
    page.wait_for_function("()=>!document.getElementById('acct-menu').open")
    page.locator("#acct-menu > summary").click()
    page.keyboard.press("Escape")
    assert not page.locator("#acct-menu").evaluate("d => d.open")


def test_quick_chat_popup_opens_and_closes(page):
    page.goto("/audit")
    assert not page.locator("#qchat").is_visible()
    page.locator("[data-qchat-open]").click()
    assert page.locator("#qchat").is_visible() and page.locator("#qchat [data-cmp-input]").evaluate("e => document.activeElement === e")
    page.keyboard.press("Escape")
    assert not page.locator("#qchat").is_visible()


def test_nav_icons_present(page):
    page.goto("/audit")
    n = page.evaluate("() => [...document.querySelectorAll('.tabbar a.t-desk, .tabbar a')].filter(a => a.offsetParent && getComputedStyle(a, '::before').maskImage !== 'none').length")
    assert n >= 5  # Inbox, Projects, tmux, Backlog, Audit (Agents / Servers moved to the sidebar)
    m = page.evaluate("() => [...document.querySelectorAll('.vs-nav .vs-row[data-p] .vs-sp')].every(e => getComputedStyle(e).maskImage !== 'none')")
    assert m



def test_inbox_tab_switch_keeps_chat_aside(page):
    page.goto("/" + CHAT)
    page.wait_for_selector("body.chat-open")
    page.locator(".tabbar a[href='/tmux']").click()
    page.wait_for_function("() => location.pathname === '/tmux' && location.hash.startsWith('#chat=')")
    page.wait_for_selector("body.chat-open")
    page.goto("/" + CHAT)
    page.wait_for_selector("body.chat-open")
    page.locator("#v5side a[href='/?view=ended']").click()  # Ended is its own page; the carried chat aside stays
    page.wait_for_function("() => location.search === '?view=ended'")
    # the carried aside is restored by drawer-init.js after the head's stylesheets load, not at URL change
    page.wait_for_function("() => document.body.classList.contains('chat-open') && location.hash.startsWith('#chat=')")


def test_agent_actions_sit_above_composer_with_tokens(page, inst):
    page.goto("/a/" + inst["agents"]["running"])
    page.wait_for_selector("[data-composer=live] .cstat .agent-actions")
    box = page.locator("[data-composer=live] .cstat").bounding_box()
    comp = page.locator("[data-composer=live] .cbox").bounding_box()
    assert box["y"] + box["height"] <= comp["y"] + 2
    for txt in ("Builder", "New Chat", "Fork"):
        assert page.locator("[data-composer=live] .cstat .agent-actions", has_text=txt).count() == 1
    assert page.locator("[data-composer=live] .cstat .agent-actions", has_text="Clone").count() == 0
    assert page.locator("details.fork-menu").count() == 0
    assert page.locator(".agent-head .agent-actions").count() == 0


def test_subagents_tab(page, inst):
    page.goto("/a/" + inst["agents"]["running"])
    page.locator("label.tab-l-sub").click()
    page.wait_for_selector("#sa-body .sa-note, #sa-body .sa-list")
    assert page.locator(".tabpanel-sub").is_visible() and not page.locator(".tabpanel-chat").is_visible()
    # radio tabs: pm.tabs mirrors the checked radio into aria-selected; the look follows :checked (tabs.css)
    assert page.locator("label.tab-l-sub").get_attribute("aria-selected") == "true"
    assert page.locator("label.tab-l-chat").get_attribute("aria-selected") == "false"
    assert "inset" in page.locator("label.tab-l-sub").evaluate("e => getComputedStyle(e).boxShadow")
    assert page.locator("label.tab-l-chat").evaluate("e => getComputedStyle(e).boxShadow") == "none"


ORANGE = "rgb(253, 140, 115)"  # --tab-on
CUR = """s => [...document.querySelectorAll(s)].filter(e => e.getClientRects().length).map(e => {
  const c = getComputedStyle(e); return [e.textContent.trim().split(/\\s/)[0], c.boxShadow, c.fontWeight, c.borderBottomColor,
  c.backgroundColor]; })"""
ACCENT = """() => { const d = document.createElement('i'); d.style.color = 'var(--accent)'; document.body.append(d);
  const c = getComputedStyle(d).color; d.remove(); return c; }"""


@pytest.mark.parametrize("url,tabs", [("/servers", ".srv-tabs .tab"), ("/agents", ".ag-tabs .tab"), ("/ntfy", ".ntfy-tabs .tab")])
def test_boxed_tab_groups(page, url, tabs):
    """.tabs-seg (Servers, Agents, ntfy): grey boxed group, the current tab white, 600, blue --accent underline."""
    page.goto(url)
    rows, accent = page.evaluate(CUR, tabs), page.evaluate(ACCENT)
    bar = page.locator(tabs.split(" ")[0]).evaluate("e => [getComputedStyle(e).borderTopWidth, getComputedStyle(e).backgroundColor]")
    on = [r for r in rows if r[1] != "none"]
    assert len(rows) >= 2 and len(on) == 1, rows
    assert on[0][1].startswith(accent) and "inset" in on[0][1] and on[0][2] == "600", on
    assert bar[0] == "1px" and bar[1] != on[0][4], (bar, on)  # boxed, the current tab is lighter than the band


@pytest.mark.parametrize("tabs", ["#pj-tabbar .tab", "[data-ptabs] .tab"])
def test_doc_tabs_border_underline(page, tabs):
    """.tabs-line (project docs, tmux|Stack|VM): 2px --accent border under the current tab, weight 500, no shadow."""
    page.goto("/p/fixproj")
    rows, accent = page.evaluate(CUR, tabs), page.evaluate(ACCENT)
    on = [r for r in rows if r[3] == accent]
    assert len(rows) >= 2 and len(on) == 1 and on[0][2] == "500", rows
    assert all(r[1] == "none" for r in rows), rows


def test_agent_tabs_keep_the_kind_underline(page, inst):
    page.goto("/a/" + inst["agents"]["running"])
    k = page.locator(".agent-head").evaluate("e => getComputedStyle(e).getPropertyValue('--k').trim()")
    shadow = page.locator("label.tab-l-chat").evaluate("e => getComputedStyle(e).boxShadow")
    probe = page.evaluate("k => { const d = document.createElement('i'); d.style.color = k; document.body.append(d);"
                          " const c = getComputedStyle(d).color; d.remove(); return c; }", k)
    assert k and shadow.startswith(probe) and not shadow.startswith(ORANGE)


def test_ntfy_period_tabs_keys_and_persist(page):
    page.goto("/ntfy")
    tabs = page.locator(".ntfy-tabs [role=tab]")
    first = tabs.nth(0)
    assert first.get_attribute("aria-selected") == "true" and first.get_attribute("tabindex") == "0"
    assert tabs.nth(1).get_attribute("tabindex") == "-1"  # roving tabindex
    first.focus()
    page.keyboard.press("ArrowRight")
    assert tabs.nth(1).get_attribute("aria-selected") == "true"
    assert tabs.nth(1).evaluate("e => document.activeElement === e")
    page.reload()
    assert page.locator(".ntfy-tabs [role=tab]").nth(1).get_attribute("aria-selected") == "true"  # pm.store
    page.locator(".ntfy-tabs [role=tab]").nth(1).focus()
    page.keyboard.press("End")
    assert page.locator(".ntfy-tabs [role=tab]").last.get_attribute("aria-selected") == "true"
    page.keyboard.press("Home")
    assert page.locator(".ntfy-tabs [role=tab]").first.get_attribute("aria-selected") == "true"


def test_project_tmux_stack_vm_tabs_persist(page):
    page.goto("/p/fixproj")
    page.locator("#ptab-stack").click()
    assert page.locator("#ptp-stack").is_visible() and not page.locator("#ptp-tmux").is_visible()
    assert not page.locator("[data-slot='tmux-section-actions']").is_visible()
    page.reload()
    assert page.locator("#ptab-stack").get_attribute("aria-selected") == "true" and page.locator("#ptp-stack").is_visible()
    page.locator("#ptab-stack").focus()
    page.keyboard.press("ArrowLeft")
    assert page.locator("#ptab-tmux").get_attribute("aria-selected") == "true" and page.locator("#ptp-tmux").is_visible()


def test_backlog_edit_item(page):
    page.goto("/backlog")
    grp = page.locator("details.bl-group[data-project='fixproj']")
    if not grp.evaluate("d=>d.open"):
        grp.locator("summary").click()
    it = grp.locator(".bl-item", has_text="Review the plans page")
    it.hover()
    it.locator("[data-bl-edit]").click()
    dlg = page.locator("dialog[data-pm-confirm][open]")
    dlg.locator("input").fill("Review the plans page (edited)")
    dlg.get_by_role("button", name="Save").click()
    page.wait_for_selector("details.bl-group[data-project='fixproj'] .bl-item:has-text('(edited)')")


@pytest.mark.parametrize("theme,width", [("light", 1400), ("dark", 1400), ("light", 390), ("dark", 390)])
def test_round_screenshots(make_page, inst, theme, width):
    """Screenshots for eyeballing (read them): menu, quick chat, inbox git, project split, agent row."""
    pg = make_page(width=width, theme=theme)
    out = f"/tmp/pm-dev/round-{theme}-{width}"
    pg.goto("/audit")
    pg.locator("#acct-menu > summary").click()
    pg.screenshot(path=out + "-menu.png")
    pg.keyboard.press("Escape")
    pg.locator("[data-qchat-open]").click()
    pg.evaluate("()=>{const l=document.querySelector('#qchat .qc-log');"
                "for(const [c,t] of [['qc-u','What is 2+2?'],['qc-a','It is 4.'],['qc-err','Not allowed (reload the page).']]){"
                "const d=document.createElement('div');d.className='qc-m '+c;d.textContent=t;l.appendChild(d);}}")
    pg.screenshot(path=out + "-qchat.png")
    pg.keyboard.press("Escape")
    pg.goto("/")
    pg.wait_for_selector("#inbox")
    pg.screenshot(path=out + "-inbox.png", full_page=True)
    pg.goto("/p/fixproj")
    pg.screenshot(path=out + "-project.png", full_page=True)
    pg.goto("/a/" + inst["agents"]["running"])
    show_composer(pg)  # phones start with the composer hidden; tap content shows it
    pg.wait_for_selector("[data-composer=live] .cstat")
    pg.screenshot(path=out + "-agent.png")
    pg.locator("label.tab-l-sub").click()
    pg.screenshot(path=out + "-subtab.png")
