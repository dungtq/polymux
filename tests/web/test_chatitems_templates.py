"""Chat items templates and static files: page wiring, outbox bubble, composer
placeholder, Jinja filters, and the static contract of chat-items.js / chatlog.js / chat-items.css."""

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import (
    builder_env,  # noqa: F401  (fixture)
    login,
)
from helpers import chat_env as env  # noqa: F401  (fixture)

from pm.agents import head as agenthead
from pm.core import timefmt
from pm.core.models import ChatItem, OutboxMsg
from pm.web.chatctx import blocks
from pm.web.render import STATIC_DIR, TEMPLATE_DIR
from pm.web.render import env as jenv

JS = STATIC_DIR / "js"
CSS = STATIC_DIR / "css"


def _agent(kind="claude", label="", window_name=""):
    return SimpleNamespace(id="claude-12", kind=SimpleNamespace(value=kind), label=label, window_name=window_name,
                           project="alpha", transcript="")


def _ob(state, **kw):
    return OutboxMsg(id=4, agent_id="claude-12", text="line one\nline **two**", state=state, created=1.0, updated=1.0, **kw)


def test_outbox_bubble_renders_alone_and_without_agent():
    t = jenv.get_template("_outbox.html").render(m=_ob("wait"), agent=_agent(), csrf="x")
    assert t.strip().startswith('<article id="ob-4" class="chat-bubble role-user ci-user ci-ob ob-wait" data-ob="4"')
    assert "line one<br" in t and "<strong>two</strong>" in t  # markdown, line breaks kept
    assert re.findall(r'data-ob-act="(\w+)"', t) == ["now", "edit", "cancel"]
    t = jenv.get_template("_outbox.html").render(m=_ob("wait"), agent=None)
    assert re.findall(r'data-ob-act="(\w+)"', t) == ["edit", "cancel"]  # Send now needs Claude's own queue
    t = jenv.get_template("_outbox.html").render(m=_ob("wait"), agent=_agent("codex"))
    assert "Send now" not in t
    t = jenv.get_template("_outbox.html").render(m=_ob("failed", error="not confirmed"), agent=None)
    assert "Not sent &middot; not confirmed" in t and 'data-ob-act="retry"' in t


def test_groups_fold_and_sit_under_their_parent():
    items = [ChatItem(seq=1, rev=1, uid="a", conv="c", kind="text", turn="t", ts=1.0, text="hi"),
             ChatItem(seq=2, rev=2, uid="b", conv="c", kind="tool", turn="t", ts=1.0, name="Agent", parent=1),
             ChatItem(seq=3, rev=3, uid="d", conv="c", kind="text", turn="t", ts=2.0, text="next"),
             ChatItem(seq=4, rev=4, uid="e", conv="c", kind="tool", turn="t", ts=2.0, name="Edit", parent=3,
                      status="ok", ended=2.25)]
    out = jenv.get_template("_chatitems.html").render(mode="poll", agent=_agent(), ended=False, blocks=blocks(items),
                                                     outbox=None, model_name="opus-5-5", now=3.0)
    assert [m for m in re.findall(r'id="((?:i|g)-\d+)"', out)] == ["i-1", "g-1", "i-2", "i-3", "g-3", "i-4"]
    assert "<details" in out and "<details open" not in out and " open>" not in out
    assert '<span class="tchip tf-agent">Sub-Agent <b>1</b></span>' in out  # chip label as before
    assert 'data-name="Agent"' in out and "0.2s" in out


def test_filters():
    assert jenv.filters["dur"] is timefmt.elapsed and jenv.filters["tmux_win"] is agenthead.tmux_win
    assert jenv.from_string("{{ 4.2 | dur }} {{ a | tmux_win }}").render(a=_agent(label="dev-pm-1-claude")) == "4.2s dev-pm:1"
    assert str(jenv.from_string("{{ t | md }}").render(t="a\nb <i>")) == "<p>a<br />\nb &lt;i&gt;</p>\n"


def test_agent_page_wiring(env, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", tmp_path.as_posix())  # no real ~/.claude settings default in the placeholder
    s, c = env
    login(s, c)
    t = c.get("/a/claude-12").text
    order = [t.index(x) for x in ('id="ci"', 'id="chat"', 'id="ci-pending"', 'data-composer="live"')]
    assert order == sorted(order)  # items, live strip, waiting outbox bubbles, composer
    assert 'data-agent="claude-12"' in t and 'hx-get="/a/claude-12/chat"' in t
    assert "chat/log" not in t and 'id="chatlog"' not in t
    assert t.index("/static/js/chatlog.js") < t.index("/static/js/chat-items.js")
    ph = re.search(r'placeholder="(Message to [^"]*)"', t).group(1)
    assert ph == "Message to claude in work:0"  # model (none known in tests: the kind) in <session>:<window>
    assert 'data-ph-short="Message claude"' in t  # composer.js swaps it in under 700px


def test_ended_page_has_items_no_strip():
    tpl = (TEMPLATE_DIR / "agent_ended.html").read_text()
    assert 'id="ci"' in tpl and "data-ended" in tpl and 'id="chat"' not in tpl and "chat-items.js" in tpl


def test_builder_uses_items_and_live_strip(builder_env, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", tmp_path.as_posix())  # no real ~/.claude settings default in the placeholder
    s, c, _proj, _ = builder_env
    login(s, c)
    t = c.get("/a/claude-12/builder").text
    msgs = t[t.index('id="bld-msgs"'):t.index('data-composer="builder"')]
    assert "data-ci-scroll" in msgs and "data-live" in msgs
    assert 'id="ci"' in msgs and 'hx-get="/a/claude-12/chat"' in msgs and 'id="ci-pending"' in msgs
    assert "chat/log" not in t and "every 4s\" hx-swap=\"innerHTML\" data-nodiff><p" not in t
    assert "/static/js/chat-items.js" in t and "/static/js/chatlog.js" in t
    assert 'placeholder="Message to claude in work:0"' in t


def test_chat_items_js_contract():
    js = (JS / "chat-items.js").read_text()
    assert len(js.splitlines()) <= 500
    for tok in ("pm:chat_items", "pm:chat_outbox", "after_rev=", "before=", "X-Rev", "X-Reset", "X-More", "X-Now",
                "data-start", "pm.fetch(", "csrf: 'both'", "window.pmConfirm", "/outbox/", "insertBySeq",
                "pm.chatScroll", "pm.chatItems", "'toggle'", "setInterval"):
        assert tok in js, tok
    assert "innerHTML" not in js and "eval(" not in js and "window.confirm" not in js and "alert(" not in js
    assert "fetch(" not in js.replace("pm.fetch(", "")  # archcheck js-fetch
    live = (JS / "live.js").read_text()
    assert "'chat_items', 'chat_outbox'" in live


def test_chatlog_js_is_scroll_only():
    js = (JS / "chatlog.js").read_text()
    for tok in ("pm.chatScroll", "follow:", "bottom:", "keep:", "restore:", "ResizeObserver", "data-ci-scroll"):
        assert tok in js, tok
    for gone in ("parameters.after", "chatlog-list", "data-turnbox", "dedupe", "hx-swap-oob"):
        assert gone not in js, gone


def test_chat_items_css_tokens_only():
    css = (CSS / "chat-items.css").read_text()
    assert len(css.splitlines()) <= 500
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(", css)
    for sel in (".ci-tg", ".ci-tr", ".ci-ts.is-wait", ".ci-end", ".ob-st", ".ob-act", ".ci-tail", "pointer: coarse",
                "max-width: 559px", "prefers-reduced-motion"):
        assert sel in css, sel


def test_kinds_without_tool_results_show_no_spinner():
    it = ChatItem(seq=2, rev=2, uid="x:1", conv="c", kind="tool", turn="t", ts=1.0, name="exec_command", line="ls")
    out = jenv.get_template("_chatitems.html").render(mode="poll", agent=_agent("codex"), ended=False,
                                                     blocks=blocks([it]), outbox=None, model_name="x", now=99.0)
    assert '<span class="ci-ts"></span>' in out and "data-start" not in out and "running" not in out



def test_outbox_bubble_flow_flag_and_live_age():
    for st in ("saved", "wait", "failed"):
        t = jenv.get_template("_outbox.html").render(m=_ob(st), agent=_agent())
        assert "data-flow" not in t, st  # waiting: stays in #ci-pending
    for st in ("sending", "inpane", "queued", "done"):
        t = jenv.get_template("_outbox.html").render(m=_ob(st), agent=_agent())
        assert " data-flow " in t, st  # delivered: chat-items.js moves it into the conversation
    t = jenv.get_template("_outbox.html").render(m=_ob("wait"), agent=_agent())
    assert '<span class="muted ob-age" data-ago="1.0">' in t  # counts from the created time, ticked client-side
    out = jenv.get_template("_chatitems.html").render(mode="full", agent=_agent(), ended=False, blocks=[],
                                                     outbox=[_ob("queued")], model_name="x", now=3.0)
    assert out.index('class="ci-items"') < out.index('class="ci-obs"') and "data-flow" in out


def test_chat_items_js_places_outbox_bubbles():
    js = (JS / "chat-items.js").read_text()
    for tok in ("pm.ciRules", "data-flow", "data-ago", "insertUser", "toFlow", "rules.replaces"):
        assert tok in js, tok


_NODE = shutil.which("node")
_HARNESS = r"""
globalThis.window = {}; globalThis.document = { getElementById: () => null };
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
const R = window.pm.ciRules;
"""


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_ci_rules_in_node():
    code = r"""
const it = (seq) => ({ seq, ob: false }), ob = { seq: 0, ob: true };
const nodes = [it(1), it(2), it(5), ob, ob];
console.log(JSON.stringify([
  R.obTarget(false, 'none'), R.obTarget(true, 'none'), R.obTarget(true, 'pending'), R.obTarget(false, 'pending'),
  R.obTarget(true, 'flow'), R.obTarget(false, 'flow'),
  R.slot(nodes, 6), R.slot(nodes, 3), R.slot(nodes, 0), R.slot([], 4),
  R.replaces(nodes, 3, 3), R.replaces(nodes, 3, 4), R.replaces(nodes, 2, 3), R.replaces(nodes, 4, 3),
  R.ago(5), R.ago(125), R.ago(7300), R.ago(90000), R.ago(-3)]));
"""
    out = subprocess.run([_NODE, "-e", _HARNESS + code, str(JS / "chat-items.js")], capture_output=True, text=True,
                         check=True)
    assert json.loads(out.stdout) == [
        "pending", "flow", "flow", "stay", "stay", "stay",  # delivered: once into the flow, never again
        3, 2, 0, 0,  # new items go before trailing bubbles, by seq otherwise
        True, True, False, False,  # the user item takes its bubble's spot only when nothing else lies between
        "5s ago", "2m ago", "2h ago", "1d ago", "0s ago"]


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_ci_rules_done_last_in_node():
    code = r"""
console.log(JSON.stringify([
  R.doneLast(['user', 'text', 'tool', 'end', 'user', 'text', 'tool', 'end']),
  R.doneLast(['user', 'text', 'end', 'user', 'text', 'tool', 'text']),
  R.doneLast(['user', 'text', 'tool', 'tool', 'end', 'user']),
  R.doneLast(['user', 'text', 'tool']),
  R.doneLast(['end', 'text']),
  R.doneLast(['user', 'text', 'end', 'user', 'tool', 'end']),
  R.doneLast([]),
  R.doneLast(['user', 'text', 'end', 'tool']),
  R.doneLast(['user', 'text', 'end', 'text']),
  R.doneLast(['user', 'text', 'end', 'end'])]));
"""
    out = subprocess.run([_NODE, "-e", _HARNESS + code, str(JS / "chat-items.js")], capture_output=True, text=True,
                         check=True)
    # the reply right above the last turn end (tool groups between); a tools-only last turn marks none, and
    # a running turn (prompt, reply or tool after the last end) marks none either: only finished turns
    assert json.loads(out.stdout) == [5, -1, -1, -1, -1, -1, -1, -1, -1, 1]


@pytest.mark.parametrize("ended", [False, True])
def test_background_tool_status_stops_ticking_when_ended(ended):
    it = ChatItem(seq=5, rev=5, uid="toolu_x", conv="c", kind="tool", turn="t", ts=100.0, name="Bash",
                  line="sleep 9", status="run", note="background")
    src = '{% from "_ci_macros.html" import tool_status with context %}{{ tool_status(it) }}'
    t = jenv.from_string(src).render(it=it, ended=ended, now=160.0)
    assert "background" in t
    if ended:  # agent gone: no ticker, no elapsed time that grows forever
        assert "data-start" not in t and "no end seen" in t and "is-run" not in t
    else:
        assert 'data-start="100.0"' in t and "is-run" in t and "1m" in t


@pytest.mark.parametrize("ended", [False, True])
def test_group_header_running_count_stops_when_ended(ended):
    it = ChatItem(seq=5, rev=5, uid="toolu_x", conv="c", kind="tool", turn="t", ts=100.0, name="Bash",
                  line="sleep 9", status="run", note="background")
    g = {"group": "g-1", "seq": 5, "parent": 1, "tools": [it], "counts": [("Bash", 1)], "running": 1}
    src = '{% from "_ci_macros.html" import group with context %}{{ group(g) }}'
    head = jenv.from_string(src).render(g=g, ended=ended, now=160.0).split("</summary>")[0]
    if ended:  # agent gone: a background task never notified is not "running"
        assert "1 no end seen" in head and "ci-spin" not in head and "running" not in head
    else:
        assert "ci-spin" in head and "1 running" in head


def test_group_header_js_does_not_spin_when_ended():
    # chat-items.js rebuilds the header from its rows (live updates, compact totals): same rule as the macro
    js = (Path(__file__).resolve().parents[2] / "src/pm/static/js/chat-items.js").read_text()
    i = js.index("function head(g)")
    body = js[i:js.index("function update(", i)]
    assert "root.hasAttribute('data-ended')" in body and "' no end seen'" in body


def _bg_done(status, note, ended_at=165.0):
    return ChatItem(seq=5, rev=5, uid="toolu_x", conv="c", kind="tool", turn="t", ts=100.0, name="Bash",
                    line="sleep 9", status=status, note=note, ended=ended_at)


@pytest.mark.parametrize("ended", [False, True])
def test_session_ended_background_tool_is_muted_stopped(ended):
    # itemsync._bg_reconcile: no exit line and the agent gone -> err "stopped · session ended", not a failure
    src = '{% from "_ci_macros.html" import tool_status with context %}{{ tool_status(it) }}'
    t = jenv.from_string(src).render(it=_bg_done("err", "stopped · session ended"), ended=ended, now=900.0)
    assert "is-bg" in t and "is-stop" in t and "is-err" not in t and "&#10007;" not in t
    assert "stopped · session ended" in t and "1m 05s" in t and "data-start" not in t
    assert "no end seen" not in t and "sr-only" in t


def test_reconciled_exit_code_renders_like_other_tools():
    src = '{% from "_ci_macros.html" import tool_status with context %}{{ tool_status(it) }}'
    err = jenv.from_string(src).render(it=_bg_done("err", "exit 3"), ended=True, now=900.0)
    ok = jenv.from_string(src).render(it=_bg_done("ok", "background"), ended=True, now=900.0)
    assert "is-err" in err and "exit 3 &middot; 1m 05s" in err
    assert "is-ok" in ok and "background" not in ok and "1m 05s" in ok


def test_group_header_counts_no_reconciled_tool_as_running():
    g = {"group": "g-1", "seq": 5, "parent": 1, "tools": [_bg_done("err", "stopped · session ended")],
         "counts": [("Bash", 1)], "running": 0}
    src = '{% from "_ci_macros.html" import group with context %}{{ group(g) }}'
    head = jenv.from_string(src).render(g=g, ended=True, now=900.0).split("</summary>")[0]
    assert "no end seen" not in head and "running" not in head and "ci-spin" not in head


def test_outbox_state_is_a_dot_with_words_in_title_and_sr_only():
    for st, word in (("saved", "Saved"), ("wait", "Waiting for turn end"), ("sending", "Sending…"),
                     ("inpane", "In pane"), ("queued", "Queued in Claude"), ("done", "In transcript ✓"),
                     ("cancelled", "Cancelled")):
        t = jenv.get_template("_outbox.html").render(m=_ob(st), agent=_agent())
        span = t[t.index('<span class="ob-st'):t.index("</span></span>", t.index('<span class="ob-st')) + 14]
        assert f'title="{word}"' in span and f'<span class="sr-only">{word}</span>' in span, st
        assert "ob-tx" not in span, st  # no visible text, only the coloured dot
    t = jenv.get_template("_outbox.html").render(m=_ob("failed", error="pane gone"), agent=_agent())
    assert 'title="Not sent &middot; pane gone"' in t and '<span class="ob-tx">Not sent</span>' in t
    assert '<span class="sr-only"> &middot; pane gone</span>' in t
    css = (CSS / "chat-items.css").read_text()
    for sel in (".ob-s-saved .ob-dot", ".ob-s-queued .ob-dot", ".ob-s-sending .ob-dot { animation", ".ob-s-done .ob-dot",
                ".ob-s-failed .ob-dot", "position: relative"):
        assert sel in css, sel


def test_user_meta_never_overflows_the_bubble():
    css = (CSS / "agent.css").read_text()
    assert "justify-content:safe flex-end" in css and "@container ububble" in css
    assert ".chatsplit .chat-bubble:not(.role-user) .chat-meta" in (CSS / "chat.css").read_text()


def test_builder_hint_line_is_hidden_in_user_bubbles():
    from pm.agents.message import builder_hint
    text = "make the header blue\n\n" + builder_hint("/home/me/dev/site")
    it = ChatItem(seq=7, rev=7, uid="u7", conv="c", kind="user", turn="t", ts=100.0, text=text)
    t = jenv.from_string('{% from "_ci_macros.html" import block with context %}{{ block({"it": it}) }}').render(
        it=it, ended=False, now=160.0, agent=None, csrf="x")
    assert "make the header blue" in t and "[pm builder:" not in t


def test_user_header_dot_before_you_and_continue_tip_right():
    ci = (TEMPLATE_DIR / "_ci_macros.html").read_text()
    for hdr in re.findall(r'<header class="chat-meta">(.*?)<strong>You</strong>', ci):
        assert "ob_state(" in hdr  # the delivery dot comes before "You", not after the time
    assert ci.count('<header class="chat-meta"><strong>You</strong>') == 0
    cm = (TEMPLATE_DIR / "_chatmsg.html").read_text()
    assert 'class="small tip tip-r"' in cm  # a left-anchored tooltip would widen the narrow aside frame
    css = (STATIC_DIR / "css" / "composer.css").read_text()
    assert ".composer--flow:has(.slash-list){overflow:visible}" in css  # slash popup not clipped (Builder)
