"""Chat aside header (.vc-head): wide = one row, every control as high as the zoom / close buttons; narrow drawer
(container vc) = row 1 id + full model pill + Open full + ×, row 2 tabs left and the Detail|Compact toggle right."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui

MEAS = """() => {
  const box = s => { const e = document.querySelector(s); if (!e || !e.getClientRects().length) return null;
    const r = e.getBoundingClientRect(); return {top: r.top, bottom: r.bottom, h: Math.round(r.height), left: r.left, right: r.right}; };
  const p = document.getElementById('vc-model'), h = document.querySelector('.vc-head');
  return {pillCut: p.scrollWidth > p.clientWidth, headOver: h.scrollWidth > h.clientWidth,
    title: box('#vc-title'), pill: box('#vc-model'), tabs: box('#vc-tabs'), seg: box('.vc-head .ci-view'),
    zb: box('.vc-head .vc-zb'), full: box('#vc-full'), close: box('#vc-close')};
}"""


def _open(make_page, inst, drawer_px: int, theme: str = "light"):
    page = make_page(width=1512, height=900, theme=theme)
    page.goto("/p/fixproj#chat=/a/" + inst["agents"]["running"])
    page.wait_for_selector("body.chat-open")
    page.wait_for_function("()=>!document.getElementById('vc-tabs').hidden && !document.getElementById('vc-model').hidden")
    page.evaluate("w=>{const c=document.getElementById('v5chat');c.style.flex='0 0 '+w+'px';c.style.maxWidth='none'}", drawer_px)
    page.wait_for_timeout(200)
    return page.evaluate(MEAS)


def _mid(b):
    return (b["top"] + b["bottom"]) / 2


def test_wide_drawer_head_is_one_row_same_height(make_page, inst):
    m = _open(make_page, inst, 1000)
    assert not m["pillCut"] and not m["headOver"], m
    assert m["seg"]["h"] == m["zb"]["h"] == m["close"]["h"] == m["full"]["h"], m
    mids = [_mid(m[k]) for k in ("title", "pill", "tabs", "seg", "zb", "full", "close")]
    assert max(mids) - min(mids) <= 3, m


def test_drawer_tabs_are_shared_tabs_and_follow_the_frame(make_page, inst):
    """The aside mirrors the framed page's radio tabs as .tab buttons (pm.tabs marks the checked one, kind underline)."""
    page = make_page(width=1512, height=900, theme="light")
    page.goto("/p/fixproj#chat=/a/" + inst["agents"]["running"])
    page.wait_for_function("()=>!document.getElementById('vc-tabs').hidden && document.querySelector('#vc-tabs .tab[aria-selected=true]')")
    sel ="() => [...document.querySelectorAll('#vc-tabs .tab')].map(b => [b.textContent, b.getAttribute('aria-selected'), b.tabIndex])"
    tabs = page.evaluate(sel)
    assert [t[0] for t in tabs][:1] == ["Chat"] and [t[1] for t in tabs].count("true") == 1 and tabs[0][1] == "true", tabs
    assert tabs[0][2] == 0 and all(t[2] == -1 for t in tabs[1:]), tabs  # roving tabindex
    assert "inset" in page.locator("#vc-tabs .tab[aria-selected=true]").evaluate("e => getComputedStyle(e).boxShadow")
    page.locator("#vc-tabs .tab", has_text="Pane").click()
    page.wait_for_function("() => document.querySelector('#vc-tabs .tab[aria-selected=true]').textContent === 'Pane'")
    assert page.frame_locator("#vc-frame").locator("#tab-pane").evaluate("r => r.checked")
    page.locator("#vc-tabs .tab[aria-selected=true]").focus()
    page.keyboard.press("Home")
    page.wait_for_function("() => document.querySelector('#vc-tabs .tab[aria-selected=true]').textContent === 'Chat'")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_narrow_drawer_head_two_rows(make_page, inst, theme):
    m = _open(make_page, inst, 400, theme)
    assert not m["pillCut"] and not m["headOver"], m
    assert m["zb"] is None, "text size stays hidden in a narrow drawer"
    assert m["seg"]["h"] == m["close"]["h"] == m["full"]["h"], m
    row1 = _mid(m["title"])
    for k in ("pill", "full", "close"):
        assert abs(_mid(m[k]) - row1) <= 3, (k, m)
    assert m["tabs"]["top"] >= m["title"]["bottom"] and m["seg"]["top"] >= m["close"]["bottom"], m
    assert abs(_mid(m["tabs"]) - _mid(m["seg"])) <= 6, m
    assert m["tabs"]["right"] <= m["seg"]["left"], "tabs left, toggle right"
    assert m["close"]["left"] > m["full"]["left"] > m["pill"]["right"], "Open full and × right of the pill"


def test_aside_pill_follows_pm_composer_in_the_frame(make_page, inst):
    """#155: a model switch in the framed composer (HX-Trigger pm:composer {pill}) updates the aside pill."""
    page = make_page(width=1512, height=900)
    page.goto("/p/fixproj#chat=/a/" + inst["agents"]["running"])
    page.wait_for_function("()=>!document.getElementById('vc-model').hidden")
    page.evaluate("""() => { const fd = document.getElementById('vc-frame').contentDocument;
      fd.body.dispatchEvent(new CustomEvent('pm:composer', {bubbles: true, detail: {ph: 'x', phShort: 'x', pill: 'opus-5-5'}})); }""")
    page.wait_for_function("()=>document.getElementById('vc-model').textContent === 'opus-5-5'")
