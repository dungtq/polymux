"""New Chat clicked for real: full agent page and drawer iframe, in-page confirm accepted (never a native popup),
audit says ok, and the page lands on the NEW agent (a new session id) instead of the old id's ended view."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from .conftest import chat_frame, modal_ok, show_composer

pytestmark = pytest.mark.ui


def _audit(inst):
    f = Path(inst["state_dir"]) / "audit.jsonl"
    rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []
    return [r for r in rows if r.get("action") == "agent_newchat"]


def _click(scope):
    btn = scope.locator("button:text-is('New Chat')")
    btn.wait_for()
    btn.click()
    return modal_ok(scope)


def test_newchat_full_page_lands_on_new_agent(page, inst):
    old = inst["agents"]["protected"]  # protected sessions are allowed (agents live there)
    page.goto(f"/a/{old}")
    assert "/clear" in _click(page)
    page.wait_for_url(re.compile(r"/a/(?!%s$)[a-z0-9-]+$" % re.escape(old)), timeout=20000)
    new = page.url.rsplit("/", 1)[1]
    page.wait_for_selector(".agent-head[data-mode='live']")
    assert page.locator(".agent-head").get_attribute("data-agent") == new != old
    assert any(r["result"] == "ok" and r["target"] == old for r in _audit(inst))


def test_newchat_in_drawer_iframe_follows_new_id(page, inst):
    old = inst["agents"]["running"]
    page.goto(f"/#chat=/a/{old}")
    page.wait_for_selector("body.chat-open")
    fr = page.frame_locator("#vc-frame")
    show_composer(chat_frame(page))  # the narrow aside starts with the composer hidden
    assert "/clear" in _click(fr)
    page.wait_for_function("(old) => /^#chat=\\/a\\//.test(location.hash) && !location.hash.endsWith(old)", arg=old, timeout=20000)
    new = page.evaluate("location.hash").rsplit("/", 1)[1]
    assert new != old
    assert page.locator("#vc-title").inner_text().strip() == new
    assert page.locator("#vc-full").get_attribute("href") == f"/a/{new}"
    head = fr.locator(".agent-head")
    assert head.get_attribute("data-agent") == new and head.get_attribute("data-mode") == "live"
    assert "embed" in page.frame_locator("#vc-frame").locator("body").get_attribute("class")
