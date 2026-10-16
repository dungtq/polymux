"""Agent URL follows the tmux pane across kinds; project tmux list shows only live in-project panes."""

from helpers import make_client, make_settings

from pm.core.models import Agent, AgentKind, AgentState
from pm.infra import snapshot as services
from pm.tmux.ctl import SEP
from pm.web.routes.pages import router


def _line(sid, sname, wid, widx, wname, pane, cwd, dead="0"):
    return SEP.join([sid, sname, "1", wid, str(widx), wname, "1", pane, "4242", "claude", cwd,
                     "1", "t", dead]) + "\n"


def _agent(kind, n):
    return Agent(id=f"{kind.value}-{n}", kind=kind, pane_id=f"%{n}", session_id="$1", pid=n,
                 cwd="/x", state=AgentState.IDLE, state_since=1.0)


def _client(fake_tmux, tmp_path, monkeypatch, out, found):
    dev = tmp_path / "dev"
    (dev / "alpha").mkdir(parents=True, exist_ok=True)
    (dev / "beta").mkdir(parents=True, exist_ok=True)
    s = make_settings(fake_tmux, tmp_path)
    monkeypatch.setenv("FAKE_TMUX_OUT", out(dev))
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes, **kw: list(found))
    c = make_client(s, router, static=True, logged_in=True)
    return s, c


def test_missing_agent_redirects_to_same_pane_other_kind(fake_tmux, tmp_path, monkeypatch):
    _, c = _client(fake_tmux, tmp_path, monkeypatch,
                   lambda d: _line("$1", "s", "@1", 0, "w", "%140", str(d / "alpha")),
                   [_agent(AgentKind.DEVIN, 140)])
    r = c.get("/a/claude-140?tab=chat&x=1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/a/devin-140?tab=chat&x=1"
    assert c.get("/a/claude-141", follow_redirects=False).status_code == 404
    assert c.get("/a/..%2f-140", follow_redirects=False).status_code == 404
    assert c.get("/a/devin-140/pane", follow_redirects=False).status_code != 303


def test_project_tmux_lists_sessions_with_live_panes_in_project(fake_tmux, tmp_path, monkeypatch):
    def out(d):
        return (_line("$1", "sess-a", "@1", 0, "live-win", "%1", str(d / "alpha"))
                + _line("$1", "sess-a", "@2", 1, "dead-win", "%2", str(d / "alpha"), dead="1")
                + _line("$1", "sess-a", "@3", 2, "other-win", "%3", str(d / "beta"))
                + _line("$2", "sess-dead", "@4", 0, "gone", "%4", str(d / "alpha"), dead="1"))
    s, c = _client(fake_tmux, tmp_path, monkeypatch, out, [])
    t = c.get("/p/alpha/live").text
    assert "sess-a" in t and "live-win" in t
    assert "other-win" in t and "dead-win" in t  # every window of a shown session is listed
    assert "sess-dead" not in t  # a session with no live pane of the project stays hidden
    assert "sess-a" in c.get("/p/alpha").text and "sess-dead" not in c.get("/p/alpha").text
