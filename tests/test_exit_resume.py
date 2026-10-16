"""Exit an idle agent, ended-sessions list, resume (fake tmux only)."""

import dataclasses
import shlex
import time
from pathlib import Path

import pytest
from helpers import audit_text, fake, mk_client, post, snap_with  # noqa: F401

import pm.web.routes.exit  # noqa: F401
from pm.agents import registry, stop
from pm.core.models import Agent, AgentKind, AgentState, Project
from pm.infra import snapshot as services
from pm.projects import projects
from pm.tmux import ctl as tmuxctl
from pm.web import render
from pm.web.render import TEMPLATE_DIR

REAL_REGISTRY = True
UUID = "11111111-2222-3333-4444-555555555555"


def idle_agent(**kw):
    return Agent(id="claude-1", kind=AgentKind.CLAUDE, pane_id="%2", session_id="$2", pid=1,
                 cwd="/", project="proj", state=AgentState.IDLE, ident=UUID, **kw)


@pytest.fixture
def client(fake, monkeypatch):  # noqa: F811
    st, calls, agent = fake
    from fastapi import FastAPI  # noqa: F401
    monkeypatch.setattr(stop.time, "sleep", lambda s: None)
    return st, calls


def mk(st):
    app, c, sess, tok, st = mk_client(st)
    app.include_router(pm.web.routes.exit.router)
    return app, c, tok, st


def _typed(calls):
    return [x for x in calls if "/exit" in " ".join(map(str, x))]


def test_exit_sends_slash_exit_and_records_resume_data(client, monkeypatch):
    from pm.agents import autoexit
    st, calls = client
    a = dataclasses.replace(idle_agent(), transcript="")
    app, c, tok, st = mk(st)
    rid = registry.assign(st, [a])[0].id
    snap_with(monkeypatch, [dataclasses.replace(a, id=rid)])
    r = post(c, tok, f"/a/{rid}/exit")
    assert r.status_code == 200 and "Sent /exit" in r.text
    assert _typed(calls)
    assert not any("C-c" in x for x in calls)  # no kill path
    info = autoexit.info_for(st, rid)
    assert info["session"] == UUID and info["kind"] == "claude" and info["manual"]
    assert "agent_exit" in audit_text(st) and '"sha256"' in audit_text(st)
    # the agent leaves on its own: the next complete snapshot marks the record ended, id kept
    registry.assign(st, [])
    rec = registry.get(st, rid)
    assert rec.ended and rec.ident == UUID
    # Resume builds claude --resume <uuid> from the record
    from pm.agents import resume as resume_mod
    proj = Project(name="proj", path=st.dev_root / "proj")
    (st.dev_root / "proj").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(resume_mod.os, "access", lambda p, m: True)  # no claude binary needed on the test host
    seen = []
    monkeypatch.setattr(tmuxctl, "run", lambda s, args, timeout=0, **kw: seen.append(list(args)) or "@9\n")
    try:
        resume_mod.resume_session(st, rec, proj)
    except Exception:  # noqa: BLE001 - only the built command matters here
        pass
    assert any("--resume" in " ".join(map(str, x)) and UUID in " ".join(map(str, x)) for x in seen)


def test_exit_in_protected_session_is_allowed_like_typing(client, monkeypatch):
    st, calls = client
    app, c, tok, st = mk(st)
    snap_with(monkeypatch, [dataclasses.replace(idle_agent(), pane_id="%1", session_id="$1")])
    r = post(c, tok, "/a/claude-1/exit")
    assert r.status_code == 200 and "Sent /exit" in r.text
    assert not any("C-c" in x for x in calls)


def test_exit_unknown_agent_is_a_clear_flash(client, monkeypatch):
    st, calls = client
    app, c, tok, st = mk(st)
    snap_with(monkeypatch, [])
    r = post(c, tok, "/a/nope-9/exit")
    assert r.status_code == 404 and 'class="flash err"' in r.text and "{" not in r.text
    assert not calls


def test_exit_cmd_per_kind():
    from pm.agents import slash
    assert slash.exit_cmd("claude") == "/exit" and slash.exit_cmd("codex") == "/quit"
    assert slash.exit_cmd("cursor-agent") == "/exit" and slash.exit_cmd("zzz") is None


def test_exit_needs_csrf(client, monkeypatch):
    st, calls = client
    app, c, tok, st = mk(st)
    snap_with(monkeypatch, [idle_agent()])
    assert post(c, tok, "/a/claude-1/exit", csrf=False).status_code == 403
    assert not calls


def test_ended_sessions_list_filters(tmp_path):
    from pm.core.config import Settings
    st = Settings(state_dir=tmp_path / "st")
    a = idle_agent()
    rid = registry.assign(st, [a], now=100)[0].id
    registry.assign(st, [], now=200)  # vanishes -> ended
    rows, total = registry.ended_sessions(st)
    assert [r.id for r in rows] == [rid] and total == 1
    assert registry.ended_sessions(st, live_idents={UUID}) == ([], 0)
    assert registry.ended_sessions(st, project="other") == ([], 0)
    bad = dataclasses.replace(a, ident="not-a-uuid", pane_id="%9", pid=5)
    registry.assign(st, [bad], now=300)
    registry.assign(st, [], now=400)
    assert total == 1 and registry.ended_sessions(st)[1] == 1  # non-uuid claude never listed
    # other kinds are listed, with or without an ident; dedup by ident, else by agent id
    dv = Agent(id="devin-1", kind=AgentKind.DEVIN, pane_id="%4", session_id="$2", pid=7,
               cwd="/", project="proj", state=AgentState.IDLE, ident=None)
    cu = dataclasses.replace(dv, id="cursor-1", kind=AgentKind.CURSOR, pane_id="%5", pid=8,
                             ident="cur-sess-1")
    registry.assign(st, [dv, cu], now=500)
    registry.assign(st, [], now=600)
    rows, total = registry.ended_sessions(st)
    assert {r.kind for r in rows} == {"claude", "devin", "cursor-agent"} and total == 3
    assert registry.ended_sessions(st, live_idents={"cur-sess-1"})[1] == 2


def test_ended_list_resume_button_only_with_resumable_ident(tmp_path):
    from jinja2 import Environment, FileSystemLoader

    from pm.core.config import Settings
    st = Settings(state_dir=tmp_path / "st")
    dv = Agent(id="devin-1", kind=AgentKind.DEVIN, pane_id="%4", session_id="$2", pid=7,
               cwd="/", project="proj", state=AgentState.IDLE, ident="dv-sess")
    ag = Agent(id="antigravity-1", kind=AgentKind.ANTIGRAVITY, pane_id="%5", session_id="$2", pid=8,
               cwd="/", project="proj", state=AgentState.IDLE, ident="agy-sess")
    ids = [r.id for r in registry.assign(st, [idle_agent(), dv, ag], now=100)]
    registry.assign(st, [], now=200)
    rows, total = registry.ended_sessions(st)
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    for n in ("ago", "ts", "dt", "aname", "kpill", "mpill", "tslot"):
        env.filters.setdefault(n, lambda v, *a, **k: str(v))
    env.filters["agent_q"] = render.agent_q
    env.globals["last_src"] = render.last_src
    html = env.from_string(
        '{% from "_agentrow.html" import ended_list, PROJECT_COLS %}{{ ended_list(rows, total, "tok", {}, PROJECT_COLS) }}'
    ).render(rows=rows, total=total)
    assert total == 3 and all(f'href="/a/{i}"' in html for i in ids)
    # claude (uuid) and devin (session name) are resumable; antigravity is not
    assert html.count("/resume") == 4 and f"/a/{ids[0]}/resume" in html and f"/a/{ids[1]}/resume" in html
    assert f"/a/{ids[2]}/resume" not in html


def test_resume_refuses_unknown_kind_and_bad_ident(client, monkeypatch):
    st, calls = client
    ags = [Agent(id="x-1", kind=AgentKind.ANTIGRAVITY, pane_id="%4", session_id="$2", pid=7,
                 cwd="/", project="proj", state=AgentState.IDLE, ident="agy-sess"),
           Agent(id="devin-1", kind=AgentKind.DEVIN, pane_id="%5", session_id="$2", pid=8,
                 cwd="/", project="proj", state=AgentState.IDLE, ident="--model=evil"),
           Agent(id="codex-1", kind=AgentKind.CODEX, pane_id="%6", session_id="$2", pid=9,
                 cwd="/", project="proj", state=AgentState.IDLE, ident="not-a-uuid"),
           Agent(id="cursor-agent-1", kind=AgentKind.CURSOR, pane_id="%7", session_id="$2", pid=10,
                 cwd="/", project="proj", state=AgentState.IDLE, ident=None)]
    rids = [r.id for r in registry.assign(st, ags, now=100)]
    registry.assign(st, [], now=200)
    snap_with(monkeypatch, [])
    app, c, tok, st = mk(st)
    for rid in rids:
        assert post(c, tok, f"/a/{rid}/resume").status_code == 409
    assert not any(x[0] == "new-window" for x in calls)


CODEX_STEM = "rollout-2026-10-11T13-00-58-01a1298c-d6a9-7101-8874-28f1b179c71b"
CODEX_UUID = "01a1298c-d6a9-7101-8874-28f1b179c71b"


@pytest.mark.parametrize("kind,ident,tail", [
    (AgentKind.CODEX, CODEX_STEM, f"resume {CODEX_UUID}"),
    (AgentKind.DEVIN, "married-scribe", "--resume married-scribe"),
    (AgentKind.CURSOR, UUID, f"--resume {UUID}"),
])
def test_resume_runs_per_kind(client, monkeypatch, tmp_path, kind, ident, tail):
    st, calls = client
    binp = tmp_path / kind.value
    binp.write_text("#!/bin/sh\n")
    binp.chmod(0o755)
    st = dataclasses.replace(st, agent_bins={kind.value: str(binp)})
    proj = tmp_path / "proj"
    proj.mkdir()
    a = Agent(id=f"{kind.value}-1", kind=kind, pane_id="%2", session_id="$2", pid=1, cwd=str(proj),
              project="proj", state=AgentState.IDLE, ident=ident)
    rid = registry.assign(st, [a], now=100)[0].id
    registry.assign(st, [], now=200)
    snap_with(monkeypatch, [])
    monkeypatch.setattr(projects, "discover", lambda s: [Project(name="proj", path=str(proj))])
    monkeypatch.setattr(tmuxctl, "list_sessions", lambda s: [])
    monkeypatch.setattr(tmuxctl, "new_session", lambda s, n, c: "$9")
    monkeypatch.setattr(tmuxctl, "run", lambda s, a, timeout=0, **kw: calls.append(a) or "@5 %7")
    app, c, tok, st = mk(st)
    r = post(c, tok, f"/a/{rid}/resume")
    assert r.status_code == 200 and "Resumed" in r.text
    nw = next(x for x in calls if x[0] == "new-window")
    assert _words(nw) == [str(binp), *shlex.split(tail)] and f"agent-{kind.value}-resume" in nw
    assert "agent_resume" in audit_text(st)
    # still running (live agent carries the same session) -> refused
    snap_with(monkeypatch, [dataclasses.replace(a, id="live-1")])
    assert post(c, tok, f"/a/{rid}/resume").status_code == 409


def test_resume_ident_validation():
    ri = registry.resume_ident
    assert ri("claude", UUID) == UUID and ri("claude", "x") is None
    assert ri("codex", CODEX_STEM) == CODEX_UUID and ri("codex", CODEX_UUID) == CODEX_UUID
    assert ri("codex", "rollout-x-" + CODEX_UUID) is None and ri("codex", "-" + CODEX_UUID) is None
    assert ri("devin", "married-scribe") == "married-scribe"
    for bad in ("-r", "--model=x", "a b", "a;b", "a/b", "", None, "a" * 81, "x.json"):
        assert ri("devin", bad) is None
    assert ri("cursor-agent", UUID) == UUID and ri("cursor-agent", "abc") is None
    assert ri("antigravity", UUID) is None


def test_resume_runs_claude_resume(client, monkeypatch, tmp_path):
    st, calls = client
    binp = tmp_path / "claude"
    binp.write_text("#!/bin/sh\n")
    binp.chmod(0o755)
    st = dataclasses.replace(st, agent_bins={"claude": str(binp)})
    proj = tmp_path / "proj"
    proj.mkdir()
    a = dataclasses.replace(idle_agent(), cwd=str(proj))
    rid = registry.assign(st, [a], now=100)[0].id
    registry.assign(st, [], now=200)
    snap_with(monkeypatch, [])
    p = Project(name="proj", path=str(proj))
    monkeypatch.setattr(projects, "discover", lambda s: [p])
    monkeypatch.setattr(tmuxctl, "list_sessions", lambda s: [])
    monkeypatch.setattr(tmuxctl, "new_session", lambda s, n, c: "$9")
    monkeypatch.setattr(tmuxctl, "run", lambda s, a, timeout=0, **kw: calls.append(a) or "@5 %7")
    app, c, tok, st = mk(st)
    r = post(c, tok, f"/a/{rid}/resume")
    assert r.status_code == 200 and "Resumed" in r.text
    nw = next(x for x in calls if x[0] == "new-window")
    assert _words(nw)[1:] == ["--resume", UUID] and "-c" in nw
    assert "agent_resume" in audit_text(st)


def _words(nw):
    """The argv after tmux's ``--`` and the shell prefix: positional args, never one shell string (#204). The
    fake tmux has no default-shell, so the fallback /bin/sh runs it."""
    words = nw[nw.index("--") + 1:]
    assert words[:4] == ["/bin/sh", "-c", 'exec "$@"', "sh"]
    return words[4:]


def _resume_env(client, monkeypatch, tmp_path, kind=AgentKind.CLAUDE, ident=UUID):
    st, calls = client
    binp = tmp_path / kind.value
    binp.write_text("#!/bin/sh\n")
    binp.chmod(0o755)
    st = dataclasses.replace(st, agent_bins={kind.value: str(binp)})
    proj = tmp_path / "proj"
    proj.mkdir()
    a = Agent(id=f"{kind.value}-1", kind=kind, pane_id="%2", session_id="$2", pid=1, cwd=str(proj),
              project="proj", state=AgentState.IDLE, ident=ident)
    rid = registry.assign(st, [a], now=100)[0].id
    registry.assign(st, [], now=200)
    snap_with(monkeypatch, [])
    monkeypatch.setattr(projects, "discover", lambda s: [Project(name="proj", path=str(proj))])
    monkeypatch.setattr(tmuxctl, "list_sessions", lambda s: [])
    monkeypatch.setattr(tmuxctl, "new_session", lambda s, n, c: "$9")
    monkeypatch.setattr(tmuxctl, "run", lambda s, a_, timeout=0, **kw: calls.append(a_) or "@5 %7")
    app, c, tok, st = mk(st)
    return st, calls, c, tok, rid, binp, a


def test_resume_and_send_passes_prompt_and_keeps_id(client, monkeypatch, tmp_path):
    st, calls, c, tok, rid, binp, a = _resume_env(client, monkeypatch, tmp_path)
    r = post(c, tok, f"/a/{rid}/resume", text="fix the tests\nthen commit\n")
    assert r.status_code == 200 and "Resumed" in r.text and f'href="/a/{rid}"' in r.text
    nw = next(x for x in calls if x[0] == "new-window")
    assert _words(nw) == [str(binp), "--resume", UUID, "--", "fix the tests\nthen commit"]
    log = audit_text(st)
    assert "agent_resume" in log and '"len":25' in log and "fix the tests" not in log
    # the resumed session is found by its ident: the same permanent id comes back live
    assert registry.assign(st, [dataclasses.replace(a, pane_id="%7")], now=300)[0].id == rid


def test_resume_and_send_refusals_start_nothing(client, monkeypatch, tmp_path):
    st, calls, c, tok, rid, binp, a = _resume_env(client, monkeypatch, tmp_path)
    r = post(c, tok, f"/a/{rid}/resume", text="/compact now")
    assert r.status_code == 400 and "slash commands need a running agent" in r.text and "<p" in r.text
    assert post(c, tok, f"/a/{rid}/resume", text="bad\x07bell").status_code == 400
    assert post(c, tok, f"/a/{rid}/resume", text="x" * 4001).status_code == 400
    assert not any(x[0] == "new-window" for x in calls)
    assert "compact" not in audit_text(st) and "bell" not in audit_text(st)
    # blank text = plain resume
    assert post(c, tok, f"/a/{rid}/resume", text="  \n ").status_code == 200
    assert _words(next(x for x in calls if x[0] == "new-window")) == [str(binp), "--resume", UUID]


@pytest.mark.parametrize("kind,ident,tail", [
    (AgentKind.CODEX, CODEX_STEM, f"resume {CODEX_UUID} -- '-x '"),
    (AgentKind.DEVIN, "married-scribe", "--resume married-scribe -- '-x '"),
    (AgentKind.CURSOR, UUID, f"--resume {UUID} -- '-x '"),
])
def test_resume_and_send_per_kind(client, monkeypatch, tmp_path, kind, ident, tail):
    st, calls, c, tok, rid, binp, a = _resume_env(client, monkeypatch, tmp_path, kind, ident)
    assert post(c, tok, f"/a/{rid}/resume", text="-x").status_code == 200
    assert _words(next(x for x in calls if x[0] == "new-window")) == [str(binp), *shlex.split(tail)]


def test_resume_refuses_running_and_unknown(client, monkeypatch, tmp_path):
    st, calls = client
    a = idle_agent()
    rid = registry.assign(st, [a], now=100)[0].id
    registry.assign(st, [], now=200)
    app, c, tok, st = mk(st)
    snap_with(monkeypatch, [dataclasses.replace(a, id="claude-live")])  # same session live
    assert post(c, tok, f"/a/{rid}/resume").status_code == 409
    assert post(c, tok, "/a/nope-9/resume").status_code == 404
    assert not any(x[0] == "new-window" for x in calls)


def test_mode_endpoint_follows_live_to_ended_to_live(client, monkeypatch):
    st, calls = client
    a = idle_agent()
    rid = registry.assign(st, [a], now=100)[0].id
    app, c, tok, st = mk(st)
    live = dataclasses.replace(a, id=rid)
    snap_with(monkeypatch, [live])
    assert c.get(f"/a/{rid}/mode").json() == {"mode": "live"}
    registry.assign(st, [], now=200)  # agent left a complete snapshot: record ended
    snap_with(monkeypatch, [])
    r = c.get(f"/a/{rid}/mode")
    assert r.json() == {"mode": "ended"} and r.headers["cache-control"] == "no-store"
    snap_with(monkeypatch, [live])  # resumed: same id is live again
    assert c.get(f"/a/{rid}/mode").json() == {"mode": "live"}
    assert c.get("/a/nope-9/mode").json() == {"mode": "gone"}
    assert c.get("/a/BAD!/mode").json() == {"mode": "gone"}


def test_pages_wire_mode_switch_and_transition_labels(client, monkeypatch):
    st, calls = client
    a = idle_agent()
    rid = registry.assign(st, [a], now=100)[0].id
    app, c, tok, st = mk(st)
    import pm.web.routes.pages
    app.include_router(pm.web.routes.pages.router)
    snap_with(monkeypatch, [dataclasses.replace(a, id=rid)])
    live = c.get(f"/a/{rid}")
    assert live.status_code == 200, live.status_code
    assert 'data-mode="live"' in live.text and "agentmode.js" in live.text
    assert 'data-transition="Exiting' in live.text
    registry.assign(st, [], now=200)
    snap_with(monkeypatch, [])
    ended = c.get(f"/a/{rid}")
    assert ended.status_code == 200, ended.status_code
    assert 'data-mode="ended"' in ended.text and "agentmode.js" in ended.text


def test_ended_page_old_record_facts_and_resume_gating(client, monkeypatch, tmp_path):
    import json

    import pm.web.routes.autoexit
    import pm.web.routes.pages
    from pm.agents import head as agenthead
    st, calls = client
    root = tmp_path / "projects"
    root.mkdir()
    tr = root / f"{UUID}.jsonl"
    tr.write_text(json.dumps({"type": "assistant", "message": {"model": "claude-test-9"}}) + "\n")
    monkeypatch.setattr(agenthead.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "projects").symlink_to(root)
    outside = tmp_path / "outside.jsonl"
    outside.write_text(json.dumps({"type": "assistant", "message": {"model": "evil"}}) + "\n")
    (root / "link.jsonl").symlink_to(outside)
    assert agenthead.model_from_record_transcript(str(root / "link.jsonl")) == ""
    a = idle_agent(transcript=str(tr))
    old = registry.assign(st, [a], now=100)[0].id
    registry.assign(st, [], now=200)
    app, c, tok, st = mk(st)
    app.include_router(pm.web.routes.pages.router)
    app.include_router(pm.web.routes.autoexit.router)
    snap_with(monkeypatch, [])
    note = c.get(f"/a/{old}/autoexit").text
    assert "Ended on its own" in note and "claude-test-9" in note and UUID[:8] in note and UUID in note
    page = c.get(f"/a/{old}").text
    assert "opens a new tmux window</span>" not in page and 'title="Opens a new tmux window' in page
    assert "cannot resume" not in page


def test_resume_without_ident_is_disabled_and_flash(client, monkeypatch):
    import pm.web.routes.pages
    st, calls = client
    a = dataclasses.replace(idle_agent(), ident=None)
    rid = registry.assign(st, [a], now=100)[0].id
    registry.assign(st, [], now=200)
    app, c, tok, st = mk(st)
    app.include_router(pm.web.routes.pages.router)
    snap_with(monkeypatch, [])
    page = c.get(f"/a/{rid}").text
    assert "No session id recorded, cannot resume" in page and "disabled" in page
    assert f'action="/a/{rid}/resume"' not in page
    r = post(c, tok, f"/a/{rid}/resume")
    assert r.status_code == 409 and "Cannot resume: no session id recorded." in r.text and "<p" in r.text
    r = post(c, tok, "/a/nope-9/resume")
    assert r.status_code == 404 and "Cannot resume: unknown agent." in r.text


def test_ended_page_is_frameable_in_the_drawer(client, monkeypatch):
    """Exit / opening an ended session in the drawer iframe (?embed=1) must not be blocked by
    frame-ancestors 'none' ("refused to connect"); the full page stays unframeable."""
    import pm.web.routes.pages
    st, calls = client
    old = registry.assign(st, [idle_agent()], now=100)[0].id
    registry.assign(st, [], now=200)
    app, c, tok, st = mk(st)
    app.include_router(pm.web.routes.pages.router)
    snap_with(monkeypatch, [])
    emb = c.get(f"/a/{old}?embed=1")
    assert emb.status_code == 200 and "Ended" in emb.text
    assert "frame-ancestors 'self'" in emb.headers["content-security-policy"]
    assert 'class="v5-embed"' in emb.text and 'id="v5chat"' not in emb.text
    assert "frame-ancestors 'none'" in c.get(f"/a/{old}").headers["content-security-policy"]


# --- Exit and kill window -------------------------------------------------------------------
def _layout(monkeypatch, windows_per_session=1, panes=1, name="free1"):
    from pm.core.models import TmuxPane, TmuxSession, TmuxWindow
    n = [0]

    def win(i):
        ps = []
        for _ in range(panes):
            n[0] += 1
            ps.append(TmuxPane(id=f"%{n[0]}", window_id=f"@{i}", session_id="$2", pid=1,
                               command="x", cwd="/"))
        return TmuxWindow(id=f"@{i}", session_id="$2", index=i, name="w", panes=tuple(ps))
    sess = (TmuxSession(id="$2", name=name, windows=tuple(win(i) for i in range(windows_per_session))),)
    monkeypatch.setattr(tmuxctl, "list_sessions", lambda s: sess)


def _kill_agent(pane):
    return Agent(id="claude-1", kind=AgentKind.CLAUDE, pane_id=pane, session_id="$2", pid=1,
                 cwd="/", project="proj", state=AgentState.IDLE, ident=UUID)


def test_exit_kill_kills_pane_by_id(client, monkeypatch):
    st, calls = client
    _layout(monkeypatch, windows_per_session=2)  # panes %1, %2 in two windows
    a = _kill_agent("%2")
    app, c, tok, st = mk(st)
    snap_with(monkeypatch, [a])
    r = post(c, tok, "/a/claude-1/exitkill")
    assert r.status_code == 200 and "Killed" in r.text
    assert ["kill-pane", "-t", "%2"] in calls
    assert not _typed(calls)  # no graceful /exit needed
    assert '"action":"agent_exit_kill"' in audit_text(st) and '"result":"ok"' in audit_text(st)


def test_exit_kill_refuses_only_pane_protected_and_guards(client, monkeypatch):
    st, calls = client
    a = _kill_agent("%1")
    app, c, tok, st = mk(st)
    snap_with(monkeypatch, [a])
    _layout(monkeypatch)  # one window, one pane: would end the whole session
    r = post(c, tok, "/a/claude-1/exitkill")
    assert r.status_code == 409 and "only pane" in r.text
    _layout(monkeypatch, windows_per_session=2, name="prot")
    r = post(c, tok, "/a/claude-1/exitkill")
    assert r.status_code == 409 and "protected" in r.text
    assert not any(x[0] == "kill-pane" for x in calls)
    assert post(c, tok, "/a/claude-1/exitkill", csrf=False).status_code == 403
    assert post(c, tok, "/a/claude-1/exitkill", origin="https://evil.example").status_code == 403
    assert '"action":"agent_exit_kill"' in audit_text(st) and '"result":"denied"' in audit_text(st)


def test_exit_kill_multi_pane_window_kills_just_the_pane(client, monkeypatch):
    st, calls = client
    _layout(monkeypatch, panes=2)  # one window, two panes: the window survives
    app, c, tok, st = mk(st)
    snap_with(monkeypatch, [_kill_agent("%2")])
    assert post(c, tok, "/a/claude-1/exitkill").status_code == 200
    assert ["kill-pane", "-t", "%2"] in calls and not any(x[0] == "kill-window" for x in calls)


def test_agent_page_has_exit_dialog():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "src" / "pm"
    t = (TEMPLATE_DIR / "agent.html").read_text() + (TEMPLATE_DIR / "_composer_macros.html").read_text()
    assert 'id="exit-dialog"' in t and 'data-dlg-open="exit-dialog"' in t
    assert 'hx-post="{{ base }}/exitkill"' in t and 'data-dlg-close' in t
    assert 'hx-confirm="Exit' not in t  # no native confirm popup
    assert "dialog.js" in (TEMPLATE_DIR / "base.html").read_text() and (root / "static/js/dialog.js").exists()


def test_ended_list_shows_ntfy_title_and_message(tmp_path):
    from jinja2 import Environment, FileSystemLoader

    from pm.core.config import Settings
    from pm.core.models import NtfyItem
    st = Settings(state_dir=tmp_path / "st")
    rid = registry.assign(st, [idle_agent()], now=100)[0].id
    registry.assign(st, [], now=200)
    rows, total = registry.ended_sessions(st)
    tpl = TEMPLATE_DIR
    env = Environment(loader=FileSystemLoader(tpl))
    for n in ("ago", "ts", "dt", "aname", "kpill"):
        env.filters.setdefault(n, lambda v, *a, **k: str(v))
    env.filters["agent_q"] = render.agent_q
    env.globals["last_src"] = render.last_src
    item = NtfyItem(id="1", time=150.0, title="claude-7cd08a - did it", message="All done, tests pass")
    html = env.from_string(
        '{% from "_agentrow.html" import ended_list %}{{ ended_list(rows, total, "", ntfy) }}'
    ).render(rows=rows, total=total, ntfy={rid: item}, aname=lambda a, k: a, kpill=lambda k: "")
    assert "Last message" in html and "ntfy title" not in html
    assert '<span class="kpill kp-src kp-ntfy" title="ntfy">ntfy</span> All done, tests pass' in html
    assert html.index('n-proj">Project') < html.index('n-main">Agent') < html.index("n-msg") < html.index("n-tmux") < html.index("n-age") < html.index("n-start") < html.index("n-res")


def test_ended_grid_shows_ntfy_title_and_message(tmp_path):
    from jinja2 import Environment, FileSystemLoader

    from pm.core.config import Settings
    from pm.core.models import NtfyItem
    st = Settings(state_dir=tmp_path / "st")
    rid = registry.assign(st, [idle_agent()], now=100)[0].id
    registry.assign(st, [], now=200)
    rows, total = registry.ended_sessions(st)
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    for n in ("ago", "ts", "dt", "aname", "kpill", "mpill", "tslot"):
        env.filters.setdefault(n, lambda v, *a, **k: str(v))
    env.filters["agent_q"] = render.agent_q
    env.globals["last_src"] = render.last_src
    item = NtfyItem(id="1", time=150.0, title="claude-7cd08a - Fix login", message="Login fixed")
    html = env.from_string(
        '{% from "_agentrow.html" import ended_list, PROJECT_COLS %}{{ ended_list(rows, total, "", ntfy, PROJECT_COLS) }}'
    ).render(rows=rows, total=total, ntfy={rid: item})
    assert '<span class="kpill kp-src kp-ntfy" title="ntfy">ntfy</span> Login fixed' in html


def test_ntfy_owner_from_agent_id_in_title(tmp_path):
    from pm.core.config import Settings
    from pm.notify import owner as ntfy_owner
    st = Settings(state_dir=tmp_path / "st")
    rid = registry.assign(st, [idle_agent()], now=100)[0].id
    o = ntfy_owner.owner_for(st, [], f"{rid} - did a thing", 150.0)
    assert o is not None and o.agent_id == rid
    assert ntfy_owner.owner_for(st, [], "claude-abcdef - unknown id", 150.0) is None
    assert ntfy_owner.owner_for(st, [], "Claude Code -- old style", 150.0) is None
