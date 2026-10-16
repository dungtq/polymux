"""Composer model/effort pill + popover (modelpick.js): static markup and asset contracts (no browser).

The browser behaviour is in tests/ui/test_composer_modelpick.py (-m ui)."""
import re
from pathlib import Path

from pm.web.render import TEMPLATE_DIR

ROOT = Path(__file__).resolve().parents[1] / "src/pm"
COMPOSER = (TEMPLATE_DIR / "_composer_macros.html").read_text()
JS = (ROOT / "static/js/modelpick.js").read_text()
CSS = (ROOT / "static/css/composer.css").read_text()


def _mp_css() -> str:
    """Every CSS line that belongs to the new .mp-* rules (also the ones inside @media blocks)."""
    return "\n".join(ln for ln in CSS.splitlines() if re.match(r"\s*\.mp-", ln))


def test_modelpick_js_included_by_agent_and_builder():
    scripts = COMPOSER[COMPOSER.index("{% macro composer_scripts("):]
    assert '<script src="/static/js/modelpick.js"' in scripts  # composer_scripts(mode), live and builder (not ended)
    for page in ("agent.html", "builder.html"):
        assert "composer_scripts(" in (TEMPLATE_DIR / page).read_text(), page
    assert (ROOT / "static/js/modelpick.js").is_file()


def test_composer_has_model_and_effort_forms_with_csrf():
    for action, field in (("model", "model"), ("effort", "effort")):
        m = re.search(r'<form[^>]*action="\{\{ base \}\}/%s"[^>]*>(.*?)</form>' % action, COMPOSER, re.S)
        assert m, f"form posting to /{action} missing"
        open_tag = re.search(r'<form[^>]*action="\{\{ base \}\}/%s"[^>]*>' % action, COMPOSER).group(0)
        assert 'method="post"' in open_tag and 'hx-post="{{ base }}/%s"' % action in open_tag
        assert 'hx-target="[data-cmp-flash]"' in open_tag  # the answer lands in this composer's notice line
        body = m.group(1)
        assert '<input type="hidden" name="csrf" value="{{ csrf }}">' in body
        assert re.search(r'<button type="submit" name="%s" value="' % field, body), f"submit button name={field} missing"


def test_old_selects_are_gone():
    assert '<select id="model-sel"' not in COMPOSER and '<select id="effort-sel"' not in COMPOSER
    assert "model-sel" not in COMPOSER and "effort-sel" not in COMPOSER
    assert "<select" not in COMPOSER


def test_modelbar_class_stays_on_the_wrapper():
    """nav.css and tests/ui/test_chat_aside_keep.py select `.mrow .modelbar`."""
    m = re.search(r'<div class="([^"]*)" data-mpick>', COMPOSER)
    assert m, "wrapper with data-mpick missing"
    classes = m.group(1).split()
    assert "modelbar" in classes and "mpick" in classes
    row = COMPOSER[COMPOSER.index('<div class="mrow">'):]
    assert row.index("model_pill(base)") < row.index("</div>{% endif %}")  # still inside .mrow


def test_pill_and_panels_markup():
    assert re.search(r'<button type="button" class="mp-pill" data-mp-toggle aria-haspopup="menu" aria-expanded="false"', COMPOSER)
    for panel in ("main", "effort", "models"):
        assert f'data-mp-panel="{panel}"' in COMPOSER
    assert "mp-row" in COMPOSER


def test_menu_is_hidden_by_default():
    tag = re.search(r'<div class="mp-menu pop"[^>]*>', COMPOSER).group(0)  # one popup family: .pop, placed by pm.popover
    assert re.search(r"\bhidden\b", tag) and 'role="menu"' in tag and "data-sheet" in tag, tag
    # only the main panel may be visible when the menu opens
    for panel in ("effort", "models"):
        ptag = re.search(r'<div class="mp-panel" data-mp-panel="%s"[^>]*>' % panel, COMPOSER).group(0)
        assert re.search(r"\bhidden\b", ptag), ptag
    assert "hidden" not in re.search(r'<div class="mp-panel" data-mp-panel="main"[^>]*>', COMPOSER).group(0)
    assert ".mp-menu[hidden]" in CSS  # the author display rule must not override [hidden]


def test_modelpick_js_has_no_fetch_or_native_popups():
    assert "fetch(" not in JS and "XMLHttpRequest" not in JS
    assert not re.search(r"\b(alert|confirm|prompt)\(|window\.open|eval\(|innerHTML|csrf", JS)


def test_modelpick_uses_the_popup_family():
    """pm.popover places, closes (Esc, outside click) and roves the menu: no own place() / outside / Esc handlers."""
    assert "pm.popover" in JS and "placement: 'top-start'" in JS and "role: 'menu'" in JS
    assert "function place(" not in JS and "getBoundingClientRect" not in JS and "closeAll" not in JS
    assert "'resize'" not in JS


def test_mp_css_uses_tokens_only():
    css = _mp_css()
    assert ".mp-pill" in css and ".mp-menu" in css and ".mp-row" in css  # the extraction found the rules
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), "hex colour in .mp-* CSS, use var(--token)"
    assert "var(--" in css
