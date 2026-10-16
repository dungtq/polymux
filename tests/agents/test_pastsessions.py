"""Import of past CLI sessions as ended records; HISTORY, ended page and Resume use them unchanged."""

import asyncio
import json
import os
import time

import pytest
from chat.test_past import U1, U2, claude_file, codex_file, cursor_db

from pm.agents import fork, pastsessions, registry, resume
from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, AgentState, Project

REAL_REGISTRY = True


@pytest.fixture(autouse=True)
def _fresh_memo():
    pastsessions._seen.clear()
    pastsessions._heads.clear()
    yield
    pastsessions._seen.clear()
    pastsessions._heads.clear()


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))  # nothing may reach the real ~/.claude etc.
    s = Settings(state_dir=tmp_path / "st", dev_root=tmp_path / "dev", extra_projects=(),
                 tmux_socket="pmtest-unused")
    cwd = str(tmp_path / "dev" / "alpha")
    return s, home, cwd, [Project(name="alpha", path=cwd)]


def _live(cwd, ident, pid=4242, kind=AgentKind.CLAUDE):
    return Agent(id="x", kind=kind, pane_id="%7", session_id="$1", pid=pid, cwd=cwd, project="alpha",
                 state=AgentState.IDLE, state_since=1.0, ident=ident)


def test_import_adds_ended_rows_per_kind(env):
    s, home, cwd, projects = env
    cf = claude_file(home, cwd, U1, age_s=3600)
    xf = codex_file(home, cwd, U2, age_s=7200)
    df = cursor_db(home, cwd, U2, age_s=9000)
    assert pastsessions.scan(s, projects, home=home) == 3
    rows, total = registry.ended_sessions(s, project="alpha")
    assert total == 3 and [r.kind for r in rows] == ["claude", "codex", "cursor-agent"]
    c = rows[0]
    assert c.id == registry.mint_id("claude", U1) and c.ident == U1 and c.pid == 0 and c.last_pane == ""
    assert c.transcript == str(cf) and c.cwd == cwd and c.project == "alpha"
    assert c.ended_at == c.last_seen == c.first_seen and abs(c.ended_at - cf.stat().st_mtime) < 1e-3
    assert rows[1].ident == xf.stem and rows[1].transcript == str(xf)
    assert rows[2].ident == U2 and rows[2].transcript == str(df)
    assert pastsessions.scan(s, projects, home=home) == 0  # memo: nothing new
    pastsessions._seen.clear()
    assert pastsessions.scan(s, projects, home=home) == 0  # registry: known idents are skipped


def test_known_ident_is_not_duplicated(env):
    s, home, cwd, projects = env
    claude_file(home, cwd, U1)
    live = registry.assign(s, [_live(cwd, U1)], now=time.time())[0]
    assert pastsessions.scan(s, projects, home=home) == 0
    registry.assign(s, [], now=time.time())  # it ends: still one row, pm's own id
    rows, total = registry.ended_sessions(s)
    assert total == 1 and rows[0].id == live.id


def test_imported_session_seen_live_later_keeps_its_id(env):
    s, home, cwd, projects = env
    claude_file(home, cwd, U1)
    codex_file(home, cwd, U2)
    pastsessions.scan(s, projects, home=home)
    rows, _ = registry.ended_sessions(s)
    ids = {r.kind: r.id for r in rows}
    stem = next(r.ident for r in rows if r.kind == "codex")
    out = registry.assign(s, [_live(cwd, U1), _live(cwd, stem, pid=99, kind=AgentKind.CODEX)], now=time.time())
    assert [a.id for a in out] == [ids["claude"], ids["codex"]]
    rec = registry.get(s, ids["claude"])
    assert not rec.ended and rec.pid == 4242 and rec.last_pane == "%7"


def test_unresumable_or_unknown_project_paths_are_skipped(env):
    s, home, cwd, projects = env
    claude_file(home, "/somewhere/else", U1)
    assert pastsessions.scan(s, projects, home=home) == 0
    assert registry.import_past(s, [type("R", (), {"kind": "devin", "ident": "-bad", "path": "/x",
                                                   "cwd": cwd, "mtime": 1.0})()]) == 0
    assert registry.ended_sessions(s) == ([], 0)


def test_imported_record_resume_and_fork_ids(env):
    s, home, cwd, projects = env
    claude_file(home, cwd, U1)
    codex_file(home, cwd, U2)
    pastsessions.scan(s, projects, home=home)
    rows, _ = registry.ended_sessions(s)
    by = {r.kind: r for r in rows}
    assert resume.build_argv(s, by["claude"])[-2:] == ["--resume", U1]
    assert resume.build_argv(s, by["codex"])[-2:] == ["resume", U2]
    assert fork.session_id(by["claude"]) == U1 and fork.session_id(by["codex"]) == by["codex"].ident


def test_ended_page_and_project_history_show_imported_session(env, fake_tmux, tmp_path, monkeypatch):
    from helpers import make_client, make_settings

    from pm.infra import snapshot as services
    from pm.tmux.ctl import SEP
    from pm.web.routes.pages import router

    _, home, _, _ = env
    dev = tmp_path / "dev"
    (dev / "alpha").mkdir(parents=True, exist_ok=True)
    s = make_settings(fake_tmux, tmp_path)
    cwd = str(dev / "alpha")
    f = claude_file(home, cwd, U1)
    with f.open("a") as fh:
        fh.write(json.dumps({"type": "assistant", "uuid": "a1", "timestamp": "2026-10-01T10:00:00Z",
                             "message": {"role": "assistant", "content": [{"type": "text",
                                                                           "text": "past answer"}]}}) + "\n")
    os.utime(f, (time.time() - 3600, time.time() - 3600))
    line = SEP.join(["$1", "s", "1", "@1", "0", "w", "1", "%1", "4242", "zsh", cwd, "1", "t", "0"]) + "\n"
    monkeypatch.setenv("FAKE_TMUX_OUT", line)
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes: [])
    c = make_client(s, router, static=True, logged_in=True)
    services.build_and_publish(s)
    projects = services.published(s).projects
    assert pastsessions.scan(s, projects, home=home) == 1
    rid = registry.mint_id("claude", U1)
    t = c.get(f"/p/{next(p.name for p in projects if p.path == cwd)}").text
    assert f"/a/{rid}" in t
    r = c.get(f"/a/{rid}")
    assert r.status_code == 200 and "ended" in r.text and f'action="/a/{rid}/resume"' in r.text
    from pm.chat import log as chatlog
    from pm.web import chatctx

    standin = chatctx.ended_standin(s, services.published(s), rid)  # what the chat feed reads
    assert standin is not None and standin.pid == 0
    chatlog.sync(s, standin)
    assert any("past answer" in m.text for m in chatlog.messages(s, rid, limit=50))


def test_loop_runs_scan_off_the_event_loop_and_stops(env, monkeypatch):
    s, home, cwd, projects = env
    claude_file(home, cwd, U1)
    calls = []
    monkeypatch.setattr(pastsessions, "FIRST_DELAY_S", 0)
    real = pastsessions.scan
    monkeypatch.setattr(pastsessions, "scan", lambda st, pr: calls.append(len(pr)) or real(st, pr, home=home))

    class Snap:
        pass

    snap = Snap()
    snap.projects = tuple(projects)

    async def go():
        class App:
            class state:
                settings = s
        task = pastsessions.start(App, lambda st: snap)
        for _ in range(100):
            await asyncio.sleep(0.02)
            if calls:
                break
        await pastsessions.stop(App)
        return task

    asyncio.run(go())
    assert calls == [1] and registry.ended_sessions(s)[1] == 1


def test_interval_zero_is_off():
    assert pastsessions.interval_s(Settings(past_sessions_interval_s=0)) == 0
    assert pastsessions.interval_s(Settings(past_sessions_interval_s=5)) == pastsessions.MIN_INTERVAL_S


def test_db_error_is_not_memoised(env, monkeypatch):
    s, home, cwd, projects = env
    claude_file(home, cwd, U1)
    real = registry.import_past
    monkeypatch.setattr(registry, "import_past", lambda *a, **k: -1)
    assert pastsessions.scan(s, projects, home=home) == 0
    monkeypatch.setattr(registry, "import_past", real)
    assert pastsessions.scan(s, projects, home=home) == 1  # retried at once, not after RECHECK_S
