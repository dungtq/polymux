"""Display label (dev-pm-1-claude) is shown to humans; routes keep the stable id."""
from pm.core.models import Agent, AgentKind, AgentState
from pm.infra import snapshot as services


def _agent():
    return Agent(
        id="claude-14", kind=AgentKind.CLAUDE, pane_id="%14", session_id="$3", pid=1,
        cwd="/home/me/dev/pm", project="pm", state=AgentState.RUNNING, state_since=1.0,
        label="dev-pm-1-claude",
    )


def test_view_sessions_hidden_from_snapshot(monkeypatch, tmp_path):
    from pm.core.config import Settings
    from pm.core.models import TmuxSession

    sess = (
        TmuxSession(id="$1", name="dev-pm"),
        TmuxSession(id="$9", name="pm-view-0123456789ab"),
    )
    monkeypatch.setattr(services.tmuxctl, "list_sessions", lambda s: sess)
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes: [])
    monkeypatch.setattr(services.projects, "discover", lambda s: [])
    snap = services.build_snapshot(Settings(dev_root=tmp_path, extra_projects=()), force=True)
    assert [s.name for s in snap.sessions] == ["dev-pm"]


def test_agent_macro_shows_label_and_keeps_id():
    from pm.web.render import env as _env  # the app's environment (custom filters included)

    tpl = _env.from_string(
        '{% from "_macros.html" import agent_text %}<a href="/a/{{ a.id }}">{{ agent_text(a) }}</a>'
    )
    html = tpl.render(a=_agent())
    assert 'href="/a/claude-14"' in html and ">dev-pm-1-claude<" in html
    assert ">claude-14<" not in html
