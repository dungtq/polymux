"""Ended page: labelled fields."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui


def _fields(page):
    return {k.inner_text().strip(): v.inner_text().strip() for k, v in
            zip(page.locator(".ended-meta .em-k").all(), page.locator(".ended-meta .em-v").all())}


def test_ended_sid_fields(page, inst):
    page.goto(f"/a/{inst['agents']['ended_sid']}")
    page.wait_for_selector(".ended-meta #autoexit-note", state="attached")
    page.wait_for_selector(".ended-meta .em-row:has(.em-k:text-is('Session'))", timeout=10000)
    f = _fields(page)
    for k in ("Project", "Directory", "Ended", "First seen", "Model", "Session"):
        assert k in f, (k, list(f))
    assert f["Project"] == "fixproj"
    assert f["Directory"].endswith("/projects/fixproj")
    assert f["Model"]
    assert f["Session"] == "00000000"


def test_ended_nosid_has_no_session_row(page, inst):
    page.goto(f"/a/{inst['agents']['ended_nosid']}")
    page.wait_for_selector(".ended-meta .em-row")
    f = _fields(page)
    assert {"Project", "Directory", "Ended"} <= set(f)
    assert "Session" not in f
