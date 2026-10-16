"""Backlog round 5: md viewer zoom, ended-page jump rail, model select width, composer snippets."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pm.web.render import TEMPLATE_DIR

SRC = Path(__file__).resolve().parents[1] / "src/pm"
T = TEMPLATE_DIR


def test_md_viewer_has_zoom_control():
    for name in ("_mdpane.html", "_drawer.html"):
        t = (T / name).read_text()
        head = re.search(r'<header class="mdpane-h">.*?</header>', t, re.S).group(0)
        head = head.replace('{% include "_mdzoom.html" %}', (T / "_mdzoom.html").read_text())  # one shared copy
        assert 'data-md-zoom="-1"' in head and 'data-md-zoom="1"' in head and "data-md-zoom-val" in head and "data-chat-zoom" not in head
    css = (SRC / "static/css/chat.css").read_text()
    assert ".mdpane-b .md" in css and "var(--md-size" in css


def test_ended_page_loads_jump_rail():
    t = (T / "agent_ended.html").read_text()
    assert "{{ jump_rail() }}" in t and 'import jump_rail' in t
    chrome = (T / "_chatchrome.html").read_text()
    assert 'data-page-jump="top"' in chrome and 'data-page-jump="bottom"' in chrome
    assert "/static/js/jumprail.js" in t and "/static/js/chat.js" in t


def test_model_pill_is_constrained():
    css = (SRC / "static/css/composer.css").read_text()
    m = re.search(r"\.mp-pill \.mp-m\{[^}]*\}", css).group(0)
    assert "text-overflow:ellipsis" in m  # a long model id is cut, the pill never widens the row
    menu = re.search(r"\.mp-menu\{[^}]*\}", css).group(0)
    assert "position:fixed" in menu  # out of the row's flow from the first open (in flow it shifted the pill before it was placed)


def test_composer_snippets_wired():
    comp = (T / "_composer_macros.html").read_text()  # shared by the agent page and Builder
    assert "data-snip" in comp and 'class="snipmenu pop" role="menu"' in comp
    # the caret sits in the composer bar, right group, just after the send button (send_split())
    split = comp[comp.index("{% macro send_split("):comp.index("{%- endmacro %}", comp.index("{% macro send_split("))]
    assert 'class="snipwrap csplit"' in split and split.index("csend tip") < split.index("csend-more") < split.index("snipmenu")
    bar = comp[comp.index("{% macro live_bar("):comp.index("{%- endmacro %}", comp.index("{% macro live_bar("))]
    assert bar.index("cbar-r") < bar.index("send_split(")
    assert "snipwrap" not in comp[comp.index('<div class="mrow">'):]
    scripts = comp[comp.index("{% macro composer_scripts("):]
    assert "/static/js/snippets.js" in scripts
    js = (SRC / "static/js/snippets.js").read_text()
    assert "SNIPPETS" in js and "dispatchEvent(new Event('input'" in js
    # one popup family: pm.popover places / closes it (no own place(), outside click or Esc handler)
    assert "pop.toggle(tog, m, { placement: 'top-end', role: 'menu' })" in js
    assert "function place(" not in js and "'keydown'" not in js and "getBoundingClientRect" not in js
    # inserts only; nothing submits or opens a native popup
    assert not re.search(r"\.submit\(|requestSubmit|alert\(|confirm\(|prompt\(", js)


@pytest.mark.skipif(shutil.which("node") is None, reason="node missing")
def test_snippet_insert_in_node():
    code = r"""
global.document={addEventListener(){}};global.window={};global.Event=function(t){this.type=t};
require(process.argv[1]);
const ta={value:'hello',selectionStart:5,selectionEnd:5,focus(){},setSelectionRange(){},dispatchEvent(){}};
window.pmSnippets.insert(ta,'go');
const b={value:'',selectionStart:0,selectionEnd:0,focus(){},setSelectionRange(){},dispatchEvent(){}};
window.pmSnippets.insert(b,'go');
console.log(JSON.stringify([ta.value,b.value]));
"""
    out = subprocess.run(["node", "-e", code, str(SRC / "static/js/snippets.js")], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == '["hello go","go"]'
