"""#185: the sidebar status dots follow agent state events without a reload (side.js fetches /side/dots)."""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.ui


def test_project_dot_follows_agent_state(make_page, inst):
    pg = make_page(width=1400, theme="light")
    pg.goto("/p/fixproj")
    row = pg.locator("#v5list .vs-row[data-p='fixproj']").first
    row.wait_for()
    pg.route("**/side/dots?kind=projects*", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps({"fixproj": "failed"})))
    pg.evaluate("()=>document.dispatchEvent(new CustomEvent('pm:agent_state'))")
    pg.wait_for_function("()=>{const d=document.querySelector(\"#v5list .vs-row[data-p='fixproj'] > .dot\"); return d && d.classList.contains('dot-failed')}")
    pg.unroute("**/side/dots?kind=projects*")
    pg.route("**/side/dots?kind=projects*", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps({"fixproj": ""})))
    pg.evaluate("()=>document.dispatchEvent(new CustomEvent('pm:agent_state'))")
    pg.wait_for_function("()=>!document.querySelector(\"#v5list .vs-row[data-p='fixproj'] > .dot\")")
