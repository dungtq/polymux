import os
import subprocess
import time

import pytest
from helpers import fake, mk_client, post, snap_with  # noqa: F401  (fake is a fixture)

from pm.agents import message as m
from pm.core.models import Agent, AgentKind
from pm.tmux import ctl as tmuxctl


def _shell_agent(s, kind=AgentKind.CLAUDE):
    sid = tmuxctl.new_session(s, "pmtest-msg")
    pane = next(p for ss in tmuxctl.list_sessions(s) if ss.id == sid
                for w in ss.windows for p in w.panes)
    tmuxctl.run(s, ["send-keys", "-t", pane.id, "--", "cat > /tmp/__pmmsg_" + str(os.getpid()),
                    "Enter"])
    return Agent(id="claude-9", kind=kind, pane_id=pane.id, session_id=sid, pid=pane.pid,
                 cwd="/")


def _cap(s, a, want=()):
    out = ""
    for _ in range(30):
        time.sleep(0.2)
        out = tmuxctl.capture_pane(s, a.pane_id)
        if all(w in out for w in want):
            break
    return out


def test_message_reaches_pane(private_tmux):
    a = _shell_agent(private_tmux)
    r = m.send_message(private_tmux, a, "hello $HOME 'x'")
    assert r.ok and r.lines == 1
    assert "hello $HOME 'x'" in _cap(private_tmux, a, ["hello $HOME 'x'"])
    m.send_message(private_tmux, a, "line1\nline2")
    out = _cap(private_tmux, a, ["line1", "line2"])
    assert "line1" in out and "line2" in out
    os.unlink(f"/tmp/__pmmsg_{os.getpid()}") if os.path.exists(f"/tmp/__pmmsg_{os.getpid()}") else None


def test_quick_key_and_interrupt(private_tmux):
    a = _shell_agent(private_tmux)
    assert m.quick_key(private_tmux, a, "y").ok
    assert m.interrupt(private_tmux, a).ok
    with pytest.raises(m.InvalidMessage):
        m.quick_key(private_tmux, a, "C-z")
    with pytest.raises(m.InvalidMessage):
        m.quick_key(private_tmux, a, "Enter; rm")


@pytest.mark.parametrize("text", ["", "   ", "x" * 4001, "a\x1b[2Jb", "a\x00b", "a\tb",
                                  "a\x7fb"])
def test_bad_text(settings, text):
    with pytest.raises(m.InvalidMessage):
        m.validate_text(text)


def test_max_len_ok():
    assert m.validate_text("x" * 4000)
    assert m.validate_text("a\r\nb") == "a\nb"


def test_non_agent_refused(private_tmux):
    a = _shell_agent(private_tmux, AgentKind.OTHER)
    with pytest.raises(m.NotAnAgent):
        m.send_message(private_tmux, a, "hi")
    with pytest.raises(m.NotAnAgent):
        m.quick_key(private_tmux, a, "y")


def test_stale_refused(private_tmux):
    a = _shell_agent(private_tmux)
    stale_pid = Agent(**{**a.__dict__, "pid": 999999})
    with pytest.raises(m.NotAnAgent):
        m.send_message(private_tmux, stale_pid, "hi")
    wrong_sess = Agent(**{**a.__dict__, "session_id": "$999"})
    with pytest.raises(m.NotAnAgent):
        m.interrupt(private_tmux, wrong_sess)
    gone = Agent(**{**a.__dict__, "pane_id": "%9999"})
    with pytest.raises(m.NotAnAgent):
        m.quick_key(private_tmux, gone, "y")
    bad = Agent(**{**a.__dict__, "pane_id": "%1; x"})
    with pytest.raises(m.NotAnAgent):
        m.quick_key(private_tmux, bad, "y")


def test_descendant_pid(private_tmux):
    a = _shell_agent(private_tmux)
    child = subprocess.Popen(["sleep", "30"])
    try:
        assert not m._is_descendant(child.pid, a.pid, m.PROC)
        assert m._is_descendant(os.getpid(), os.getpid(), m.PROC)
    finally:
        child.kill()
        child.wait()


def test_snippets(settings):
    base = m.load_snippets(settings)
    assert len(base) == 5
    s2 = type(settings)(snippets=[("Mine", "do x"), ("Continue", "go"), 5])
    got = dict(m.load_snippets(s2))
    assert got["Mine"] == "do x" and got["Continue"] == "go" and len(got) == 6


def test_quick_keys_contract():
    assert m.QUICK_KEYS == tuple(
        "y n 1 2 3 4 5 6 7 8 9 Enter Escape Tab BTab BSpace Space Up Down Left Right Home End "
        "PageUp PageDown C-c C-d C-l C-x C-s".split())
    assert set(m.QUICK_KEYS) == tmuxctl.ALLOWED_KEYS


def test_describe_sanitizes():
    try:
        try:
            raise tmuxctl.TmuxError("can't find pane %62\x1b[0m\n" + "x" * 300)
        except tmuxctl.TmuxError as e:
            raise m.MessageError("send failed") from e
    except m.MessageError as e:
        d = m.describe(e)
    assert d.startswith("tmux: can't find pane %62") and len(d) <= 120 and "\x1b" not in d
    assert m.describe(m.MessageError("x")) == "x"
    te = tmuxctl.TmuxError("tmux failed: TimeoutExpired")
    assert m.describe(te) == "tmux: tmux failed: TimeoutExpired"


@pytest.mark.parametrize("text", ["trailing;", ";", r"back\slash \;", "a; b"])
def test_text_survives_tmux_arg_parsing(private_tmux, text):
    a = _shell_agent(private_tmux)
    m.send_message(private_tmux, a, text)
    out = _cap(private_tmux, a, [text])
    assert text in out


def test_retry_once_on_tmux_error(private_tmux, monkeypatch):
    a = _shell_agent(private_tmux)
    real, n = tmuxctl.send_keys, {"n": 0}

    def flaky(s, pane, text, literal=True):
        n["n"] += 1
        if n["n"] == 1:
            raise tmuxctl.TmuxError("transient")
        return real(s, pane, text, literal)
    monkeypatch.setattr(tmuxctl, "send_keys", flaky)
    assert m.send_message(private_tmux, a, "retried-ok").ok and n["n"] == 2
    monkeypatch.setattr(tmuxctl, "send_keys",
                        lambda *x, **k: (_ for _ in ()).throw(tmuxctl.TmuxError("down")))
    with pytest.raises(m.MessageError) as ei:
        m.send_message(private_tmux, a, "never")
    assert m.describe(ei.value) == "tmux: down"


# Stand-in for a TUI that treats back-to-back bytes as a paste: a CR arriving < 50 ms
# after the previous byte is swallowed (inserted as newline), otherwise it submits.
_TUI = r"""
import os, sys, time, tty, termios, select
out = sys.argv[1]
tty.setraw(0)
buf, last = b"", 0.0
print("READY", flush=True)
while True:
    select.select([0], [], [])
    d = os.read(0, 4096)
    now = time.monotonic()
    for ch in d:
        c = bytes([ch])
        if c in (b"\r", b"\n"):
            if now - last < 0.05:
                buf += b"|"          # swallowed as part of a paste
            else:
                open(out, "wb").write(buf); buf = b""
        else:
            buf += c
    last = now
"""


def _tui_agent(s, tmp_path):
    script, out = tmp_path / "tui.py", tmp_path / "submitted.txt"
    script.write_text(_TUI)
    a = _shell_agent(s)
    tmuxctl.run(s, ["send-keys", "-t", a.pane_id, "C-c"])
    tmuxctl.run(s, ["send-keys", "-t", a.pane_id, "--", f"python3 {script} {out}", "Enter"])
    _cap(s, a, ["READY"])
    return a, out


def _wait(out, secs=4.0):
    end = time.time() + secs
    while time.time() < end:
        if out.exists() and out.read_bytes():
            return out.read_bytes()
        time.sleep(0.1)
    return b""


def test_enter_delay_makes_submit_register(private_tmux, tmp_path):
    a, out = _tui_agent(private_tmux, tmp_path)
    # old behaviour: text immediately followed by Enter -> swallowed by the paste heuristic
    tmuxctl.send_keys(private_tmux, a.pane_id, "glued")
    tmuxctl.send_key(private_tmux, a.pane_id, "Enter")
    assert _wait(out, 1.0) == b""
    # new behaviour: pause between text and Enter -> submit registers
    time.sleep(0.2)
    tmuxctl.send_key(private_tmux, a.pane_id, "Enter")  # flush the swallowed line
    assert _wait(out) == b"glued|"
    out.unlink()
    assert m.send_message(private_tmux, a, "spaced").ok
    assert _wait(out) == b"spaced"


# ------------------------------------------------------------------ Builder mode hint
def test_builder_hint_helpers():
    h = m.builder_hint("/home/me/dev/site")
    assert h.startswith(m.BUILDER_HINT) and "/home/me/dev/site" in h and "\n" not in h and "Preview:" in h
    assert "\x1b" not in m.builder_hint("/a\x1b[2Jb")  # never a control char (validate_text would refuse)
    out = m.with_builder_hint("make it blue", "/p")
    assert out == "make it blue\n\n" + m.builder_hint("/p") and m.validate_text(out) == out
    assert m.with_builder_hint("/help", "/p") == "/help"
    assert m.with_builder_hint("   ", "/p") == "   "
    big = "x" * (m.MAX_LEN - 10)
    assert m.with_builder_hint(big, "/p") == big  # would not fit: dropped, the message itself still goes
    assert m.strip_builder_hint(out) == "make it blue"
    assert m.strip_builder_hint("a\n[pm builder: x]\nb") == "a\n[pm builder: x]\nb"  # only a trailing line
    assert m.strip_builder_hint("plain") == "plain" and m.strip_builder_hint(None) == ""
    assert "https://mock.example.test/..." in m.builder_hint("/p", "https://mock.example.test")
    assert "https://" not in m.builder_hint("/p")  # no allowed host: only project paths are offered


def _route(fake, monkeypatch, **settings):
    import dataclasses
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])
    app, c, _sess, tok, st = mk_client(dataclasses.replace(st, **settings))
    return c, tok, st, calls


def _stored(st, aid="claude-1"):
    from pm.chat import outbox
    return [x.text for x in outbox.pending(st, aid)]


def test_route_appends_hint_only_in_builder_mode(fake, monkeypatch):
    c, tok, st, _calls = _route(fake, monkeypatch)
    assert post(c, tok, "/a/claude-1/message", text="plain").status_code == 200
    assert post(c, tok, "/a/claude-1/message", text="blue", mode="builder").status_code == 200
    assert post(c, tok, "/a/claude-1/message", text="x" * (m.MAX_LEN - 5), mode="builder").status_code == 200
    assert post(c, tok, "/a/claude-1/message", text="", mode="builder").status_code == 400  # hint never rescues
    got = _stored(st)
    assert got[0] == "plain" and got[1] == "blue\n\n" + m.builder_hint("/", "https://mock.example.test") and got[2] == "x" * (m.MAX_LEN - 5)


def test_route_hint_setting_off_and_slash(fake, monkeypatch):
    c, tok, st, calls = _route(fake, monkeypatch, builder_hint=False)
    assert post(c, tok, "/a/claude-1/message", text="blue", mode="builder").status_code == 200
    assert _stored(st) == ["blue"]
    c, tok, st, calls = _route(fake, monkeypatch)
    post(c, tok, "/a/claude-1/message", text="/help", mode="builder")
    assert not any("pm builder" in " ".join(map(str, a)) for a in calls)
    assert all("pm builder" not in t for t in _stored(st))


def test_hint_strip_filter_registered():
    from pm.web.render import env
    assert env.filters["pm_hint_strip"]("hi\n\n" + m.builder_hint("/p")) == "hi"
