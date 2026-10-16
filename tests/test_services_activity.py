from dataclasses import replace

from pm.core.models import Agent, AgentKind, AgentState, TmuxPane
from pm.infra import snapshot as services
from pm.tmux import ctl as tmuxctl


def test_window_activity_parses_and_fails_soft(fake_tmux, monkeypatch):
    monkeypatch.setenv("FAKE_TMUX_OUT", "$1 @1 1700000000\n$1 @2 bad\n$2 @3 1700000500\n")
    assert services.window_activity(fake_tmux) == {"@1": 1700000000.0, "@3": 1700000500.0}
    assert "list-windows" in fake_tmux.extras["log"].read_text()
    monkeypatch.setenv("FAKE_TMUX_ERR", "boom")
    assert services.window_activity(fake_tmux) == {}


def test_fill_activity_only_when_unknown():
    mk = lambda i, pane, la: Agent(id=i, kind=AgentKind.CODEX, pane_id=pane, session_id="$1",
                                   pid=1, cwd="/x", state=AgentState.IDLE, last_active=la)
    panes = [TmuxPane("%1", "@1", "$1", 1, "x", "/x"), TmuxPane("%2", "@2", "$1", 2, "x", "/x")]
    out = services._fill_activity([mk("a-1", "%1", 0), mk("a-2", "%2", 5)], panes, {"@1": 77.0, "@2": 99.0})
    assert [a.last_active for a in out] == [77.0, 5]
    assert replace(out[0]).state_since == 0  # never invented
