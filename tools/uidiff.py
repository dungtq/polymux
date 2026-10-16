#!/usr/bin/env python3
"""uidiff: pixel + computed-style diff of the same pages served by two pm trees (dev instances only).

Usage: uv run tools/uidiff.py --base ~/polymux --head ~/polymux-wt-feature
       [--routes / /tmux ...] [--themes light,dark] [--widths 1300,1512,390] [--out DIR]
       [--tolerance N] [--base-port 8141] [--head-port 8142] [--keep] [--retries 1]

Starts (or reuses) a dev instance from each tree (`<tree>/tools/devpm.py start --port N`), logs in to each
with its throwaway token, then for every theme x width x route: freezes the page clock, normalises
dynamic text (relative times, clocks, dates, hashes, per-instance ports/paths/agent ids), masks
`--mask` elements, takes a full-page screenshot of both and counts differing pixels (Pillow). Also
compares getComputedStyle of the root custom properties and of up to --max-elements sampled elements.
Exit 1 when an image differs by more than --tolerance pixels or a computed style differs.
Pillow is not a project dependency: when it is missing the script re-runs itself under
`uv run --with pillow`. Instances it started are stopped at exit (unless --keep). Reused (--keep) instances
must have the same history: /audit and /settings list every login, so extra logins on one side show as diffs.
Never touches port 8093 or the real token; only navigates (no typing except the dev login).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from PIL import Image, ImageChops
except ImportError:  # pragma: no cover - re-exec with pillow added to the project env
    if os.environ.get("UIDIFF_REEXEC"):
        raise SystemExit("uidiff: Pillow missing; run: uv run --with pillow tools/uidiff.py ...") from None
    os.environ["UIDIFF_REEXEC"] = "1"
    os.execvp("uv", ["uv", "run", "--with", "pillow", "python", __file__, *sys.argv[1:]])

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from uishot import SMOKE, check_safe, expand_route, parse_last_json, slug  # noqa: E402

FORBIDDEN_PORT = 8093
ROUTES = SMOKE + ["/p", "/p/{project}/launch", "/servers", "/backlog", "/audit", "/settings"]
WIDTHS = "1300,1512,390"
MOBILE_WIDTH = 500  # widths below this get isMobile + hasTouch (pointer: coarse)
SAMPLE = ("body, header, nav, main, aside, footer, h1, h2, h3, .tabbar, .tabs, .row, tr, th, td, .btn, button, "
          "a, input, select, textarea, table, .composer, .chat, .pane, .badge, .dot, .muted, .side, .sidebar, "
          ".drawer, .card, .pill")
# live host metrics (servers page): every digit inside becomes 0, progress bars go to 0
VOLATILE = ".sum-grid, .srv-grid, .srv-card, .hc-ms, [data-uidiff-volatile]"
MASK = ("time, .srv-stamp, .c-age, .em-age, .aud-time, .stamp, .sum-value, .sum-detail, .sum-badge, .hc-dot, .hc-ms, "
        "[data-uidiff-mask]")
# software raster, no LCD text / partial raster: the same DOM paints the same pixels in both pages
CHROMIUM_ARGS = ["--disable-gpu", "--force-color-profile=srgb", "--disable-lcd-text", "--font-render-hinting=none",
                 "--disable-partial-raster", "--disable-skia-runtime-opts"]
NO_MOTION_CSS = "*, *::before, *::after { transition: none !important; animation: none !important; }"
OUT = Path(os.environ.get("TMPDIR", "/tmp")) / "pm-dev" / "uidiff"
FROZEN_MS = int(time.time()) * 1000

# Date frozen at the tool's start time, advancing only with time since page load (timers still work).
FREEZE_JS = """(() => {
  const T = %d, R = Date, t0 = performance.now();
  const now = () => Math.floor(T + performance.now() - t0);
  class D extends R { constructor(...a) { if (a.length) super(...a); else super(now()); } static now() { return now(); } }
  window.Date = D;
})();""" % FROZEN_MS

# Rewrites dynamic text in place (same rules on both sides). arg: {subs: [[literal, repl]...], volatile: selector}
NORMALIZE_JS = r"""(arg) => {
  const rx = [
    [/\b([a-z]+(?:-[a-z]+)?)-[0-9a-f]{6}\b/g, '$1-xxxxxx'],
    [/\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b/g, '#######'],
    [/\b\d{4}-\d{2}-\d{2}\b/g, '0000-00-00'],
    [/\b\d{1,2}:\d{2}(:\d{2})?\b/g, (m, s) => '00:00' + (s ? ':00' : '')],
    [/\b\d+(\.\d+)?\s?(s|sec|secs|m|min|mins|h|hr|hrs|d|w|mo|y)\b( ago)?/g, (m, a, u, g) => '0' + u + (g || '')],
    [/\bpid \d+\b/g, 'pid 0'],
  ];
  const fix = (s) => {
    for (const [a, b] of arg.subs) s = s.split(a).join(b);
    for (const [r, f] of rx) s = s.replace(r, f);
    return s;
  };
  const vol = arg.volatile ? [...document.querySelectorAll(arg.volatile)] : [];
  const w = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
  const skip = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT']);
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    if (n.parentElement && skip.has(n.parentElement.tagName)) continue;
    let v = fix(n.nodeValue);
    if (vol.some((el) => el.contains(n))) v = v.replace(/\d/g, '0');
    if (v !== n.nodeValue) n.nodeValue = v;
  }
  for (const el of vol) for (const m of el.querySelectorAll('progress, meter')) m.value = 0;
  for (const el of document.querySelectorAll('input[value], textarea')) {
    const v = fix(el.value || '');
    if (v !== el.value) el.value = v;
  }
}"""

# Computed properties compared per sampled element (--all-props: every longhand, ~5x slower).
PROPS = ("display position top right bottom left float z-index box-sizing width height min-width min-height max-width "
         "max-height margin-top margin-right margin-bottom margin-left padding-top padding-right padding-bottom "
         "padding-left border-top-width border-right-width border-bottom-width border-left-width border-top-style "
         "border-bottom-style border-left-style border-right-style border-top-color border-right-color "
         "border-bottom-color border-left-color border-top-left-radius border-top-right-radius "
         "border-bottom-left-radius border-bottom-right-radius outline-style outline-width outline-color "
         "outline-offset color background-color background-image box-shadow opacity visibility overflow-x "
         "overflow-y font-family font-size font-weight font-style line-height letter-spacing text-align "
         "text-decoration-line text-transform text-overflow white-space vertical-align flex-direction flex-wrap "
         "flex-grow flex-shrink flex-basis justify-content align-items align-self gap row-gap column-gap "
         "grid-template-columns grid-template-rows order cursor fill stroke transform transition-property "
         "transition-duration accent-color caret-color list-style-type").split()

# Root custom properties (names collected from every same-origin stylesheet) + sampled element styles.
STYLES_JS = r"""([sel, cap, props]) => {
  const names = new Set();
  const walk = (rules) => {
    for (const r of rules || []) {
      if (r.style) for (const p of r.style) if (p.startsWith('--')) names.add(p);
      if (r.cssRules) walk(r.cssRules);
    }
  };
  for (const sh of document.styleSheets) { try { walk(sh.cssRules); } catch (e) {} }
  const root = getComputedStyle(document.documentElement), vars = {};
  for (const n of [...names].sort()) vars[n] = root.getPropertyValue(n).trim();
  const els = [...document.querySelectorAll(sel)].slice(0, cap), out = [];
  for (const el of els) {
    const cs = getComputedStyle(el), st = {};
    for (const p of (props || cs)) if (!p.startsWith('--')) st[p] = cs.getPropertyValue(p);
    const cls = (typeof el.className === 'string' ? el.className.trim().split(/\s+/).slice(0, 3).join('.') : '');
    out.push({tag: el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (cls ? '.' + cls : ''), st});
  }
  return {vars, els: out};
}"""


# ---- pure helpers -------------------------------------------------------

def norm_subs(info: dict) -> list[list[str]]:
    """Literal per-instance strings (ports, paths, agent ids) -> neutral placeholders, longest first."""
    port = str(info.get("port", ""))
    subs: list[list[str]] = []
    if info.get("root"):
        subs.append([str(info["root"]), str(Path(info["root"]).parent / "PORT")])
    if info.get("url"):
        subs.append([info["url"], "http://127.0.0.1:PORT"])
    for key, aid in sorted((info.get("agents") or {}).items()):
        subs.append([str(aid), f"agent-{key}"])
    if port:
        subs.append([f"pmtest-dev-{port}", "pmtest-dev-PORT"])
        subs.append([f":{port}", ":PORT"])
    return sorted(subs, key=lambda s: -len(s[0]))


def style_diffs(a: dict, b: dict, limit: int = 40) -> tuple[list[str], list[str]]:
    """(failures, notes): value diffs of shared root vars and sampled elements; vars only on one side are notes."""
    fails: list[str] = []
    notes: list[str] = []
    va, vb = a.get("vars", {}), b.get("vars", {})
    for n in sorted(set(va) | set(vb)):
        if n not in va:
            notes.append(f"var {n} only in head = {vb[n]!r}")
        elif n not in vb:
            notes.append(f"var {n} only in base = {va[n]!r}")
        elif va[n] != vb[n]:
            fails.append(f"var {n}: {va[n]!r} -> {vb[n]!r}")
    ea, eb = a.get("els", []), b.get("els", [])
    if len(ea) != len(eb):
        fails.append(f"sampled element count {len(ea)} -> {len(eb)}")
    for i, (x, y) in enumerate(zip(ea, eb)):
        if x["tag"] != y["tag"]:
            fails.append(f"el[{i}] {x['tag']} -> {y['tag']} (structure differs; later elements not compared)")
            break
        for p in sorted(set(x["st"]) | set(y["st"])):
            if x["st"].get(p) != y["st"].get(p):
                fails.append(f"el[{i}] {x['tag']} {p}: {x['st'].get(p)!r} -> {y['st'].get(p)!r}")
    if len(fails) > limit:
        fails = fails[:limit] + [f"... {len(fails) - limit} more"]
    return fails, notes


def pixel_diff(pa: Path, pb: Path, diff_out: Path | None = None) -> int:
    """Differing pixel count (size mismatch counts the non-overlapping area); writes a diff image when > 0."""
    a, b = Image.open(pa).convert("RGB"), Image.open(pb).convert("RGB")
    w, h = max(a.width, b.width), max(a.height, b.height)
    ca, cb = Image.new("RGB", (w, h), (255, 0, 255)), Image.new("RGB", (w, h), (0, 255, 0))
    ca.paste(a, (0, 0))
    cb.paste(b, (0, 0))
    mask = ImageChops.difference(ca, cb).convert("L").point(lambda v: 255 if v else 0)
    n = mask.histogram()[255]
    if n and diff_out is not None:
        base = ca.convert("L").point(lambda v: 160 + v // 3).convert("RGB")
        base.paste((255, 0, 0), (0, 0), mask)
        base.save(diff_out)
    return n


# ---- instances ----------------------------------------------------------

def tree_of_pid(pid: int) -> Path | None:
    try:
        return Path(os.readlink(f"/proc/{pid}/cwd")).resolve()
    except OSError:
        return None


def devpm(tree: Path, *args: str) -> subprocess.CompletedProcess:
    py = tree / ".venv/bin/python"
    return subprocess.run([str(py if py.exists() else sys.executable), str(tree / "tools/devpm.py"), *args],
                          capture_output=True, text=True, timeout=180, check=False)


def start(tree: Path, port: int) -> tuple[dict, bool]:
    """(instance info, started_here). Refuses a healthy instance on the port that runs from another tree."""
    if port <= FORBIDDEN_PORT or port < 8095:
        raise SystemExit(f"uidiff: port {port} refused (dev ports start at 8095)")
    st = devpm(tree, "status", "--port", str(port), "--json")
    cur = parse_last_json(st.stdout) if st.stdout.strip() else {}
    running = bool(cur.get("running"))
    if running and tree_of_pid(int(cur.get("pid") or 0)) not in (tree, None):
        raise SystemExit(f"uidiff: port {port} runs pm from {tree_of_pid(cur['pid'])}, not {tree}; pick another port")
    r = devpm(tree, "start", "--port", str(port))
    if r.returncode:
        raise SystemExit(f"uidiff: devpm start {tree} --port {port} failed: {(r.stderr or r.stdout).strip()[-500:]}")
    info = parse_last_json(r.stdout)
    reused = running and info.get("pid") == cur.get("pid")
    return info, not reused


def login(ctx, info: dict, theme: str):
    token = Path(info["token_file"]).read_text().strip()
    page = ctx.new_page()
    page.goto(info["url"] + "/login", wait_until="networkidle")
    page.fill("#token", token)
    page.press("#token", "Enter")
    page.wait_for_load_state("networkidle")
    if "/login" in page.url:
        raise SystemExit(f"uidiff: login rejected on {info['url']}")
    return page


def capture(page, info: dict, route: str, path: Path, args) -> dict:
    page.goto(info["url"] + route, wait_until="networkidle")
    page.wait_for_timeout(args.settle)
    page.evaluate(NORMALIZE_JS, {"subs": norm_subs(info), "volatile": args.volatile})
    styles = page.evaluate(STYLES_JS, [args.sample, args.max_elements, None if args.all_props else list(PROPS)])
    page.add_style_tag(content=NO_MOTION_CSS)  # after the style capture, so transitions are still compared
    page.wait_for_timeout(50)
    masks = [page.locator(args.mask)] if args.mask else []
    page.screenshot(path=str(path), full_page=True, animations="disabled", caret="hide", mask=masks,
                    mask_color="#888888")
    return styles


# ---- main ---------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, type=Path)
    ap.add_argument("--head", required=True, type=Path)
    ap.add_argument("--routes", nargs="*", default=None, help="default: uishot smoke + main pages")
    ap.add_argument("--themes", default="light,dark")
    ap.add_argument("--widths", default=WIDTHS)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--tolerance", type=int, default=0, help="allowed differing pixels per image")
    ap.add_argument("--base-port", type=int, default=8141)
    ap.add_argument("--head-port", type=int, default=8142)
    ap.add_argument("--keep", action="store_true", help="leave started instances running")
    ap.add_argument("--retries", type=int, default=1, help="re-shoot a differing pair this many times")
    ap.add_argument("--settle", type=int, default=400, help="ms to wait after networkidle")
    ap.add_argument("--mask", default=MASK, help="CSS selector of elements painted over before the shot")
    ap.add_argument("--volatile", default=VOLATILE, help="CSS selector of live-metric blocks whose digits are zeroed")
    ap.add_argument("--sample", default=SAMPLE, help="CSS selector of elements whose computed style is compared")
    ap.add_argument("--max-elements", type=int, default=300)
    ap.add_argument("--all-props", action="store_true", help="compare every computed longhand, not just PROPS")
    ap.add_argument("--no-style", action="store_true", help="skip the computed-style comparison")
    args = ap.parse_args(argv)

    trees = {"base": args.base.resolve(), "head": args.head.resolve()}
    ports = {"base": args.base_port, "head": args.head_port}
    if ports["base"] == ports["head"]:
        raise SystemExit("uidiff: --base-port and --head-port must differ")
    for t in trees.values():
        if not (t / "tools/devpm.py").exists():
            raise SystemExit(f"uidiff: {t} has no tools/devpm.py")
    t0 = time.monotonic()
    out = args.out or OUT / time.strftime("%Y%m%d-%H%M%S")
    for d in ("base", "head", "diff"):
        (out / d).mkdir(parents=True, exist_ok=True)

    started: list[tuple[Path, int]] = []
    try:
        with ThreadPoolExecutor(2) as ex:  # start both at once so server-side times line up
            futs = {k: ex.submit(start, trees[k], ports[k]) for k in trees}
            infos = {}
            for k, f in futs.items():
                infos[k], mine = f.result()
                if mine:
                    started.append((trees[k], ports[k]))
        for k, info in infos.items():
            check_safe(info["url"], info["token_file"])
            print(f"{k}: {trees[k]} -> {info['url']}")
        return run(args, infos, out, t0)
    finally:
        if not args.keep:
            for tree, port in started:
                devpm(tree, "stop", "--port", str(port))


def shoot_side(args, info: dict, jobs: list[tuple[str, int, str, str]], outdir: Path) -> dict:
    """One side, own Playwright (sync API is per thread). jobs: (theme, width, name, route) -> {name: styles|error}."""
    from playwright.sync_api import sync_playwright

    res: dict = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=CHROMIUM_ARGS)
        try:
            for (theme, width) in dict.fromkeys((t, w) for t, w, _, _ in jobs):
                mobile = width < MOBILE_WIDTH
                ctx = browser.new_context(
                    viewport={"width": width, "height": 844 if mobile else 900}, device_scale_factor=1,
                    is_mobile=mobile, has_touch=mobile, color_scheme=theme if theme != "auto" else None,
                    reduced_motion="reduce", bypass_csp=True)  # CSP would block the no-motion <style>
                ctx.add_init_script(f"try{{localStorage.setItem('pm-theme','{theme}')}}catch(e){{}}")
                ctx.add_init_script(FREEZE_JS)
                try:
                    page = login(ctx, info, theme)
                    for t, w, name, route in jobs:
                        if (t, w) != (theme, width):
                            continue
                        try:
                            res[name] = capture(page, info, route, outdir / name, args)
                        except Exception as e:  # noqa: BLE001
                            res[name] = {"error": str(e).splitlines()[0]}
                finally:
                    ctx.close()
        finally:
            browser.close()
    return res


def shoot(args, infos: dict, jobs: dict, out: Path) -> dict:
    """jobs: {side: [(theme, width, name, route)]}; both sides at once so server-side times line up."""
    with ThreadPoolExecutor(len(infos)) as ex:
        futs = {k: ex.submit(shoot_side, args, infos[k], jobs[k], out / k) for k in infos}
        return {k: f.result() for k, f in futs.items()}


def run(args, infos: dict, out: Path, t0: float) -> int:
    themes = [t for t in args.themes.split(",") if t]
    widths = [int(w) for w in args.widths.split(",") if w]
    routes = {}
    for r in args.routes or ROUTES:
        try:
            routes[r] = {k: expand_route(r, info) for k, info in infos.items()}
        except KeyError as e:
            print(f"skip {r}: {e}")
    names = [(theme, width, f"{slug(r)}-{theme}-{width}.png", r) for theme in themes for width in widths
             for r in routes]
    pending = names
    pix: dict[str, int] = {}
    sty: dict[str, dict] = {}
    for attempt in range(args.retries + 1):
        got = shoot(args, infos, {k: [(t, w, n, routes[r][k]) for t, w, n, r in pending] for k in infos}, out)
        for _, _, name, _ in pending:
            a, b = got["base"].get(name, {}), got["head"].get(name, {})
            if "error" in a or "error" in b:
                print(f"ERROR {name}: {a.get('error') or b.get('error')}")
                pix[name] = -1
                continue
            sty[name] = {"base": a, "head": b}
            pix[name] = pixel_diff(out / "base" / name, out / "head" / name, out / "diff" / name)
        pending = [j for j in pending if not 0 <= pix[j[2]] <= args.tolerance]
        if not pending:
            break
        if attempt < args.retries:
            print(f"re-shooting {len(pending)} differing pairs")
    img_fail = style_fail = 0
    notes: set[str] = set()
    for _, _, name, _ in names:
        n = pix[name]
        if n < 0 or n > args.tolerance:
            img_fail += 1
            print(f"DIFF {name}: {n} px  {out / 'diff' / name if n > 0 else 'capture failed'}")
        else:
            (out / "diff" / name).unlink(missing_ok=True)
            print(f"ok   {name}: {n} px")
        if name in sty and not args.no_style:
            fails, nt = style_diffs(sty[name]["base"], sty[name]["head"])
            notes.update(nt)
            if fails:
                style_fail += 1
                print(f"STYLE {name}: {len(fails)} diffs")
                for f in fails:
                    print(f"    {f}")
    for n in sorted(notes)[:60]:
        print(f"note {n}")
    print(f"summary: {len(names)} pairs, {img_fail} image diffs, {style_fail} style diffs, "
          f"{time.monotonic() - t0:.0f}s, out={out}")
    return 1 if img_fail or style_fail else 0


if __name__ == "__main__":
    sys.exit(main())
