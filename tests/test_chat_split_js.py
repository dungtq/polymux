"""chat.js / composer / qchat.js split (Phase 5, composer C1): script tags in load order, file size limit, shared
namespaces, and the listeners each set of files registers at load time."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pm.web.render import TEMPLATE_DIR

PM = Path(__file__).resolve().parents[1] / "src/pm"
JS = PM / "static/js"
TPL = TEMPLATE_DIR
CHAT = ["chat.js", "chat-md.js"]  # the chat tab glue, listed by each page
COMPOSER = ["composer-dock.js", "agentmode.js", "chat-send.js", "atfiles.js",
            "issue-save.js", "snippets.js", "modelpick.js", "newchat.js"]  # composer_scripts(mode), in this order
BASE = ["popup.js", "composer.js", "composer-attach.js", "slash.js"]  # base.html: every page (quick chat is a composer)
QCHAT = ["qchat.js", "qchat-menu.js"]
SCRIPT = re.compile(r'<script src="/static/js/([^"]+)" defer></script>')

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _consecutive(template: str, names: list[str]) -> bool:
    got = SCRIPT.findall((TPL / template).read_text())
    i = got.index(names[0])
    return got[i:i + len(names)] == names


@pytest.mark.parametrize("template", ["agent.html", "agent_ended.html", "builder.html"])
def test_chat_scripts_in_order(template):
    t = (TPL / template).read_text()
    assert _consecutive(template, CHAT) and "composer_scripts(" in t  # one composer bundle, no own composer list
    assert "chat-composer.js" not in t and "chat-attach.js" not in t


def test_composer_scripts_in_order():
    assert _consecutive("_composer_macros.html", COMPOSER)
    m = (TPL / "_composer_macros.html").read_text()
    ended = m[m.index("{% macro composer_scripts("):]
    assert ended.index("agentmode.js") < ended.index("{% if mode != 'ended' %}")  # an ended page loads only these


def test_qchat_scripts_in_order_after_core():
    assert _consecutive("base.html", QCHAT) and _consecutive("base.html", BASE)
    got = SCRIPT.findall((TPL / "base.html").read_text())
    assert got.index("core.js") < got.index("composer.js") < got.index("qchat.js")
    for t in ("_composer_macros.html", "launch.html"):  # loaded once per page: never listed again
        assert not {"composer.js", "composer-attach.js", "slash.js"} & set(SCRIPT.findall((TPL / t).read_text())), t


@pytest.mark.parametrize("name", CHAT + COMPOSER + BASE + QCHAT)
def test_split_files_at_most_500_lines(name):
    assert len((JS / name).read_text().splitlines()) <= 500


def test_shared_namespace_contract():
    cmp_js, att, send = (JS / "composer.js").read_text(), (JS / "composer-attach.js").read_text(), (JS / "chat-send.js").read_text()
    assert "pm.composer = {" in cmp_js and "pm.composer.use = use;" in cmp_js
    assert "pm.composerAttach = { chipList: chipList, chipsReady: chipsReady, syncRequired: syncRequired, upload: upload };" in att
    for f in ("chipList", "chipsReady", "syncRequired"):
        assert f"at.{f}" in send
    assert not re.search(r"pm\.chat\b", cmp_js + att + send + (JS / "chat.js").read_text())  # the old shared namespace is gone
    assert "pm.qchat = {" in (JS / "qchat.js").read_text()
    assert "var q = window.pm && window.pm.qchat;\n  if (!q) return;" in (JS / "qchat-menu.js").read_text()


CHAT_HARNESS = r"""
const reg = [];
globalThis.window = { pm: {}, addEventListener: (t) => reg.push('w:' + t), matchMedia: () => ({ matches: false }) };
globalThis.document = { readyState: 'complete', addEventListener: (t, f, o) => reg.push('d:' + t + (o === true ? ':cap' : '')),
  getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] };
globalThis.location = { pathname: '/a/x' };
for (const f of process.argv.slice(1)) eval(require('fs').readFileSync(f, 'utf8'));
console.log(JSON.stringify({ reg, cmp: Object.keys(window.pm.composer).sort(), att: Object.keys(window.pm.composerAttach).sort(),
  comp: typeof window.pmComposer }));
"""

HARNESS_FILES = ["composer.js", "composer-dock.js", "composer-attach.js", "chat-send.js", "chat.js", "chat-md.js"]
CHAT_REG = [
    "d:htmx:beforeSwap", "d:pm:agent_state", "d:change", "w:pagehide", "w:resize", "d:htmx:load",  # composer.js (dock: a plugin)
    "d:click", "d:change", "d:paste", "d:dragover", "d:dragleave", "d:drop",  # composer-attach.js
    "d:htmx:configRequest", "d:htmx:beforeRequest", "d:htmx:beforeSwap", "d:htmx:afterRequest", "d:click", "d:change",  # chat-send.js
    "d:click", "d:htmx:afterSettle", "d:htmx:load", "d:htmx:afterRequest", "d:scroll:cap", "d:click", "d:pm:composer",
    "d:htmx:beforeSwap", "d:htmx:load",  # chat.js (pm:composer: the heading pill follows a model switch)
    "d:click", "d:keydown", "d:htmx:afterSettle", "d:htmx:load", "d:pm:ci-update",  # chat-md.js
]


@node
def test_chat_files_register_listeners_once_in_order():
    out = subprocess.run(["node", "-e", CHAT_HARNESS, *[str(JS / n) for n in HARNESS_FILES]],
                         capture_output=True, text=True, check=True)
    res = json.loads(out.stdout)
    assert res["reg"] == CHAT_REG
    assert res["cmp"] == ["histList", "histMove", "init", "insert", "root", "running", "sends", "slot", "sync", "use"]
    assert res["att"] == ["chipList", "chipsReady", "syncRequired", "upload"] and res["comp"] == "object"


QCHAT_HARNESS = r"""
const reg = [];
function fake(name) {
  return { style: {}, classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } }, dataset: {},
    options: [], hidden: true, value: '', textContent: '',
    addEventListener: (t) => reg.push(name + ':' + t), setAttribute() {}, appendChild() {}, focus() {},
    querySelector: (s) => fake(name + '>' + s), querySelectorAll: (s) => [fake(name + '>>' + s)] };
}
globalThis.window = { pm: {}, addEventListener: (t) => reg.push('w:' + t), matchMedia: () => ({ matches: false }),
  innerWidth: 1000, innerHeight: 800 };
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.location = { href: 'https://x/' };
globalThis.history = { replaceState() {} };
globalThis.document = { readyState: 'complete', addEventListener: (t) => reg.push('d:' + t),
  getElementById: (id) => process.env.NOQC ? null : fake('#' + id), querySelector: () => null, querySelectorAll: () => [] };
window.pm.esc = (t) => String(t);
for (const f of process.argv.slice(1)) eval(require('fs').readFileSync(f, 'utf8'));
console.log(JSON.stringify({ reg, frame: typeof window.__qchatFrame }));
"""

QCHAT_REG = [
    "#qchat>.qc-max:click", "#qchat>.qc-head:dblclick", "#qchat>.qc-head:pointerdown", "w:resize",
    "#qchat>.qc-mcps>.qc-mcppop:change", "#qchat>.qc-skl>.qc-mcppop:change", "#qchat>.qc-mcps>.qc-mcpbtn:click",
    "#qchat>.qc-skl>.qc-mcpbtn:click", "#qchat>#qc-tools:change", "#qchat>#qc-dir:change",
    "#qchat>>.qc-hsearch:input", "#qchat>[data-qc-hist]:click", "#qchat>[data-qc-expand]:click",
    "#qchat>[data-cmp-form]:submit",  # qchat.js (Enter, paste, drop, attach, "/" and Stop: the shared composer)
    "#qchat>.qc-x:click", "#qchat>.qc-new:click", "#qchat>.qc-rmenu:click", "#qchat:click", "d:click", "d:keydown",
]


@node
@pytest.mark.parametrize("noqc", [False, True])
def test_qchat_files_register_listeners_once_in_order(noqc):
    import os
    env = dict(os.environ, NOQC="1") if noqc else {k: v for k, v in os.environ.items() if k != "NOQC"}
    out = subprocess.run(["node", "-e", QCHAT_HARNESS, *[str(JS / n) for n in QCHAT]],
                         capture_output=True, text=True, check=True, env=env)
    res = json.loads(out.stdout)
    # no #qchat on the page: both files do nothing
    assert res == ({"reg": [], "frame": "undefined"} if noqc else {"reg": QCHAT_REG, "frame": "function"})
