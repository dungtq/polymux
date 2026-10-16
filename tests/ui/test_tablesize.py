"""Markdown table columns resize by dragging a header edge (static/js/tablesize.js); double-click resets."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui

TABLE = ("<div class='md' id='t-md'><div class='md-table'><table><thead><tr><th>File</th><th>Role</th><th>Notes</th></tr>"
         "</thead><tbody><tr><td><code>src/pm/static/js/tablesize.js</code></td><td>resize</td>"
         "<td>Drag a header edge to change the column width.</td></tr></tbody></table></div></div>")


def _inject(page):
    page.evaluate("""(html) => { const w = document.createElement('div'); w.innerHTML = html;
        w.style.cssText = 'position:fixed;top:80px;left:20px;width:340px;z-index:100;background:var(--bg)';
        document.body.appendChild(w); }""", TABLE)


def _widths(page):
    return page.evaluate("[...document.querySelectorAll('#t-md th')].map(t => t.getBoundingClientRect().width)")


@pytest.mark.parametrize("theme,width", [("light", 1300), ("dark", 390)])
def test_mouse_drag_resizes_and_dblclick_resets(make_page, inst, theme, width):
    page = make_page(width=width, theme=theme)
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(f"/a/{inst['agents']['idle']}")
    _inject(page)
    before = _widths(page)
    box = page.locator("#t-md th").nth(1).bounding_box()
    x, y = box["x"] + box["width"] - 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    assert "ts-hot" in page.locator("#t-md th").nth(1).get_attribute("class")
    page.mouse.down()
    page.mouse.move(x + 20, y, steps=4)
    page.mouse.move(x + 40, y, steps=4)
    page.mouse.up()
    after = _widths(page)
    assert page.locator("#t-md table.ts-fixed").count() == 1
    assert after[1] == pytest.approx(before[1] + 40, abs=3)
    assert after[0] == pytest.approx(before[0], abs=2)  # the other columns keep their width
    page.mouse.move(x + 40, y)
    page.mouse.dblclick(x + 40, y)
    assert page.locator("#t-md table.ts-fixed").count() == 0
    assert _widths(page) == pytest.approx(before, abs=2)
    assert not errs


def test_touch_drag_resizes(make_page, inst):
    page = make_page(width=390, theme="light")
    page.goto(f"/a/{inst['agents']['idle']}")
    _inject(page)
    before = _widths(page)[0]
    page.evaluate("""() => { const th = document.querySelectorAll('#t-md th')[0], r = th.getBoundingClientRect();
        const x = r.right - 4, y = r.top + r.height / 2;
        const ev = (t, cx) => th.dispatchEvent(new PointerEvent(t, {bubbles: true, pointerId: 9, pointerType: 'touch',
            isPrimary: true, clientX: cx, clientY: y, button: 0, buttons: 1}));
        ev('pointerdown', x); ev('pointermove', x - 20); ev('pointermove', x - 50); ev('pointerup', x - 50); }""")
    assert _widths(page)[0] == pytest.approx(before - 50, abs=3)


def test_no_error_on_page_without_tables(make_page, inst):
    page = make_page(width=1300, theme="light")
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto("/")
    page.mouse.move(200, 200)
    page.mouse.down()
    page.mouse.move(260, 200)
    page.mouse.up()
    page.mouse.dblclick(260, 200)
    assert not errs
