import pytest

from pm.agents import stop
from pm.core.models import Agent, AgentKind
from pm.tmux import ctl as tmuxctl


def mk(aid, pane, project=None):
    return Agent(aid, AgentKind.CLAUDE, pane, "$0", 1, "/", project)


def pane_out(rows):
    sep = tmuxctl.SEP
    return "\n".join(sep.join(["$1", "s", "0", "@1", "0", "w", "1", pid, "1", cmd, "/", "1", "t"])
                     for pid, cmd in rows)


def test_stop_keys_and_still_running(fake_tmux, monkeypatch):
    monkeypatch.setenv("FAKE_TMUX_OUT", pane_out([("%5", "node")]))
    waits = []
    r = stop.stop_agent(fake_tmux, mk("claude-5", "%5"), sleep=waits.append)
    assert r.status == "still_running" and waits
    log = fake_tmux.extras["log"].read_text()
    assert "send-keys" in log and "Escape" in log and "C-c" in log
    assert "kill" not in log


def test_stop_stopped_and_gone(fake_tmux, monkeypatch):
    monkeypatch.setenv("FAKE_TMUX_OUT", pane_out([("%5", "zsh")]))
    assert stop.stop_agent(fake_tmux, mk("a", "%5"), sleep=lambda s: None).status == "stopped"
    assert stop.stop_agent(fake_tmux, mk("a", "%9"), sleep=lambda s: None).status == "gone"


def test_stop_bad_pane_id(fake_tmux):
    r = stop.stop_agent(fake_tmux, mk("a", "%5; rm"), sleep=lambda s: None)
    assert r.status == "error"


def test_panic_scopes(fake_tmux, monkeypatch):
    monkeypatch.setenv("FAKE_TMUX_OUT", pane_out([("%1", "node"), ("%2", "bash")]))
    ags = [mk("c-1", "%1", "alpha"), mk("c-2", "%2", "beta"), mk("c-3", "%bad", "alpha")]
    res = stop.panic(fake_tmux, ags, "project:alpha", sleep=lambda s: None)
    assert set(res) == {"c-1", "c-3"}
    assert res["c-1"].status == "still_running" and res["c-3"].status == "error"
    res = stop.panic(fake_tmux, ags, "all", sleep=lambda s: None)
    assert res["c-2"].status == "stopped"


@pytest.mark.parametrize("scope", ["", "project:", "project:a b", "project:../x", "x", "ALL"])
def test_bad_scope(fake_tmux, scope):
    with pytest.raises(ValueError):
        stop.panic(fake_tmux, [], scope)


def test_panic_real_private_tmux(private_tmux):
    out = tmuxctl.list_sessions(private_tmux)
    pane = out[0].windows[0].panes[0]
    a = Agent("x-1", AgentKind.OTHER, pane.id, pane.session_id, 1, "/", "p")
    res = stop.panic(private_tmux, [a], "all", delay=0.2)
    assert res["x-1"].status == "stopped"  # shell pane, never killed
    assert tmuxctl.list_sessions(private_tmux)
