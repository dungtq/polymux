"""Builder preview aside: Page | API switch, endpoints from openapi.json, mock Send shows the example (#159)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.ui

SPEC = {"openapi": "3.0.3", "info": {"title": "Pets"}, "paths": {
    "/pets": {"get": {"summary": "List pets", "responses": {"200": {"description": "OK", "content": {
        "application/json": {"example": [{"id": 1, "name": "Rex"}]}}}}},
              "post": {"summary": "Add a pet", "requestBody": {"content": {"application/json": {"example": {"name": "Tom"}}}},
                       "responses": {"201": {"description": "Created", "content": {"application/json": {
                           "example": {"id": 2, "name": "Tom"}}}}, "400": {"description": "Bad input"}}}},
    "/pets/{id}": {"get": {"summary": "One pet", "parameters": [
        {"name": "id", "in": "path", "required": True, "schema": {"type": "integer", "example": 7}}],
        "responses": {"200": {"description": "OK", "content": {"application/json": {"example": {"id": 7}}}}}}}}}
SHOTS = Path(os.environ.get("PM_UI_SHOTS", "/tmp/pm-dev/ui-out"))


def _until(page, fn, timeout=8000):
    waited = 0
    while not page.evaluate(fn):
        assert waited < timeout, fn
        page.wait_for_timeout(100)
        waited += 100


@pytest.fixture
def spec(inst):
    f = Path(inst["root"]) / "projects/fixproj/openapi.json"
    f.write_text(json.dumps(SPEC))
    try:
        yield f
    finally:
        f.unlink(missing_ok=True)


def _api(page, inst):
    page.goto(f"/a/{inst['agents']['running']}/builder")
    page.wait_for_selector("#builder-frame:not([hidden])")
    page.click("#bld-view button[data-bld-view=api]")
    page.wait_for_selector("#bld-eps button")


@pytest.mark.parametrize("width,theme", [(1400, "light"), (1400, "dark"), (390, "light"), (390, "dark")])
def test_api_pane_lists_and_mock_send(make_page, inst, spec, width, theme):
    page = make_page(width=width, theme=theme)
    if width < 700:
        page.goto(f"/a/{inst['agents']['running']}/builder")
        page.click("label[for=bt-prev]")
        page.click("#bld-view button[data-bld-view=api]")
        page.wait_for_selector("#bld-eps button")
    else:
        _api(page, inst)
    assert page.locator("#bld-stage").is_hidden() and page.locator("#bld-api").is_visible()
    assert page.locator("#bld-eps button").count() == 3
    assert "openapi.json" in page.locator("#bld-apisrc").inner_text()
    assert page.locator("#bld-target").input_value() == "mock" and page.locator("#bld-target").is_disabled()
    page.locator("#bld-eps button", has_text="Add a pet").click()
    assert page.locator("#bld-m").input_value() == "POST" and page.locator("#bld-rpath").input_value() == "/pets"
    assert json.loads(page.locator("#bld-b").input_value()) == {"name": "Tom"}
    page.click("#bld-send")
    _until(page, "() => !document.getElementById('bld-res').hidden")
    assert page.locator("#bld-restx").inner_text().startswith("201")
    assert json.loads(page.locator("#bld-resb").inner_text()) == {"id": 2, "name": "Tom"}
    page.locator("#bld-eps button", has_text="One pet").click()
    assert page.locator("#bld-rpath").input_value() == "/pets/7"  # path parameter example filled in
    page.click("#bld-send")
    _until(page, "() => document.getElementById('bld-resb').textContent.includes('7')")
    page.fill("#bld-rpath", "/nope")
    page.click("#bld-send")
    _until(page, "() => document.getElementById('bld-restx').textContent.startsWith('404')")
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"builder-api-{width}-{theme}.png"))
    assert page._pm_natives == []


def test_view_remembered_and_back_to_page(make_page, inst, spec):
    page = make_page(width=1400, theme="light")
    _api(page, inst)
    page.reload()
    page.wait_for_selector("#bld-eps button")
    assert page.locator("#bld-view button[data-bld-view=api]").get_attribute("aria-pressed") == "true"
    page.click("#bld-view button[data-bld-view=page]")
    assert page.locator("#bld-stage").is_visible() and page.locator("#bld-api").is_hidden()
    assert page.locator("#bld-path").is_visible() and page.locator("#bld-target").is_hidden()


def test_no_spec_shows_hint(make_page, inst):
    page = make_page(width=1400, theme="light")
    page.goto(f"/a/{inst['agents']['running']}/builder")
    page.wait_for_selector("#builder-frame:not([hidden])")
    page.click("#bld-view button[data-bld-view=api]")
    _until(page, "() => document.getElementById('bld-apisrc').textContent === '' && !document.getElementById('bld-apinote').hidden")
    assert "No API found" in page.locator("#bld-apinote").inner_text()
    assert page.locator("#bld-work").is_hidden()
