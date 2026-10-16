"""Resizable Markdown table columns: tablesize.js/.css exist, base.html loads them, and the docs viewer's table
is a real table (display:block would break table-layout:fixed)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pm.web.render import TEMPLATE_DIR

PM = Path(__file__).resolve().parent.parent / "src/pm"
JS = PM / "static/js/tablesize.js"
CSS = PM / "static/css/tablesize.css"


def test_files_loaded_by_base():
    base = (TEMPLATE_DIR / "base.html").read_text()
    assert '<script src="/static/js/tablesize.js" defer></script>' in base
    assert '<link rel="stylesheet" href="/static/css/tablesize.css">' in base
    assert JS.is_file() and CSS.is_file()


def test_js_is_delegated_and_targets_md_tables():
    js = JS.read_text()
    assert "'.md th, .pj-md th'" in js
    for ev in ("pointerdown", "pointermove", "pointerup", "pointercancel"):
        assert f"d.addEventListener('{ev}'" in js
    assert "tableLayout = 'fixed'" in js
    assert not re.search(r"\b(alert|confirm|prompt)\(", js)


def test_css_uses_tokens_only():
    css = CSS.read_text()
    assert "var(--accent)" in css
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", css)
    assert ":root" not in css
    assert "touch-action:pan-y" in css


def test_docs_table_not_display_block():
    fp = (PM / "static/css/filespane.css").read_text()
    rule = re.search(r"\.pj-md table\{([^}]*)\}", fp)
    assert rule and "display:block" not in rule.group(1)


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_js_runs_without_tables():
    """Loaded on every page: evaluating it with no table (and no pm helpers) must not throw."""
    stub = ("var L={};globalThis.window=globalThis;globalThis.document={addEventListener:function(t,f){L[t]=f}};"
            "globalThis.setTimeout=function(){};")
    run = ("L.pointermove({pointerType:'mouse',buttons:0,target:{closest:function(){return null}}});"
           "L.pointerdown({button:0,target:null});L.pointerup({pointerId:1});L.dblclick({target:{}});")
    r = subprocess.run(["node", "-e", stub + JS.read_text() + run], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
