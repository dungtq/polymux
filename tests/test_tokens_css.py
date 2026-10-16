"""One token file: every :root custom property lives in static/css/tokens.css, linked first in base.html."""
import re
from pathlib import Path

from pm.web.render import TEMPLATE_DIR

ROOT = Path(__file__).resolve().parents[1] / "src/pm"
STATIC = ROOT / "static"


def _no_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def test_no_root_token_blocks_outside_tokens_css():
    files = sorted((STATIC / "css").glob("*.css"))
    for f in files:
        if f.name == "tokens.css":
            continue
        css = _no_comments(f.read_text())
        # a bare :root rule (or :root[data-theme]) holding declarations; ':root[...] .x{...}' descendant rules are fine
        assert not re.search(r"(^|[{}\s]):root(\[[^\]]*\])?\s*\{", css), f.name


def test_tokens_css_has_both_themes_and_touch_overrides():
    css = (STATIC / "css/tokens.css").read_text()
    assert "color-scheme:light dark" in css
    assert ':root[data-theme="light"]{color-scheme:light}' in css
    assert ':root[data-theme="dark"]{color-scheme:dark}' in css
    for tok in ("--page", "--bg", "--fg", "--muted", "--line", "--hover", "--accent", "--accent-soft",
                "--running", "--waiting", "--failed", "--btn-green", "--pane-bg"):
        assert re.search(rf"{tok}:light-dark\(#[0-9a-f]{{3,6}},#[0-9a-f]{{3,6}}\)", css), tok
    assert "@media (pointer:coarse){\n  :root{--ctl-h:40px; --row-h:40px; --chip-h:34px; --nav-h:44px}" in css
    # base.css's (ex-pm.css) old coarse --ctl-font:.9rem was always overridden by chrome.css's later base value
    assert ".9rem" not in css


def test_base_links_tokens_first_and_core_js_first_deferred():
    html = (TEMPLATE_DIR / "base.html").read_text()
    sheets = re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)
    assert sheets[0] == "/static/css/tokens.css" and sheets[1] == "/static/css/base.css"
    deferred = re.findall(r'<script src="([^"]+)" defer>', html)
    assert deferred[0] == "/static/js/core.js"
    assert html.index("/static/js/theme.js") < html.index("/static/js/core.js") < html.index("/static/css/tokens.css")


def _component_css() -> dict[str, str]:
    return {f.name: _no_comments(f.read_text()) for f in sorted((STATIC / "css").glob("*.css")) if f.name != "tokens.css"}


def _selectors(css: str) -> list[str]:
    """Every comma-separated selector of every rule (at-rule preludes skipped), whitespace-normalised."""
    out = []
    for prelude in re.findall(r"([^{}]+)\{", css):
        if prelude.strip().startswith("@"):
            continue
        out += [" ".join(s.split()) for s in prelude.split(",")]
    return out


def test_component_css_uses_size_tokens():
    """#170: --fs-s and --r-pill instead of their literal values; 999px only as a media-query breakpoint."""
    for name, css in _component_css().items():
        assert ".8125rem" not in css, name
        assert not re.search(r"(?<![\w-])999px", re.sub(r"@media[^{]*\{", "", css)), name


def test_component_css_has_no_light_dark_literals():
    """#170: theme pairs are named tokens in tokens.css, not light-dark(#..) in a component file."""
    for name, css in _component_css().items():
        assert not re.search(r"light-dark\(\s*(#|rgb|hsl)", css), name


def test_hcell_defined_once_in_rows_css():
    """#170: the sort-link header cell has one spec (rows.css); the lists.css and chrome.css copies were dead."""
    bare = (".hcell", ".hcell:hover", ".hcell.on", ".hcell .arr")
    for name, css in _component_css().items():
        hits = [s for s in _selectors(css) if s in bare]
        if name == "rows.css":
            assert sorted(hits) == sorted(bare), hits
        else:
            assert not hits, (name, hits)


def test_filter_chips_one_place_and_dead_chips_gone():
    """#170: the filter chips (.aud-chip, .srv-chip, .set-chip) are styled in chrome.css only; unused chip classes are gone."""
    for name, css in _component_css().items():
        for dead in ("sortchip", "ntfy-btn", "ntfy-chips", "aud-clear"):
            assert dead not in css, (name, dead)
        assert not re.search(r"\.ntfy-chip(?![\w-])", css), name
        if name != "chrome.css":
            assert not re.search(r"\.(aud|srv|set)-chip(?![\w-])", css), name
