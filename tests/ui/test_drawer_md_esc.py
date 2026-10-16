"""Esc with the Markdown popup open in the chat aside closes the popup only; the next Esc closes the aside."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui

CHAT = "#chat=/a/devfix"


def _open_md(pg, path):
    pg.wait_for_function("()=>{const f=document.getElementById('vc-frame'); return f && f.contentDocument && f.contentDocument.readyState==='complete' && f.contentWindow.location.pathname.startsWith('/a/')}")
    fr = pg.locator("#vc-frame").element_handle().content_frame()
    fr.evaluate("(p) => parent.postMessage({pm: 'drawer', act: 'md', id: 'devfix', path: p, y: 120}, location.origin)", path)  # what chat-md.js sends
    pg.wait_for_selector("#v5md:not([hidden])")


def test_esc_closes_md_popup_then_aside(make_page, inst, tmp_path):
    md = tmp_path / "notes.md"
    md.write_text("# Notes\n\nhello\n")
    pg = make_page(width=1400, theme="light")
    pg.goto("/p/fixproj" + CHAT)
    pg.wait_for_selector("body.chat-open")
    _open_md(pg, str(md))
    pg.locator("#v5md").click()  # focus in the parent page, where the bug was
    pg.keyboard.press("Escape")
    pg.wait_for_selector("#v5md", state="hidden")
    assert pg.evaluate("()=>document.body.classList.contains('chat-open')"), "Esc on the md popup closed the aside too"
    pg.keyboard.press("Escape")
    pg.wait_for_function("()=>!document.body.classList.contains('chat-open')")


def test_esc_leaves_full_screen_md_first(make_page, inst, tmp_path):
    md = tmp_path / "full.md"
    md.write_text("# Full\n")
    pg = make_page(width=1400, theme="dark")
    pg.goto("/p/fixproj" + CHAT)
    pg.wait_for_selector("body.chat-open")
    _open_md(pg, str(md))
    pg.evaluate("()=>document.getElementById('v5md').classList.add('is-full')")
    pg.locator("#v5md").click()
    pg.keyboard.press("Escape")
    assert pg.evaluate("()=>!document.getElementById('v5md').classList.contains('is-full')")
    assert pg.evaluate("()=>!document.getElementById('v5md').hidden and document.body.classList.contains('chat-open')".replace(" and ", " && "))
