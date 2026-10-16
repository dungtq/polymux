"""New project dialog routes (GET /p/new/plan, POST /p/new/run NDJSON), the plugin hook ``new_project_steps``, and
the launch form partial GET /launch/form (launchctx view model, classic ``_launch_form.html``)."""

from __future__ import annotations

import json
import re
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import login
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.agents import launch as launch_mod
from pm.core.config import Settings
from pm.core.models import Tier
from pm.projects import newproject as npj
from pm.security import auth
from pm.security.audit import AuditError
from pm.security.csrf import Csrf
from pm.web import plugins
from pm.web.routes import newproject as routes

ORIGIN = "https://pm.example.test"


def _plugin(name, hook):
    return plugins.Plugin(name, SimpleNamespace(new_project_steps=hook))


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    dev = tmp_path / "dev"
    dev.mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    st = Settings(state_dir=tmp_path / "st", token_file=tmp_path / "tok", totp_file=tmp_path / "totp",
                  dev_root=dev, extra_projects=(), tier_ceiling=Tier.MESSAGE)
    monkeypatch.setattr(routes.services, "build_snapshot", lambda *a, **k: None)
    app = FastAPI()
    app.state.settings = st
    app.state.plugins = []
    auth.install(app, st)
    app.include_router(routes.router)
    s = app.state.sessions.create("1.2.3.4")
    c = TestClient(app, base_url=ORIGIN)
    c.cookies.set(auth.COOKIE_NAME, app.state.sessions.cookie_value(s))
    tok = Csrf(st.state_dir, st.allowed_origins).token(s.id)
    return SimpleNamespace(app=app, st=st, c=c, tok=tok, dev=dev)


def run(e, origin=ORIGIN, **data):
    return e.c.post("/p/new/run", data={"csrf": e.tok, **data}, headers={"Origin": origin})


def lines(r):
    return [json.loads(x) for x in r.text.splitlines()]


def audit(e):
    return [json.loads(x) for x in e.st.audit_file.read_text().splitlines()] if e.st.audit_file.exists() else []


def tier(e, t):
    e.app.state.settings = e.st = Settings(**{**e.st.__dict__, "tier_ceiling": t})


# ------------------------------------------------------------------ plan
def test_plan_lists_steps_and_templates(app_env):
    r = app_env.c.get("/p/new/plan", params={"name": "demo", "commit": "1"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    d = r.json()
    assert d["ok"] and d["error"] == "" and d["templates"] == [{"name": "empty", "description": ""}]
    assert [s["id"] for s in d["steps"]] == ["folder", "readme", "git", "remote", "commit"]
    assert d["steps"][3]["kind"] == "skip" and set(d["steps"][0]) == {"id", "text", "kind"}
    assert list(app_env.dev.iterdir()) == []  # planning changes nothing


@pytest.mark.parametrize(("params", "err"), [({"name": "a b"}, "name:"), ({"name": "x", "agent": "nope"}, "agent"),
                                             ({"name": "dup"}, "already exists")])
def test_plan_reports_refusals(app_env, params, err):
    (app_env.dev / "dup").mkdir()
    d = app_env.c.get("/p/new/plan", params=params).json()
    assert not d["ok"] and err in d["error"]


def test_plan_agent_needs_tier_2_and_bypass_confirm(app_env):
    d = app_env.c.get("/p/new/plan", params={"name": "x", "agent": "claude", "permission_mode": "bypassPermissions"}).json()
    assert not d["ok"] and d["error"] == "bypass mode not confirmed" and d["steps"][-1]["id"] == "agent"
    tier(app_env, Tier.SAFE)
    d = app_env.c.get("/p/new/plan", params={"name": "x", "agent": "claude"}).json()
    assert not d["ok"] and "tier 2" in d["error"]


def test_plan_needs_login(app_env):
    app_env.c.cookies.clear()
    assert app_env.c.get("/p/new/plan", params={"name": "x"}, follow_redirects=False).status_code in (303, 401)


# ------------------------------------------------------------------ run
def test_run_streams_and_audits_each_step(app_env):
    r = run(app_env, name="demo", template="empty")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    got = lines(r)
    assert [(x["id"], x["state"]) for x in got[:-1]] == [("folder", "done"), ("readme", "done"), ("git", "done"),
                                                         ("remote", "skipped")]
    assert got[-1] == {"end": True, "ok": True, "project": "demo", "url": "/p/demo",
                       "done": ["folder", "readme", "git", "remote"], "failed": "", "not_run": []}
    assert (app_env.dev / "demo" / ".git").is_dir()
    steps = [a for a in audit(app_env) if a["action"] == "project_create" and a["result"] != "attempt"]
    assert [(a["detail"]["step"], a["result"]) for a in steps] == [("folder", "ok"), ("readme", "ok"), ("git", "ok"),
                                                                   ("remote", "ok")]


def test_run_plugin_step_failure_stops(app_env):
    seen = []

    def hook(spec):
        def stack(settings, s, path):
            seen.append(s.options.get("stack"))
            seen.append(threading.current_thread().name.rsplit("_", 1)[0])  # plugin steps: the jobs pool
            raise RuntimeError("compose failed")
        return [npj.Step("stack", "Create stack " + spec.options.get("stack", ""), "stack", stack)]

    app_env.app.state.plugins = [_plugin("acme", hook)]
    got = lines(run(app_env, name="demo", git="0", stack="web", agent="claude"))
    assert [(x["id"], x["state"]) for x in got[:-1]] == [("folder", "done"), ("readme", "done"),
                                                         ("acme.stack", "failed"), ("agent", "not_run")]
    assert got[2]["text"] == "Create stack web: compose failed" and seen == ["web", "pm-jobs"]
    assert got[-1]["ok"] is False and got[-1]["failed"] == "acme.stack" and got[-1]["not_run"] == ["agent"]
    errs = [a for a in audit(app_env) if a["result"] == "error"]
    assert errs and errs[0]["detail"]["step"] == "acme.stack"


def test_run_launches_first_agent(app_env, monkeypatch):
    calls = []

    def fake_launch(settings, launch, project, split_pane=None):
        calls.append((launch, project))
        assert threading.current_thread().name.startswith("pm-tmux")  # launched like every other agent
        return launch_mod.LaunchResult("$1", "@9", "agent-claude", project.path, pane_id="%9")

    monkeypatch.setattr(launch_mod, "launch_agent", fake_launch)
    got = lines(run(app_env, name="demo", git="0", agent="claude", model="opus", permission_mode="plan",
                    prompt="hello"))
    assert got[-2] == {"id": "agent", "ok": True, "text": "Launched claude in window agent-claude (@9)", "state": "done"}
    (launch, project), = calls
    assert (launch.project, launch.kind.value, launch.model, launch.permission_mode, launch.prompt) == (
        "demo", "claude", "opus", "plan", "hello")
    assert project.name == "demo" and project.path == str(app_env.dev.resolve() / "demo")


def test_run_refusals_before_any_step(app_env):
    (app_env.dev / "dup").mkdir()
    assert run(app_env, name="a/b").status_code == 400
    assert run(app_env, name="dup").status_code == 409
    assert run(app_env, name="x", agent="claude", permission_mode="bypassPermissions").status_code == 400
    tier(app_env, Tier.SAFE)
    assert run(app_env, name="x", agent="claude").status_code == 403
    assert run(app_env, name="x", origin="https://evil.example").status_code == 403
    assert sorted(p.name for p in app_env.dev.iterdir()) == ["dup"]
    assert [a["result"] for a in audit(app_env) if a["result"] != "attempt"] == ["denied"] * 5


def test_run_without_csrf_refused(app_env):
    r = app_env.c.post("/p/new/run", data={"name": "x"}, headers={"Origin": ORIGIN})
    assert r.status_code == 403 and list(app_env.dev.iterdir()) == []


def test_run_stops_when_audit_fails(app_env, monkeypatch):
    real = routes.audit_result

    def flaky(request, action, target, result, detail=None):
        if (detail or {}).get("step") == "readme":
            raise AuditError("disk full")
        return real(request, action, target, result, detail)

    monkeypatch.setattr(routes, "audit_result", flaky)
    got = lines(run(app_env, name="demo"))
    assert [x["id"] for x in got] == ["folder", "readme"] and got[-1]["text"] == "audit unavailable; stopped"
    assert not (app_env.dev / "demo" / ".git").exists()  # git init never ran


# ------------------------------------------------------------------ plugin hook
def test_hook_prefixes_ids_and_drops_bad_results(caplog):
    ok = npj.Step("vm", "Create VM", "vm", lambda *a: "")
    good = _plugin("acme", lambda spec: [ok])
    bad = [_plugin("b1", lambda spec: "nope"), _plugin("b2", lambda spec: [npj.Step("Bad id", "x", "k", lambda *a: "")]),
           _plugin("b3", lambda spec: [npj.Step("x", "x", "k")]), _plugin("b4", lambda spec: [ok, ok]),
           _plugin("b5", lambda spec: 1 / 0), _plugin("b6", lambda spec: None)]
    got = plugins.new_project_steps([*bad, good], npj.Spec(name="x"))
    assert [(s.id, s.text, s.kind) for s in got] == [("acme.vm", "Create VM", "vm")]
    assert sum("new_project_steps() returned the wrong type" in r.message for r in caplog.records) == 4


def test_loader_refuses_non_callable_hook():
    class EP:
        name = "acme"

        def load(self):
            return SimpleNamespace(name="acme", new_project_steps="not callable")

    assert plugins.load(Settings(), [EP()]) == []


# ------------------------------------------------------------------ launch form partial
def test_launch_form_partial(env):  # noqa: F811
    s, c = env
    login(s, c)
    r = c.get("/launch/form", params={"project": "alpha"})
    assert r.status_code == 200
    t = r.text
    assert "<html" not in t and 'data-launch-form' in t and 'action="/p/alpha/launch"' in t
    assert 'data-project-pick' in t and 'name="kind"' in t and 'name="permission_mode"' in t
    assert "already running here" in t and 'href="/a/claude-12"' in t
    data = json.loads(re.search(r'data-json="([^"]*)"', t).group(1).replace("&#34;", '"').replace("&amp;", "&"))
    assert {"remembered", "templates", "worktree", "slash", "modes", "models"} <= set(data)
    assert "prompt" not in data["remembered"]
    t0 = c.get("/launch/form", params={"project": "alpha", "todo": "0"}).text
    assert "Prompt preview" in t0 and "open &lt;b&gt;thing&lt;/b&gt;" in t0  # escaped TODO text
    assert c.get("/launch/form", params={"project": "nope"}).status_code == 200  # falls back to a known project


def test_launch_form_needs_login(env):  # noqa: F811
    _s, c = env
    assert c.get("/launch/form", follow_redirects=False).status_code in (303, 401)


@pytest.mark.anyio
async def test_form_model_keys(env):  # noqa: F811
    from pm.infra import snapshot as services
    from pm.web import launchctx

    s, _c = env
    snap = services.build_snapshot(s, force=True)
    p = snap.project("alpha")
    m = await launchctx.form_model(s, snap, p, todo=0)
    assert set(m) == {"project", "base", "projects", "running", "kinds", "launch_data", "bypass_allowed", "todo",
                      "todo_selected"}
    assert m["base"] == "/p/alpha" and m["projects"] == ["alpha"] and [a.id for a in m["running"]] == ["claude-12"]
    assert "claude" in m["kinds"] and "other" not in m["kinds"] and m["bypass_allowed"] is True
    assert m["todo"] == [{"index": 0, "text": "open <b>thing</b>", "prompt": m["todo"][0]["prompt"], "busy": ""}]
    assert m["todo_selected"] == 0 and m["launch_data"]["remembered"]["prompt"] == m["todo"][0]["prompt"]
    modes = m["launch_data"]["modes"]["claude"]
    assert modes["efforts"] and all(x["help"] for x in modes["modes"])
    assert (await launchctx.form_model(s, snap, p, todo=5))["todo_selected"] is None
