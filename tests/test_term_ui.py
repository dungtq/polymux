import hashlib
import re

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from pm.web.render import STATIC_DIR, TEMPLATE_DIR
from pm.web.render import env as _env

XT = STATIC_DIR / "vendor" / "xterm"
JS = (STATIC_DIR / "js" / "term.js").read_text()
EVIL = "<script>alert(1)</script>"
CSRF = 'tok"><img src=x onerror=alert(1)>'


def render(name=EVIL, csrf=CSRF):
    return _env.get_template("term.html").render(
        session_name=name, sid="s12", csrf=csrf, ticket_path="/term/ticket",
        ws_path="/term/ws", idle_s=300, max_s=1800, locked=False, nav="tmux",
    )


def test_no_inline_script_style_or_handlers():
    html = render("plain", "tok")
    for m in re.finditer(r"<script\b([^>]*)>", html):
        assert "src=" in m.group(1)
    assert "<style" not in html
    assert not re.search(r"\sstyle=", html)
    assert not re.search(r"\son[a-z]+\s*=", html)
    assert "javascript:" not in html


def test_inline_script_bodies_empty():
    html = render("plain")
    for m in re.finditer(r"<script\b[^>]*>(.*?)</script>", html, re.S):
        assert m.group(1).strip() == ""


def test_data_attributes_and_ui():
    html = render("plain")
    for a in ('data-sid="s12"', 'data-ticket-path="/term/ticket"', 'data-ws-path="/term/ws"',
              'data-idle-s="300"', 'data-max-s="1800"', "READ-ONLY", 'href="/tmux"',
              "/static/vendor/xterm/xterm.js", "/static/vendor/xterm/addon-fit.js",
              "/static/vendor/xterm/xterm.css", "/static/js/term.js", "/static/css/term.css",
              "term-reconnect", "term-bottom", "term-font-inc"):
        assert a in html, a


def test_escaping():
    html = render()
    assert EVIL not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x" not in html
    assert "&lt;img" in html and "&#34;" in html


def test_vendored_files_and_versions():
    names = ["xterm.js", "xterm.css", "addon-fit.js", "LICENSE", "VERSIONS.txt"]
    for n in names:
        assert (XT / n).stat().st_size > 0, n
    v = (XT / "VERSIONS.txt").read_text()
    for n in ["xterm.js", "xterm.css", "addon-fit.js"]:
        assert n in v
    assert "MIT" in v and "sha256" in v and re.search(r"\d+\.\d+\.\d+", v)


def test_term_js_is_read_only_and_csp_safe():
    assert not re.search(r"onData|onKey|onBinary|\.paste\(|\.input\(", JS)
    assert "disableStdin: true" in JS
    assert "eval(" not in JS and "new Function" not in JS and "innerHTML" not in JS
    assert "http://" not in JS and "cdn" not in JS.lower()
    # the only sends go through sendJSON with init/resize/pong
    assert len(re.findall(r"\bws\.send\(", JS)) == 1
    types = set(re.findall(r'type: "(\w+)"', JS))
    assert types <= {"init", "resize", "pong", "window"} | {"ping"}, types
    for code in ("4401", "4403", "4408", "4409", "4413", "4429"):
        assert code in JS
    assert "scrollback: 5000" in JS


def test_static_served():
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    c = TestClient(app)
    for p in ("vendor/xterm/xterm.js", "vendor/xterm/xterm.css", "vendor/xterm/addon-fit.js",
              "js/term.js", "css/term.css"):
        assert c.get(f"/static/{p}").status_code == 200, p


def test_window_nav_buttons_and_aria():
    html = render("plain")
    for a in ('id="term-prev"', 'id="term-next"',  " Prev</button>", "Next <svg",
              'id="term-winlabel"', 'aria-live="polite"', 'aria-label="Previous window"',
              'aria-label="Next window"'):
        assert a in html, a
    # buttons start disabled (single window / not connected)
    assert re.search(r'id="term-prev"[^>]*\sdisabled', html)
    assert re.search(r'id="term-next"[^>]*\sdisabled', html)
    lab = re.search(r'<span[^>]*id="term-winlabel"[^>]*>', html).group(0)
    assert 'role="status"' in lab and 'aria-live="polite"' in lab
    assert html.index("term-nav") < html.index('id="term-scroll"')


def test_window_nav_js_and_css():
    assert 'sendJSON({ type: "window", dir: dir })' in JS
    assert '"prev"' in JS and '"next"' in JS and "ArrowLeft" in JS and "ArrowRight" in JS
    assert "altKey" in JS and "textContent" in JS and "scrollToBottom" in JS
    assert not re.search(r"onData|onKey|\.paste\(", JS)
    css = (STATIC_DIR / "css" / "term.css").read_text()
    assert "var(--ctl-h)" in css and "44px" not in css and "safe-area-inset-bottom" in css
    assert "position: fixed" in css and "@media" in css
    assert "<script" not in css


def test_no_non_ascii_decorative_glyphs():
    css = (STATIC_DIR / "css" / "term.css").read_text()
    html = render("plain")
    tpl = (TEMPLATE_DIR / "term.html").read_text()
    for name, text in (("html", html), ("template", tpl), ("js", JS), ("css", css)):
        assert not re.search(r"[^\x00-\x7f]", text), name
        assert not re.search(r"&#(?:x[0-9a-fA-F]+|\d{3,});", text), name
        assert not re.search(r"\\u[0-9a-fA-F]{4}", text), name
        assert not re.search(r"&(?:[a-z]+arr|[lr]aquo|middot|bull);", text, re.I), name


def test_history_controls_and_js():
    html = render("plain")
    for a in ('id="term-hist-btn"', 'id="term-hist-refresh"', 'id="term-live"',
              'id="term-hist-count"', 'id="term-hist"', 'data-history-path="/term/history"',
              ">History<", ">Refresh<", ">Live<", "<svg"):
        assert a in html, a
    assert 'aria-label="Previous window"' in html and 'aria-label="Next window"' in html
    assert re.search(r'id="term-hist-btn"[^>]*\sdisabled', html)
    for a in ("historyPath", "scrollToBottom", "scrollback: 10500", "convertEol: true",
              'e.key === "h"', "no-store"):
        assert a in JS, a
    assert "scrollback: 5000" in JS
    assert len(re.findall(r"\bws\.send\(", JS)) == 1  # history is HTTP GET, not the socket
    css = (STATIC_DIR / "css" / "term.css").read_text()
    assert "touch-action: pan-y" in css and "-webkit-overflow-scrolling: touch" in css


FONTS = STATIC_DIR / "vendor" / "fonts"
CSS = (STATIC_DIR / "css" / "term.css").read_text()
MACROS = TEMPLATE_DIR / "_macros.html"


def test_nerd_font_vendored_and_declared():
    woff = FONTS / "SymbolsNerdFontMono-Regular.woff2"
    assert woff.read_bytes()[:4] == b"wOF2" and woff.stat().st_size < 2_000_000
    v = (FONTS / "VERSIONS.txt").read_text()
    assert hashlib.sha256(woff.read_bytes()).hexdigest() in v
    for w in ("Nerd Fonts", "github.com/ryanoasis/nerd-fonts/releases/download/", "MIT", "sha256"):
        assert w in v
    assert (FONTS / "LICENSE").stat().st_size > 0
    ff = re.search(r"@font-face\s*\{([^}]*)\}", CSS).group(1)
    assert '"Symbols Nerd Font Mono"' in ff and "SymbolsNerdFontMono-Regular.woff2" in ff
    assert "font-display: swap" in ff and "U+E000-F8FF" in ff and "unicode-range" in ff
    assert "U+0020" not in ff and "U+0000" not in ff  # never overrides ordinary glyphs


def test_terminals_use_symbols_font_after_load():
    assert JS.count('"Symbols Nerd Font Mono"') == 1
    assert 'fonts.load("14px "' in JS and "options.fontFamily = FONT_FULL" in JS
    assert "histTerm.options.fontFamily = FONT_FULL" in JS
    assert "fontFamily: fontFamily" in JS and JS.count("fontFamily: fontFamily") == 2
    assert "ui-monospace, SFMono-Regular, Menlo, Consolas" in JS
    assert not re.search(r"keydown[^;]*send|\.send\([^)]*key", JS, re.I)


def macro(**ctx):
    src = ('{% from "_macros.html" import term_link with context %}' + ctx.pop("call"))
    return _env.from_string(src).render(**ctx).strip()


def test_term_link_macro_variants():
    on = {"terminal_enabled": True}
    assert macro(call='{{ term_link("$12") }}', **on) == \
        '<a class="button small" href="/term/s12">Terminal</a>'
    assert macro(call='{{ term_link("s12") }}', **on) == \
        '<a class="button small" href="/term/s12">Terminal</a>'
    out = macro(call='{{ term_link("$12", "@34", "/a/claude-140") }}', **on)
    assert out == ('<a class="button small" href="/term/s12?w=w34&amp;back=%2Fa%2Fclaude-140">'
                   'Terminal</a>')
    assert 'w=w34' in macro(call='{{ term_link("$12", "w34") }}', **on)
    assert "back=" not in macro(call='{{ term_link("$12", "@34") }}', **on)
    assert "w=" not in macro(call='{{ term_link("$12", None, "/tmux") }}', **on)
    assert "w=" not in macro(call='{{ term_link("$12", "@x") }}', **on)
    # pm* sessions, disabled terminal, missing context: no link
    assert macro(call='{{ term_link("$1", name="pm-main") }}', **on) == ""
    assert "href" in macro(call='{{ term_link("$1", name="work") }}', **on)
    assert macro(call='{{ term_link("$1") }}', terminal_enabled=False) == ""
    assert macro(call='{{ term_link("$1") }}') == ""


def test_term_link_macro_escapes_back():
    out = macro(call='{{ term_link("$1", None, back) }}', terminal_enabled=True,
                back='/a/x"><script>')
    assert "<script>" not in out
