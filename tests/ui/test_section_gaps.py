"""Project page: exactly one hairline between the ntfy table and the tmux section, a collapsed tmux session row is one
row tall, and the next section's separator follows the list after a small gap (no empty band). Root causes were
the last ntfy row's bottom border + the section's own border-top 24px lower, and `.ssewrap h2:first-child {margin-top:20px}`
hitting the tmux heading inside `.sec-h`."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui

JS = """()=>{
 const px=v=>parseFloat(v)||0, r=e=>e.getBoundingClientRect();
 const sec=document.querySelector('#proj-live > .sec-h'); const scs=getComputedStyle(sec);
 const tb=document.querySelector('.pj-main>.pj-tabbar'); const tabbar=!!tb&&getComputedStyle(tb).display!=='none';
 const h2=sec.querySelector('h2, [role=tab]');  // the tmux heading, or its tab when tmux | Stack | VM are tabs
 const srows=[...document.querySelectorAll('#proj-live .srow:not([data-open])')];
 const lst=srows[srows.length-1].parentElement;
 let nxt=lst.nextElementSibling;  // in a tab panel: the next shown section after the panel
 if(!nxt&&lst.parentElement.classList.contains('ptab-panel')){nxt=lst.parentElement.nextElementSibling;
   while(nxt&&getComputedStyle(nxt).display==='none') nxt=nxt.nextElementSibling;}
 const ncs=getComputedStyle(nxt);
 const ls=srows[srows.length-1];
 const wins=getComputedStyle(ls.querySelector('.c-wins')).display;
 return {tabbar, secBorderTop:px(scs.borderTopWidth),
  headGap:r(h2).top-(r(sec).top+px(scs.borderTopWidth)),
  rowH:srows.map(e=>Math.round(r(e).height)), wins, lastRowBorder:px(getComputedStyle(ls).borderBottomWidth),
  listToNext:r(nxt).top-r(ls).bottom, nextBorder:px(ncs.borderTopWidth), nextTag:nxt.tagName,
  listBorderBottom:px(getComputedStyle(lst).borderBottomWidth)}}"""


@pytest.mark.parametrize("theme,width", [("light", 1300), ("dark", 1300), ("light", 390)])
def test_one_hairline_and_tight_collapsed_rows(make_page, inst, theme, width):
    page = make_page(width=width, theme=theme)
    page.goto(f"/p/{inst['project']}", wait_until="networkidle")
    m = page.evaluate(JS)
    assert m["secBorderTop"] == (0 if m["tabbar"] else 1), m   # one hairline: the tab bar's underline, else the section's own
    assert m["headGap"] <= 20, m                  # heading sits 16px under the hairline, no extra margin
    assert m["wins"] == "none", m                 # collapsed windows list takes no space
    assert len(set(m["rowH"])) <= 2 and max(m["rowH"]) <= (48 if width < 700 else 41), m   # one line per collapsed row (39 / 38 last; 47 on touch widths)
    assert m["lastRowBorder"] == 0 and m["listBorderBottom"] == 0, m
    assert m["nextBorder"] == 1 and 0 <= m["listToNext"] <= 12, m
    assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth+1")


def test_expand_still_works(make_page, inst):
    page = make_page()
    page.goto(f"/p/{inst['project']}", wait_until="networkidle")
    row = page.locator("#proj-live .srow").first
    h0 = row.bounding_box()["height"]
    row.locator(".srow-tg").click()
    page.wait_for_timeout(200)
    assert row.bounding_box()["height"] > h0
    assert row.locator(".c-wins").is_visible()
