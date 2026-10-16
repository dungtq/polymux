"""New project (create dir under the projects root) and tmux Close / Close all on the project page."""

from __future__ import annotations

import subprocess
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pm.core.config import Settings
from pm.core.models import Tier, TmuxPane, TmuxSession, TmuxWindow
from pm.projects import projects
from pm.security import auth
from pm.security.csrf import Csrf
from pm.tmux import ctl as tmuxctl
from pm.tmux import kill as tmuxkill
from pm.web.routes import agents as routes_agents
from pm.web.routes import newproject as routes_newproject

ORIGIN = "https://pm.example.test"


def sess(i, name, attached=0, protected=False):
    return TmuxSession(
        id=f"${i}", name=name, protected=protected, attached=attached,
        windows=(TmuxWindow(id=f"@{i}", session_id=f"${i}", index=0, name="w",
                            panes=(TmuxPane(id=f"%{i}", window_id=f"@{i}", session_id=f"${i}",
                                            pid=1, command="x", cwd="/"),)),))


@pytest.fixture
def env(tmp_path, monkeypatch):
    dev = tmp_path / "dev"
    dev.mkdir()
    st = Settings(state_dir=tmp_path / "st", token_file=tmp_path / "tok", totp_file=tmp_path / "totp",
                  dev_root=dev, extra_projects=(), tier_ceiling=Tier.MESSAGE,
                  unprotected_sessions=("free*",))
    calls: list = []
    sessions = [sess(1, "free1"), sess(2, "free2", attached=1), sess(3, "prot", protected=True),
                sess(4, "other")]
    monkeypatch.setattr(tmuxctl, "run", lambda s, a, timeout=0: calls.append(a) or "")
    monkeypatch.setattr(tmuxctl, "list_sessions", lambda s: tuple(sessions))
    monkeypatch.delenv("TMUX_PANE", raising=False)
    proj = SimpleNamespace(name="demo", sessions=("$1", "$2", "$3"), pane_ids=("%1", "%2", "%3"))
    snap = SimpleNamespace(project=lambda n: proj if n == "demo" else None,
                           session=lambda i: next((s for s in sessions if s.id == i), None))

    async def fake_snap(request):
        return snap

    monkeypatch.setattr("pm.web.agentctl.snapshot", fake_snap)
    monkeypatch.setattr(routes_newproject.services, "build_snapshot", lambda *a, **k: None)
    app = FastAPI()
    app.state.settings = st
    auth.install(app, st)
    app.include_router(routes_newproject.router)
    app.include_router(routes_agents.router)
    s = app.state.sessions.create("1.2.3.4")
    c = TestClient(app, base_url=ORIGIN)
    c.cookies.set(auth.COOKIE_NAME, app.state.sessions.cookie_value(s))
    tok = Csrf(st.state_dir, st.allowed_origins).token(s.id)
    return SimpleNamespace(st=st, c=c, tok=tok, calls=calls, dev=dev)


def post(e, url, origin=ORIGIN, **data):
    return e.c.post(url, data={"csrf": e.tok, **data}, headers={"Origin": origin} if origin else {},
                    follow_redirects=False)


def audit(e):
    return e.st.audit_file.read_text() if e.st.audit_file.exists() else ""


# ---------------------------------------------------------------- new project
def test_create_plain(env):
    r = post(env, "/p/new", name="alpha")
    assert r.status_code == 303 and r.headers["location"] == "/p/alpha"
    assert (env.dev / "alpha").is_dir() and not (env.dev / "alpha" / ".git").exists()
    assert "project_create" in audit(env) and '"result":"ok"' in audit(env)


def test_create_git_and_readme(env):
    r = post(env, "/p/new", name="beta.1", git_init="1", readme="1")
    assert r.status_code == 303
    assert (env.dev / "beta.1" / ".git").is_dir()
    assert (env.dev / "beta.1" / "README.md").read_text() == "# beta.1\n"
    assert post(env, "/p/new", name="gamma").status_code == 303


def test_htmx_redirect_header(env):
    r = env.c.post("/p/new", data={"csrf": env.tok, "name": "hx"},
                   headers={"Origin": ORIGIN, "HX-Request": "true"})
    assert r.status_code == 200 and r.headers["HX-Redirect"] == "/p/hx"


@pytest.mark.parametrize("bad", ["", ".hidden", "a/b", "../x", "a b", "x;y", "-", "a" * 65, "node_modules", "é"])
def test_bad_names(env, bad):
    if bad == "-":
        bad = "a\x00b"
    r = post(env, "/p/new", name=bad)
    assert r.status_code == 400
    assert list(env.dev.iterdir()) == []
    assert '"result":"denied"' in audit(env)


def test_existing_refused(env):
    (env.dev / "dup").mkdir()
    assert post(env, "/p/new", name="dup").status_code == 409
    (env.dev / "afile").write_text("x")
    assert post(env, "/p/new", name="afile").status_code == 409
    (env.dev / "lnk").symlink_to(env.dev / "dup")
    assert post(env, "/p/new", name="lnk").status_code == 409


def test_guards(env):
    assert post(env, "/p/new", origin="https://evil.example", name="x").status_code == 403
    assert env.c.post("/p/new", data={"csrf": "bad", "name": "x"}, headers={"Origin": ORIGIN}).status_code == 403
    assert list(env.dev.iterdir()) == []


def test_create_project_helper_confined(env):
    with pytest.raises(projects.NewProjectError):
        projects.create_project(env.st, "../escape")
    assert not (env.dev.parent / "escape").exists()


# ---------------------------------------------------------------- tmux close
def test_close_session_via_existing_route(env):
    r = post(env, "/tmux/session/s1/kill", confirm="free1")
    assert r.status_code == 200 and ["kill-session", "-t", "$1"] in env.calls
    env.calls.clear()
    assert post(env, "/tmux/session/s3/kill", confirm="prot").status_code == 403
    assert post(env, "/tmux/session/s1/kill", confirm="nope").status_code == 403
    assert not env.calls


def test_close_all_skips_protected_and_attached(env):
    r = post(env, "/p/demo/tmux/close-all")
    assert r.status_code == 200
    kills = [a for a in env.calls if a[0] == "kill-session"]
    assert kills == [["kill-session", "-t", "$1"]]  # $2 attached, $3 protected, $4 not in project
    assert "free2" in r.text and "prot" in r.text
    log = audit(env)
    assert "tmux_project_close_all" in log and '"closed":1' in log


def test_close_all_unknown_project_and_guards(env):
    assert post(env, "/p/nope/tmux/close-all").status_code == 404
    assert post(env, "/p/demo/tmux/close-all", origin="https://evil.example").status_code == 403
    st = replace(env.st, tier_ceiling=Tier.SAFE)
    env.c.app.state.settings = st
    assert post(env, "/p/demo/tmux/close-all").status_code == 403
    assert not [a for a in env.calls if a[0] == "kill-session"]


def test_close_sessions_refuses_own_session(env, monkeypatch):
    monkeypatch.setenv("TMUX_PANE", "%1")
    res = tmuxkill.close_sessions(env.st, ["$1", "$99"])
    assert res.closed == () and res.skipped[0][0] == "free1"


def test_templates_render_actions():
    from pm.web.render import env as _env
    s = [sess(1, "free1"), sess(2, "prot", protected=True)]
    html = _env.get_template("_project_live.html").render(
        agents=[], sessions=s, project=SimpleNamespace(name="demo"), all_agents=[], csrf="T",
        now=0, ended=[], ended_total=0, ended_ntfy={}, agent_ntfy={}, ended_models={})
    assert html.count("/tmux/session/s1/kill") == 2  # hx-post + action
    assert "/tmux/session/s2/kill" not in html
    assert "/p/demo/tmux/close-all" in html and "hx-confirm" in html
    assert 'name="confirm" value="free1"' in html
