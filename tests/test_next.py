"""Exit button, context suggestion, new chat from last message, clear ended windows (fake tmux)."""

import dataclasses
import sqlite3

import pytest
from helpers import audit_text, fake, mk_client, post, snap_with  # noqa: F401
from test_tmuxkill import mkout

import pm.web.routes.next
from pm.agents import launch as launch_mod
from pm.agents import lineage
from pm.core.models import Agent, AgentKind, AgentState, ChatView, Project
from pm.projects import projects
from pm.tmux import ctl as tmuxctl
from pm.tmux import kill as tmuxkill

REAL_REGISTRY = True


def mk(st):
    app, c, sess, tok, st = mk_client(st)
    app.include_router(pm.web.routes.next.router)
    return app, c, tok, st


def agent():
    return Agent(id="claude-1", kind=AgentKind.CLAUDE, pane_id="%2", session_id="$2", pid=1,
                 cwd="/", project="proj", state=AgentState.IDLE)


def test_continue_launches_same_kind_with_message(fake, monkeypatch):
    st, calls, _ = fake
    app, c, tok, st = mk(st)
    snap_with(monkeypatch, [agent()])
    proj = Project(name="proj", path="/tmp")
    monkeypatch.setattr(pm.projects.projects, "discover", lambda s: [proj])
    monkeypatch.setattr(pm.web.routes.next, "chat_view", lambda s, a, p=None: ChatView(
        agent_id=a.id, state=AgentState.IDLE, last_role="assistant", last_text="do the thing"))
    seen = []
    res = launch_mod.LaunchResult("$2", "@9", "agent-claude", "/tmp", pane_id="%9")
    monkeypatch.setattr(launch_mod, "launch_agent", lambda s, l, p: seen.append(l) or res)
    r = post(c, tok, "/a/claude-1/continue")
    assert r.status_code == 200 and "agent-claude" in r.text
    assert seen[0].kind is AgentKind.CLAUDE and seen[0].prompt == lineage.continue_prompt("claude-1", "do the thing")
    assert "agent_continue" in audit_text(st) and "do the thing" not in audit_text(st)


def test_continue_keeps_the_agents_model(fake, monkeypatch):
    from pm.agents import head as agenthead
    st, calls, _ = fake
    app, c, tok, st = mk(st)
    snap_with(monkeypatch, [agent()])
    monkeypatch.setattr(pm.projects.projects, "discover", lambda s: [Project(name="proj", path="/tmp")])
    monkeypatch.setattr(pm.web.routes.next, "chat_view", lambda s, a, p=None: ChatView(
        agent_id=a.id, state=AgentState.IDLE, last_role="assistant", last_text="go"))
    seen = []
    res = launch_mod.LaunchResult("$2", "@9", "agent-claude", "/tmp", pane_id="%9")
    monkeypatch.setattr(launch_mod, "launch_agent", lambda s, l, p: seen.append(l) or res)
    monkeypatch.setattr(agenthead, "live_model", lambda a: "claude-opus-5-5")
    assert post(c, tok, "/a/claude-1/continue").status_code == 200
    monkeypatch.setattr(agenthead, "live_model", lambda a: "claude")  # unknown: the kind name, no --model
    assert post(c, tok, "/a/claude-1/continue").status_code == 200
    assert [l.model for l in seen] == ["claude-opus-5-5", None]


def test_continue_refuses_without_assistant_message_or_csrf(fake, monkeypatch):
    st, calls, _ = fake
    app, c, tok, st = mk(st)
    snap_with(monkeypatch, [agent()])
    monkeypatch.setattr(pm.projects.projects, "discover", lambda s: [Project(name="proj", path="/tmp")])
    monkeypatch.setattr(pm.web.routes.next, "chat_view", lambda s, a, p=None: ChatView(
        agent_id=a.id, state=AgentState.IDLE, last_role="user", last_text="hi"))
    assert post(c, tok, "/a/claude-1/continue").status_code == 409
    assert post(c, tok, "/a/claude-1/continue", csrf=False).status_code == 403


def test_continue_prompt_too_long(fake, monkeypatch):
    st, calls, _ = fake
    app, c, tok, st = mk(st)
    snap_with(monkeypatch, [agent()])
    monkeypatch.setattr(pm.projects.projects, "discover", lambda s: [Project(name="proj", path="/tmp")])
    monkeypatch.setattr(pm.web.routes.next, "chat_view", lambda s, a, p=None: ChatView(
        agent_id=a.id, state=AgentState.IDLE, last_role="assistant", last_text="x" * 30000))
    assert post(c, tok, "/a/claude-1/continue").status_code == 400


def test_continue_limit_counts_the_preamble(fake, monkeypatch):
    st, calls, _ = fake
    app, c, tok, st = mk(st)
    snap_with(monkeypatch, [agent()])
    monkeypatch.setattr(pm.projects.projects, "discover", lambda s: [Project(name="proj", path="/tmp")])
    monkeypatch.setattr(launch_mod, "launch_agent", lambda *a: pytest.fail("must not launch"))
    room = 20000 - len(lineage.continue_prompt("claude-1", ""))
    r = post(c, tok, "/a/claude-1/continue", text="x" * (room + 1))
    assert r.status_code == 400 and "message too long" in r.text
    assert "too long" in audit_text(st) and "x" * 50 not in audit_text(st)


def test_continue_prompt_has_preamble_and_records_lineage(fake, monkeypatch):
    st, calls, _ = fake
    app, c, tok, st = mk(st)
    snap_with(monkeypatch, [agent()])
    monkeypatch.setattr(pm.projects.projects, "discover", lambda s: [Project(name="proj", path="/tmp")])
    seen = []
    res = launch_mod.LaunchResult("$2", "@9", "agent-claude", "/tmp", pane_id="%9")
    monkeypatch.setattr(launch_mod, "launch_agent", lambda s, l, p: seen.append(l) or res)
    assert post(c, tok, "/a/claude-1/continue", text="next steps").status_code == 200
    assert seen[0].prompt.startswith("Here is the previous work from claude-1. Do not continue it yet")
    assert seen[0].prompt.endswith("---\nnext steps")
    con = sqlite3.connect(st.db_file)
    try:
        got = con.execute("SELECT src_id, how, new_pane, kind, resolved_id FROM agent_lineage").fetchall()
    finally:
        con.close()
    assert got == [("claude-1", "continue", "%9", "claude", None)]
    log = audit_text(st)
    assert "next steps" not in log and "Here is the previous work" not in log


def dead_out(rows):
    """rows: (sid, sname, att, wid, wname, pid, dead)."""
    sep = tmuxctl.SEP
    return "\n".join(sep.join([sid, sn, str(att), wid, "0", wn, "1", pid, "1", "zsh", "/", "1", "t", d])
                     for sid, sn, att, wid, wn, pid, d in rows)


@pytest.fixture
def fk(fake_tmux, monkeypatch):
    s = dataclasses.replace(fake_tmux, unprotected_sessions=("work*",))
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setenv("FAKE_TMUX_OUT", dead_out([
        ("$1", "work1", 0, "@1", "done", "%1", "1"),
        ("$1", "work1", 0, "@2", "live", "%2", "0"),
        ("$3", "other", 0, "@3", "gone", "%3", "1"),
        ("$4", "work4", 0, "@4", "d1", "%4", "1"),
        ("$4", "work4", 0, "@5", "d2", "%5", "1"),
    ]))
    return s


def log(s):
    f = s.extras["log"]
    return f.read_text() if f.exists() else ""


def test_clear_kills_only_dead_windows(fk):
    r = tmuxkill.clear_ended_windows(fk, "$1")
    assert r.killed == ("done",) and r.kept == 1
    assert "kill-window\n-t\n@1\n" in log(fk) and "@2\n" not in log(fk).split("kill-window")[1]


def test_clear_keeps_one_window_when_all_dead(fk):
    r = tmuxkill.clear_ended_windows(fk, "$4")
    assert len(r.killed) == 1 and r.kept == 1


def test_clear_refuses_protected(fk):
    with pytest.raises(tmuxkill.KillRefused):
        tmuxkill.clear_ended_windows(fk, "$3")
    assert "kill-" not in log(fk)


def test_clear_route_refuses_protected_and_needs_csrf(fake, monkeypatch):
    st, calls, _ = fake
    app, c, tok, st = mk(st)
    assert post(c, tok, "/tmux/session/s2/clear", csrf=False).status_code == 403
    assert post(c, tok, "/tmux/session/s1/clear").status_code == 403  # $1 is protected in the fake
    assert not any("kill-" in x for x in calls)


def test_continue_from_stored_message_by_seq(fake, monkeypatch):
    from pm.core.models import ChatMsg

    st, calls, _ = fake
    app, c, tok, st = mk(st)
    snap_with(monkeypatch, [agent()])
    monkeypatch.setattr(pm.projects.projects, "discover", lambda s: [Project(name="proj", path="/tmp")])
    rows = {5: ChatMsg(seq=5, uid="a", conv="c", role="assistant", ts=1.0, text="older reply"),
            4: ChatMsg(seq=4, uid="u", conv="c", role="user", ts=1.0, text="a prompt")}
    asked = []

    class Log:
        @staticmethod
        def get(settings, agent_id, seq):
            asked.append((agent_id, seq))
            return rows.get(seq)
    monkeypatch.setattr("pm.web.chatctx.get_chatlog", lambda: Log)
    monkeypatch.setattr(pm.web.routes.next, "chat_view", lambda *a, **k: pytest.fail("chat_view must not be read"))
    seen = []
    res = launch_mod.LaunchResult("$2", "@9", "agent-claude", "/tmp", pane_id="%9")
    monkeypatch.setattr(launch_mod, "launch_agent", lambda s, l, p: seen.append(l) or res)
    r = post(c, tok, "/a/claude-1/continue", seq="5")
    assert r.status_code == 200 and seen[0].prompt == lineage.continue_prompt("claude-1", "older reply") and asked == [("claude-1", 5)]
    assert "older reply" not in audit_text(st)
    assert post(c, tok, "/a/claude-1/continue", seq="4").status_code == 409  # user message
    assert post(c, tok, "/a/claude-1/continue", seq="99").status_code == 409  # unknown
    for bad in ("x", "-1", "１", "9" * 30):
        assert post(c, tok, "/a/claude-1/continue", seq=bad).status_code == 400
    assert len(seen) == 1


def test_continue_uses_edited_text_from_composer(fake, monkeypatch):
    st, calls, _ = fake
    app, c, tok, st = mk(st)
    snap_with(monkeypatch, [agent()])
    monkeypatch.setattr(pm.projects.projects, "discover", lambda s: [Project(name="proj", path="/tmp")])
    monkeypatch.setattr(pm.web.routes.next, "chat_view", lambda *a, **k: pytest.fail("chat_view must not be read"))
    seen = []
    res = launch_mod.LaunchResult("$2", "@9", "agent-claude", "/tmp", pane_id="%9")
    monkeypatch.setattr(launch_mod, "launch_agent", lambda s, l, p: seen.append(l) or res)
    r = post(c, tok, "/a/claude-1/continue", text="edited and extended")
    assert r.status_code == 200 and seen[0].prompt == lineage.continue_prompt("claude-1", "edited and extended")
    assert "edited and extended" not in audit_text(st)


def _setup_continue(fake, monkeypatch, bins=None):
    import dataclasses
    st, calls, _ = fake
    if bins:
        st = dataclasses.replace(st, agent_bins=bins)
    app, c, tok, st = mk(st)
    snap_with(monkeypatch, [agent()])
    monkeypatch.setattr(pm.projects.projects, "discover", lambda s: [Project(name="proj", path="/tmp")])
    monkeypatch.setattr(pm.web.routes.next, "chat_view", lambda s, a, p=None: ChatView(
        agent_id=a.id, state=AgentState.IDLE, last_role="assistant", last_text="last reply"))
    return c, tok, st, calls


def test_continue_custom_prompt_has_no_preamble(fake, monkeypatch):
    c, tok, st, _ = _setup_continue(fake, monkeypatch)
    seen = []
    res = launch_mod.LaunchResult("$2", "@9", "agent-claude", "/tmp", pane_id="%9")
    monkeypatch.setattr(launch_mod, "launch_agent", lambda s, l, p: seen.append(l) or res)
    monkeypatch.setattr(pm.web.routes.next, "chat_view", lambda *a, **k: pytest.fail("chat_view must not be read"))
    got = []
    monkeypatch.setattr(lineage, "record", lambda *a: got.append(a))
    r = post(c, tok, "/a/claude-1/continue", mode="custom", text="  my own new secret prompt ", seq="5")
    assert r.status_code == 200 and "in agent-claude with this message" in r.text
    assert seen[0].prompt == "my own new secret prompt"
    assert got and got[0][1:] == ("claude-1", "continue", "%9", "claude")
    audit = audit_text(st)
    assert '"mode":"custom"' in audit and '"where":"window"' in audit and "secret prompt" not in audit


def test_continue_custom_refuses_empty_and_too_long(fake, monkeypatch):
    c, tok, st, _ = _setup_continue(fake, monkeypatch)
    monkeypatch.setattr(launch_mod, "launch_agent", lambda *a, **k: pytest.fail("must not launch"))
    assert post(c, tok, "/a/claude-1/continue", mode="custom").status_code == 400
    assert post(c, tok, "/a/claude-1/continue", mode="custom", text="   ").status_code == 400
    assert post(c, tok, "/a/claude-1/continue", mode="custom", text="x" * 20_001).status_code == 400
    assert "empty prompt" in audit_text(st)


def test_continue_invalid_mode_or_where(fake, monkeypatch):
    c, tok, st, _ = _setup_continue(fake, monkeypatch)
    monkeypatch.setattr(launch_mod, "launch_agent", lambda *a, **k: pytest.fail("must not launch"))
    for bad in ({"mode": "x"}, {"mode": "Custom"}, {"where": "tab"}, {"where": "%1"}):
        assert post(c, tok, "/a/claude-1/continue", text="hi", **bad).status_code == 400
    assert "bad mode" in audit_text(st) and "bad where" in audit_text(st)


def test_continue_where_pane_is_refused(fake, monkeypatch):
    """The split-pane launch is gone: ``where=pane`` is an unknown value (400, audited), nothing starts."""
    c, tok, st, calls = _setup_continue(fake, monkeypatch, {"claude": "/bin/true"})
    monkeypatch.setattr(launch_mod, "launch_agent", lambda *a, **k: pytest.fail("must not launch"))
    r = post(c, tok, "/a/claude-1/continue", where="pane", text="hello")
    assert r.status_code == 400
    assert not any(x and x[0] in ("split-window", "new-window") for x in calls)
    assert "bad where" in audit_text(st) and "hello" not in audit_text(st)
    assert pm.web.routes.next.WHERES == ("window",)


def test_continue_starts_its_own_window(fake, monkeypatch):
    """The real launch path: one new-window call (with remain-on-exit in the same sequence), never a split."""
    c, tok, st, calls = _setup_continue(fake, monkeypatch, {"claude": "/bin/true"})
    monkeypatch.setattr(tmuxctl, "new_session", lambda s, n, c_: "$7")
    monkeypatch.setattr(tmuxctl, "run", lambda s, a, timeout=0, **kw: calls.append(a) or "@8 %9")
    r = post(c, tok, "/a/claude-1/continue", where="window", text="hello")
    assert r.status_code == 200 and "Started a new claude in agent-claude with this message" in r.text
    assert not any(x and x[0] == "split-window" for x in calls)
    (nw,) = [x for x in calls if x and x[0] == "new-window"]
    assert nw[:7] == ["new-window", "-d", "-P", "-F", "#{window_id} #{pane_id}", "-t", "$7:"]
    assert "remain-on-exit" in nw and nw[-2:] == ["--", "agent-claude"]
