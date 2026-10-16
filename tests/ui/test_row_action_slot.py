"""The row exit / resume button lives in the row's expandable detail panel (rowx.js, since 2026-10-12; no longer a
hover slot at the row end). Closed: the panel and its button are hidden and the row grid never overflows its link.
Opened (click the chevron before the agent id): the panel sits under the row link, the button is visible, fully
opaque and does not overlap any cell of the row. Project page (docs pane open and closed) and Inbox, over the
widths where the column sets change."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui

OVERFLOW = """()=>{const bad=[];for(const w of document.querySelectorAll('.nwrap')){
 if(!w.querySelector('.n-exit')||!w.offsetParent)continue;const a=w.querySelector(':scope > a');
 if(a.scrollWidth>a.clientWidth+1)bad.push('overflow '+(a.scrollWidth-a.clientWidth))}
 return bad}"""
OVERLAP = """(w)=>{const bad=[];const btn=w.querySelector('.n-exit button').getBoundingClientRect();const a=w.querySelector(':scope > a');
 for(const c of [...a.querySelectorAll(':scope > span')].filter(s=>s.offsetParent&&s.textContent.trim())){
  const r=c.getBoundingClientRect();
  if(r.right>btn.left+0.5&&r.left<btn.right&&r.bottom>btn.top+0.5&&r.top<btn.bottom)bad.push(c.className)}
 return bad}"""


@pytest.mark.parametrize("path", ["/p/{project}", "/"])
@pytest.mark.parametrize("width", [760, 1000, 1200, 1500])
@pytest.mark.parametrize("pane", [True, False])
def test_no_cell_under_row_action(make_page, inst, path, width, pane):
    page = make_page(width=width)
    page.goto(path.format(project=inst["project"]), wait_until="networkidle")
    if not pane:
        close = page.locator(".pj-close")
        if close.count() and close.first.is_visible():
            close.first.click()
    assert page.evaluate(OVERFLOW) == []
    row = page.locator(".nwrap:has(.n-exit button):has(.rx-tg)").locator("visible=true").first  # exit (idle); review is a link
    btn = row.locator(".n-exit button")
    assert not btn.is_visible(), "closed row must not show its action button"
    row.locator(".rx-tg").first.click()
    assert row.get_attribute("data-open") == "1"
    btn.wait_for(state="visible")
    handle = row.element_handle()
    assert page.evaluate(OVERLAP, handle) == []
    assert page.evaluate("(w)=>getComputedStyle(w.querySelector('.n-exit')).opacity", handle) == "1"
    assert page.evaluate(OVERFLOW) == []
