"""Row colours: hover = --hover, the row open in the drawer = --accent-soft, nothing else filled; the selection
survives in-place (nodiff) refreshes and clears on close. Same on the tmux session page and the inbox."""
from __future__ import annotations

import os
import subprocess

import pytest

pytestmark = pytest.mark.ui

ROWS = """()=>[...document.querySelectorAll('main .tx-row.tx-w')].map(r=>({cls:r.className,
  bg:getComputedStyle(r).backgroundColor,blue:getComputedStyle(r).backgroundColor!=='rgba(0, 0, 0, 0)'}))"""


def _session_with_two_windows(inst):
    """Add a second (active) window to the first agent's session -> row 1 agent, row 2 active window."""
    sock = inst["socket"]
    env = {**os.environ, "TMUX": ""}
    out = subprocess.run(["tmux", "-L", sock, "list-sessions", "-F", "#{session_id}\t#{session_name}"],
                         capture_output=True, text=True, env=env, timeout=10).stdout.splitlines()
    name = out[0].split("\t")[1]
    for n in out:
        s, nm = n.split("\t")
        if nm.startswith("dev-"):
            name = nm
            break
    subprocess.run(["tmux", "-L", sock, "new-window", "-t", name + ":", "-n", "extra"], env=env, timeout=10, check=True)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_single_highlight(make_page, inst, theme):
    pg = make_page(width=1300, theme=theme)
    _session_with_two_windows(inst)
    pg.goto("/tmux")
    pg.wait_for_load_state("networkidle")
    href = pg.eval_on_selector_all("main a[href^='/tmux/']", "e=>e.map(a=>a.getAttribute('href'))")
    target = None
    for h in dict.fromkeys(href):
        pg.goto(h)
        pg.wait_for_load_state("networkidle")
        if pg.locator("main .tx-row.tx-w").count() >= 2 and pg.locator("main .tx-row a.tx-agent").count():
            target = h
            break
    assert target, "no session with an agent and two windows"
    rows = pg.evaluate(ROWS)
    assert sum(r["blue"] for r in rows) == 0

    agent_row = pg.locator("main .tx-row.tx-w", has=pg.locator("a.tx-agent")).first
    agent_row.locator("a.tx-agent").click()
    pg.wait_for_selector("body.chat-open")
    rows = pg.evaluate(ROWS)
    lit = [r for r in rows if r["blue"]]
    assert len(lit) == 1 and "v5-active" in lit[0]["cls"], rows  # only the row open in the drawer
    pg.screenshot(path=f"/tmp/pm-dev/row-focus-{theme}.png")

    # in-place refresh (nodiff resets class to the server's): the selection comes back, still one row
    pg.evaluate("""()=>{document.querySelectorAll('.v5-active').forEach(e=>e.classList.remove('v5-active'));
      document.querySelector('main').dispatchEvent(new CustomEvent('nodiff:swapped',{bubbles:true}))}""")
    lit = [r for r in pg.evaluate(ROWS) if r["blue"]]
    assert len(lit) == 1 and "v5-active" in lit[0]["cls"]

    # click away / close: selection cleared, the active window is the only highlight again
    pg.locator("#vc-close").click()
    pg.wait_for_function("!document.body.classList.contains('chat-open')")
    rows = pg.evaluate(ROWS)
    assert not any("v5-active" in r["cls"] for r in rows)
    assert not [r for r in rows if r["blue"]]


def test_inbox_selected_row_is_filled(make_page, inst):
    pg = make_page(width=1300, theme="light")
    pg.goto("/")
    pg.wait_for_load_state("networkidle")
    row = pg.locator("main a.nrow").first
    row.hover()
    hover = row.evaluate("e=>getComputedStyle(e).backgroundColor")
    assert hover != "rgba(0, 0, 0, 0)"
    row.click()
    pg.wait_for_selector("body.chat-open")
    pg.mouse.move(5, 500)
    sel = pg.evaluate("()=>{const e=document.querySelector('main a.nrow.v5-active');return e&&getComputedStyle(e).backgroundColor}")
    assert sel and sel != "rgba(0, 0, 0, 0)" and sel != hover


@pytest.mark.parametrize("path", ["/", "/p/{project}"])
def test_pin_moves_row_and_open_recap_survives(make_page, inst, path):
    """#161/#162: pinning moves the row to the Pinned group (across lists: Idle fold -> top list) and back; the live
    morph moves the same node, so an open recap is neither blanked nor fetched again (nodiff.js keyed pool)."""
    pg = make_page(width=1400, theme="light")
    fetches = []
    pg.on("request", lambda r: fetches.append(r.url) if "/recap-row" in r.url else None)
    pg.goto(path.replace("{project}", inst["project"]))
    pg.wait_for_load_state("networkidle")
    aid = inst["agents"]["idle"]
    sel = f".nwrap[data-key='w-{aid}']"
    in_idle = f"()=>{{const e=document.querySelector(\"{sel}\");return !!e&&!!e.closest('.idle-fold')}}"
    assert pg.evaluate(in_idle)
    pg.locator(f"{sel} .rx-tg").click()
    pg.wait_for_function(f"()=>document.querySelector(\"{sel} .n-recap\").getAttribute('data-loaded')")
    pg.wait_for_load_state("networkidle")
    n = len(fetches)
    assert n >= 1
    pg.evaluate(f"()=>{{window.__recap=document.querySelector(\"{sel} .n-recap\")}}")
    pg.locator(sel).hover()
    pg.locator(f"{sel} .rowpin button").click()
    pg.wait_for_function(f"()=>{{const e=document.querySelector(\"{sel}\");return !!e&&!e.closest('.idle-fold')}}")
    prev = pg.evaluate(f"()=>{{const p=document.querySelector(\"{sel}\").previousElementSibling;return p&&p.dataset.key}}")
    assert prev == "g-pinned"
    assert pg.evaluate(f"()=>document.querySelector(\"{sel} .n-recap\")===window.__recap")  # same node, still open
    assert pg.evaluate(f"()=>document.querySelector(\"{sel}\").hasAttribute('data-open')")
    assert pg.locator(f"{sel} .rowpin button").get_attribute("aria-pressed") == "true"
    pg.locator(f"{sel} .rowpin button").click()  # unpin: back into the Idle fold
    pg.wait_for_function(in_idle)
    assert pg.evaluate(f"()=>document.querySelector(\"{sel} .n-recap\")===window.__recap")
    pg.wait_for_timeout(500)
    assert len(fetches) == n, fetches  # never re-fetched
