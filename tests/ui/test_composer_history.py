"""Composer history (composer.js): ArrowUp in an empty composer recalls this agent's earlier messages,
ArrowDown goes forward and back to the draft, Esc restores the draft, an edit ends browsing.
Types into the DEV composer only; never sends."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui

TA = "[data-composer=live] textarea[name=text]"


def _history(page) -> list[str]:
    raw = page.evaluate("""()=>Array.from(document.querySelectorAll('#ci .ci-user .chat-copy[data-raw]'))
        .map(b=>b.getAttribute('data-raw')||'')""")
    out: list[str] = []
    for s in raw:
        if s.strip() and (not out or out[-1] != s):
            out.append(s)
    return out


def test_arrow_up_down_recall(page, inst):
    page.goto(f"/a/{inst['agents']['running']}")
    page.wait_for_selector("#ci .ci-user .chat-copy[data-raw]")
    hist = _history(page)
    assert len(hist) >= 2 and not any("\n" in h for h in hist[-2:]), "dev fixture: need two one-line user messages"
    ta = page.locator(TA)
    ta.fill("")
    ta.focus()
    ta.press("ArrowUp")
    assert ta.input_value() == hist[-1]
    caret = page.evaluate("()=>{const t=document.querySelector('[data-composer=live] textarea[name=text]');return [t.selectionStart,t.value.length]}")
    assert caret[0] == caret[1]
    ta.press("ArrowUp")
    assert ta.input_value() == hist[-2]
    ta.press("ArrowDown")
    assert ta.input_value() == hist[-1]
    ta.press("ArrowDown")  # past the newest: the (empty) draft
    assert ta.input_value() == ""
    ta.fill("")
    ta.press("ArrowUp")
    ta.press("Escape")
    assert ta.input_value() == ""
    # an edit ends browsing: ArrowUp in a non-empty box is a plain caret move
    ta.press("ArrowUp")
    ta.type("x")
    typed = ta.input_value()
    ta.press("ArrowUp")
    assert ta.input_value() == typed
    ta.fill("")  # leave no draft behind; nothing was sent
