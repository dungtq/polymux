"""UI suite: Playwright against the DEV pm instance (port 8105, never the real pm on 8093).

Each test module gets a fresh instance (devpm stop/start), so tests that mutate (Exit, Resume, Fork)
cannot disturb other modules. A module may set LIMITS = "default" | "warn" | "red".
Login uses only the throwaway token file of the dev instance.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DEVPM = REPO / "tools/devpm.py"
PORT = int(os.environ.get("PM_UI_PORT", "8105"))
OUT = Path(os.environ.get("PM_UI_OUT", "/tmp/pm-dev/ui-out"))


def pytest_collection_modifyitems(items):
    for it in items:
        if "tests/ui" in str(it.fspath).replace(os.sep, "/"):
            it.add_marker(pytest.mark.ui)


def _devpm(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(DEVPM), *args, "--port", str(PORT)], capture_output=True,
                          text=True, timeout=180, cwd=REPO)


def _last_json(text: str) -> dict:
    dec, i, last = json.JSONDecoder(), 0, None
    while i < len(text):
        if text[i] == "{":
            try:
                obj, end = dec.raw_decode(text, i)
                last, i = obj, end
                continue
            except ValueError:
                pass
        i += 1
    if last is None:
        raise RuntimeError("no JSON in devpm output: " + text[:300])
    return last


@pytest.fixture(scope="module")
def inst(request):
    """Fresh dev instance for this module; stopped afterwards."""
    assert PORT != 8093
    limits = getattr(request.module, "LIMITS", "default")
    _devpm("stop")
    r = _devpm("start", "--limits", limits)
    if r.returncode:
        pytest.fail(f"devpm start failed: {r.stderr.strip() or r.stdout.strip()}")
    info = _last_json(r.stdout)
    info["_token"] = Path(info["token_file"]).read_text().strip()  # never printed
    try:
        yield info
    finally:
        _devpm("stop")


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def make_page(browser, inst, request):
    """Factory: logged-in page. make_page(width=1300, theme='light')."""
    ctxs = []

    def mk(width: int = 1300, height: int = 900, theme: str = "light"):
        ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme,
                                  base_url=inst["url"])
        ctxs.append(ctx)
        ctx.add_init_script(f"try{{if(!localStorage.getItem('pm-theme'))localStorage.setItem('pm-theme','{theme}')}}catch(e){{}}")
        page = ctx.new_page()
        page.set_default_timeout(15000)
        natives = []  # pm never uses alert/confirm/prompt: a native popup is a test failure
        page.on("dialog", lambda d: (natives.append(f"{d.type}: {d.message}"), d.dismiss()))
        page._pm_natives = natives
        page.goto("/login")
        page.fill("#token", inst["_token"])
        page.press("#token", "Enter")
        page.wait_for_load_state("networkidle")
        assert "/login" not in page.url, "login rejected"
        request.node._pm_pages = getattr(request.node, "_pm_pages", []) + [page]
        return page

    yield mk
    for c in ctxs:
        c.close()
    bad = [m for pg in getattr(request.node, "_pm_pages", []) for m in getattr(pg, "_pm_natives", [])]
    assert not bad, f"native browser popup(s) fired, use the in-page modal (static/js/dialog.js): {bad}"


@pytest.fixture
def page(make_page):
    return make_page()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" and rep.failed:
        for n, pg in enumerate(getattr(item, "_pm_pages", [])):
            try:
                OUT.mkdir(parents=True, exist_ok=True)
                name = re.sub(r"[^A-Za-z0-9_.-]+", "_", item.nodeid)
                pg.screenshot(path=str(OUT / f"{name}-{n}.png"), full_page=True)
            except Exception:  # noqa: BLE001
                pass


def tmux_windows(inst: dict) -> list[str]:
    """window names/ids across all sessions on the dev tmux socket (pmtest-dev-8105)."""
    sock = inst["socket"]
    assert sock == f"pmtest-dev-{PORT}"
    r = subprocess.run(["tmux", "-L", sock, "list-windows", "-a", "-F", "#{session_name}:#{window_id}:#{window_name}"],
                       capture_output=True, text=True, timeout=10, env={**os.environ, "TMUX": ""})
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def modal_ok(scope, timeout: int = 15000):
    """Click OK in the in-page confirm modal (dialog.js) of a page or frame_locator; returns its title text."""
    dlg = scope.locator("dialog[data-pm-confirm][open]")
    dlg.wait_for(timeout=timeout)
    title = dlg.locator(".pmdlg-title").inner_text()
    dlg.locator("[data-dlg-ok]").click()
    return title


def show_composer(scope):
    """Phones (and the narrow chat aside iframe) start with the composer hidden (composer-dock.js hides it once on load;
    tapping the chat content shows it). `scope` is a Page or Frame: wait for that initial hide, then tap like a user."""
    scope.wait_for_function("()=>document.readyState==='complete'")
    try:
        scope.wait_for_function("()=>document.body.classList.contains('msgbar-hidden')", timeout=2000)
    except Exception:  # noqa: BLE001  wide viewport: the composer is never hidden
        pass
    scope.evaluate("""()=>{if(document.body.classList.contains('msgbar-hidden'))document.querySelector('main').click()}""")
    scope.wait_for_function("()=>!document.body.classList.contains('msgbar-hidden')")


def chat_frame(page):
    """The loaded chat aside iframe (#vc-frame) as a Frame (frame_locator has no evaluate)."""
    page.wait_for_function("()=>{const f=document.getElementById('vc-frame');return !!(f&&f.contentDocument&&f.contentDocument.body&&f.contentDocument.querySelector('[data-composer=live]'))}")
    return next(f for f in page.frames if f != page.main_frame and "/a/" in f.url)
