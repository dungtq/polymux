"""Project stack routes (web.routes.stackops + the control restart route) with a fake docker compose."""

import json
import subprocess

from helpers import ORIGIN, TOKEN, audit_text, make_client, make_settings, pane_line

from pm.core.models import Tier
from pm.infra import stacks
from pm.security import auth
from pm.security.csrf import Csrf

PS = [{"Service": "web", "Name": "alpha-web-1", "State": "running", "Health": "", "Image": "nginx",
       "Publishers": [{"TargetPort": 80, "PublishedPort": 8080, "Protocol": "tcp"}]}]


def _env(fake_tmux, tmp_path, monkeypatch, login=True, rc=0, **over):
    calls: list[list[str]] = []

    def fake_run(argv, *, timeout, cwd=None, text=False, **kw):
        calls.append(list(argv))
        out = {"ps": "\n".join(json.dumps(r) for r in PS), "logs": "hello " + TOKEN + "\n"}.get(argv[2], "")
        return subprocess.CompletedProcess(argv, rc if argv[2] not in ("ps", "logs") else 0, out,
                                           "boom " + TOKEN if rc else "")
    monkeypatch.setattr(stacks, "run_argv", fake_run)
    s = make_settings(fake_tmux, tmp_path, state="st", disable_poll=True, **over)
    alpha = tmp_path / "dev" / "alpha"
    alpha.mkdir(parents=True)
    (alpha / "compose.yaml").write_text("services: {}\n")
    monkeypatch.setenv("FAKE_TMUX_OUT", pane_line("$1", "work", "@1", "%12", str(alpha)))
    c = make_client(s, logged_in=login)
    tok = ""
    if login:
        sess = c.app.state.sessions.session_from_cookie(c.cookies.get(auth.COOKIE_NAME))
        tok = Csrf(s.state_dir, s.allowed_origins).token(sess.id)
    return c, tok, s, calls


def _post(c, tok, url, **data):
    return c.post(url, data={"csrf": tok, **data}, headers={"Origin": ORIGIN})


def test_auth_and_unknown_project(fake_tmux, tmp_path, monkeypatch):
    c, _tok, _s, _calls = _env(fake_tmux, tmp_path, monkeypatch, login=False)
    for u in ("/p/alpha/stack", "/p/alpha/stack/logs"):
        assert c.get(u, follow_redirects=False).status_code in (303, 401, 403)
    assert c.post("/p/alpha/stack/up").status_code in (401, 403)


def test_view_json(fake_tmux, tmp_path, monkeypatch):
    c, _tok, _s, _calls = _env(fake_tmux, tmp_path, monkeypatch)
    assert c.get("/p/nope/stack").status_code == 404
    r = c.get("/p/alpha/stack")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    v = r.json()
    assert v["project"] == "alpha" and v["compose_file"] == "compose.yaml" and (v["running"], v["total"]) == (1, 1)
    assert v["services"] == [{"name": "web", "container": "alpha-web-1", "state": "running", "health": "",
                              "ports": "8080->80/tcp", "image": "nginx"}]
    assert v["templates"] == []


def test_logs_masked_and_service_checked(fake_tmux, tmp_path, monkeypatch):
    c, _tok, _s, calls = _env(fake_tmux, tmp_path, monkeypatch)
    r = c.get("/p/alpha/stack/logs?service=web&tail=10")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert "hello" in r.text and TOKEN not in r.text
    assert calls[-1][-3:] == ["10", "--", "web"]
    assert c.get("/p/alpha/stack/logs?service=nope").status_code == 404
    assert c.get("/p/alpha/stack/logs?tail=x").status_code == 422


def test_up_down_guarded_and_audited(fake_tmux, tmp_path, monkeypatch):
    c, tok, s, calls = _env(fake_tmux, tmp_path, monkeypatch)
    assert c.post("/p/alpha/stack/up", data={"csrf": tok}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/p/alpha/stack/up", headers={"Origin": ORIGIN}).status_code == 403
    assert not [a for a in calls if a[2] == "up"]
    r = _post(c, tok, "/p/alpha/stack/up")
    assert r.status_code == 200 and "Stack up." in r.text and r.headers["hx-trigger"] == "stack-changed"
    r = _post(c, tok, "/p/alpha/stack/down")
    assert r.status_code == 200 and "volumes kept" in r.text
    assert [a for a in calls if a[2] in ("up", "down")] == [["docker", "compose", "up", "-d"],
                                                           ["docker", "compose", "down"]]
    log = audit_text(s)
    assert '"stack_up"' in log and '"stack_down"' in log
    assert _post(c, tok, "/p/nope/stack/up").status_code == 404
    assert '"unknown project"' in audit_text(s)


def test_down_needs_message_tier(fake_tmux, tmp_path, monkeypatch):
    c, tok, _s, calls = _env(fake_tmux, tmp_path, monkeypatch, tier_ceiling=Tier.SAFE)
    assert _post(c, tok, "/p/alpha/stack/down").status_code == 403
    assert _post(c, tok, "/p/alpha/stack/up").status_code == 200
    assert not [a for a in calls if a[2] == "down"]


def test_failure_is_502_and_audit_has_no_output(fake_tmux, tmp_path, monkeypatch):
    c, tok, s, _calls = _env(fake_tmux, tmp_path, monkeypatch, rc=1)
    r = _post(c, tok, "/p/alpha/stack/up")
    assert r.status_code == 502 and TOKEN not in r.text
    log = audit_text(s)
    assert '"stack_up"' in log and '"error"' in log and TOKEN not in log and "boom" not in log


def test_restart_is_the_control_route(fake_tmux, tmp_path, monkeypatch):
    """stackops has no restart of its own: /p/{n}/stack/restart stays control.stack_restart."""
    from pm.web.routes import stackops
    assert not [r for r in stackops.router.routes if r.path.endswith("/restart")]
    c, tok, s, calls = _env(fake_tmux, tmp_path, monkeypatch)
    r = _post(c, tok, "/p/alpha/stack/restart")
    assert r.status_code == 200 and "Stack restarted." in r.text and '"stack_restart"' in audit_text(s)
    assert calls[-1] == ["docker", "compose", "restart"]
