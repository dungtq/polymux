"""Auto-exit of idle agents: plan, sweep, window close, audit fail-closed, routes (fake tmux only)."""

import dataclasses
import time

import pytest
from helpers import audit_text, mk_client, post, snap_with

import pm.web.routes.autoexit
from pm.agents import autoexit, registry, stop
from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, AgentState, TmuxPane, TmuxSession, TmuxWindow
from pm.infra import snapshot as services
from pm.security.audit import AuditError, AuditLog
from pm.tmux import ctl as tmuxctl
from pm.tmux import kill as tmuxkill

REAL_REGISTRY = True
UUID = "11111111-2222-3333-4444-555555555555"
NOW = 1_000_000_000.0
DAY = 86400


def mk_agent(i=2, **kw):
    base = dict(id=f"claude-a1b2c{i}", kind=AgentKind.CLAUDE, pane_id=f"%{i}", session_id=f"${i}", pid=1,
                cwd="/tmp", project="proj", state=AgentState.IDLE, ident=UUID,
                last_active=NOW - 2 * DAY, window_name="w")
    base.update(kw)
    return Agent(**base)


def mk_sessions(cmd="bash", attached=0, panes=1, name="free1", dead=False):
    ps = tuple(TmuxPane(id=f"%{2 + k}", window_id="@2", session_id="$2", pid=4242 + k, command=cmd,
                        cwd="/", dead=dead) for k in range(panes))
    return (TmuxSession(id="$2", name=name, attached=attached,
                        windows=(TmuxWindow(id="@2", session_id="$2", index=0, name="w", panes=ps),)),)


@pytest.fixture
def env(tmp_path, monkeypatch):
    st = Settings(state_dir=tmp_path / "st", token_file=tmp_path / "tok", totp_file=tmp_path / "totp",
                  dev_root=tmp_path / "dev", extra_projects=(), autoexit_sessions=("free*",),
                  tmux_socket="pmtest-fake")
    calls = []
    state = {"sessions": mk_sessions()}
    monkeypatch.setattr(tmuxctl, "run", lambda s, a, timeout=0, **kw: calls.append(a) or "")
    monkeypatch.setattr(tmuxctl, "list_sessions", lambda s: state["sessions"])
    monkeypatch.setattr(tmuxctl, "send_key", lambda s, p, k: calls.append(["send-keys", p, k]))
    monkeypatch.setattr(stop.time, "sleep", lambda s: None)
    monkeypatch.setattr(autoexit, "_children", lambda pid: False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    return st, calls, state


def snap(agents, sessions):
    return services.Snapshot(ts=NOW, agents=tuple(agents), sessions=tuple(sessions))


def test_plan_filters(env):
    st, _, state = env
    s = state["sessions"]
    ok = mk_agent()
    cases = [
        dataclasses.replace(ok, state=AgentState.RUNNING),
        dataclasses.replace(ok, last_active=NOW - 3600),
        dataclasses.replace(ok, last_active=0.0),
        dataclasses.replace(ok, ident="nope", session_uuid=None),
        dataclasses.replace(ok, kind=AgentKind.CODEX),
    ]
    for a in cases:
        assert autoexit.plan(st, snap([a], s), NOW) == []
    c = autoexit.plan(st, snap([ok], s), NOW)
    assert [x.agent.id for x in c] == [ok.id]
    # protected session
    assert autoexit.plan(st, snap([ok], mk_sessions(name="prot")), NOW) == []
    # disabled
    assert autoexit.plan(st, snap([ok], s), NOW, timeout_s=0) == []


def test_default_allow_list_exits_any_non_pm_session(env):
    st, calls, state = env
    st = dataclasses.replace(st, autoexit_sessions=("*",), unprotected_sessions=())  # default; empty no longer blocks
    ok = mk_agent()
    assert [c.session for c in autoexit.plan(st, snap([ok], mk_sessions(name="work")), NOW)] == ["work"]
    # pm* stays refused: plan, exit and window close
    pm_s = mk_sessions(name="pm-main")
    assert autoexit.plan(st, snap([ok], pm_s), NOW) == []
    state["sessions"] = pm_s
    assert "protected" in autoexit.close_window_if_empty(st, ok)
    assert autoexit.sweep(st, AuditLog(st.audit_file), snap([ok], pm_s), NOW) == []
    assert not any(c[0] == "kill-window" for c in calls)
    # a session outside the allow-list is refused too
    st2 = dataclasses.replace(st, autoexit_sessions=("work*",))
    assert autoexit.plan(st2, snap([ok], mk_sessions(name="other")), NOW) == []


def test_default_allow_list_sweep_closes_window(env):
    st, calls, state = env
    st = dataclasses.replace(st, autoexit_sessions=("*",), unprotected_sessions=())
    state["sessions"] = mk_sessions(name="work")
    a = mk_agent()
    done = autoexit.sweep(st, AuditLog(st.audit_file), snap([a], state["sessions"]), NOW)
    assert done == [(a.id, "stopped")] and ["kill-window", "-t", "@2"] in calls


def test_exit_one_refuses_pm_session_and_own_session_skipped(env, monkeypatch):
    st, calls, state = env
    st = dataclasses.replace(st, autoexit_sessions=("*",))
    a = mk_agent()
    state["sessions"] = mk_sessions(name="pmx")
    cand = autoexit.Candidate(a, DAY, "w", "pmx")
    assert autoexit.exit_one(st, AuditLog(st.audit_file), cand, NOW) != "stopped"
    assert not any(c[0] == "kill-window" for c in calls)
    state["sessions"] = mk_sessions(name="work")
    monkeypatch.setenv("TMUX_PANE", "%2")  # pm's own session is still skipped under "*"
    calls.clear()
    assert autoexit.sweep(st, AuditLog(st.audit_file), snap([a], state["sessions"]), NOW) == []
    assert calls == []


def test_timeout_setting(env):
    st, _, _ = env
    assert autoexit.idle_timeout_s(st) == DAY
    assert autoexit.set_idle_timeout_s(st, 7200) == 7200 and autoexit.idle_timeout_s(st) == 7200
    autoexit.set_idle_timeout_s(st, 0)
    assert autoexit.idle_timeout_s(st) == 0
    a = mk_agent()
    assert autoexit.plan(st, snap([a], mk_sessions()), NOW) == []


def test_sweep_exits_records_and_closes_window(env):
    st, calls, state = env
    a = mk_agent()
    rid = registry.assign(st, [dataclasses.replace(a, id="x")], now=NOW - 5 * DAY)[0].id
    a = dataclasses.replace(a, id=rid)
    audit = AuditLog(st.audit_file)
    # the fake pane already shows a bare shell, so exit_agent reports "stopped"
    done = autoexit.sweep(st, audit, snap([a], state["sessions"]), NOW)
    assert done == [(rid, "stopped")]
    assert sum(1 for c in calls if c[-1] == "C-c") == 2
    assert ["kill-window", "-t", "@2"] in calls
    rec = registry.get(st, rid)
    assert rec.ended and rec.ident == UUID
    info = autoexit.info_for(st, rid)
    assert info["session"] == UUID and info["project"] == "proj" and info["window"] == "w"
    assert info["cwd"] == "/tmp" and info["kind"] == "claude"
    text = audit.path.read_text()
    assert text.count("agent_autoexit") == 2 and "autoexit" in text and UUID in text
    assert "ntfy" not in "".join(map(str, calls))


def test_window_kept_cases(env, monkeypatch):
    st, calls, state = env
    a = mk_agent()
    for sessions, why in ((mk_sessions(attached=1), "attached"), (mk_sessions(panes=2), "other panes"),
                          (mk_sessions(name="prot"), "protected"), (mk_sessions(cmd="vim"), "still running")):
        state["sessions"] = sessions
        assert why in autoexit.close_window_if_empty(st, a)
    monkeypatch.setattr(autoexit, "_children", lambda pid: True)
    state["sessions"] = mk_sessions()
    assert "child" in autoexit.close_window_if_empty(st, a)
    monkeypatch.setattr(autoexit, "_children", lambda pid: None)
    assert "child" in autoexit.close_window_if_empty(st, a)
    assert not any(c[0] == "kill-window" for c in calls)


def test_still_running_not_recorded(env, monkeypatch):
    st, calls, state = env
    a = mk_agent()
    monkeypatch.setattr(stop, "_check", lambda s, ag: stop.StopResult(ag.id, "still_running"))
    done = autoexit.sweep(st, AuditLog(st.audit_file), snap([a], state["sessions"]), NOW)
    assert done == [(a.id, "still_running")]
    assert autoexit.info_for(st, a.id) is None
    assert not any(c[0] == "kill-window" for c in calls)


def test_audit_fail_closed(env):
    st, calls, state = env

    class Broken(AuditLog):
        def write(self, entry):
            raise AuditError("x")

    a = mk_agent()
    done = autoexit.sweep(st, Broken(st.audit_file), snap([a], state["sessions"]), NOW)
    assert done == [(a.id, "audit unavailable")]
    assert calls == []


def test_own_session_and_errors_skipped(env, monkeypatch):
    st, calls, state = env
    a = mk_agent()
    monkeypatch.setenv("TMUX_PANE", "%2")  # pm itself runs in the agent's session
    assert autoexit.sweep(st, AuditLog(st.audit_file), snap([a], state["sessions"]), NOW) == []
    monkeypatch.delenv("TMUX_PANE")
    bad = dataclasses.replace(snap([a], state["sessions"]), errors=("tmux",))
    assert autoexit.sweep(st, AuditLog(st.audit_file), bad, NOW) == []
    assert calls == []


def test_max_per_cycle(env):
    st, calls, state = env
    ags = [mk_agent(i=2, id=f"claude-a1b2c{k}") for k in range(5)]
    done = autoexit.sweep(st, AuditLog(st.audit_file), snap(ags, state["sessions"]), NOW)
    assert len(done) == autoexit.MAX_PER_CYCLE


# ---- routes


def mk(st):
    app, c, sess, tok, st = mk_client(st)
    app.include_router(pm.web.routes.autoexit.router)
    return app, c, tok, st


def test_routes_save_preview(env, monkeypatch):
    st, calls, state = env
    a = mk_agent(last_active=time.time() - 2 * DAY)
    snap_with(monkeypatch, [a], state["sessions"])
    app, c, tok, st = mk(st)
    r = c.get("/settings/autoexit/preview")
    assert r.status_code == 200 and a.id in r.text and "Nothing was changed" in r.text
    assert not calls
    assert post(c, tok, f"/a/{a.id}/keepalive", keep="1").status_code in (404, 405)  # feature removed
    # settings
    assert post(c, tok, "/settings/autoexit", hours="abc").status_code == 200
    assert autoexit.idle_timeout_s(st) == DAY
    r = post(c, tok, "/settings/autoexit", hours="0")
    assert "disabled" in r.text and autoexit.idle_timeout_s(st) == 0
    assert "off" in c.get("/settings/autoexit/preview").text
    r = post(c, tok, "/settings/autoexit", hours="6")
    assert autoexit.idle_timeout_s(st) == 6 * 3600
    assert post(c, tok, "/settings/autoexit", csrf=False, hours="6").status_code == 403
    assert "autoexit_save" in audit_text(st)


def test_ended_note(env, monkeypatch):
    st, calls, state = env
    app, c, tok, st = mk(st)
    assert "Ended on its own" in c.get("/a/claude-a1b2c2/autoexit").text
    a = mk_agent()
    autoexit.sweep(st, AuditLog(st.audit_file), snap([a], state["sessions"]), NOW)
    t = c.get(f"/a/{a.id}/autoexit").text
    assert "Auto-exit, idle" in t and UUID[:8] in t and UUID in t and "Resume" not in t

