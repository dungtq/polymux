"""Static JS contracts that Playwright checks live (strict CSP: no inline scripts)."""
from pathlib import Path

from pm.web.render import TEMPLATE_DIR

STATIC = Path(__file__).resolve().parents[1] / "src/pm/static"
TEMPLATES = TEMPLATE_DIR


def test_error_replies_become_flash_text():
    js = (STATIC / "pm.js").read_text()
    assert "htmx:beforeSwap" in js and "j.detail" in js and "flash err" in js
    # isError must stay true so form[data-clear] keeps the draft after a failed send
    assert "isError = false" not in js


def test_ntfy_js_loaded_from_base_not_fragment():
    # htmx runs with allowScriptTags=false: a <script> inside a swapped fragment never runs
    assert "/static/js/ntfy.js" in (TEMPLATES / "base.html").read_text()
    assert "<script" not in (TEMPLATES / "_ntfy.html").read_text()
