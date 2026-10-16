"""Main chat Compact view (docs/HANDOFF-chat-compact.md): the pure planner of static/js/chat-compact.js run in
node, the template hooks it relies on (data-turn, data-dur), the Detail | Compact toggle partial and where it is
included, script/style order in base.html, and static rules for chat-compact.js."""

import json
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from helpers import (
    builder_env,  # noqa: F401  (fixture)
    login,
)
from helpers import chat_env as env  # noqa: F401  (fixture)

from pm.core.models import ChatItem
from pm.web.chatctx import blocks
from pm.web.render import STATIC_DIR, TEMPLATE_DIR
from pm.web.render import env as jenv

JS = STATIC_DIR / "js"
TPL = TEMPLATE_DIR
_NODE = shutil.which("node")

# no `document` at all: plan must load and run without a DOM
_HARNESS = r"""
globalThis.window = {};
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
const P = window.pm.ciCompact.plan;
const u = (seq, turn) => ({ k: 'user', seq, turn, parent: 0 });
const x = (seq, turn, short) => ({ k: 'text', seq, turn, parent: 0, short: !!short });
const g = (seq, turn, parent, run) => ({ k: 'tg', seq, turn, parent, run: !!run });
const e = (seq, turn, dur) => ({ k: 'end', seq, turn, parent: 0, dur: dur || 0 });
const ob = { k: 'ob', seq: 0, turn: '', parent: 0 };
const cases = JSON.parse(process.argv[2]);
"""


def _plan(code: str):
    out = subprocess.run([_NODE, "-e", _HARNESS + code, str(JS / "chat-compact.js"), "[]"], capture_output=True,
                         text=True, check=True, timeout=20)
    return json.loads(out.stdout)


def _node(i):
    return {"t": "node", "i": i}


def _notes(key, texts, tools, took=""):
    return {"t": "notes", "key": key, "texts": texts, "tools": tools, "took": took}


def _final(i, tools, took=""):
    return {"t": "final", "i": i, "tools": tools, "took": took}


needs_node = pytest.mark.skipif(_NODE is None, reason="node not installed")


@needs_node
def test_plan_loads_without_window_or_document():
    code = r"""
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
console.log(JSON.stringify(typeof globalThis.pm.ciCompact.plan));
"""
    out = subprocess.run([_NODE, "-e", code, str(JS / "chat-compact.js")], capture_output=True, text=True,
                         check=True, timeout=20)
    assert json.loads(out.stdout) == "function"


@needs_node
def test_plan_finished_multi_reply_turn():
    # notes = every reply but the last; the last reply is the final; tools after it (its own: parent = its seq,
    # and any later group) go to the final
    got = _plan(r"""console.log(JSON.stringify(P([
  u(1, 't1'), x(2, 't1'), g(3, 't1', 2), x(4, 't1'), g(5, 't1', 4), x(6, 't1'), g(7, 't1', 6), g(8, 't1', 4),
  e(9, 't1')])));""")
    assert got == [_node(0), _notes(1, [1, 3], [2, 4]), _final(5, [6, 7]), _node(8)]


@needs_node
def test_plan_single_reply_turn_has_no_notes():
    got = _plan(r"""console.log(JSON.stringify(P([u(1, 't1'), x(2, 't1'), g(3, 't1', 2), e(4, 't1')])));""")
    assert got == [_node(0), _final(1, [2]), _node(3)]


@needs_node
def test_plan_running_turn_all_notes_no_final():
    got = _plan(r"""console.log(JSON.stringify(P([u(1, 't1'), x(2, 't1'), g(3, 't1', 2), x(4, 't1')])));""")
    assert got == [_node(0), _notes(1, [1, 3], [2])]


@needs_node
def test_plan_mid_turn_user_splits_notes():
    got = _plan(r"""console.log(JSON.stringify(P([
  u(1, 't1'), x(2, 't1'), g(3, 't1', 2), u(4, 't1'), x(5, 't1'), g(6, 't1', 5), x(7, 't1'), e(8, 't1')])));""")
    assert got == [_node(0), _notes(1, [1], [2]), _node(3), _notes(4, [4], [5]), _final(6, []), _node(7)]


@needs_node
def test_plan_tools_only_turn_with_end():
    got = _plan(r"""console.log(JSON.stringify(P([u(1, 't1'), g(2, 't1', 0), e(3, 't1')])));""")
    assert got == [_node(0), _notes(1, [], [1]), _node(2)]


@needs_node
def test_plan_tool_group_before_first_text():
    # the group before any reply (parent = the user seq) stays in the notes, not with the final
    got = _plan(r"""console.log(JSON.stringify(P([
  u(1, 't1'), g(2, 't1', 1), x(3, 't1'), g(4, 't1', 3), x(5, 't1'), e(6, 't1')])));""")
    assert got == [_node(0), _notes(1, [2], [1, 3]), _final(4, []), _node(5)]


@needs_node
def test_plan_tools_before_only_reply_go_to_final():
    # a segment with 0 texts that holds the final: its tools join the final's, no tools-only notes block
    got = _plan(r"""console.log(JSON.stringify(P([u(1, 't1'), g(2, 't1', 1), x(3, 't1'), e(4, 't1')])));""")
    assert got == [_node(0), _final(2, [1]), _node(3)]
    got = _plan(r"""console.log(JSON.stringify(P([
  u(1, 't1'), g(2, 't1', 1), x(3, 't1'), g(4, 't1', 3), e(5, 't1')])));""")
    assert got == [_node(0), _final(2, [1, 3]), _node(4)]
    # same after a mid-turn user message: first segment keeps its notes, the second has none
    got = _plan(r"""console.log(JSON.stringify(P([
  u(1, 't1'), x(2, 't1'), u(3, 't1'), g(4, 't1', 0), x(5, 't1'), e(6, 't1')])));""")
    assert got == [_node(0), _notes(1, [1], []), _node(2), _final(4, [3]), _node(5)]


@needs_node
def test_plan_tools_only_notes_stay_without_final():
    # running turn whose segment has tools only: notes block stays (no final to fold into)
    got = _plan(r"""console.log(JSON.stringify(P([u(1, 't1'), g(2, 't1', 1)])));""")
    assert got == [_node(0), _notes(1, [], [1])]


@needs_node
def test_plan_segment_without_user_keys_on_first_item():
    got = _plan(r"""console.log(JSON.stringify(P([g(2, 't1', 0), x(3, 't1'), x(4, 't1'), e(5, 't1')])));""")
    assert got == [_notes(2, [1], [0]), _final(2, []), _node(3)]


@needs_node
def test_plan_outbox_bubbles_stay_last_as_nodes():
    got = _plan(r"""console.log(JSON.stringify(P([ob, u(1, 't1'), x(2, 't1'), e(3, 't1'), ob])));""")
    assert got == [_node(1), _final(2, []), _node(3), _node(0), _node(4)]


@needs_node
def test_plan_two_consecutive_turns_keys():
    got = _plan(r"""console.log(JSON.stringify(P([
  u(1, 't1'), x(2, 't1'), g(3, 't1', 2), x(4, 't1'), e(5, 't1'),
  u(6, 't2'), x(7, 't2'), g(8, 't2', 7), x(9, 't2'), e(10, 't2')])));""")
    assert got == [_node(0), _notes(1, [1], [2]), _final(3, []), _node(4),
                   _node(5), _notes(6, [6], [7]), _final(8, []), _node(9)]


@needs_node
def test_plan_groups_all_turns_of_one_user_message():
    # Claude ends a turn while it waits for a sub-agent; the next turn has no user message: one span, one notes
    # block, one final, "took" = the turns added up
    got = _plan(r"""console.log(JSON.stringify(P([
  u(1, 't1'), x(2, 't1', 1), g(3, 't1', 2), e(4, 't1', 7),
  x(5, 't2', 1), g(6, 't2', 5), e(7, 't2', 13), x(8, 't3'), e(9, 't3', 31)])));""")
    assert got == [_node(0), _notes(1, [1, 4], [2, 5]), _final(7, [], "51s"), _node(3), _node(6), _node(8)]


@needs_node
def test_plan_short_last_reply_stays_a_note_with_took():
    got = _plan(r"""console.log(JSON.stringify(P([
  u(1, 't1'), x(2, 't1', 1), g(3, 't1', 2), e(4, 't1', 40), x(5, 't2', 1), e(6, 't2', 30)])));""")
    assert got == [_node(0), _notes(1, [1, 4], [2], "1m"), _node(3), _node(5)]
    # a short single reply too: no final bubble, a one-step notes block
    got = _plan(r"""console.log(JSON.stringify(P([u(1, 't1'), x(2, 't1', 1), e(3, 't1', 5)])));""")
    assert got == [_node(0), _notes(1, [1], [], "5s"), _node(2)]


@needs_node
def test_plan_running_background_task_keeps_span_open():
    # the turn ended but a background task still runs: Claude comes back, so no final yet and no "took"
    got = _plan(r"""console.log(JSON.stringify(P([u(1, 't1'), x(2, 't1'), g(3, 't1', 2, 1), e(4, 't1', 9)])));""")
    assert got == [_node(0), _notes(1, [1], [2]), _node(3)]
    # a span whose last item is not a turn end is still running
    got = _plan(r"""console.log(JSON.stringify(P([u(1, 't1'), x(2, 't1'), e(3, 't1', 9), x(4, 't2')])));""")
    assert got == [_node(0), _notes(1, [1, 3], []), _node(2)]


@needs_node
def test_dur_parse_and_format_round_trip():
    got = _plan(r"""const C = window.pm.ciCompact;
console.log(JSON.stringify([C.secs('45s'), C.secs('5m'), C.secs('2h 3m'), C.secs('1d 4h'), C.secs(''), C.secs(null),
  C.dur(45), C.dur(300), C.dur(7380), C.dur(100800), C.dur(-3)]));""")
    assert got == [45, 300, 7380, 100800, 0, 0, "45s", "5m", "2h 3m", "1d 4h", "0s"]


# ---- templates ----

def _agent():
    return SimpleNamespace(id="claude-12", kind=SimpleNamespace(value="claude"), label="", window_name="",
                           project="alpha", transcript="")


def _render(items):
    return jenv.get_template("_chatitems.html").render(mode="poll", agent=_agent(), ended=False,
                                                       blocks=blocks(items), outbox=None, model_name="x", now=99.0)


def _it(seq, kind, turn="t1", **kw):
    return ChatItem(seq=seq, rev=seq, uid=f"u{seq}", conv="c", kind=kind, turn=turn, ts=1.0, **kw)


def test_items_carry_data_turn_and_end_duration():
    out = _render([_it(1, "user", text="go"), _it(2, "text", text="a"),
                   _it(3, "tool", name="Read", parent=2, turn="t1"), _it(4, "text", text="b"),
                   _it(5, "end", note="2 replies · 3 tool calls · 41s")])
    for i in ("i-1", "i-2", "i-4", "i-5"):
        assert re.search(rf'id="{i}" [^>]*data-turn="t1"', out), i
    assert re.search(r'<details id="g-\d+" class="ci-tg" [^>]*data-turn="t1"', out)  # from its first tool
    assert re.search(r'<p id="i-5" class="ci-end" [^>]*data-dur="41s"', out)


def test_end_item_without_duration_has_no_data_dur():
    out = _render([_it(1, "user", text="go"), _it(2, "text", text="a"), _it(3, "end", note="1 reply · 0 tool calls")])
    end = re.search(r'<p id="i-3" class="ci-end"[^>]*>', out).group(0)
    assert "data-dur" not in end and 'data-turn="t1"' in end


def test_end_note_wording_feeds_data_dur():
    # pins pm.chat.itemsync.end_note to the end_item macro: a wording change must not silently drop "took …"
    from pm.chat.itemsync import end_note

    note = end_note(2, 3, 41.0)
    dur = note.split(" · ")[-1]
    assert dur and len(note.split(" · ")) == 3
    out = _render([_it(1, "user", text="go"), _it(2, "text", text="a"), _it(3, "end", note=note)])
    end = re.search(r'<p id="i-3" class="ci-end"[^>]*>', out).group(0)
    assert re.search(r'data-dur="([^"]*)"', end).group(1) == dur
    out = _render([_it(1, "user", text="go"), _it(2, "text", text="a"), _it(3, "end", note=end_note(1, 0, None))])
    end = re.search(r'<p id="i-3" class="ci-end"[^>]*>', out).group(0)
    assert "data-dur" not in end


def test_toggle_partial_buttons():
    t = jenv.from_string('{% from "_chatview.html" import chatview %}{{ chatview() }}').render()
    assert 'role="group"' in t and 'aria-label="Chat view"' in t
    btns = re.findall(r'<button type="button" data-chat-view="(\w+)" aria-pressed="(\w+)"', t)
    assert btns == [("detail", "false"), ("compact", "true")]


@pytest.mark.parametrize("name", ["agent.html", "agent_ended.html", "builder.html", "_drawer.html"])
def test_toggle_included(name):
    src = (TPL / name).read_text()
    if name in ("agent.html", "agent_ended.html"):  # via _chatchrome.html jumpzoom() (chatview + zoom)
        assert '{% from "_chatchrome.html" import jump_rail, jumpzoom %}' in src and "{{ jumpzoom() }}" in src
        src = (TPL / "_chatchrome.html").read_text()
    assert '{% from "_chatview.html" import chatview %}' in src and "{{ chatview() }}" in src


def test_toggle_placement():
    for name in ("agent.html", "agent_ended.html"):
        assert '<span class="ah-tools">{{ jumpzoom() }}</span>' in (TPL / name).read_text(), name
    assert "{{ chatview() }}{{ zoomctl() }}" in (TPL / "_chatchrome.html").read_text()
    b = (TPL / "builder.html").read_text()
    head = re.search(r'<header class="bld-ph">.*?</header>', b).group(0)
    assert "{{ chatview() }}" in head
    d = (TPL / "_drawer.html").read_text()
    tools = d[d.index('id="vc-tools"'):d.index('zoomctl("vc-zoom")')]
    assert "{{ chatview() }}" in tools


def test_base_loads_compact_assets_in_order():
    base = (TPL / "base.html").read_text()
    assert base.index("/static/css/chat-items.css") < base.index("/static/css/chat-compact.css")
    assert "/static/js/chat-items.js" not in base


def test_agent_page_loads_compact_js_before_chat_items(env):
    s, c = env
    login(s, c)
    t = c.get("/a/claude-12").text
    assert t.index("/static/js/chat-compact.js") < t.index("/static/js/chat-items.js")
    assert t.index("/static/css/chat-items.css") < t.index("/static/css/chat-compact.css")
    assert 'data-chat-view="compact"' in t


def test_builder_page_loads_compact_js_before_chat_items(builder_env):
    s, c, _proj, _ = builder_env
    login(s, c)
    t = c.get("/a/claude-12/builder").text
    assert t.index("/static/js/chat-compact.js") < t.index("/static/js/chat-items.js")
    assert 'data-chat-view="detail"' in t


# ---- static rules ----

def test_chat_compact_js_static_rules():
    js = (JS / "chat-compact.js").read_text()
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    uses = [ln for ln in code.splitlines() if "localStorage" in ln]
    assert uses, "expected localStorage use"
    for ln in uses:
        assert re.search(r"try\s*\{[^}]*localStorage", ln), ln
    assert "fetch(" not in code and "XMLHttpRequest" not in code
    assert "innerHTML" not in code and "eval(" not in code
    assert "pm.ciCompact" in code
