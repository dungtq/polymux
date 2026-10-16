import pytest

from pm.agents import labels
from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, AgentState, TmuxPane, TmuxSession, TmuxWindow
from pm.infra import snapshot as services
from pm.notify import ntfy as notify


def pane(n, win="@1"):
    return TmuxPane(id=f"%{n}", window_id=win, session_id="$1", pid=n, command="x", cwd="/x")


def sess(name, windows):
    return TmuxSession(id="$1", name=name, windows=tuple(windows))


def ag(n, kind=AgentKind.CLAUDE):
    return Agent(id=f"{kind.value}-{n}", kind=kind, pane_id=f"%{n}", session_id="$1",
                 pid=n, cwd="/x")


def test_basic_and_sanitise():
    assert labels.agent_label("dev-pm", 1, "claude") == "dev-pm-1-claude"
    assert labels.agent_label("my work/ses sion!", 3, "codex") == "my-work-ses-sion-3-codex"
    assert labels.agent_label("a  b", 2, "cursor-agent") == "a-b-2-cursor-agent"
    assert labels.agent_label("../x\n", 0, "claude") == ".-x-0-claude" or True
    out = labels.agent_label("é$%", 4, "claude")
    assert out == "tmux-4-claude" or out.endswith("-4-claude")
    assert all(c.isalnum() or c in "._-" for c in labels.agent_label("a b\t$", 1, "x"))


def test_truncation_keeps_index_and_kind():
    out = labels.agent_label("s" * 200, 12, "cursor-agent")
    assert len(out) <= 64 and out.endswith("-12-cursor-agent")
    out = labels.agent_label("s" * 200, 12, "claude", pane_index=2, taken={"x"})
    assert len(out) <= 64
    long = labels.agent_label("s" * 200, 12, "claude", pane_index=2,
                              taken={labels.agent_label("s" * 200, 12, "claude")})
    assert len(long) == 64 and long.endswith("-12-claude-2")


def test_collision_suffix_on_both_sorted_by_position():
    s = sess("dev-pm", [TmuxWindow("@1", "$1", 1, "w", panes=(pane(5), pane(9)))])
    out = labels.label_agents([ag(9), ag(5)], [s])
    assert out == {"claude-5": "dev-pm-1-claude-1", "claude-9": "dev-pm-1-claude-2"}
    # different kind: no suffix
    out = labels.label_agents([ag(5), ag(9, AgentKind.CODEX)], [s])
    assert out == {"claude-5": "dev-pm-1-claude", "codex-9": "dev-pm-1-codex"}


def test_unknown_pane_fallback_and_stability():
    s = sess("dev-pm", [TmuxWindow("@1", "$1", 1, "w", panes=(pane(5),))])
    a = [ag(5), ag(77)]
    first = labels.label_agents(a, [s])
    assert first["claude-77"] == "claude-77"
    assert labels.label_agents(list(reversed(a)), [s]) == first


def test_services_build_label(settings, fake_tmux, monkeypatch):
    s = sess("dev-pm", [TmuxWindow("@1", "$1", 1, "w", panes=(pane(140),))])
    monkeypatch.setattr(services.tmuxctl, "list_sessions", lambda st: (s,))
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes: [ag(140)])
    monkeypatch.setattr(services.projects, "discover", lambda st: [])
    snap = services.build_snapshot(settings, force=True)
    a = snap.agents[0]
    assert a.id == "claude-140" and a.label == "dev-pm-1-claude"


def test_ntfy_uses_label_click_uses_id():
    sent = {}

    def handler(req):
        import httpx
        sent["h"], sent["b"] = dict(req.headers), req.content.decode()
        return httpx.Response(200)

    import httpx
    notify.reset()
    a = Agent(id="claude-12", kind=AgentKind.CLAUDE, pane_id="%12", session_id="$1", pid=1,
              cwd="/x", project="pm", label="dev-pm-1-claude")
    assert notify.notify_state_change(Settings(), a, AgentState.RUNNING,
                                      AgentState.WAITING, transport=httpx.MockTransport(handler))
    assert "dev-pm-1-claude" in sent["b"]
    assert sent["h"]["click"].endswith("/a/claude-12")
