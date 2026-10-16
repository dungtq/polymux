"""Composer "Save as issue" (issue-save.js + the snippets caret menu) and the `#draft=` prefill:
string-level contracts, plus node for the pure helpers."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pm.web.render import TEMPLATE_DIR

SRC = Path(__file__).resolve().parents[1] / "src/pm"
JS = SRC / "static/js/issue-save.js"
SNIP = SRC / "static/js/snippets.js"
T = TEMPLATE_DIR


def test_menu_item_appended_in_build():
    js = SNIP.read_text()
    build = js[js.index("function build(m)"):js.index("function insert(")]
    assert "if (m.childElementCount) return;" in build  # built once, the item is not a static template button
    assert "data-issue-save" in build and "'Save as issue'" in build and "snipitem" in build
    assert "role', 'separator'" in build and "role', 'menuitem'" in build
    assert "Save the text and images as an issue in this project (not sent)" in build
    assert "window.pm.issueSave" in build  # only where issue-save.js is loaded
    assert build.index("SNIPPETS.forEach") < build.index("data-issue-save")  # snippets first, the action last
    assert "data-issue-save" not in (T / "_composer_macros.html").read_text()


def test_posts_to_issue_with_csrf_header():
    js = JS.read_text()
    assert re.search(r"pm\.fetch\(url, \{ method: 'POST', body: body, csrf: 'header' \}\)", js)
    assert "/message$/, '/issue'" in js
    assert "body.set('text', text)" in js and "body.set('images', paths.join('\\n'))" in js
    assert "chipsReady" in js and "is-uploading" in js  # waits for uploads, sends finished chips only
    assert not re.search(r"(?<![\w.])fetch\(", js)  # pm.fetch only
    assert not re.search(r"\.submit\(|requestSubmit|htmx\.trigger|/message['\"]", js)  # never sent to the agent
    assert "btn.disabled = true" in js and "Issue #" in js and "pm.esc(url)" in js


def test_draft_prefill_uses_replace_state():
    js = JS.read_text()
    assert "draft=" in js and "history.replaceState" in js and "location.search + d.hash" in js
    assert "ta.focus()" in js and "DRAFT_MAX = 4000" in js


def test_pages_load_issue_save_after_attach():
    m = (T / "_composer_macros.html").read_text()
    t = m[m.index("{% macro composer_scripts("):]
    base = (T / "base.html").read_text()  # composer.js and composer-attach.js: every page, in <head> (before the body's)
    assert base.index("/static/js/composer.js") < base.index("/static/js/composer-attach.js") < base.index("</head>")
    assert "issue-save.js" not in base and "composer-attach.js" not in t and "issue-save.js" in t
    assert t.index("{% if mode != 'ended' %}") < t.index("issue-save.js")  # only with a live composer (agent page, Builder)
    for page in ("agent.html", "builder.html"):
        assert "composer_scripts(" in (T / page).read_text()


def test_issue_save_size():
    assert len(JS.read_text().splitlines()) <= 500


@pytest.mark.skipif(shutil.which("node") is None, reason="node missing")
def test_helpers_in_node():
    code = r"""
global.window={pm:{esc:String,chat:{}}};window.self=window.top=window;
global.document={readyState:'complete',addEventListener(){},querySelector(){return null},getElementById(){return null}};
global.location={hash:'',pathname:'/a/x',search:''};global.history={replaceState(){}};
require(process.argv[1]);
const s=window.pm.issueSave;
console.log(JSON.stringify([
  s.split('\n  Fix the  bar \n\nline 2\nline 3\n\n'),
  s.split('  \n \n'),
  s.issueUrl('/a/claude-1/message'), s.issueUrl('/a/x/other'),
  s.draftFrom('#draft=Fix%20issue%20%2312%3A%20t%0Apmctl%20issue%20show%2012'),
  s.draftFrom('#draft=a+b&chat=/a/y'), s.draftFrom('#chat=/a/y'), s.draftFrom(''),
  s.draftFrom('#draft=' + 'x'.repeat(5000)).text.length, s.draftFrom('#draft=%E0%A4%A').text
]));
"""
    out = subprocess.run(["node", "-e", code, str(JS)], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [
        {"title": "Fix the  bar", "body": "line 2\nline 3"},
        {"title": "", "body": ""},
        "/a/claude-1/issue", "",
        {"text": "Fix issue #12: t\npmctl issue show 12", "hash": ""},
        {"text": "a b", "hash": "#chat=/a/y"}, None, None,
        4000, "",
    ]


def test_saved_flash_link_is_not_boxed():
    js = JS.read_text()
    assert '<a class="flash-link" href=' in js
    css = (SRC / "static/css/composer.css").read_text()
    assert ".flash-inline .flash>.flash-link{" in css
