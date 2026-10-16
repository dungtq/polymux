"""Slash palette front-end contracts (string-level, plus node for pure helpers)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "src/pm/static/js/slash.js"
CSS = ROOT / "src/pm/static/css/chat.css"


def test_js_contract():
    js = JS.read_text()
    assert "data-slash" in js and "window.pmSlash" in js
    for k in ("Escape", "ArrowUp", "ArrowDown", "Tab", "Enter"):
        assert "'%s'" % k in js
    assert "parameters.confirm = '1'" in js and "htmx:configRequest" in js
    assert "data-slash-dismiss" in js and "slash-card" in js
    assert "aria-selected" in js and "aria-disabled" in js and "aria-expanded" in js
    assert "pointerdown" in js and "}, true);" in js
    assert not re.search(r"\bon[a-z]+\s*=", js.replace("addEventListener", ""))
    assert "javascript:" not in js and "style.cssText" not in js


def test_css_tokens():
    css = CSS.read_text()
    block = css[css.index("/* Slash-command palette"):]
    for c in (".slash-list", ".slash-opt", ".slash-cmd", ".slash-desc", "aria-disabled"):
        assert c in block
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", block)
    assert "var(--" in block


@pytest.mark.skipif(shutil.which("node") is None, reason="node missing")
def test_helpers_in_node():
    code = r"""
globalThis.window={};globalThis.document=undefined;
eval(require('fs').readFileSync(process.argv[1],'utf8'));
const p=window.pmSlash;
const items=[{cmd:'/context',desc:'show usage',risk:'info'},{cmd:'/clear',desc:'wipe',risk:'danger'},{cmd:'/compact',desc:'context shrink',risk:'action'}];
console.log(JSON.stringify([p.filter(items,'/c').map(i=>i.cmd),p.filter(items,'/ear').map(i=>i.cmd),p.firstToken('/clear now'),
 !!p.needsConfirm(items,'/CLEAR x'),!!p.needsConfirm(items,'/context'),!!p.needsConfirm(items,'/clear/x'),!!p.needsConfirm(items,'hi /clear')]));
"""
    out = subprocess.run(["node", "-e", code, str(JS)], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [["/context", "/clear", "/compact"], ["/clear"], "/clear", True, False, False, False]
