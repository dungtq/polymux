"""Quick chat v2 front end against the dev instance. The /qchat/* data routes are stubbed with Playwright
(fixed JSON), so this checks the popup wiring (history, options, send body, conv frame, ?qchat=, menus) independent
of the backend. Message text here is fixture text only. Set PM_QC_SHOTS=<dir> to also write screenshots."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from .conftest import modal_ok

pytestmark = pytest.mark.ui

NOW = time.time()
_MID = time.mktime(time.localtime(NOW)[:3] + (0, 0, 0, 0, 0, -1))
T = min(1.0, (NOW - _MID - 1) / 1000)  # "Today" rows stay after local midnight even when the test runs at 00:05
SHOTS = os.environ.get("PM_QC_SHOTS")
QIN = "#qchat [data-composer=qchat] [data-cmp-input]"


def _conv(i, title, profile="none", ago=60, **kw):
    c = {"id": i, "title": title, "session": f"s{i}", "model": "sonnet", "profile": profile, "folder": "" if profile == "none" else "app",
         "mcp": [], "skills": [], "created": NOW - ago, "updated": NOW - ago, "msgs": 2, "archived": False}
    c.update(kw)
    return c


CONVS = [
    _conv(1, "Why does the dev instance fail on 8095?", "read", 300 * T, mcp=["tavily"], skills=["frr"]),
    _conv(2, "Regex for ISO dates with optional time zone", "none", 600 * T, model="haiku"),
    _conv(3, "Rename the qchat.db columns before release", "full", 900 * T),
    _conv(4, "nginx: proxy SSE without buffering", "none", 86400 * 3),
    _conv(5, "Draft the pm PLAN.md security section", "none", 86400 * 40, archived=True, model="opus"),
]
OPTIONS = {
    "models": ["sonnet", "opus", "haiku"], "profiles": ["none", "read", "full"], "default_profile": "none",
    "folders": ["fixproj", "app", "web"], "default_folder": "fixproj", "skills_in_none": True, "nav_search": True,
    "mcp": [{"name": "tavily", "source": "manual", "type": "http", "on": True}, {"name": "context7", "source": "~/.claude.json", "type": "stdio", "on": False}],
    "skills": [{"name": "frr", "source": "~/.claude/skills", "description": "FRR", "on": True}, {"name": "junos", "source": "~/.claude/skills", "description": "", "on": False}],
}
TOOLS = [{"id": "t1", "name": "Read", "fam": "read", "line": "src/pm/core/config.py", "text": "port: int = 8093", "err": False}]
MSGS = [
    {"id": 1, "role": "user", "text": "Why does it fail?", "html": "", "tools": [], "cost": None, "model": "", "ts": NOW - 300},
    {"id": 2, "role": "assistant", "text": "Stale pid.", "html": "<p>Stale <code>pid</code>.</p>", "tools": TOOLS, "cost": 0.031, "model": "sonnet", "ts": NOW - 290},
]


def _sse(ev, data):
    return f"event: {ev}\ndata: {json.dumps(data)}\n\n"


def stub(page, sent):
    def j(route, obj, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(obj))

    def convs(route):
        q = parse_qs(re.sub(r"^[^?]*\??", "", route.request.url)).get("q", [""])[0].lower()
        j(route, {"convs": [c for c in CONVS if q in c["title"].lower()]})

    def one(route):
        m = re.search(r"/qchat/convs/(\d+)$", route.request.url)
        c = next((x for x in CONVS if x["id"] == int(m.group(1))), None) if m else None
        j(route, {"conv": c, "messages": MSGS if c else []}) if c else j(route, {}, 404)

    def post(route):
        sent.append((route.request.url.split("?")[0], parse_qs(route.request.post_data or "", keep_blank_values=True)))
        if route.request.url.endswith("/qchat/send"):
            body = (_sse("conv", {"id": 9, "title": "Hello there"}) + _sse("delta", {"t": "Hi"}) +
                    _sse("done", {"session": "s9", "html": "<p>Hi</p>", "tools_n": 0, "cost": 0.002}))
            route.fulfill(status=200, content_type="text/event-stream", body=body)
        else:
            route.fulfill(status=200, content_type="application/json", body="{}")

    page.route(re.compile(r".*/qchat/options$"), lambda r: j(r, OPTIONS))
    page.route(re.compile(r".*/qchat/convs(\?.*)?$"), convs)
    page.route(re.compile(r".*/qchat/convs/\d+$"), one)
    page.route(re.compile(r".*/qchat/(send|convs/\d+/(rename|delete)|delete-all)$"), post)
    page.route(re.compile(r".*/qchat/convs/\d+/export$"), lambda r: r.fulfill(status=200, content_type="text/markdown", body="# t\n"))


def shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def open_popup(page):
    page.evaluate("localStorage.removeItem('pm.qchat.v2')")
    page.locator("[data-qchat-open]").first.click()
    page.wait_for_selector("#qchat:not([hidden])")
    page.wait_for_function("() => document.querySelector('#qc-tools').options.length > 1")


def test_options_fill_controls_and_send_body(page):
    sent = []
    stub(page, sent)
    page.goto("/")
    open_popup(page)
    assert page.locator("#qc-model option").all_inner_texts() == ["default", "sonnet", "opus", "haiku"]
    assert page.locator(".qc-fold").is_hidden()  # folder only for read|full
    page.select_option("#qc-tools", "full")
    assert page.locator(".qc-fold").is_visible()
    assert page.locator(".qc-note.qc-warn").is_visible()
    assert page.locator(".qc-skl .qc-mcpn").inner_text() == "1"  # frr is on by default
    page.select_option("#qc-dir", "web")
    page.fill(QIN, "Hello there")
    shot(page, "send-before")
    page.press(QIN, "Enter")
    page.wait_for_selector(".qc-a.md")
    url, form = next(s for s in sent if s[0].endswith("/qchat/send"))
    assert form["profile"] == ["full"] and form["folder"] == ["web"] and form["conv"] == [""]
    assert form["mcp"] == ["tavily"] and form["skills"] == ["frr"] and form["text"] == ["Hello there"]
    assert page.locator(".qc-tt").inner_text() == "Hello there"  # conv frame sets the title
    assert page.locator("#qc-dir").is_disabled()  # folder locked once the conversation has a message
    page.fill(QIN, "again")
    page.press(QIN, "Enter")
    page.wait_for_function("() => document.querySelectorAll('.qc-a.md').length == 2")
    assert [s for s in sent if s[0].endswith("/qchat/send")][1][1]["conv"] == ["9"]
    shot(page, "send-after")


def test_history_dropdown_groups_and_open(page):
    stub(page, [])
    page.goto("/")
    open_popup(page)
    page.click("[data-qc-hist]")
    hp = page.locator(".qc-hist")
    hp.wait_for()
    assert hp.locator(".qc-grp").all_inner_texts()[:2] == ["Today", "Last 7 days"]
    assert hp.locator(".qc-row").count() == len(CONVS)
    assert hp.locator(".qc-tag.t-full").count() == 1 and hp.locator(".qc-tag.t-old").count() == 1
    shot(page, "history")
    hp.locator(".qc-row").first.click()
    page.wait_for_selector(".qc-step")
    assert hp.is_hidden()
    shot(page, "loaded")
    assert page.locator("#qc-tools").input_value() == "read" and page.locator("#qc-dir").is_disabled()
    assert page.locator(".qc-m.qc-a.md code").inner_text() == "pid"
    assert "src/pm/core/config.py" in page.locator(".st-l").inner_text()
    assert "0.031" in page.locator(".qc-meta").inner_text()
    page.click("[data-qc-hist]")
    page.locator(".qc-hist .qc-row").nth(4).click()  # archived
    page.wait_for_function("() => !document.querySelector('.qc-gone').hidden")


def test_query_param_opens_conversation(page):
    stub(page, [])
    page.goto("/?qchat=3")
    page.wait_for_selector("#qchat:not([hidden])")
    page.wait_for_selector(".qc-m.qc-u")
    assert page.locator(".qc-tt").inner_text() == CONVS[2]["title"]
    assert "qchat=" not in page.url


def test_full_window_side_list_and_row_menu(page):
    sent = []
    stub(page, sent)
    page.goto("/")
    open_popup(page)
    page.click(".qc-max")
    assert page.locator(".qc-side").is_visible() and page.locator("[data-qc-hist]").is_hidden()
    page.locator(".qc-side .qc-row").nth(1).hover()
    page.locator(".qc-side .qc-row").nth(1).locator(".qc-rx").click()
    assert page.locator(".qc-rmenu").is_visible()
    shot(page, "full-menu")
    page.click(".qc-rmenu [data-qc-act=rename]")
    dlg = page.locator("dialog[data-pm-confirm][open]")
    dlg.locator("input").fill("Renamed")
    modal_ok(page)
    assert any(u.endswith("/qchat/convs/2/rename") and f["title"] == ["Renamed"] for u, f in sent)
    page.locator(".qc-side .qc-row").nth(1).locator(".qc-rx").click(force=True)
    page.click(".qc-rmenu [data-qc-act=delete]")
    modal_ok(page)
    page.wait_for_timeout(200)
    assert any(u.endswith("/qchat/convs/2/delete") for u, f in sent)


def test_slash_menu_lists_ticked_skills(page):
    stub(page, [])
    page.goto("/")
    open_popup(page)
    page.fill(QIN, "use /f")
    page.locator("#qchat .slash-list .slash-opt").first.wait_for()  # slash.js on the composer (data-slash: the ticked skills)
    assert page.locator("#qchat .slash-list .slash-cmd").all_inner_texts() == ["/frr"]
    page.press(QIN, "Tab")
    assert page.input_value(QIN) == "use /frr "


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_narrow_no_overflow(make_page, theme):
    page = make_page(width=390, height=800, theme=theme)
    stub(page, [])
    page.goto("/")
    open_popup(page)
    page.select_option("#qc-tools", "read")
    page.click("[data-qc-hist]")
    page.locator(".qc-hist .qc-row").first.wait_for()
    shot(page, f"narrow-{theme}-hist")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert page.evaluate("(() => { const r = document.querySelector('#qchat').getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth + 1; })()")


def _send_key(page, key):
    ok = page.evaluate("""k => { const b = new FormData(); b.set('paste_lines', '10'); b.set('exts', 'png,jpg,md,txt');
        b.set('send_key', k); return pm.fetch('/settings/composer', {method: 'POST', body: b, csrf: 'both'}).then(r => r.ok); }""", key)
    assert ok


def test_enter_follows_the_send_key(page):
    """Quick chat is the shared composer: Settings > Composer decides (mod-enter: Enter is a newline, Ctrl+Enter sends)."""
    sent = []
    stub(page, sent)
    page.goto("/settings")
    _send_key(page, "mod-enter")
    try:
        page.goto("/")
        open_popup(page)
        root = page.locator("#qchat [data-composer=qchat]")
        assert root.get_attribute("data-send-key") == "mod-enter"
        assert root.locator("[data-cmp-send]").get_attribute("title") == "Send message (Ctrl+Enter)"
        page.fill(QIN, "line one")
        page.press(QIN, "End")
        page.press(QIN, "Enter")
        page.wait_for_timeout(300)
        assert page.input_value(QIN) == "line one\n" and not [s for s in sent if s[0].endswith("/qchat/send")]
        page.press(QIN, "Control+Enter")
        page.wait_for_selector(".qc-a.md")
        assert [s for s in sent if s[0].endswith("/qchat/send")][0][1]["text"] == ["line one"]
        assert page.input_value(QIN) == ""  # the box (and its draft) cleared like a sent message
    finally:
        _send_key(page, "enter")
    page.goto("/")
    open_popup(page)
    assert page.locator("#qchat [data-composer=qchat]").get_attribute("data-send-key") == "enter"


def test_send_turns_into_stop_while_the_answer_runs(page):
    stub(page, [])
    page.goto("/")
    open_popup(page)
    send = page.locator("#qchat [data-cmp-send]")
    assert send.is_disabled() and "is-stop" not in (send.get_attribute("class") or "")  # empty box: Send is off
    page.fill(QIN, "x")
    assert send.is_enabled()
    page.fill(QIN, "")
    page.evaluate("() => pm.composer.running(document.querySelector('#qchat [data-composer]'), true)")  # what run() does
    assert "is-stop" in send.get_attribute("class") and send.get_attribute("title") == "Stop the answer" and send.is_enabled()
    page.evaluate("() => pm.composer.running(document.querySelector('#qchat [data-composer]'), false)")
    assert "is-stop" not in send.get_attribute("class")


def test_sub_popups_are_popovers(page):
    """History dropdown, row menu and the Skills picker: pm.popover layers (placed fixed, Esc closes only the top one)."""
    stub(page, [])
    page.goto("/")
    open_popup(page)
    page.click("[data-qc-hist]")
    hp = page.locator(".qc-hist")
    hp.locator(".qc-row").first.wait_for()
    assert hp.evaluate("e => e.classList.contains('pop') && e.style.position === 'fixed'")
    hb = page.locator("[data-qc-hist]").bounding_box()
    box = hp.bounding_box()
    assert abs(box["y"] - (hb["y"] + hb["height"])) < 12 and abs(box["x"] - hb["x"]) < 12  # under its button
    hp.locator(".qc-row").first.hover()
    hp.locator(".qc-row").first.locator(".qc-rx").click()
    menu = page.locator(".qc-rmenu")
    assert menu.is_visible() and page.evaluate("() => document.activeElement.matches('.qc-rmenu [role=menuitem]')")
    page.keyboard.press("Escape")  # the row menu only
    assert menu.is_hidden() and hp.is_visible() and page.locator("#qchat").is_visible()
    page.keyboard.press("Escape")  # then the dropdown
    assert hp.is_hidden() and page.locator("#qchat").is_visible()
    page.select_option("#qc-tools", "read")
    page.click(".qc-skl .qc-mcpbtn")
    pick = page.locator(".qc-skl .qc-mcppop")
    assert pick.is_visible() and page.evaluate("() => document.activeElement.matches('.qc-sksearch')")
    assert pick.bounding_box()["y"] < page.locator(".qc-skl .qc-mcpbtn").bounding_box()["y"]  # opens upwards
    page.locator(".qc-log").click()  # outside
    assert pick.is_hidden()
    page.keyboard.press("Escape")  # nothing open: the panel closes
    assert not page.locator("#qchat").is_visible()


def test_btw_use_in_composer_inserts_into_the_agent_composer(page, inst):
    """btw on an agent page: the answer's "Use in composer" puts the text into the agent's composer (never quick chat's own
    box, which is a composer too), unsent."""
    sent = []
    stub(page, sent)
    aid = inst["agents"]["idle"]
    page.goto(f"/a/{aid}")
    page.wait_for_load_state("networkidle")
    live = page.locator("[data-composer=live] [data-cmp-input]")
    live.fill("before ")
    page.evaluate("id => pm.qchat.aside(id, 'fixture agent', 'what is x?')", aid)
    use = page.locator("#qchat .qc-use")
    use.wait_for()
    body = next(f for u, f in sent if u.endswith("/qchat/send"))
    assert body["agent"] == [aid] and body["text"] == ["what is x?"]
    use.click()
    assert live.input_value() == "before Hi"  # at the cursor (the end), the plain answer text
    assert page.input_value(QIN) == ""  # quick chat's own box untouched
    assert not [u for u, f in sent if u.endswith("/message")]  # not sent to the agent
