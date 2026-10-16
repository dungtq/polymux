#!/usr/bin/env python3
"""uishot: read-only screenshot + sanity check of the pm DEV instance (never port 8093).

Usage: uv run tools/uishot.py [routes...] [--themes light,dark] [--widths 1300,390]
       [--out DIR] [--port N] [--smoke] [--url URL --token-file FILE] [--ui NAME] [--no-dev-badge]
Routes may use {agent:<key>} and {project} (from instance.json), e.g. /a/{agent:running}.
Logs in once with the dev token file, then only navigates; never types or clicks on pages.
--ui NAME sets the cookie pm_ui=NAME (host of the URL, path /) to shoot another UI, e.g. a theme.
--no-dev-badge hides the dev instance badge (README and site screenshots).
Without --url it runs `devpm.py start --port N`, which reuses (or restarts with its own --pm-bin) the instance.
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

SMOKE = ["/", "/a/{agent:running}", "/a/{agent:ended_sid}", "/tmux", "/agents", "/p/{project}"]
FORBIDDEN_PORT = 8093
# --no-dev-badge: hide the dev badge (classic, dt) through CSSOM; pm's CSP (style-src 'self') blocks an inline <style>
DEV_BADGE_SEL = ".x-dev,.devtag"
HIDE_JS = "s => document.querySelectorAll(s).forEach(e => e.style.setProperty('display', 'none', 'important'))"
HERE = Path(__file__).resolve().parent


# ---- pure helpers -------------------------------------------------------

def slug(route: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", route).strip("-")
    return s or "root"


def expand_route(route: str, info: dict) -> str:
    def agent(m: re.Match) -> str:
        key = m.group(1)
        agents = info.get("agents") or {}
        if key not in agents:
            raise KeyError(f"unknown agent key {key!r} (have: {', '.join(sorted(agents))})")
        return str(agents[key])

    out = re.sub(r"\{agent:([A-Za-z0-9_\-]+)\}", agent, route)
    if "{project}" in out:
        if not info.get("project"):
            raise KeyError("instance.json has no project")
        out = out.replace("{project}", str(info["project"]))
    return out


def has_h_overflow(scroll_width: int, inner_width: int) -> bool:
    return scroll_width > inner_width + 1


def filter_console(items: list[dict]) -> list[str]:
    """items: {kind: console|pageerror|response, text, url?, status?}; returns problem strings."""
    out = []
    for it in items:
        kind = it.get("kind")
        url = it.get("url") or ""
        if "favicon" in url or "favicon" in (it.get("text") or ""):
            continue
        if kind == "response":
            if int(it.get("status", 0)) >= 400:
                out.append(f"HTTP {it['status']} {url}")
        elif kind == "console":
            if it.get("type") == "error":
                out.append(f"console.error: {it.get('text', '')}")
        elif kind == "pageerror":
            out.append(f"pageerror: {it.get('text', '')}")
    return out


def parse_set_cookie(header: str | None) -> dict | None:
    """{name, value} of the first cookie in a Set-Cookie header (None if absent). WebKit refuses to
    keep a Secure cookie over plain http, so the caller re-adds it for the http URL itself."""
    if not header:
        return None
    first = header.split("\n")[0].split(";")[0]
    name, sep, value = first.partition("=")
    name = name.strip()
    return {"name": name, "value": value.strip()} if sep and name else None


def count_lines(path: Path) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def new_lines(path: Path, before: int) -> list[str]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    return [ln for ln in lines[before:] if ln.strip()]


def parse_last_json(text: str) -> dict:
    last = None
    dec = json.JSONDecoder()
    i = 0
    while i < len(text):
        if text[i] != "{":
            i += 1
            continue
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict):
            last = obj
        i = end
    if last is None:
        raise ValueError("no JSON object in output")
    return last


def real_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


def check_safe(url: str, token_file: str) -> None:
    port = urlparse(url).port
    if port == FORBIDDEN_PORT:
        raise SystemExit(f"refusing: url port {FORBIDDEN_PORT} is the real pm")
    tf = Path(token_file).expanduser().resolve()
    h = real_home()
    for bad in (h / ".config" / "pm", h / ".local" / "state" / "pm"):
        if tf == bad or bad in tf.parents:
            raise SystemExit(f"refusing: token file under {bad}")


def devpm_start_cmd(port: int) -> list[str]:
    """Only --port: devpm then keeps the instance's own --pm-bin / --extra-toml / --limits, so a stale-code
    restart never swaps a plugin instance for the classic pm."""
    return [sys.executable, str(HERE / "devpm.py"), "start", "--port", str(port)]


# ---- runtime ------------------------------------------------------------

def get_info(args) -> dict:
    if args.url:
        if not args.token_file:
            raise SystemExit("--url needs --token-file")
        return {"url": args.url.rstrip("/"), "token_file": args.token_file, "agents": {}, "project": None,
                "state_dir": None}
    r = subprocess.run(devpm_start_cmd(args.port), capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise SystemExit(f"devpm start failed: {r.stderr.strip() or r.stdout.strip()}")
    return parse_last_json(r.stdout)


def ui_cookies(url: str, ui: str | None) -> list[dict]:
    """Cookies for ``--ui NAME``: ``pm_ui=NAME`` on the host of ``url``, path / (empty without --ui)."""
    if not ui:
        return []
    return [{"name": "pm_ui", "value": ui, "domain": urlparse(url).hostname or "", "path": "/"}]


def new_context(browser, base: str, theme: str, width: int, ui: str | None):
    ctx = browser.new_context(viewport={"width": width, "height": 900},
                              color_scheme="dark" if theme == "dark" else "light")
    ctx.add_init_script(f"try{{localStorage.setItem('pm-theme','{theme}')}}catch(e){{}}")
    cookies = ui_cookies(base, ui)
    if cookies:
        ctx.add_cookies(cookies)
    return ctx


def login(ctx, page, base: str, token: str, engine: str) -> bool:
    """Log in with the dev token; False if the login page is still shown."""
    if engine == "webkit":
        # WebKit's page responses omit Set-Cookie on the 303 from /login: post through the context's
        # request API (no redirect follow) and hand the session cookie to the browser for this URL
        r = ctx.request.post(base + "/login", form={"token": token, "next": "/"},
                             headers={"Origin": base}, max_redirects=0)
        ck = next((c for c in (parse_set_cookie(h["value"]) for h in r.headers_array
                               if h["name"].lower() == "set-cookie") if c), None)
        if ck:
            ctx.add_cookies([{**ck, "url": base}])
        page.goto(base + "/", wait_until="networkidle")
        return "/login" not in page.url
    page.goto(base + "/login", wait_until="networkidle")
    page.fill("#token", token)
    page.press("#token", "Enter")
    page.wait_for_load_state("networkidle")
    return "/login" not in page.url


def shoot_routes(page, base: str, routes: list[str], out: Path, theme: str, width: int, *,
                 hide_dev_badge: bool = False) -> int:
    """Screenshot + check every route on a logged-in page; prints one line each, returns the failures."""
    fails = 0
    events: list[dict] = []
    page.on("console", lambda m, ev=events: ev.append(
        {"kind": "console", "type": m.type, "text": m.text}))
    page.on("pageerror", lambda e, ev=events: ev.append({"kind": "pageerror", "text": str(e)}))
    page.on("response", lambda r, ev=events: ev.append(
        {"kind": "response", "status": r.status, "url": r.url})
        if r.url.startswith(base) else None)
    for route in routes:
        events.clear()
        name = f"{slug(route)}-{theme}-{width}.png"
        reasons = []
        try:
            page.goto(base + route, wait_until="networkidle")
            page.wait_for_timeout(400)
            if hide_dev_badge:
                page.evaluate(HIDE_JS, DEV_BADGE_SEL)
            page.screenshot(path=str(out / name), full_page=True)
            sw, iw = page.evaluate(
                "[document.documentElement.scrollWidth, window.innerWidth]")
            if has_h_overflow(sw, iw):
                reasons.append(f"horizontal overflow {sw}>{iw}")
            reasons += filter_console(events)
        except Exception as e:  # noqa: BLE001
            reasons.append(f"error: {str(e).splitlines()[0]}")
        if reasons:
            fails += 1
            print(f"FAIL {out / name} " + "; ".join(reasons))
        else:
            print(f"ok {out / name}")
    return fails


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("routes", nargs="*")
    ap.add_argument("--themes", default="light,dark")
    ap.add_argument("--widths", default="1300,390")
    ap.add_argument("--out", default=None)
    ap.add_argument("--port", type=int, default=8095)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--engine", choices=("chromium", "webkit"), default="chromium")
    ap.add_argument("--url")
    ap.add_argument("--token-file")
    ap.add_argument("--ui", metavar="NAME", help="select a UI with the cookie pm_ui=NAME")
    ap.add_argument("--no-dev-badge", action="store_true",
                    help="hide the dev instance badge (for README and site screenshots)")
    args = ap.parse_args(argv)

    info = get_info(args)
    base = info["url"].rstrip("/")
    check_safe(base, info["token_file"])
    token = Path(info["token_file"]).read_text().strip()
    themes = [t for t in args.themes.split(",") if t]
    widths = [int(w) for w in args.widths.split(",") if w]
    routes = list(args.routes) + (SMOKE if args.smoke else [])
    if not routes:
        raise SystemExit("no routes (give routes or --smoke)")
    try:
        routes = [expand_route(r, info) for r in routes]
    except KeyError as e:
        raise SystemExit(f"route expansion: {e}") from e
    out = Path(args.out or (Path(info.get("root") or "/tmp") / "shots"))
    out.mkdir(parents=True, exist_ok=True)
    cerr = Path(info["state_dir"]) / "client-errors.jsonl" if info.get("state_dir") else None
    before = count_lines(cerr) if cerr else 0

    from playwright.sync_api import sync_playwright

    fails = total = 0
    with sync_playwright() as pw:
        browser = getattr(pw, args.engine).launch(headless=True)
        try:
            for theme in themes:
                for width in widths:
                    ctx = new_context(browser, base, theme, width, args.ui)
                    page = ctx.new_page()
                    if not login(ctx, page, base, token, args.engine):
                        print("FAIL /login login-rejected")
                        return 1
                    total += len(routes)
                    fails += shoot_routes(page, base, routes, out, theme, width,
                                          hide_dev_badge=args.no_dev_badge)
                    ctx.close()
        finally:
            browser.close()
    ce = new_lines(cerr, before) if cerr else []
    for ln in ce:
        print(f"FAIL client-error {ln[:300]}")
    fails += len(ce)
    print(f"summary: {total} shots, {fails} failures, out={out}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
