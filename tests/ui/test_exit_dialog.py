"""Exit uses an in-page <dialog> (no native confirm), also in the drawer iframe; New Chat lands on the new agent."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from .conftest import OUT, chat_frame, modal_ok, show_composer, tmux_windows

pytestmark = pytest.mark.ui


def _shot(page, name):
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f"exitdlg-{name}.png"))


def _audit(inst, action):
    f = Path(inst["state_dir"]) / "audit.jsonl"
    rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []
    return [r for r in rows if r.get("action") == action]




@pytest.mark.parametrize("theme,width", [("light", 1300), ("dark", 1300), ("light", 390)])
def test_dialog_open_close_ways(make_page, inst, theme, width):
    page = make_page(width=width, theme=theme)
    page.goto(f"/a/{inst['agents']['idle']}")
    show_composer(page)
    opener = page.locator("button[data-dlg-open=exit-dialog]")
    dlg = page.locator("#exit-dialog")
    opener.click()
    assert dlg.is_visible()
    box = dlg.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1
    _shot(page, f"{theme}-{width}")
    page.keyboard.press("Escape")
    assert not dlg.is_visible()
    opener.click()
    dlg.locator("[data-dlg-close]").click()
    assert not dlg.is_visible()
    opener.click()
    page.mouse.click(2, 2)  # backdrop
    assert not dlg.is_visible()
    assert not _audit(inst, "agent_exit") and not _audit(inst, "agent_exit_kill")


def test_dialog_in_drawer_iframe(make_page, inst):
    page = make_page(width=1300, theme="dark")
    page.goto(f"/#chat=/a/{inst['agents']['idle']}")
    page.wait_for_selector("body.chat-open")
    fr = page.frame_locator("#vc-frame")
    show_composer(chat_frame(page))  # the narrow aside starts with the composer hidden
    fr.locator("button[data-dlg-open=exit-dialog]").click()
    assert fr.locator("#exit-dialog").is_visible()
    _shot(page, "drawer-dark")
    fr.locator("#exit-dialog [data-dlg-close]").click()
    assert not fr.locator("#exit-dialog").is_visible()


def test_kill_refused_for_only_pane(make_page, inst):
    page = make_page()
    page.goto(f"/a/{inst['agents']['idle']}")
    before = tmux_windows(inst)
    page.locator("button[data-dlg-open=exit-dialog]").click()
    page.locator("#exit-dialog form[action$='/exitkill'] button").click()
    page.wait_for_selector("[data-composer=live] [data-cmp-flash] .flash.err")
    assert "only pane" in page.locator("[data-composer=live] [data-cmp-flash]").inner_text()
    assert tmux_windows(inst) == before
    assert any(r["result"] == "denied" for r in _audit(inst, "agent_exit_kill"))


def test_exit_and_kill_window(make_page, inst):
    page = make_page()
    aid = inst["agents"]["running"]
    page.goto(f"/a/{aid}")
    sess = next(w.split(":")[0] for w in tmux_windows(inst) if w.startswith("dev-running:"))
    subprocess.run(["tmux", "-L", inst["socket"], "new-window", "-d", "-t", sess + ":", "sleep 600"],
                   check=True, env={**os.environ, "TMUX": ""}, timeout=10)  # the session now has two windows
    two = tmux_windows(inst)
    page.locator("button[data-dlg-open=exit-dialog]").click()
    page.locator("#exit-dialog form[action$='/exitkill'] button").click()
    for _ in range(100):
        if len(tmux_windows(inst)) < len(two):
            break
        page.wait_for_timeout(100)
    assert len(tmux_windows(inst)) == len(two) - 1, (page.locator('[data-composer=live] [data-cmp-flash]').inner_text(), _audit(inst, 'agent_exit_kill'))
    assert not page.locator("#exit-dialog").is_visible()
    assert any(r["result"] == "ok" for r in _audit(inst, "agent_exit_kill"))


def test_exit_and_kill_in_drawer(make_page, inst):
    """Both choices work from the drawer iframe; the modal is rendered inside the embed page."""
    page = make_page(width=1300, theme="light")
    aid = inst["agents"]["waiting"]
    page.goto(f"/#chat=/a/{aid}")
    page.wait_for_selector("body.chat-open")
    fr = page.frame_locator("#vc-frame")
    show_composer(chat_frame(page))  # the narrow aside starts with the composer hidden
    fr.locator("button[data-dlg-open=exit-dialog]").click()
    fr.locator("#exit-dialog form[action$='/exitkill'] button").click()
    fr.locator("[data-composer=live] [data-cmp-flash] .flash.err").wait_for()
    assert "only pane" in fr.locator("[data-composer=live] [data-cmp-flash]").inner_text()
    fr.locator("button[data-dlg-open=exit-dialog]").click()
    fr.locator("#exit-dialog form[action$='/exit'] button").click()
    fr.locator(".agent-head[data-mode='ended']").wait_for(state="attached", timeout=60000)  # embedded head is collapsed
    assert any(r["result"] == "ok" for r in _audit(inst, "agent_exit"))


def test_ntfy_page_has_chat_aside(make_page, inst):
    """/ntfy loads the drawer: an agent link in <main> opens the chat aside on the same page."""
    page = make_page(width=1300, theme="dark")
    page.goto("/ntfy")
    page.evaluate("(id) => { const a = document.createElement('a'); a.id = 'tl'; a.href = '/a/' + id; a.textContent = 'x';"
                  " document.querySelector('main').appendChild(a); }", inst["agents"]["running"])
    page.click("#tl")
    page.wait_for_selector("body.chat-open")
    assert page.url.split("#")[0].rstrip("/").endswith("/ntfy")
