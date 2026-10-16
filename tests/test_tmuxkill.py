import dataclasses
import subprocess

import pytest

from pm.tmux import ctl as tmuxctl
from pm.tmux import kill as tmuxkill


def mkout(rows):
    sep = tmuxctl.SEP
    return "\n".join(sep.join([sid, sn, str(att), wid, "0", wn, "1", pid, "1", "zsh", "/", "1", "t"])
                     for sid, sn, att, wid, wn, pid in rows)


@pytest.fixture
def fk(fake_tmux, monkeypatch):
    s = dataclasses.replace(fake_tmux, unprotected_sessions=("work*", "pmx"))
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setenv("FAKE_TMUX_OUT", mkout([
        ("$1", "work1", 0, "@1", "win", "%1"),
        ("$2", "work2", 2, "@2", "busy", "%2"),
        ("$3", "other", 0, "@3", "prot", "%3"),
        ("$4", "pmmain", 0, "@4", "pm", "%4"),
        ("$5", "workme", 0, "@5", "mine", "%5"),
    ]))
    return s


def killed(s):
    f = s.extras["log"]
    return f.exists() and "kill-" in f.read_text()


def test_ok(fk):
    r = tmuxkill.kill_window(fk, "@1", "win")
    assert r.session == "work1" and killed(fk)
    tmuxkill.kill_session(fk, "$1", "work1")
    tmuxkill.kill_pane(fk, "%1", "%1")


def test_confirm_mismatch(fk):
    for bad in ("", "WIN", "win ", "x"):
        with pytest.raises(tmuxkill.ConfirmMismatch):
            tmuxkill.kill_window(fk, "@1", bad)
    with pytest.raises(tmuxkill.ConfirmMismatch):
        tmuxkill.kill_session(fk, "$1", "win")
    assert not killed(fk)


def test_protected_and_pm(fk):
    for fn, t, c in [(tmuxkill.kill_window, "@3", "prot"), (tmuxkill.kill_session, "$3", "other"),
                     (tmuxkill.kill_pane, "%3", "%3"), (tmuxkill.kill_session, "$4", "pmmain")]:
        with pytest.raises(tmuxkill.KillRefused):
            fn(fk, t, c)
    assert not killed(fk)


def test_attached(fk):
    with pytest.raises(tmuxkill.KillRefused):
        tmuxkill.kill_window(fk, "@2", "busy")
    tmuxkill.kill_window(fk, "@2", "busy", force_attached=True)
    assert killed(fk)


def test_self_session(fk, monkeypatch):
    monkeypatch.setenv("TMUX_PANE", "%5")
    with pytest.raises(tmuxkill.KillRefused):
        tmuxkill.kill_window(fk, "@5", "mine")
    with pytest.raises(tmuxkill.KillRefused):
        tmuxkill.kill_pane(fk, "%5", "%5")
    tmuxkill.kill_window(fk, "@1", "win")  # other session still fine


def test_self_unresolvable_fails_closed(fk, monkeypatch):
    monkeypatch.setenv("TMUX_PANE", "%99")
    with pytest.raises(tmuxkill.KillRefused):
        tmuxkill.kill_window(fk, "@1", "win")
    monkeypatch.setenv("TMUX_PANE", "%1; x")
    with pytest.raises(tmuxkill.KillRefused):
        tmuxkill.kill_window(fk, "@1", "win")
    assert not killed(fk)


@pytest.mark.parametrize("bad", ["@1; kill-server", "1", "$1", "@", "@-1", "%1", "@9", ""])
def test_bad_ids(fk, bad):
    with pytest.raises((tmuxkill.KillRefused, tmuxctl.TmuxError)):
        tmuxkill.kill_window(fk, bad, "win")
    assert not killed(fk)


def test_real_private_tmux(private_tmux, monkeypatch):
    monkeypatch.delenv("TMUX_PANE", raising=False)
    s = dataclasses.replace(private_tmux, unprotected_sessions=("scratch*",))
    sid = tmuxctl.new_session(s, "scratch1")
    with pytest.raises(tmuxkill.KillRefused):  # pmtest-base is protected
        base = [x for x in tmuxctl.list_sessions(s) if x.name == "pmtest-base"][0]
        tmuxkill.kill_session(s, base.id, "pmtest-base")
    tmuxkill.kill_session(s, sid, "scratch1")
    assert [x.name for x in tmuxctl.list_sessions(s)] == ["pmtest-base"]
