"""style-src is 'self' (src/pm/web/render.py): a style="..." attribute parsed from markup is refused (WebKit logs
"Refused to apply a stylesheet ... style-src"). Scripts set styles through the CSSOM (el.style.x = ...), never markup."""
import re
from pathlib import Path

from pm.web.render import TEMPLATE_DIR

ROOT = Path(__file__).resolve().parent.parent / "src/pm"
ATTR = re.compile(r"""\sstyle\s*=\s*["'\\]""")
TAG = re.compile(r"<style[\s>]", re.I)
# files that legitimately carry their own looser CSP (login page, terminal, builder preview)
SKIP = {"term.js", "term.html", "login.html"}


def _files(base, pattern):
    out = [p for p in base.rglob(pattern) if "vendor" not in p.parts and p.name not in SKIP]
    assert out, f"no {pattern} under {base}: the scan would pass on nothing"
    return out


def test_js_builds_no_style_attribute_or_style_element():
    bad = []
    for p in _files(ROOT / "static/js", "*.js"):
        for n, line in enumerate(p.read_text().splitlines(), 1):
            code = line.split("//")[0] if "'<" not in line and '"<' not in line else line
            if ATTR.search(code) or TAG.search(code):
                bad.append(f"{p.name}:{n}")
    assert not bad, bad


def test_templates_have_no_inline_style():
    bad = []
    for p in _files(TEMPLATE_DIR, "*.html"):
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if ATTR.search(line) or TAG.search(line):
                bad.append(f"{p.name}:{n}")
    assert not bad, bad


def test_launch_popup_proj_label_is_a_css_class():
    js = (ROOT / "static/js/launchpop.js").read_text()
    css = (ROOT / "static/css/shell.css").read_text()
    assert "className = 'lp-proj'" in js and ".lp-proj{" in css
