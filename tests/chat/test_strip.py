"""pm.chat.strip: open turn, pane tail above the input box, the strip facts (no real tmux)."""

import time

import pytest

from pm.chat import itemsync, strip
from pm.chat import log as chatlog
from pm.chat.sources import claude
from pm.core.models import Agent, AgentKind, AgentState, ChatItem, ChatView

RULE = "─" * 40


def item(seq, kind, **kw):
    base = dict(seq=seq, rev=seq, uid=f"u{seq}", conv="c", kind=kind, turn="t1", ts=1700000000.0 + seq)
    base.update(kw)
    return ChatItem(**base)


def turn():
    return [item(1, "user", text="go"), item(2, "text", text="ok"),
            item(3, "tool", name="Read", status="ok", parent=2), item(4, "tool", name="Bash", status="err", parent=2),
            item(5, "text", text="more"), item(6, "tool", name="Bash", status="run", parent=5), item(7, "end")]


def test_open_turn():
    items = turn()
    assert strip.open_turn(items) == []  # ended
    assert [i.seq for i in strip.open_turn(items[:-1])] == [3, 4, 6]
    assert strip.open_turn([]) == []


def test_pane_tail_basics():
    assert strip.pane_tail("a\n\nb\nc\n") == "a\nb\nc"
    assert strip.pane_tail("\n".join(str(i) for i in range(20)), 3) == "17\n18\n19"


def test_pane_tail_cuts_at_the_input_box():
    pane = "\n".join(["out1", "out2", "out3", RULE, "> typed", RULE, "  ? for shortcuts", "", ""])
    assert strip.pane_tail(pane) == "out1\nout2\nout3"
    claude = "\n".join(["x1", "x2", RULE, "❯ ", RULE, "  ⏵⏵ accept edits on"])
    assert strip.pane_tail(claude) == "x1\nx2"
    boxed = "\n".join(["y1", "╭" + RULE + "╮", "│ > hi" + " " * 30 + "│", "╰" + RULE + "╯"])
    assert strip.pane_tail(boxed) == "y1"


def test_pane_tail_keeps_wide_rules_in_command_output():
    # a ─── rule printed by a command (pytest, tables) is not the input box: no prompt line after it
    out = "\n".join(["== tests ==", RULE, "a passed", "b passed", RULE, "2 passed in 0.1s"])
    assert strip.pane_tail(out, 10) == out
    # a rule followed by a prompt, but far above the bottom: still output (e.g. a pasted transcript)
    far = "\n".join(["head", RULE, "> quoted"] + [f"line{i}" for i in range(12)])
    assert strip.pane_tail(far, 20).startswith("head\n" + RULE + "\n> quoted")


def _agent():
    return Agent(id="claude-1", kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=2, cwd="/",
                 state=AgentState.RUNNING)


def test_read_waiting_tool_captures_once(monkeypatch, settings):
    now = time.time()
    items = [item(1, "user", ts=now - 60), item(2, "text", ts=now - 50),
             item(3, "tool", name="Bash", status="run", parent=2, ts=now - 30),
             item(4, "tool", name="Bash", status="run", note="background", parent=2, ts=now - 40)]
    monkeypatch.setattr(strip.chat_items, "list_items", lambda *a, **k: items)
    pane = "l1\nl2\n" + RULE + "\n> \n" + RULE + "\n"
    caps, seen, synced = [], [], []
    monkeypatch.setattr(strip.tmuxctl, "capture_pane", lambda s, pid, lines: caps.append(pid) or pane)
    view = ChatView(agent_id="claude-1", state=AgentState.RUNNING)
    monkeypatch.setattr(strip, "chat_view", lambda s, a, p=None: seen.append(p) or view)

    class Log:
        @staticmethod
        def sync(s, a):
            synced.append(a.id)

    got, st = strip.read(settings, _agent(), False, chatlog=Log)
    assert got is view and caps == ["%1"] and seen == [pane] and synced == ["claude-1"]
    assert st == strip.Strip(2, "Bash", items[2].ts, "l1\nl2")


def test_read_no_wait_and_ended(monkeypatch, settings):
    now = time.time()
    monkeypatch.setattr(strip.chat_items, "list_items",
                        lambda *a, **k: [item(1, "user", ts=now), item(2, "tool", status="run", ts=now - 2)])
    monkeypatch.setattr(strip.tmuxctl, "capture_pane", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    seen = []
    monkeypatch.setattr(strip, "chat_view", lambda s, a, p=None: seen.append(p) or "v")

    class Broken:
        @staticmethod
        def sync(s, a):
            raise RuntimeError("store broken")

    assert strip.read(settings, _agent(), False, chatlog=Broken) == ("v", strip.Strip(tools=1))
    assert strip.read(settings, _agent(), True) == ("v", strip.Strip())
    assert seen == [None, ""]


def test_read_hides_claude_queued_block_for_own_send_now(monkeypatch, settings):
    from pm.chat import outbox

    monkeypatch.setattr(strip.chat_items, "list_items", lambda *a, **k: [])
    view = ChatView(agent_id="claude-q1", state=AgentState.RUNNING, queued="also add a CHANGELOG line")
    monkeypatch.setattr(strip, "chat_view", lambda s, ag, p=None: view)
    agent = type("A", (), {"id": "claude-q1", "pane_id": "%1"})()
    m = outbox.add(settings, "claude-q1", "also add a  CHANGELOG line")
    outbox.set_state(settings, m.id, "sending")
    shown, _ = strip.read(settings, agent, False)
    assert shown.queued  # only "sending": Claude's block stays
    outbox.set_state(settings, m.id, "queued")
    shown, _ = strip.read(settings, agent, False)
    assert shown.queued == ""
    other = ChatView(agent_id="claude-q1", state=AgentState.RUNNING, queued="typed in the pane")
    monkeypatch.setattr(strip, "chat_view", lambda s, ag, p=None: other)
    shown, _ = strip.read(settings, agent, False)
    assert shown.queued == "typed in the pane"


def test_read_hides_queued_block_when_the_pane_wraps_it(monkeypatch, settings):
    from pm.chat import outbox

    monkeypatch.setattr(strip.chat_items, "list_items", lambda *a, **k: [])
    up = "/home/me/.local/state/pm/uploads/0123456789abcdef01234567.png"
    m = outbox.add(settings, "claude-q2", f"please fix the header\nand the footer too\n{up}")
    outbox.set_state(settings, m.id, "sending")
    outbox.set_state(settings, m.id, "queued")
    view = ChatView(agent_id="claude-q2", state=AgentState.RUNNING,
                    queued="please fix the header and the footer too [Image #1]")
    monkeypatch.setattr(strip, "chat_view", lambda s, ag, p=None: view)
    agent = type("A", (), {"id": "claude-q2", "pane_id": "%1"})()
    shown, _ = strip.read(settings, agent, False)
    assert shown.queued == ""


# ---------------------------------------------------------------- background rows (real items db, no tmux)
def _ts(n):
    return f"2026-10-12T16:{n // 60:02d}:{n % 60:02d}.000Z"


def _user(uid, n):
    return {"type": "user", "uuid": uid, "timestamp": _ts(n), "message": {"role": "user", "content": "go"}}


def _launch(uid, tid, name, line, n, stop="end_turn"):
    inp = {"command": line, "run_in_background": True} if name == "Bash" else {"description": line, "prompt": "p"}
    return {"type": "assistant", "uuid": uid, "timestamp": _ts(n),
            "message": {"role": "assistant", "model": "m", "stop_reason": stop,
                        "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def _bg_conv(tmp_path, entries, stem="11111111-2222-3333-4444-555555555555"):
    import json

    p = tmp_path / f"{stem}.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return p


@pytest.fixture(autouse=True)
def _no_real_claude(monkeypatch, tmp_path):
    """Never read the real ~/.claude/sessions or /tmp/claude-<uid> task files."""
    monkeypatch.setattr(chatlog, "live_session_id", lambda pid: None)
    monkeypatch.setattr(itemsync, "task_exit",
                        lambda t, task, tmp_root=None: claude.task_exit(t, task, tmp_root=tmp_path / "claude-tmp"))


def _bg_agent(path, aid="claude-bg1"):  # a live agent (pid > 1): its background tasks are not closed as ended
    return Agent(id=aid, kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=4242, cwd="/nowhere",
                 transcript=str(path), state=AgentState.RUNNING)


def _read(monkeypatch, settings, a, ended=False):
    from pm.chat import itemsync

    itemsync._SEEN.discard(a.id)
    itemsync.sync(settings, a)
    monkeypatch.setattr(strip, "chat_view", lambda s, ag, p=None: "v")
    monkeypatch.setattr(strip.tmuxctl, "capture_pane", lambda *x, **k: "")
    return strip.read(settings, a, ended)[1]


def test_strip_bg_rows_across_turns(monkeypatch, settings, tmp_path):
    from helpers import bg_result, task_note

    from pm.chat.sources.common import parse_ts

    p = _bg_conv(tmp_path, [
        _user("u1", 1), _launch("a1", "t1", "Bash", "make watch", 2), bg_result("r1", "t1", "bk1", _ts(3)),
        _user("u2", 10), _launch("a2", "t2", "Agent", "review the diff", 11),
        bg_result("r2", "t2", "ag1", _ts(12), shape="agent"),
        _user("u3", 20), _launch("a3", "t3", "Bash", "sleep 5", 21), bg_result("r3", "t3", "bk3", _ts(22)),
        task_note("n3", "bk3", _ts(30), tool_id="t3"),
        _user("u4", 40)])
    st = _read(monkeypatch, settings, _bg_agent(p))
    assert [(r.name, r.line, r.ts) for r in st.bg] == [("Agent", "review the diff", parse_ts(_ts(11))),
                                                       ("Bash", "make watch", parse_ts(_ts(2)))]
    assert st.wait_name == "" and st.tail == ""  # a background tool never "waits"


def test_strip_bg_empty_when_ended(monkeypatch, settings, tmp_path):
    from helpers import bg_result

    p = _bg_conv(tmp_path, [_user("u1", 1), _launch("a1", "t1", "Bash", "make watch", 2),
                            bg_result("r1", "t1", "bk1", _ts(3))])
    a = _bg_agent(p)
    assert len(_read(monkeypatch, settings, a).bg) == 1
    assert _read(monkeypatch, settings, a, ended=True).bg == ()


def test_strip_bg_only_current_conv(monkeypatch, settings, tmp_path):
    from helpers import bg_result

    old = _bg_conv(tmp_path, [_user("u1", 1), _launch("a1", "t1", "Bash", "old watch", 2),
                              bg_result("r1", "t1", "bk1", _ts(3))], stem="aaaaaaaa-2222-3333-4444-555555555555")
    new = _bg_conv(tmp_path, [_user("v1", 1), _launch("b1", "s1", "Bash", "new watch", 2),
                              bg_result("q1", "s1", "bk2", _ts(3))], stem="bbbbbbbb-2222-3333-4444-555555555555")
    _read(monkeypatch, settings, _bg_agent(old))
    st = _read(monkeypatch, settings, _bg_agent(new))
    assert [r.line for r in st.bg] == ["new watch"]


def test_strip_bg_limit(monkeypatch, settings, tmp_path):
    from helpers import bg_result

    from pm.chat import items

    entries = [_user("u1", 1)]
    for i in range(14):
        entries += [_launch(f"a{i}", f"t{i}", "Bash", f"job {i}", 2 + i, stop=None),
                    bg_result(f"r{i}", f"t{i}", f"bk{i}", _ts(2 + i))]
    p = _bg_conv(tmp_path, entries)
    a = _bg_agent(p)
    st = _read(monkeypatch, settings, a)
    assert [r.line for r in st.bg] == [f"job {i}" for i in range(13, 3, -1)]  # newest 10
    con = items.connect(settings)
    try:
        assert len(items.running_background(con, a.id, p.stem, limit=3)) == 3
        assert items.running_background(con, a.id, "other-conv") == []
    finally:
        con.close()
