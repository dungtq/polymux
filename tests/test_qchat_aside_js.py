"""btw (pm's /btw): composer button, /btw intercept, quick chat hooks, script loading (string-level + rendered)."""
import re
import shutil
import subprocess

import pytest
from helpers import chat_env as env  # noqa: F401  (fixture)
from helpers import login

from pm.web.render import STATIC_DIR, TEMPLATE_DIR

ASIDE = STATIC_DIR / "js/qchat-aside.js"
QCHAT = STATIC_DIR / "js/qchat.js"
BASE = TEMPLATE_DIR / "base.html"


def test_btw_intercept_is_capture_phase_submit():
    js = ASIDE.read_text()
    m = re.search(r'document\.addEventListener\("submit", function \(e\) \{[\s\S]*?\n    \}, true\);', js)
    assert m, "the /btw intercept must be a capture-phase submit listener on document"
    block = m.group(0)
    assert '"[data-composer] [data-cmp-form]"' in block and "BTW.exec" in block
    assert "e.preventDefault(); e.stopImmediatePropagation();" in block
    assert "var BTW = /^\\/btw(?:\\s+([\\s\\S]*))?$/i;" in js
    # never posts itself: no fetch / htmx call from the aside script, only quick chat's own send
    assert "fetch(" not in js and "htmx.ajax" not in js and "XMLHttpRequest" not in js and ".submit()" not in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node missing")
def test_btw_regex_in_node():
    code = r"""
const src=require('fs').readFileSync(process.argv[1],'utf8');
const BTW=eval(/var BTW = (\/.*\/i);/.exec(src)[1]);
const f=s=>{const m=BTW.exec(s.trim());return m?(m[1]||'').trim():null};
console.log(JSON.stringify([f('/btw what is x'),f('/BTW  why?'),f('/btw'),f('  /btw\nmulti\nline '),f('/btwx'),f('hi /btw x'),f('/b')]));
"""
    out = subprocess.run(["node", "-e", code, str(ASIDE)], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == '["what is x","why?","","multi\\nline",null,null,null]'


def test_qchat_hooks():
    js = QCHAT.read_text()
    assert 'if (C && C.agent && !C.id) body.append("agent", C.agent);' in js
    assert 'new CustomEvent("pm:qchat-conv", { detail: C })' in js
    assert "setAgent: function (a)" in js and "loadOpts: loadOpts" in js
    a = ASIDE.read_text()
    assert "q.aside = aside" in a and '"pm:qchat-conv"' in a and '"qc-use"' in a and "Use in composer" in a
    assert len(js.splitlines()) <= 500


def test_base_loads_aside_everywhere_after_qchat():
    t = BASE.read_text()
    tag = '<script src="/static/js/qchat-aside.js" defer></script>'
    assert t.count(tag) == 1
    i = t.index(tag)
    start = t.rindex("{% if not embed %}", 0, i)
    assert "{% endif %}" in t[start:i]  # outside the guard: the drawer hands /btw to the top page's quick chat
    assert t.index('/static/js/qchat-menu.js') < i


def test_use_in_composer_goes_through_the_shared_insert():
    """"Use in composer": the agent composer's own pm.composer.insert (its document: the drawer frame has its own), never
    quick chat's box (a composer too since C2) and no second copy of the cursor / maxlength / input-event code."""
    a = ASIDE.read_text()
    assert "W.pm && W.pm.composer" in a and "cmp.insert(ta, text)" in a
    assert '[data-composer="live"] [data-cmp-form], [data-composer="builder"] [data-cmp-form]' in a
    assert "setSelectionRange" not in a and "maxLength - ta.value.length" not in a
    assert "setSelectionRange" not in QCHAT.read_text()  # qchat.js has no insert copy either (composer-attach.js inline)


def test_drawer_hands_off_to_top_quick_chat():
    a = ASIDE.read_text()
    assert "window.top.pm && window.top.pm.qchat" in a and "up.aside(id, lbl, t)" in a
    assert 'b.hidden = false' in a  # the button shows only once a quick chat can take it


def test_composer_renders_aside_button(env):
    s, c = env
    login(s, c)
    t = c.get("/a/claude-12").text
    comp = t[t.index('data-composer="live"'):t.index("</section>", t.index('data-composer="live"'))]
    left = comp[comp.index('class="cbar-l"'):comp.index('class="cbar-r"')]
    m = re.search(r'<button[^>]*\bdata-aside\b[^>]*>', left)
    assert m and 'data-agent="claude-12"' in m.group(0) and "data-label=" in m.group(0)
    assert 'class="cicon tip"' in m.group(0)
    assert 'aria-label="btw: ask a side question about this chat (Quick chat, not sent to the agent)"' in m.group(0)
    assert "/static/js/qchat-aside.js" in t
    e = c.get("/a/claude-12?embed=1").text  # drawer: rendered hidden, shown by the script when the top page has quick chat
    assert re.search(r'<button[^>]*\bdata-aside hidden\b', e) and "/static/js/qchat-aside.js" in e
