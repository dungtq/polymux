"""Red limits (85/92) with a high-context agent: limit percents are red, whatever the context level."""
from __future__ import annotations

import pytest

from .test_limits import RED, _colours

pytestmark = pytest.mark.ui

LIMITS = "red"


def test_red_limits_are_red(page, inst):
    ctx, lims, cls = _colours(page, inst, "lowctx", 0)
    assert len(lims) == 2 and all(c == RED[0] for c in lims), lims
    assert "lvl-red" not in cls  # context of lowctx stays ok: limits do not recolour the summary
