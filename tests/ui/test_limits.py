"""Plan-limit colours are independent of the context warning (default limits 24/40 are green)."""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.ui

LIMITS = "default"
GREEN = (26, 127, 55), (63, 185, 80)   # --lvl-ok light / dark
WARN = (154, 103, 0), (210, 153, 34)
RED = (207, 34, 46), (248, 81, 73)


def _rgb(s):
    return tuple(int(x) for x in re.findall(r"\d+", s)[:3])


def _colours(page, inst, key, theme_idx):
    page.goto(f"/a/{inst['agents'][key]}")
    page.wait_for_selector(".stats-sum .ts-lim")
    ctx = _rgb(page.evaluate("getComputedStyle(document.querySelector('.stats-sum .ts-ctx')).color"))
    lims = [_rgb(c) for c in page.evaluate(
        "[...document.querySelectorAll('.stats-sum .ts-lim .tl-p')].map(e=>getComputedStyle(e).color)")]
    cls = page.evaluate("document.querySelector('.stats-sum').className")
    return ctx, lims, cls


@pytest.mark.parametrize("theme,idx", [("light", 0), ("dark", 1)])
def test_default_limits_green_while_context_high(make_page, inst, theme, idx):
    page = make_page(theme=theme)
    ctx, lims, cls = _colours(page, inst, "highctx", idx)
    assert "lvl-warn" in cls or "lvl-red" in cls, cls
    assert ctx in (WARN[idx], RED[idx]), ctx
    assert len(lims) == 2 and all(c == GREEN[idx] for c in lims), lims


def test_default_limits_green_low_context(page, inst):
    ctx, lims, cls = _colours(page, inst, "lowctx", 0)
    assert "lvl-warn" not in cls and "lvl-red" not in cls
    assert all(c == GREEN[0] for c in lims)
    pcts = page.evaluate("[...document.querySelectorAll('.stats-sum .ts-lim .tl-p')].map(e=>e.textContent)")
    assert pcts == ["24%", "40%"]
