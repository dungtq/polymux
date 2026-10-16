"""Fork an agent conversation: mode table, argv per kind, route guards, markup (fake tmux only)."""

import dataclasses
import re
import sqlite3
from pathlib import Path

import pytest
from helpers import audit_text, fake, mk_client, post, snap_with  # noqa: F401
from helpers import pages_env as env  # noqa: F401  (fixture)
from jinja2 import Environment, FileSystemLoader

import pm.web.routes.fork
from pm.agents import fork, registry
from pm.core.models import Agent, AgentKind, AgentState, Project
from pm.projects import projects
from pm.tmux import ctl as tmuxctl
from pm.web import render
from pm.web.render import TEMPLATE_DIR

REAL_REGISTRY = True
UUID = "11111111-2222-3333-4444-555555555555"
ROLL = f"rollout-2026-10-07T10-00-00-{UUID}"


def test_fork_mode_table():
    assert fork.fork_mode("claude") == "native" and fork.fork_mode(AgentKind.CODEX) == "native"
    for k in ("devin", "cursor-agent", "antigravity", "agy", AgentKind.DEVIN, AgentKind.CURSOR):
        assert fork.fork_mode(k) == "handoff"
    assert fork.fork_mode("other") is None and fork.fork_mode("bogus") is None


def bins(st, tmp_path, *names):
    d = {}
    for n in names:
        p = tmp_path / ("bin-" + n)
        p.write_text("#!/bin/sh\n")
        p.chmod(0o755)
        d[n] = str(p)
    return dataclasses.replace(st, agent_bins=d), d


def ag(kind, ident, transcript="", cwd="/", **kw):
    return Agent(id=f"{kind}-1", kind=kind, pane_id="%2", session_id="$2", pid=1, cwd=cwd,
                 project="proj", state=AgentState.IDLE, ident=ident, transcript=transcript, **kw)


def test_argv_native(fake, tmp_path):
    st, calls, _ = fake
    st, b = bins(st, tmp_path, "claude", "codex")
    assert fork.build_argv(st, ag(AgentKind.CLAUDE, UUID)) == (
        "claude", [b["claude"], "--resume", UUID, "--fork-session"], "native")
    assert fork.build_argv(st, ag(AgentKind.CODEX, ROLL)) == (
        "codex", [b["codex"], "fork", UUID], "native")


def test_argv_handoff(fake, tmp_path):
    st, calls, _ = fake
    st, b = bins(st, tmp_path, "devin", "cursor-agent", "antigravity")
    f = tmp_path / "sess.json"
    f.write_text("{}")
    for kind, name in ((AgentKind.DEVIN, "devin"), (AgentKind.CURSOR, "cursor-agent")):
        k, argv, mode = fork.build_argv(st, ag(kind, "abc", str(f)))
        assert k == name and mode == "handoff" and argv[:2] == [b[name], "--"]
        assert str(f) in argv[2] and len(argv) == 3
    # antigravity kind string works without the enum member; binary comes from agent_bins
    class A:  # duck typed record
        kind, ident, transcript, cwd = "agy", "c1", str(f), "/"
    k, argv, mode = fork.build_argv(st, A)
    assert k == "antigravity" and argv[0] == b["antigravity"] and mode == "handoff"
    # codex without a uuid degrades to handoff
    st, b2 = bins(st, tmp_path, "codex")
    assert fork.build_argv(st, ag(AgentKind.CODEX, "weird", str(f)))[2] == "handoff"


def test_refusals(fake, tmp_path):
    st, calls, _ = fake
    st, b = bins(st, tmp_path, "claude", "devin")
    with pytest.raises(fork.ForkError):
        fork.build_argv(st, ag(AgentKind.CLAUDE, None))
    with pytest.raises(fork.ForkError):
        fork.build_argv(st, ag(AgentKind.CLAUDE, "not-a-uuid"))
    with pytest.raises(fork.ForkError):
        fork.build_argv(st, ag(AgentKind.DEVIN, "abc", "/nonexistent/file"))
    with pytest.raises(fork.ForkError):
        fork.build_argv(st, ag(AgentKind.OTHER, UUID))


def route_env(fake, monkeypatch, tmp_path, kind=AgentKind.CLAUDE, ident=UUID, transcript=""):
    st, calls, _ = fake
    st, b = bins(st, tmp_path, "claude", "codex", "devin")
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    a = ag(kind, ident, transcript, cwd=str(proj))
    rid = registry.assign(st, [a], now=100)[0].id
    a = dataclasses.replace(a, id=rid)
    snap_with(monkeypatch, [a])
    p = Project(name="proj", path=str(proj))
    monkeypatch.setattr(projects, "discover", lambda s: [p])
    monkeypatch.setattr(tmuxctl, "new_session", lambda s, n, c: "$9")
    monkeypatch.setattr(pm.agents.fork, "live_session_id", lambda pid: None)
    monkeypatch.setattr(tmuxctl, "run", lambda s, a_, timeout=0, **kw: calls.append(a_) or "@5 %7")
    app, c, tok, st = mk(st)
    return st, calls, c, tok, rid, proj


def mk(st):
    app, c, sess, tok, st = mk_client(st)
    app.include_router(pm.web.routes.fork.router)
    return app, c, tok, st


def test_route_window_and_audit(fake, monkeypatch, tmp_path):
    st, calls, c, tok, rid, proj = route_env(fake, monkeypatch, tmp_path)
    monkeypatch.setattr(tmuxctl, "list_sessions", lambda s: [])
    r = post(c, tok, f"/a/{rid}/fork")
    assert r.status_code == 200 and "Forked" in r.text and "agent-claude-fork" in r.text
    nw = next(x for x in calls if x[0] == "new-window")
    assert "-d" in nw and not any(x[0] == "split-window" for x in calls)  # background window only
    assert nw[nw.index("-n") + 1] == "agent-claude-fork" and nw[nw.index("-c") + 1] == str(proj)
    assert nw[nw.index("--") + 1:][5:] == ["--resume", UUID, "--fork-session"]  # sh -c 'exec "$@"' sh bin ...
    log = audit_text(st)
    assert "agent_fork" in log and "native" in log and UUID[:8] in log and UUID not in log


def test_route_where_is_ignored(fake, monkeypatch, tmp_path):
    st, calls, c, tok, rid, proj = route_env(fake, monkeypatch, tmp_path)
    monkeypatch.setattr(tmuxctl, "list_sessions", lambda s: [])
    for where in ("pane", "bogus"):
        assert post(c, tok, f"/a/{rid}/fork", where=where).status_code == 200
    assert not any(x[0] == "split-window" for x in calls)
    assert sum(1 for x in calls if x[0] == "new-window") == 2
    assert "where" not in audit_text(st)


def test_route_records_lineage(fake, monkeypatch, tmp_path):
    from pm.agents import lineage
    st, calls, c, tok, rid, proj = route_env(fake, monkeypatch, tmp_path)
    monkeypatch.setattr(tmuxctl, "list_sessions", lambda s: [])
    assert post(c, tok, f"/a/{rid}/fork").status_code == 200
    con = sqlite3.connect(st.db_file)
    try:
        rows = con.execute("SELECT src_id, how, new_pane, kind FROM agent_lineage").fetchall()
    finally:
        con.close()
    assert rows == [(rid, "fork", "%7", "claude")]
    assert lineage.of(st, rid) is None


def test_route_handoff_devin(fake, monkeypatch, tmp_path):
    f = tmp_path / "d.json"
    f.write_text("{}")
    st, calls, c, tok, rid, proj = route_env(fake, monkeypatch, tmp_path, AgentKind.DEVIN, "d1", str(f))
    monkeypatch.setattr(tmuxctl, "list_sessions", lambda s: [])
    assert post(c, tok, f"/a/{rid}/fork").status_code == 200
    nw = next(x for x in calls if x[0] == "new-window")
    assert str(f) in nw[-1] and "--resume" not in nw[-1] and "handoff" in audit_text(st)


def test_route_unknown_session_409(fake, monkeypatch, tmp_path):
    st, calls, c, tok, rid, proj = route_env(fake, monkeypatch, tmp_path, ident=None)
    assert post(c, tok, f"/a/{rid}/fork").status_code == 409
    assert not any(x[0] in ("new-window", "split-window") for x in calls)


def test_route_guards(fake, monkeypatch, tmp_path):
    st, calls, c, tok, rid, proj = route_env(fake, monkeypatch, tmp_path)
    assert post(c, tok, f"/a/{rid}/fork", csrf=False).status_code == 403
    assert post(c, tok, f"/a/{rid}/fork", origin="https://evil.example").status_code == 403
    assert post(c, tok, "/a/nope-9/fork").status_code == 404
    c.cookies.clear()
    assert post(c, tok, f"/a/{rid}/fork").status_code in (401, 403)
    assert not calls


def test_route_ended_record(fake, monkeypatch, tmp_path):
    st, calls, c, tok, rid, proj = route_env(fake, monkeypatch, tmp_path)
    registry.assign(st, [], now=200)  # ended
    snap_with(monkeypatch, [])
    monkeypatch.setattr(tmuxctl, "list_sessions", lambda s: [])
    assert post(c, tok, f"/a/{rid}/fork").status_code == 200
    assert next(x for x in calls if x[0] == "new-window")[-1] == "--fork-session"


def test_argv_with_prompt(fake, tmp_path):
    st, calls, _ = fake
    st, b = bins(st, tmp_path, "claude", "codex", "devin")
    assert fork.build_argv(st, ag(AgentKind.CLAUDE, UUID), "go on")[1] == [
        b["claude"], "--resume", UUID, "--fork-session", "--", "go on"]
    assert fork.build_argv(st, ag(AgentKind.CODEX, ROLL), "-v")[1] == [b["codex"], "fork", UUID, "--", "-v "]
    f = tmp_path / "sess.json"
    f.write_text("{}")
    k, argv, mode = fork.build_argv(st, ag(AgentKind.DEVIN, "abc", str(f)), "try plan B")
    assert mode == "handoff" and argv[:2] == [b["devin"], "--"] and len(argv) == 3
    assert str(f) in argv[2] and argv[2].endswith("answer my message below.\n\ntry plan B")
    assert "wait for my next instruction" in fork.build_argv(st, ag(AgentKind.DEVIN, "abc", str(f)))[1][2]


def test_route_fork_and_send(fake, monkeypatch, tmp_path):
    st, calls, c, tok, rid, proj = route_env(fake, monkeypatch, tmp_path)
    monkeypatch.setattr(tmuxctl, "list_sessions", lambda s: [])
    r = post(c, tok, f"/a/{rid}/fork", text="explore option two")
    assert r.status_code == 200 and "Forked" in r.text
    nw = next(x for x in calls if x[0] == "new-window")
    assert nw[nw.index("--") + 1:][5:] == ["--resume", UUID, "--fork-session", "--", "explore option two"]
    log = audit_text(st)
    assert '"len":18' in log and "explore" not in log


def test_route_fork_and_send_handoff_and_ended(fake, monkeypatch, tmp_path):
    f = tmp_path / "d.json"
    f.write_text("{}")
    st, calls, c, tok, rid, proj = route_env(fake, monkeypatch, tmp_path, AgentKind.DEVIN, "d1", str(f))
    registry.assign(st, [], now=200)  # ended
    snap_with(monkeypatch, [])
    monkeypatch.setattr(tmuxctl, "list_sessions", lambda s: [])
    assert post(c, tok, f"/a/{rid}/fork", text="and add tests").status_code == 200
    last = next(x for x in calls if x[0] == "new-window")[-1]
    assert str(f) in last and "and add tests" in last and "handoff" in audit_text(st)


def test_route_fork_and_send_refusals(fake, monkeypatch, tmp_path):
    st, calls, c, tok, rid, proj = route_env(fake, monkeypatch, tmp_path)
    r = post(c, tok, f"/a/{rid}/fork", text="/model opus")
    assert r.status_code == 400 and "slash commands need a running agent" in r.text
    assert post(c, tok, f"/a/{rid}/fork", text="a\x1bb").status_code == 400
    assert not any(x[0] in ("new-window", "split-window") for x in calls)
    assert "opus" not in audit_text(st)


_env = Environment(loader=FileSystemLoader(TEMPLATE_DIR),
                   autoescape=True)
_env.filters.update(ts=str, dt=str, urlencode=str, ago=str, agent_q=render.agent_q, model_short=str, placeholder=lambda a: ("", ""))
_env.globals["agent_name"] = lambda a: a.label
_env.globals["tz_label"] = lambda: "UTC+7"


def _ended(kind, project="pm"):
    class A: id = "x-1"; label = "x"; window_name = None
    class R: cwd = "/x"; ended_at = 1; last_seen = 1; first_seen = 1; last_pane = "%1"
    R.kind, R.project = kind, project
    R.resume_id = "abc" if kind in ("claude", "codex", "devin", "cursor-agent") else None
    return _env.get_template("agent_ended.html").render(csrf="t", agent=A, rec=R, project=None,
                                                         now_in_pane=None, resumable=True)


def test_ended_page_fork_button():
    for k in ("claude", "codex", "devin", "cursor-agent", "antigravity"):
        h = _ended(k)
        assert 'action="/a/x-1/fork"' in h and 'name="where"' not in h
        assert "style=" not in h and "onclick" not in h
    assert "/resume" not in _ended("antigravity") and all("/resume" in _ended(k) for k in ("claude", "codex", "devin", "cursor-agent"))
    assert "/fork" not in _ended("other") and "/fork" not in _ended("claude", project="")


def test_ended_page_actions_in_head_and_composer():
    h = _ended("claude")
    head = h[h.index('class="agent-head"'):h.index('id="flash"')]
    assert 'action="/a/x-1/resume"' in head and 'action="/a/x-1/fork"' in head and 'data-chat-zoom' in head
    assert 'ended-foot' not in h and 'flash-foot' not in h and h.count('action="/a/x-1/resume"') == 2
    comp = h[h.index('data-composer="ended"'):]  # the ended composer: Resume and send (its form) / Fork and send
    assert 'action="/a/x-1/resume"' in comp and 'hx-post="/a/x-1/fork"' in comp and "data-cmp-alt" in comp
    assert h.count('id="flash"') == 1 and 'data-page-jump="bottom"' in h  # the jump rail stays, no second bottom button
    assert 'class="agent-actions"' not in _ended("other") and 'class="agent-actions"' not in _ended("claude", project="")


def test_ended_page_origin_link():
    from types import SimpleNamespace as NS
    base = _ended("claude")
    assert "Origin" not in base and "am-origin" not in base
    class A: id = "x-1"; label = "x"; window_name = None
    class R: cwd = "/x"; ended_at = 1; last_seen = 1; first_seen = 1; last_pane = "%1"; kind = "claude"; project = "pm"; resume_id = "abc"
    o = NS(agent_id="claude-7", how="fork", label="Fork from")
    h = _env.get_template("agent_ended.html").render(csrf="t", agent=A, rec=R, project=None, now_in_pane=None,
                                                      resumable=True, origin=o)
    assert '<span class="em-k">Origin</span>' in h and "Fork from: " in h
    assert 'href="/a/claude-7" target="_blank" rel="noopener"' in h


def test_live_page_single_fork_form(env):
    from helpers import login
    s, c = env
    login(s, c)
    t = c.get("/a/claude-12").text
    assert "fork-menu" not in t and "fork-group" not in t and "fork-pop" not in t
    assert 'name="where"' not in t and 'value="pane"' not in t
    assert len(re.findall(r'action="/a/[^"]+/fork"', t)) == 1
    assert "Fork in new window" not in t and "Fork in split pane" not in t
    assert "Clone</a>" not in t and "launch?like=" not in t
