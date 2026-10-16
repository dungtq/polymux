"""dt theme files (templates, CSS, icon sprite): static rules of docs/themes/dt/CONDUCT.md checked on the files alone.

No app: V7-SEC-2 (no inline script/style/handlers), V7-UI-4 (no native popups), V7-UI-2 (tokens.css is the only
:root and colour-literal file, z-index from --z-* tokens), V7-CODE-2 (CSS lines <= 120, files <= 500 lines), every
/static/t/dt/... URL and icon a template references exists, and base.html renders (plain Jinja, a stub dt_shell)
for a page, a session tab and embed with unique ids.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import jinja2
import pytest

from pm.web import themes
from pm.web.dt import shell as dtshell

from .kit import render_base

THEME = themes.HERE / "dt"
TPL = THEME / "templates"
STATIC = THEME / "static"
CSS = STATIC / "css"
TEMPLATES = sorted(TPL.rglob("*.html"))
CSS_FILES = sorted(CSS.rglob("*.css"))
THEME_TEXT = sorted([*TEMPLATES, *CSS_FILES, *(STATIC / "js").rglob("*.js")])

_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_HEX = re.compile(r"(?<![\w#&-])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])")
_COLOR_FN = re.compile(r"(?<![\w-])(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(", re.I)
_URL = re.compile(r"url\((?:\"[^\"]*\"|'[^']*'|[^)]*)\)", re.I)
_DT_URL = re.compile(r"""(?:href|src)=["'](/static/t/dt/[^"'#?{]+)""")
_CLASSIC_URL = re.compile(r"""(?:href|src)=["'](/static/(?!t/)[^"'#?{]+)""")
_ICON = re.compile(r"""icons\.svg#([\w-]+)|\{\{\s*ic\s*\}\}([\w-]+)""")
_POPUP = re.compile(r"(?<![\w$])((?:window\.|self\.|globalThis\.)?)(alert|confirm|prompt)\s*\(")


def _rel(p: Path) -> str:
    return p.relative_to(THEME).as_posix()


def _html(p: Path) -> str:
    return _JINJA_COMMENT.sub(" ", p.read_text(encoding="utf-8"))


def _css(p: Path) -> str:
    return _CSS_COMMENT.sub(" ", p.read_text(encoding="utf-8"))


def test_theme_has_the_shell_files():
    names = {_rel(p) for p in TEMPLATES}
    for n in ("base.html", "_top", "_sidebar", "_sessions", "_tabs", "_layers", "_bnav", "_aside", "_classic"):
        want = n if n.endswith(".html") else f"templates/shell/{n}.html"
        assert want in names or f"templates/{want}" in names, want
    assert (CSS / "tokens.css").is_file() and (STATIC / "icons.svg").is_file()


@pytest.mark.parametrize("path", TEMPLATES, ids=_rel)
def test_no_inline_script_style_or_handlers(path):
    text = _html(path)
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text, re.I), "inline <script>"
    assert not re.search(r"<style[\s>]", text, re.I), "<style> element"
    assert not re.search(r"\sstyle\s*=", text, re.I), "style= attribute"
    assert not re.search(r"\son[a-z]+\s*=", text, re.I), "on*= event handler attribute"
    assert "javascript:" not in text.lower()


@pytest.mark.parametrize("path", THEME_TEXT, ids=_rel)
def test_no_native_popups(path):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".css":
        text = _css(path)
    bad = [m.group(0) for m in _POPUP.finditer(text) if m.group(1) or not text[: m.start()].endswith(".")]
    assert not bad, f"native popup call(s): {bad}"
    assert "window.open(" not in text


@pytest.mark.parametrize("path", CSS_FILES, ids=_rel)
def test_root_and_colour_literals_only_in_tokens(path):
    if path.name == "tokens.css":
        return
    code = _css(path)
    assert ":root" not in code, ":root outside tokens.css"
    vals = _URL.sub("", code)
    assert not _HEX.findall(vals), f"hex colours: {_HEX.findall(vals)}"
    assert not _COLOR_FN.findall(vals), "colour functions outside tokens.css"


@pytest.mark.parametrize("path", CSS_FILES, ids=_rel)
def test_css_limits_and_z_tokens(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 500
    long = [i for i, ln in enumerate(lines, 1) if len(ln) > 120]
    assert not long, f"lines over 120 chars: {long}"
    for m in re.finditer(r"z-index\s*:\s*([^;}]*)", _css(path)):
        v = m.group(1).replace("!important", "").strip()
        assert v in ("auto", "inherit", "initial", "unset") or "var(--z-" in v, f"raw z-index {v!r}"


def test_weights_at_most_600():
    for p in CSS_FILES:
        for w in re.findall(r"font-weight\s*:\s*(\d+)", _css(p)):
            assert int(w) <= 600, f"{_rel(p)}: font-weight {w}"


def test_referenced_dt_assets_exist():
    for p in TEMPLATES:
        for url in _DT_URL.findall(_html(p)):
            assert (STATIC / url.removeprefix("/static/t/dt/")).is_file(), f"{_rel(p)} -> {url}"


def test_referenced_classic_assets_exist():
    pm = pytest.importorskip("pm")
    core_static = Path(pm.__file__).parent / "static"
    for p in TEMPLATES:
        for url in _CLASSIC_URL.findall(_html(p)):
            if url.startswith(("/static/manifest", "/static/icon")):
                continue
            assert (core_static / url.removeprefix("/static/")).is_file(), f"{_rel(p)} -> {url}"


def test_icons_exist_in_the_sprite():
    sprite = set(re.findall(r'<symbol id="([\w-]+)"', (STATIC / "icons.svg").read_text(encoding="utf-8")))
    used = {a or b for p in TEMPLATES for a, b in _ICON.findall(_html(p))}
    # NavRow.icon, rendered via {{ ic }}{{ r.icon }}
    used |= {s.icon for s in dtshell.DT_NAV.values()} | {dtshell.PLUGIN_ICON, dtshell.AGENT_ICON}
    assert used and used <= sprite, f"missing icons: {sorted(used - sprite)}"


# ---------------------------------------------------------------- render (plain Jinja, stub dt_shell)
@pytest.mark.parametrize("cur_agent", [False, True])
def test_base_renders_the_shell(cur_agent):
    html = render_base(cur_agent)
    for hook in ('<header class="x-top"', 'id="x-tabs"', 'id="x-side"', 'id="x-sess"', 'id="main"', 'id="flash"',
                 'id="x-aside"', 'class="x-bnav"', 'id="x-pal"', 'id="x-keys"', 'id="x-dlg"', 'id="x-menu"',
                 'id="x-sheet"', 'id="x-scrim"', 'id="x-toast"', 'id="x-acct"', 'id="sp-dlg"', "data-live"):
        assert hook in html, hook
    ids = Counter(re.findall(r'\sid="([^"]+)"', html))
    assert not [i for i, n in ids.items() if n > 1], ids
    assert ('class="x x-sessview' in html) is cur_agent
    assert html.count('aria-current="page"') >= 1
    assert 'href="?ui=classic"' in html and 'href="?ui=dt"' not in html
    # the core loads first, the dt scripts last, in the documented order
    srcs = re.findall(r'<script src="([^"]+)"', html)
    assert srcs[0] == "/static/t/dt/js/head.js"
    assert srcs.index("/static/js/core.js") < srcs.index("/static/t/dt/js/core.js")
    dt = [s.rsplit("/", 1)[1] for s in srcs if s.startswith("/static/t/dt/js/") and not s.endswith("head.js")]
    assert dt == ["core.js", "reload.js", "layers.js", "keys.js", "live.js", "aside.js", "qstream.js", "btw.js",
                  "tabs.js", "sidebar.js", "search.js", "ticker.js", "notifs.js", "attach.js", "launch-prompt.js", "launch.js",
                  "qchat.js", "qchat-menus.js", "settings.js", "settings-forms.js", "settings-servers.js",
                  "settings-popup.js", "mdview.js", "peek.js", "table.js", "filter.js", "parts.js", "listkeys.js"]
    links = re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)
    assert links.index("/static/t/dt/css/tokens.css") > max(i for i, h in enumerate(links) if h.startswith("/static/css/"))
    dtcss = [h.rsplit("/", 1)[1] for h in links if h.startswith("/static/t/dt/css/") and "classic-fix" not in h]
    # notifs.css is page CSS too (the Notifications list): after the parts, or table.css's --g-cols default wins
    assert dtcss == ["tokens.css", "base.css", "shell.css", "aside.css", "layers.css", "keys.css", "parts.css",
                     "table.css", "notifs.css", "launch.css", "qchat.css", "settings.css", "peek.css"]
    assert "notifs" not in _between(html, "</head>", "</body>")  # the bell's assets are in <head>, not in _top.html
    assert '<main id="main" class="x-main x-cl">' in html  # un-ported content keeps the classic compat sizes
    assert ">1h<" in html  # server-rendered session age (SessRow.age)


def test_embed_renders_content_only():
    html = render_base(embed=True)
    assert 'class="x x-embed' in html and "<h1>Inbox</h1>" in html
    dt = re.findall(r'<script src="/static/t/dt/js/([^"]+)"', html)
    # no key table, tab strip, sidebar or bell; aside.js stays for the aside iframe's Esc / key bridge
    assert dt == ["head.js", "core.js", "reload.js", "layers.js", "live.js", "aside.js", "table.js", "filter.js",
                  "parts.js"]
    assert "notifs.css" not in html
    for gone in ('class="x-top"', 'id="x-side"', 'id="x-aside"', 'class="x-bnav"', 'id="x-pal"'):
        assert gone not in html, gone


def _between(html: str, start: str, end: str) -> str:
    i = html.index(start)
    return html[i:html.index(end, i)]


@pytest.mark.parametrize("cur_agent", [False, True])
def test_tab_strip_markup(cur_agent):
    strip = _between(render_base(cur_agent), 'id="x-tabs"', "</nav>")
    tabs = re.findall(r'<div class="x-tab([^"]*)" role="presentation" data-tab-kind="(\w+)" data-tab-id="([^"]*)"',
                      strip)
    assert [(k, i) for _, k, i in tabs] == [("home", ""), ("agent", "claude-1c70ee"), ("term", "$3")]
    assert ["on" in c for c, _, _ in tabs] == [not cur_agent, cur_agent, False]
    links = re.findall(r'<a role="tab" href="([^"]+)" aria-selected="(\w+)" tabindex="(-?\d)"', strip)
    assert [h for h, _, _ in links] == ["/", "/a/claude-1c70ee", "/term/s3"]
    assert [(s, t) for _, s, t in links].count(("true", "0")) == 1  # one current tab, the one Tab stop
    assert all(t == ("0" if s == "true" else "-1") for _, s, t in links)
    assert not re.search(r"<a\b(?:(?!</a>).)*<button", strip, re.S), "a button inside a tab link"
    assert strip.count('data-act="tabclose"') == 2  # never on the home tab


def test_session_rows_show_the_id():
    sess = _between(render_base(), 'id="x-sess"', "</details>")
    assert '<bdi>codex-44aa01</bdi>' in sess and '<bdi>fix-ci</bdi>' not in sess
    assert 'data-title="fix-ci"' in sess and 'title="fix-ci · codex-44aa01 · waiting"' in sess
    assert 'title="claude-1c70ee · running"' in sess  # no window name: id and state only


def test_bottom_nav_badges_only_for_needs_you():
    bnav = _between(render_base(), 'class="x-bnav"', "</nav>")
    assert re.findall(r'<span class="b([^"]*)"[^>]*>(\d+)<', bnav) == [(" hot", "3")]  # Inbox; not projects / tmux


def test_stop_all_dialog_and_verb():
    html = render_base()
    assert 'data-act="stop"' in html and 'aria-controls="x-stop"' in html
    dlg = _between(html, '<dialog id="x-stop"', "</dialog>")
    assert 'hx-post="/panic"' in dlg and 'name="confirm"' in dlg and 'data-act="close"' in dlg
    keys = (STATIC / "js" / "keys.js").read_text(encoding="utf-8")
    assert "stop: (el) => stopAll(el)" in keys and "'x-stop'" in keys


def test_classic_include_keeps_the_shell_dependencies():
    """base.html's lock bar uses classic passkey.js (data-passkey); _classic.html must keep loading it."""
    base, classic = _html(TPL / "base.html"), _html(TPL / "shell" / "_classic.html")
    if 'data-passkey="' in base:
        assert '<script src="/static/js/passkey.js"' in classic
    # the phone dock script is the page's own (composer_scripts); a second copy here would run it twice
    assert "composer-dock.js" not in classic


# ---------------------------------------------------------------- arch-guard rules on the page files
def test_templates_only_loop():
    """Templates: no logic beyond loops and conditions; lists and URL state are built in Python view models
    (pm.web.dt.inbox / ntfy / session / menus), never with append / extend or request.query_params here."""
    for p in TEMPLATES:
        src = _html(p)
        for bad in (".append(", ".extend(", "query_params"):
            assert bad not in src, f"{_rel(p)}: {bad}"


_EVERY = re.compile(r'<div id="([\w-]+)"[^>]*hx-trigger="[^"]*\bevery \d+m?s')


def test_no_timed_polls_but_the_documented_classic_strip():
    """V7-CODE-7: live data comes from the stream; the only timed polls are the classic live strip's (#chat, #sa-live)
    and the Servers panel (#sv-panel, README A21), kept with a written reason in PARTS.md §13."""
    polls = {(_rel(p), m) for p in TEMPLATES for m in _EVERY.findall(_html(p))}
    assert polls == {("templates/agent.html", "chat"), ("templates/agent.html", "sa-live"),
                     ("templates/builder.html", "chat"), ("templates/builder.html", "sa-live"),
                     ("templates/servers.html", "sv-panel")}, polls  # README A21
    parts = (THEME.parents[4] / "docs" / "themes" / "dt" / "PARTS.md").read_text(encoding="utf-8")
    assert "## 13. Timed polls" in parts and "#chat" in parts and "#sa-live" in parts and "#sv-panel" in parts


def test_classic_dependencies_are_listed_as_removal_debt():
    """A dt file that reaches classic JS (window.pm.*, nodiff, the stopImmediatePropagation guard against classic
    tabs.js) is named in shell/_classic.html's removal-debt list."""
    head = (TPL / "shell" / "_classic.html").read_text(encoding="utf-8")
    debt = head[head.index("Removal debt"):head.index("-#}")]
    for p in sorted((STATIC / "js").glob("*.js")):
        if re.search(r"window\.pm\.(?!7)|window\.pm &&|nodiff", p.read_text(encoding="utf-8")):
            assert f"js/{p.name}" in debt, p.name
    for name in ("tabs.js", "parts.js"):  # their capture-phase guard against classic tabs.js
        assert f"js/{name}" in debt and "stopImmediatePropagation" in (STATIC / "js" / name).read_text()
    for hook in ("window.pm.composer", "window.pm.chatScroll", "nodiff", "stopImmediatePropagation", "tabpanel-pane",
                 ".has-md", ".is-full", ".bld-split"):
        assert hook in debt, hook


def test_design_fixes_of_the_m2_review():
    base, layers, parts = _css(CSS / "base.css"), _css(CSS / "layers.css"), _css(CSS / "parts.css")
    session, fix = _css(CSS / "session.css"), _css(CSS / "classic-fix.css")
    assert re.search(r"\.chip\.k\{color:var\(--fg\);border-color:", base)  # AA: kind colour on the border only
    assert re.search(r"b,strong\{font-weight:600\}", base)  # no browser bold (700)
    assert ".dot.unknown" not in base.split("--dot-wait")[0].rsplit("\n", 1)[-1]  # only waiting is yellow
    assert re.search(r"\.dot\.idle,\.dot\.unknown\{background:var\(--dot-idle\)\}", base)
    assert re.search(r"@media \(pointer:coarse\)\{\s*:is\(\.x-pop,\.x-menu,\.x-sheet\) kbd\{display:none\}", layers)
    assert re.search(r"\.p-fbox input\[type=search\]:focus-visible\{outline:2px solid var\(--accent\)", parts)
    assert ".p-tools>.sp+*{margin-left:auto}" in parts
    assert not re.search(r"minmax\(\d+px|\b9px\b", session)  # A4: Builder columns from builder.css / tokens
    assert "--bld-left:var(--bld-chat-w)" in session
    assert re.search(r"\.c-hd \.c-bbtn\{width:var\(--ib\);height:var\(--ib\)", session)  # D6: 40px touch target
    assert "height:calc(var(--ib) + var(--s1))" in session  # D7: 44px phone tab line on touch
    for sel in (".tl-p", ".lim-p", ".stats-line strong"):
        assert sel in fix


def test_this_browser_flags_have_their_rules():
    # Settings › Display: "Sessions list in the sidebar" off -> html.no-sess (head.js); "Shortcut helper" ->
    # html[data-ktoast] honoured by the one toast helper (a toast with a key chip is a shortcut's)
    shell, layers = _css(CSS / "shell.css"), (STATIC / "js" / "layers.js").read_text(encoding="utf-8")
    assert ".no-sess :is(.x-shead,.x-sfilter,.x-sess){display:none}" in shell
    assert "getAttribute('data-ktoast')" in layers and "(key && kt === 'off')" in layers
    assert "if (kt === 'action') key = ''" in layers
    assert "o.undo || 'This cannot be undone.'" in layers  # a typed confirm may say what is kept instead


def test_visually_hidden_never_makes_the_page_taller():
    # a .vh with no positioned ancestor is placed against the page: pinned top-left it adds no scroll height
    assert re.search(r"\.vh\{position:absolute;top:0;left:0;", _css(CSS / "base.css"))


def test_section_heading_and_check_row_are_parts():
    parts = _css(CSS / "parts.css")
    assert "h2.p-h2{" in parts and "label.p-chk input[type=checkbox]" in parts
    for p in CSS_FILES:
        if p.name != "parts.css":
            assert not re.search(r"\.(a|pj|sv)-h2\b|\.a-chk\b|\.pj-nchk\b", _css(p)), p.name
