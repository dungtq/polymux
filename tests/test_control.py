import subprocess
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pm.core.config import Settings
from pm.core.models import Tier, TmuxPane, TmuxSession, TmuxWindow
from pm.infra import snapshot as services
from pm.notify.events import Hub
from pm.security import auth
from pm.security.csrf import Csrf
from pm.security.unlock import Unlocker
from pm.tmux import ctl as tmuxctl
from pm.web.routes import control as routes_control

ORIGIN = "https://pm.example.test"


def mk_sessions():
    def s(i, name, wins):
        return TmuxSession(id=f"${i}", name=name, protected=tmuxctl.is_protected(
            name, Settings(unprotected_sessions=("free*",))),
            windows=tuple(TmuxWindow(id=f"@{w}", session_id=f"${i}", index=n, name=wn,
                                     panes=(TmuxPane(id=f"%{w}", window_id=f"@{w}",
                                                     session_id=f"${i}", pid=1, command="x",
                                                     cwd="/"),))
                          for n, (w, wn) in enumerate(wins)))
    return (s(1, "prot", [(1, "a")]), s(2, "free1", [(2, "main")]), s(3, "pm-self", [(3, "x")]))


@pytest.fixture
def env(tmp_path, monkeypatch):
    dev = tmp_path / "dev"
    (dev / "proj").mkdir(parents=True)
    st = Settings(state_dir=tmp_path / "st", token_file=tmp_path / "tok",
                  totp_file=tmp_path / "totp", dev_root=dev, extra_projects=(),
                  unprotected_sessions=("free*",),
                  layouts={"proj": ["editor", {"name": "logs", "cmd": "tail -f x"}]})
    calls = []
    sessions = list(mk_sessions())

    def fake_run(settings, args, timeout=0):
        calls.append(args)
        return "$9\n" if args[0] in ("new-session", "new-window") else ""

    monkeypatch.setattr(tmuxctl, "run", fake_run)
    monkeypatch.setattr(tmuxctl, "list_sessions", lambda s: tuple(sessions))
    monkeypatch.setattr(services, "build_snapshot", lambda *a, **k: None)  # tmux mutations force one (tmux_changed)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    return st, calls, sessions, monkeypatch


def client(st, unlocked=True, ceiling=Tier.SAFE, require_unlock=False):
    from dataclasses import replace
    st = replace(st, tier_ceiling=ceiling, require_unlock=require_unlock)
    app = FastAPI()
    app.state.settings = st
    auth.install(app, st)
    app.state.unlocker = Unlocker(st.totp_file, st.unlock_ttl_s)
    app.include_router(routes_control.router)
    sess = app.state.sessions.create("1.2.3.4")
    if unlocked:
        sess.unlocked_until = time.time() + 300
    c = TestClient(app, base_url="https://pm.example.test")
    c.cookies.set(auth.COOKIE_NAME, app.state.sessions.cookie_value(sess))
    tok = Csrf(st.state_dir, st.allowed_origins).token(sess.id)
    return app, c, sess, tok, st


def post(c, tok, url, origin=ORIGIN, **data):
    h = {"Origin": origin} if origin else {}
    return c.post(url, data={"csrf": tok, **data}, headers=h)


def audit_lines(st):
    return st.audit_file.read_text().splitlines() if st.audit_file.exists() else []


def test_guard_failures(env):
    st, calls, *_ = env
    app, c, sess, tok, st = client(st, unlocked=False, require_unlock=True)
    assert post(c, tok, "/tmux/session/new", name="x").status_code == 403  # locked
    sess.unlocked_until = time.time() + 99
    assert post(c, "bad", "/tmux/session/new", name="x").status_code == 403
    assert post(c, tok, "/tmux/session/new", origin="https://evil.example", name="x").status_code == 403
    assert not calls
    app, c, sess, tok, st = client(st, ceiling=Tier.VIEW)
    assert post(c, tok, "/tmux/session/new", name="x").status_code == 403
    assert not calls


def test_session_new_and_window_new(env):
    st, calls, *_ = env
    app, c, sess, tok, st = client(st)
    r = post(c, tok, "/tmux/session/new", name="work")
    assert r.status_code == 200 and "work" in r.text
    assert post(c, tok, "/tmux/session/new", name="bad name;").status_code == 400
    assert post(c, tok, "/tmux/window/new", session="s2", name="w1").status_code == 200
    assert post(c, tok, "/tmux/window/new", session="x; rm", name="w1").status_code == 400
    assert any(a[0] == "new-window" and "$2:" in a for a in calls)
    log = "\n".join(audit_lines(st))
    assert "tmux_session_new" in log and '"result":"ok"' in log


def test_rename(env):
    st, calls, sessions, mp = env
    app, c, sess, tok, st = client(st)
    assert post(c, tok, "/tmux/session/s1/rename", name="n").status_code == 403  # protected
    assert post(c, tok, "/tmux/session/s3/rename", name="n").status_code == 403  # pm*
    assert post(c, tok, "/tmux/window/w1/rename", name="n").status_code == 403
    assert post(c, tok, "/tmux/session/s99/rename", name="n").status_code == 404
    assert not any(a[0].startswith("rename") for a in calls)
    assert post(c, tok, "/tmux/session/s2/rename", name="renamed").status_code == 200
    assert post(c, tok, "/tmux/window/w2/rename", name="wn").status_code == 200
    assert [a[0] for a in calls] == ["rename-session", "rename-window"]
    # caller's own session is protected even if allow-listed
    mp.setenv("TMUX_PANE", "%2")
    assert post(c, tok, "/tmux/session/s2/rename", name="again").status_code == 403
    assert '"result":"denied"' in "\n".join(audit_lines(st))


def test_rename_fails_closed_when_tmux_down(env):
    st, calls, sessions, mp = env

    def boom(s):
        raise tmuxctl.TmuxError("x")
    mp.setattr(tmuxctl, "list_sessions", boom)
    app, c, sess, tok, st = client(st)
    assert post(c, tok, "/tmux/session/s2/rename", name="n").status_code == 502
    assert not any(a[0].startswith("rename") for a in calls)


def test_audit_failure_blocks_action(env):
    st, calls, *_ = env
    app, c, sess, tok, st = client(st)
    st.state_dir.mkdir(parents=True, exist_ok=True)
    st.audit_file.mkdir()  # open() on a directory -> AuditError
    r = post(c, tok, "/tmux/session/new", name="work")
    assert r.status_code == 503 and not calls


def test_unknown_project_404(env):
    st, *_ = env
    app, c, sess, tok, st = client(st)
    for u in ("git/fetch", "git/pull", "git/push", "stack/restart", "tmux/ensure"):
        assert post(c, tok, f"/p/nope/{u}").status_code == 404


def git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={"PATH": "/usr/bin:/bin", "HOME": str(cwd), "GIT_AUTHOR_NAME": "t",
                        "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


def test_git_flow_and_push_gitdev_only(env, tmp_path):
    st, *_ = env
    repo = st.dev_root / "proj"
    git("init", "-b", "main", cwd=repo)
    (repo / "a").write_text("1")
    git("add", "a", cwd=repo)
    git("commit", "-m", "m", cwd=repo)
    other = tmp_path / "other.git"
    other.mkdir()
    git("init", "--bare", cwd=other)
    git("remote", "add", "origin", str(other), cwd=repo)
    app, c, sess, tok, st = client(st)
    r = post(c, tok, "/p/proj/git/push")  # no gitdev remote -> refused
    assert r.status_code == 403
    assert not list(other.glob("refs/heads/*"))
    bare = tmp_path / "bare.git"
    bare.mkdir()
    git("init", "--bare", "-b", "main", cwd=bare)
    git("remote", "add", "gitdev", str(bare), cwd=repo)
    assert post(c, tok, "/p/proj/git/push").status_code == 200
    assert (bare / "refs/heads/main").exists()
    assert post(c, tok, "/p/proj/git/fetch").status_code == 200
    assert post(c, tok, "/p/proj/git/pull").status_code == 200
    log = "\n".join(audit_lines(st))
    assert "git_push" in log and "git_fetch" in log and "git_pull" in log


def test_stack_restart(env):
    st, calls, sessions, mp = env
    app, c, sess, tok, st = client(st)
    seen = []
    mp.setattr(routes_control.stacks, "restart", lambda p: seen.append(p) or True)
    assert post(c, tok, "/p/proj/stack/restart").status_code == 200 and seen
    mp.setattr(routes_control.stacks, "restart", lambda p: False)
    assert post(c, tok, "/p/proj/stack/restart").status_code == 502


def test_ensure_layout(env):
    st, calls, sessions, mp = env
    app, c, sess, tok, st = client(st)
    # no session named proj -> create session then windows (list_sessions is static fake)
    assert post(c, tok, "/p/proj/tmux/ensure").status_code == 200
    kinds = [a[0] for a in calls]
    assert kinds[0] == "new-session" and "kill-window" not in kinds
    assert not any("tail" in " ".join(a) for a in calls)  # commands not run
    # existing session with one window present -> only the missing one is created
    calls.clear()
    sessions.append(TmuxSession(id="$7", name="proj", windows=(
        TmuxWindow(id="@7", session_id="$7", index=0, name="editor"),)))
    assert post(c, tok, "/p/proj/tmux/ensure").status_code == 200
    assert [a[0] for a in calls] == ["new-window"] and "logs" in calls[0]
    st.layouts["bad"] = ["x y"]
    assert post(c, tok, "/p/other/tmux/ensure").status_code == 404


def test_unlock_disabled_by_default(env):
    st, *_ = env
    app, c, sess, tok, st = client(st, unlocked=False)
    assert c.get("/unlock").status_code == 404
    assert post(c, tok, "/unlock/provision").status_code == 404
    assert post(c, tok, "/unlock", code="123456").status_code == 404
    assert not st.totp_file.exists()
    assert post(c, tok, "/tmux/session/new", name="x").status_code == 200  # no unlock needed


def test_unlock_flow(env):
    st, *_ = env
    app, c, sess, tok, st = client(st, unlocked=False, require_unlock=True)
    assert 'name="csrf"' in c.get("/unlock").text
    assert post(c, tok, "/unlock/provision", origin="https://evil.example").status_code == 403
    r = post(c, tok, "/unlock/provision")
    assert r.status_code == 200 and "otpauth://" in r.text
    assert "otpauth" not in "\n".join(audit_lines(st))
    assert post(c, tok, "/unlock/provision").status_code == 409
    assert post(c, "bad", "/unlock", code="123456").status_code == 403
    assert post(c, tok, "/unlock", code="000000").status_code == 403
    assert sess.unlocked_until <= time.time()
    import pyotp
    code = pyotp.TOTP(st.totp_file.read_text().strip()).now()
    assert post(c, tok, "/unlock", code=code).status_code == 200
    assert sess.unlocked_until > time.time()


def test_session_new_cwd_must_be_a_directory_under_the_roots(env, tmp_path):
    st, calls, *_ = env
    app, c, sess, tok, st = client(st)
    proj = tmp_path / "dev" / "proj"
    (proj / "sub").mkdir()
    (proj / "file").write_text("x")
    (proj / "esc").symlink_to(tmp_path)  # a link out of the root
    (proj / "inside").symlink_to(proj / "sub")
    ok = {"": None, str(proj): str(proj), str(proj / "sub") + "/": str(proj / "sub"),
          str(proj / "inside"): str(proj / "sub"), str(tmp_path / "dev"): str(tmp_path / "dev")}
    for raw, want in ok.items():
        calls.clear()
        r = post(c, tok, "/tmux/session/new", name="work", cwd=raw)
        assert r.status_code == 200, raw
        (args,) = [a for a in calls if a[0] == "new-session"]
        assert (args[args.index("-c") + 1] if "-c" in args else None) == want, raw
    bad = {"relative/path": 400, str(proj / "missing"): 403, str(proj / "file"): 403, "/etc": 403,
           str(proj / "esc"): 403, str(proj / ".." / ".." / "st"): 403, str(tmp_path / "devx"): 403,
           str(proj) + "\0": 400, "/" + "a" * 5000: 400}
    for raw, status in bad.items():
        calls.clear()
        assert post(c, tok, "/tmux/session/new", name="work", cwd=raw).status_code == status, raw
        assert not calls, raw
    log = "\n".join(audit_lines(st))
    assert f'"cwd":"{proj / "sub"}"' in log and '"result":"denied"' in log


def test_tmux_mutations_refresh_the_snapshot_and_publish(env):
    """New / rename: a forced snapshot rebuild and an ``inbox`` event {action}, so lists update at once."""
    st, calls, sessions, mp = env
    builds = []
    mp.setattr(services, "build_snapshot", lambda settings, **k: builds.append(k))
    app, c, sess, tok, st = client(st)
    app.state.hub = hub = Hub()
    assert post(c, tok, "/tmux/session/new", name="work").status_code == 200
    assert post(c, tok, "/tmux/window/new", session="s2", name="w1").status_code == 200
    assert post(c, tok, "/tmux/session/s2/rename", name="renamed").status_code == 200
    assert post(c, tok, "/tmux/window/w2/rename", name="wn").status_code == 200
    assert post(c, tok, "/tmux/session/new", name="bad name;").status_code == 400  # refused: no refresh
    assert builds == [{"force": True}] * 4
    assert [(e.type, e.data) for e in hub.replay(0)] == [
        ("inbox", {"action": a}) for a in ("tmux_session_new", "tmux_window_new", "tmux_session_rename",
                                            "tmux_window_rename")]
