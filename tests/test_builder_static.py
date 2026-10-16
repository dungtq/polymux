"""Builder page assets: no inline script/style, no hard-coded colours, no native popups."""
import re
from pathlib import Path

from pm.web.render import TEMPLATE_DIR

ROOT = Path(__file__).resolve().parents[1] / "src/pm"
TPL = (TEMPLATE_DIR / "builder.html").read_text()
CSS = (ROOT / "static/css/builder.css").read_text()
JS = (ROOT / "static/js/builder.js").read_text()
SCREEN = (ROOT / "static/js/builder-screen.js").read_text()
API = (ROOT / "static/js/builder-api.js").read_text()


def test_template_has_no_inline_script_or_style():
    assert not re.search(r"<script(?![^>]*\bsrc=)", TPL)
    assert " style=" not in TPL and "<style" not in TPL
    assert not re.search(r"\son[a-z]+=", TPL)
    assert "/static/js/builder.js" in TPL and "/static/css/builder.css" in TPL


def test_css_uses_tokens_only():
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", CSS)
    assert "var(--" in CSS


def test_js_is_clean():
    for src in (JS, SCREEN, API):
        assert not re.search(r"\b(alert|confirm|prompt)\(|window\.open|eval\(|innerHTML|javascript:", src)


def test_page_fills_the_viewport_without_a_gap():
    assert "--bld-h" not in CSS and "112px" not in CSS
    assert "height:calc(100dvh - var(--top-h,44px));display:flex;flex-direction:column" in CSS
    assert ".bld-page main>#builder{flex:1 1 auto;min-height:0" in CSS
    assert re.search(r"\.bld-split\{[^}]*flex:1 1 auto;min-height:0", CSS)
    assert "var(--nav-clear,60px)" in CSS  # phones stop above the bottom nav


def test_divider_is_a_hairline_then_accent():
    assert re.search(r"\.bld-div::after\{[^}]*width:1px[^}]*background:var\(--line\)", CSS)
    assert re.search(r"\.bld-div:hover::after[^{]*\{width:3px;background:var\(--accent\)", CSS)
    assert "@container bldmsgs" in CSS


def test_js_external_urls_and_preview_line():
    assert "hosts.indexOf(u.origin)" in JS and "u.protocol !== 'https:'" in JS  # allowlist check
    assert "/^Preview:\\s*(\\S+)\\s*$/i" in JS and "pm:ci-update" in JS
    assert "allow-same-origin" not in TPL  # the server-rendered (local) frame stays strict
    assert JS.count("allow-same-origin") == 1 and "isExt ? SB + ' allow-same-origin' : SB" in JS  # external frames only
    assert "ensureFrame(true)" in JS and "ensureFrame(false)" in JS and "u.origin === location.origin" in JS
    assert "data-frame-hosts=" in TPL and "!ext" in JS  # the mtime poll does not reload an external page


def test_screen_size_presets_and_chat_links():
    for key, wh in (("mbp14", "1512\" data-h=\"982"), ("dell27", "2560\" data-h=\"1440"), ("dell34h", "1720\" data-h=\"1440"),
                    ("dell34t", "2293\" data-h=\"1440"), ("ip17pm", "440\" data-h=\"956")):
        assert f'value="{key}" data-w="{wh}"' in TPL, key
    assert "dv-desk" not in TPL and "dv-desk" not in CSS and "pm-builder-device" in SCREEN
    assert ".bld-stage[data-size] .bld-frame" in CSS and "new ResizeObserver(applySize)" in SCREEN
    assert "msgs.addEventListener('click'" in JS and "allowed(a.href)" in JS and "ctrlKey" in JS  # plain click only


def test_zoom_source_and_shared_helpers():
    assert 'id="bld-zoom"' in TPL and 'value="fit"' in TPL and 'value="200"' in TPL and "pm-builder-zoom" in SCREEN
    assert "{ passive: false }" in SCREEN and "e.ctrlKey || e.metaKey" in SCREEN  # ctrl/cmd + wheel only
    assert "justify-content:safe center" in CSS and "#builder.bld-ctrl .bld-frame{pointer-events:none}" in CSS
    assert 'id="bld-src"' in TPL and 'value="files"' in TPL and 'value="url"' in TPL and 'id="bld-hosts"' in TPL
    assert "setMode('url')" in JS and "setMode('files')" in JS and "'https://' + v" in JS
    assert "window.pm.builder" in SCREEN and ".builder = { load: load, store: store }" in JS  # one load/store
    assert TPL.index("builder.js") < TPL.index("builder-screen.js")


def test_api_pane_markup_and_script():
    assert 'id="bld-view"' in TPL and 'data-bld-view="page"' in TPL and 'data-bld-view="api"' in TPL
    assert 'id="bld-api"' in TPL and 'id="bld-eps"' in TPL and 'id="bld-req"' in TPL and 'id="bld-resb"' in TPL
    assert 'value="mock"' in TPL and 'data-api-url=' in TPL and 'class="primary" id="bld-send"' in TPL
    assert TPL.index("builder.js") < TPL.index("builder-api.js")  # uses window.pm.builder from builder.js
    assert 'id="bt-chat"' in TPL and 'id="bt-prev"' in TPL  # phone Chat | Preview tabs stay
    assert "<iframe" not in TPL.split('id="bld-api"', 1)[1]  # responses never in a frame
    assert not re.search(r"(?<![.\w])fetch\(", API) and "csrf: 'header'" in API  # pm.fetch only, CSRF header
    assert "innerHTML" not in API and "insertAdjacentHTML" not in API and "resB.textContent" in API
    assert "'bld:changed'" in JS and "'bld:changed'" in API  # file change -> spec read again
    assert "#builder[data-view=api] .bld-stage" in CSS and "@container bldprev" in CSS
