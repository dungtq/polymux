import json
import os
import subprocess
import time
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jinja2 import ChoiceLoader, DictLoader
from starlette.websockets import WebSocketDisconnect

from pm.security import auth
from pm.security.audit import AuditError
from pm.security.csrf import Csrf
from pm.tmux import ctl as tmuxctl
from pm.web import render
from pm.web.routes import term as routes_term

ORIGIN = "https://pm.example.test"
STUB = ("sid={{ sid }} name={{ session_name }} csrf={{ csrf }} t={{ ticket_path }} "
        "w={{ ws_path }} i={{ idle_s }} m={{ max_s }} back={{ back_url }} bl={{ back_label }} "
        "win={{ window }}")


@pytest.fixture(autouse=True)
def stub_template(monkeypatch):
    env = render.env
    monkeypatch.setattr(env, "loader", ChoiceLoader([DictLoader({"term.html": STUB}), env.loader]))


def tmux(settings, *args):
    subprocess.run(["tmux", "-L", settings.tmux_socket, *args], check=True, timeout=10,
                   env={**os.environ, "TMUX": ""}, capture_output=True)


def sid_of(settings, name):
    s = next(s for s in tmuxctl.list_sessions(settings) if s.name == name)
    return "s" + s.id[1:]


class Env:
    pass


def make_env(tmp_path, private_tmux, **over):
    st = replace(private_tmux, state_dir=tmp_path / "st", token_file=tmp_path / "tok",
                 totp_file=tmp_path / "totp", terminal_enabled=True, **over)
    tmux(st, "new-session", "-d", "-s", "demo", "-x", "100", "-y", "30", "echo HELLO; cat")
    app = FastAPI()
    app.state.settings = st
    auth.install(app, st)
    app.include_router(routes_term.router)
    sess = app.state.sessions.create("1.2.3.4")  # NOT unlocked: no step-up expected
    c = TestClient(app, base_url=ORIGIN)
    c.cookies.set(auth.COOKIE_NAME, app.state.sessions.cookie_value(sess))
    e = Env()
    e.st, e.app, e.c, e.sess = st, app, c, sess
    e.csrf = Csrf(st.state_dir, st.allowed_origins).token(sess.id)
    e.sid = sid_of(st, "demo")
    return e


@pytest.fixture
def env(tmp_path, private_tmux):
    return make_env(tmp_path, private_tmux)


def ticket(e, sid=None, origin=ORIGIN):
    r = e.c.post("/term/ticket", data={"csrf": e.csrf, "sid": sid or e.sid},
                 headers={"Origin": origin})
    return r


def mint(e, sid=None):
    r = ticket(e, sid)
    assert r.status_code == 200, r.text
    return r.json()["ticket"]


def audit(e):
    p = e.st.audit_file
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def wait_close_audit(e, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if any(a["action"] == "terminal.close" for a in audit(e)):
            return
        time.sleep(0.05)
    raise AssertionError("no terminal.close audit line")


def client_close(ws):
    """Deliver a real disconnect and let the server finish (TestClient would cancel it)."""
    ws.send({"type": "websocket.disconnect", "code": 1000})


def init_msg(ws, cols=100, rows=30):
    ws.send_text(json.dumps({"type": "init", "cols": cols, "rows": rows}))


def read_until(ws, needle: bytes, timeout=8.0):
    buf, end = b"", time.time() + timeout
    while time.time() < end:
        m = ws.receive()
        if m.get("bytes"):
            buf += m["bytes"]
            if needle in buf:
                return buf
    raise AssertionError("needle not seen")


def connect(e, tok, origin=ORIGIN):
    return e.c.websocket_connect(f"/term/ws?ticket={tok}", headers={"Origin": origin})


def close_code(ws, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            m = ws.receive()
        except WebSocketDisconnect as e:
            return e.code
        if m["type"] == "websocket.close":
            return m["code"]
    raise AssertionError("no close")


def wait_attach_gone(st, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if not attach_children(st):
            return True
        time.sleep(0.05)
    return False


def view_sessions(st):
    out = subprocess.run(["tmux", "-L", st.tmux_socket, "list-sessions", "-F", "#{session_name}"],
                         capture_output=True, text=True, env={**os.environ, "TMUX": ""}).stdout
    return [n for n in out.split() if n.startswith("pm-view-")]


def recv_window(ws, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        m = ws.receive()
        if m.get("text") and '"window"' in m["text"]:
            return json.loads(m["text"])
    raise AssertionError("no window message")


def attach_children(st):
    out = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            argv = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
        except OSError:
            continue
        if b"attach-session" in argv and st.tmux_socket.encode() in argv:
            out.append(int(pid))
    return out


def test_page_and_csp(env):
    r = env.c.get(f"/term/{env.sid}")
    assert r.status_code == 200
    assert "name=demo" in r.text and "t=/term/ticket" in r.text and "w=/term/ws" in r.text
    assert "i=300" in r.text and "m=1800" in r.text
    csp = r.headers["content-security-policy"]
    assert "connect-src 'self' wss://pm.example.test" in csp


def test_disabled_404(tmp_path, private_tmux):
    e = make_env(tmp_path, private_tmux)
    e.app.state.settings = replace(e.st, terminal_enabled=False)
    assert e.c.get(f"/term/{e.sid}").status_code == 404
    assert ticket(e).status_code == 404
    with pytest.raises(WebSocketDisconnect):
        with connect(e, "x"):
            pass


def test_page_unknown_and_protected(env):
    assert env.c.get("/term/s9999").status_code == 404
    assert env.c.get("/term/bogus").status_code == 404
    base = sid_of(env.st, "pmtest-base")  # matches pm*
    assert env.c.get(f"/term/{base}").status_code == 404
    assert ticket(env, base).status_code == 404


def test_page_needs_login(env):
    env.c.cookies.clear()
    r = env.c.get(f"/term/{env.sid}", follow_redirects=False)
    assert r.status_code == 303


def test_ticket_guards(env):
    assert ticket(env, origin="https://evil.example").status_code == 403
    r = env.c.post("/term/ticket", data={"csrf": "bad", "sid": env.sid},
                   headers={"Origin": ORIGIN})
    assert r.status_code == 403
    env.c.cookies.clear()
    assert ticket(env).status_code == 401


def test_ticket_ok_not_logged_and_unlock_not_needed(env):
    r = ticket(env)
    assert r.status_code == 200 and r.json()["expires_in"] == 30
    tok = r.json()["ticket"]
    assert tok not in env.st.audit_file.read_text()
    assert any(a["action"] == "terminal.ticket" and a["result"] == "ok" for a in audit(env))


def test_require_unlock_setting_enforced(tmp_path, private_tmux):
    e = make_env(tmp_path, private_tmux, require_unlock=True)
    assert ticket(e).status_code == 403  # locked
    e.sess.unlocked_until = time.time() + 60
    assert ticket(e).status_code == 200


def test_ws_origin_rejected_before_accept(env):
    tok = mint(env)
    with pytest.raises(WebSocketDisconnect) as ei:
        with connect(env, tok, origin="https://evil.example"):
            pass
    assert ei.value.code == 4403
    assert not any(a["action"] == "terminal.open" for a in audit(env))
    # the ticket was not consumed by the origin rejection
    with connect(env, tok) as ws:
        init_msg(ws)
        read_until(ws, b"HELLO")


def test_ws_missing_origin_rejected(env):
    tok = mint(env)
    with pytest.raises(WebSocketDisconnect) as ei:
        with env.c.websocket_connect(f"/term/ws?ticket={tok}"):  # no Origin header
            pass
    assert ei.value.code == 4403
    with pytest.raises(WebSocketDisconnect) as ei:
        with env.c.websocket_connect(f"/term/ws?ticket={tok}",
                                     headers={"Sec-Fetch-Site": "same-origin"}):
            pass
    assert ei.value.code == 4403  # no sec-fetch-site fallback on the WebSocket
    assert not any(a["action"] == "terminal.open" for a in audit(env))


@pytest.mark.parametrize("how", ["missing", "bogus", "reused", "expired", "wrong_session", "no_cookie"])
def test_ws_ticket_rejections(env, how):
    tok = mint(env)
    if how == "missing":
        tok = ""
    elif how == "bogus":
        tok = "nope"
    elif how == "reused":
        with connect(env, tok) as ws:
            init_msg(ws)
            read_until(ws, b"HELLO")
        assert wait_attach_gone(env.st)
    elif how == "expired":
        store = env.app.state.term_tickets
        store._clock = lambda: time.monotonic() + 31
    elif how == "wrong_session":
        other = env.app.state.sessions.create("x")
        env.c.cookies.set(auth.COOKIE_NAME, env.app.state.sessions.cookie_value(other))
    elif how == "no_cookie":
        env.c.cookies.clear()
    with pytest.raises(WebSocketDisconnect) as ei:
        with connect(env, tok):
            pass
    assert ei.value.code == 4401


def test_ws_protected_renamed_after_ticket(env):
    tok = mint(env)
    tmux(env.st, "rename-session", "-t", "demo", "pmx")
    with pytest.raises(WebSocketDisconnect) as ei:
        with connect(env, tok):
            pass
    assert ei.value.code == 4404


def test_concurrency_cap(tmp_path, private_tmux):
    e = make_env(tmp_path, private_tmux, terminal_max=1)
    with connect(e, mint(e)) as ws1:
        init_msg(ws1)
        read_until(ws1, b"HELLO")
        with connect(e, mint(e)) as ws2:
            assert close_code(ws2) == 4429
    assert wait_attach_gone(e.st)
    with connect(e, mint(e)) as ws3:  # slot released
        init_msg(ws3)
        read_until(ws3, b"HELLO")


def test_bad_init(env):
    with connect(env, mint(env)) as ws:
        ws.send_text("not json")
        assert close_code(ws) == 4400
    assert not attach_children(env.st)


def test_idle_timeout(tmp_path, private_tmux):
    e = make_env(tmp_path, private_tmux, terminal_idle_s=1)
    t0 = time.time()
    with connect(e, mint(e)) as ws:
        init_msg(ws)
        assert close_code(ws) == 4408
    assert time.time() - t0 < 6
    assert wait_attach_gone(e.st)


def test_hard_cap(tmp_path, private_tmux):
    e = make_env(tmp_path, private_tmux, terminal_max_s=1)
    with connect(e, mint(e)) as ws:
        init_msg(ws)
        assert close_code(ws) == 4409
    assert wait_attach_gone(e.st)


def test_inbound_never_reaches_pane(tmp_path, private_tmux):
    e = make_env(tmp_path, private_tmux)
    out = tmp_path / "typed.txt"
    tmux(e.st, "new-session", "-d", "-s", "sink", "-x", "100", "-y", "30",
         f"cat > {out}")
    sid = sid_of(e.st, "sink")
    with connect(e, mint(e, sid)) as ws:
        init_msg(ws)
        ws.send_text(json.dumps({"type": "resize", "cols": 90, "rows": 20}))
        ws.send_bytes(b"BINTYPED\r\n")
        ws.send_text("TEXTTYPED\r\n")
        ws.send_text(json.dumps({"type": "input", "data": "JSONTYPED\r\n"}))
        ws.send_text(json.dumps({"type": "init", "cols": 50, "rows": 50}))
        ws.send_text(json.dumps({"type": "pong"}))
        time.sleep(1.0)
        client_close(ws)
        wait_close_audit(e)
    assert wait_attach_gone(e.st)
    cap = subprocess.run(["tmux", "-L", e.st.tmux_socket, "capture-pane", "-p", "-t", "sink"],
                         capture_output=True, text=True, env={**os.environ, "TMUX": ""}).stdout
    assert "TYPED" not in cap
    assert (out.read_text() if out.exists() else "") == ""
    wait_close_audit(e)
    close = [a for a in audit(e) if a["action"] == "terminal.close"][-1]
    assert close["detail"]["dropped_in"] == 4  # bin, text, json input, extra init


def test_oversize_frame(env):
    with connect(env, mint(env)) as ws:
        init_msg(ws)
        ws.send_text("x" * 5000)
        assert close_code(ws) == 1009


def test_audit_open_close_no_content(env):
    tok = mint(env)
    with connect(env, tok) as ws:
        init_msg(ws)
        read_until(ws, b"HELLO")
        ws.send_text("SECRETKEYSTROKES")
        time.sleep(0.2)
        client_close(ws)
        wait_close_audit(env)
    assert wait_attach_gone(env.st)
    wait_close_audit(env)
    raw = env.st.audit_file.read_text()
    assert "HELLO" not in raw and "SECRETKEYSTROKES" not in raw and tok not in raw
    a = audit(env)
    op = [x for x in a if x["action"] == "terminal.open"]
    cl = [x for x in a if x["action"] == "terminal.close"]
    assert len(op) == 1 and len(cl) == 1 and op[0]["target"] == "$" + env.sid[1:]
    d = cl[0]["detail"]
    assert set(d) == {"duration_s", "bytes_out", "dropped_in", "reason"}
    assert d["bytes_out"] > 0 and d["dropped_in"] == 1 and d["reason"] == "client closed"


def test_audit_failure_refuses(env, monkeypatch):
    tok = mint(env)
    log = env.app.state.audit
    real = log.write

    def boom(entry):
        if entry.action == "terminal.open":
            raise AuditError("x")
        real(entry)

    monkeypatch.setattr(log, "write", boom)
    with pytest.raises(WebSocketDisconnect) as ei:
        with connect(env, tok):
            pass
    assert ei.value.code == 4503
    assert not attach_children(env.st)
    assert env.app.state.term_broker.active == 0


def test_no_leaks_after_many_cycles(env):
    def fds():
        return len(os.listdir("/proc/self/fd"))

    def cycle():
        with connect(env, mint(env)) as ws:
            init_msg(ws)
            read_until(ws, b"HELLO")

    cycle()  # warm up portal threads etc.
    assert wait_attach_gone(env.st)
    before = fds()
    for _ in range(50):
        cycle()
    assert wait_attach_gone(env.st)
    assert attach_children(env.st) == []
    assert view_sessions(env.st) == []
    assert env.app.state.term_broker.active == 0
    assert env.app.state.term_broker.views == set()
    assert fds() <= before
    # tmux session untouched
    assert any(s.name == "demo" for s in tmuxctl.list_sessions(env.st))


def test_term_csp_allows_inline_style_only_for_terminal():
    from pm.web.render import CSP
    from pm.web.routes.term import term_csp

    c = term_csp("https://pm.example.test")
    assert "style-src 'self' 'unsafe-inline'" in c
    assert "wss://pm.example.test" in c
    assert "script-src 'self';" in c and "unsafe-inline" not in c.split("script-src")[1].split(";")[0]
    assert "unsafe-inline" not in CSP


def test_window_protocol_over_route(env):
    tmux(env.st, "new-window", "-d", "-t", "demo:", "-n", "second", "cat")
    tmux(env.st, "new-window", "-d", "-t", "demo:", "-n", "third", "cat")
    flags = lambda: subprocess.run(  # noqa: E731
        ["tmux", "-L", env.st.tmux_socket, "list-windows", "-t", "demo", "-F", "#{window_active}"],
        capture_output=True, text=True, env={**os.environ, "TMUX": ""}).stdout
    before = flags()
    with connect(env, mint(env)) as ws:
        init_msg(ws)
        first = recv_window(ws)
        assert set(first) == {"type", "index", "pos", "count", "name"} and first["count"] == 3
        ws.send_text(json.dumps({"type": "window", "dir": "next"}))
        nxt = recv_window(ws)
        assert nxt["pos"] == first["pos"] % 3 + 1
        assert env.app.state.term_broker.views and len(view_sessions(env.st)) == 1
        assert flags() == before
        client_close(ws)
        wait_close_audit(env)
    assert wait_attach_gone(env.st)
    assert view_sessions(env.st) == []
    close = [a for a in audit(env) if a["action"] == "terminal.close"][-1]
    assert close["detail"]["dropped_in"] == 0


def test_view_session_removed_on_abnormal_disconnect(env):
    with connect(env, mint(env)) as ws:
        init_msg(ws)
        recv_window(ws)
        assert len(view_sessions(env.st)) == 1
    # leaving the context cancels the server task (TestClient): still cleaned
    assert wait_attach_gone(env.st)
    end = time.time() + 5
    while view_sessions(env.st) and time.time() < end:
        time.sleep(0.05)
    assert view_sessions(env.st) == []


def test_route_cleans_stale_view_sessions_only(env):
    tid = "$" + env.sid[1:]
    stale = tmuxctl.new_view_session(env.st, tid, "pm-view-" + "e" * 12)
    tmux(env.st, "new-session", "-d", "-s", "pm-other", "cat")
    env.app.state.term_broker = term_broker = routes_term.term.Broker()
    # age the session by shifting the broker's wall clock view of it
    real = tmuxctl.list_view_sessions
    tmuxctl.list_view_sessions = lambda s: [(i, n, a, c - 120) for i, n, a, c in real(s)]
    try:
        with connect(env, mint(env)) as ws:
            init_msg(ws)
            recv_window(ws)
    finally:
        tmuxctl.list_view_sessions = real
    names = subprocess.run(["tmux", "-L", env.st.tmux_socket, "list-sessions", "-F", "#{session_name}"],
                           capture_output=True, text=True, env={**os.environ, "TMUX": ""}).stdout
    assert "pm-view-" + "e" * 12 not in names and "pm-other" in names and "demo" in names
    assert stale and term_broker.active == 0


# ---------------- history ----------------
def hist(e, sid=None, n=None, origin=None):
    q = {"sid": sid or e.sid}
    if n is not None:
        q["n"] = n
    h = {"Origin": origin} if origin else {}
    return e.c.get("/term/history", params=q, headers=h, follow_redirects=False)


def fresh(e):
    e.app.state.term_hist_last = {}  # defeat the 1/s limiter between calls


def test_history_requires_login_enabled_and_live_view(env):
    r = hist(env)
    assert r.status_code == 409  # no ticket-validated connection yet
    assert view_sessions(env.st) == []
    env.c.cookies.clear()
    assert hist(env).status_code in (303, 401)


def test_history_disabled_404(tmp_path, private_tmux):
    e = make_env(tmp_path, private_tmux)
    e.app.state.settings = replace(e.st, terminal_enabled=False)
    assert hist(e).status_code == 404


def test_history_validation(env):
    with connect(env, mint(env)) as ws:
        init_msg(ws)
        recv_window(ws)
        for bad in ("s9999", "bogus", "$1", "s1;x"):
            fresh(env)
            assert hist(env, sid=bad).status_code == 404, bad
        fresh(env)
        assert hist(env, sid=sid_of(env.st, "pmtest-base")).status_code == 404
        fresh(env)
        assert hist(env, n="abc").status_code == 400
        fresh(env)
        assert hist(env, origin="https://evil.example").status_code == 403
        fresh(env)
        r = hist(env, origin=ORIGIN)
        assert r.status_code == 200
        client_close(ws)


def test_history_rate_limit_and_headers(env):
    with connect(env, mint(env)) as ws:
        init_msg(ws)
        recv_window(ws)
        r = hist(env)
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store"
        j = r.json()
        assert set(j) == {"lines", "text", "window"} and "HELLO" in j["text"]
        assert j["window"]["type"] == "window"
        assert hist(env).status_code == 429
        client_close(ws)


def test_history_other_login_session_has_no_view(env):
    other = env.app.state.sessions.create("5.6.7.8")
    with connect(env, mint(env)) as ws:
        init_msg(ws)
        recv_window(ws)
        c2 = TestClient(env.app, base_url=ORIGIN)
        c2.cookies.set(auth.COOKIE_NAME, env.app.state.sessions.cookie_value(other))
        assert c2.get("/term/history", params={"sid": env.sid}).status_code == 409
        client_close(ws)


def test_history_tail_ansi_clamp_and_no_leaks(tmp_path, private_tmux):
    e = make_env(tmp_path, private_tmux)
    tmux(e.st, "new-session", "-d", "-s", "big", "-x", "100", "-y", "30",
         "sh -c 'i=0; while [ $i -lt 2600 ]; do printf \"\\033[31mline-$i\\033[0m\\n\"; i=$((i+1)); done; echo TAILMARK; cat'")
    tmux(e.st, "set-option", "-t", "big", "history-limit", "20000")
    sid = sid_of(e.st, "big")
    time.sleep(1.0)
    with connect(e, mint(e, sid)) as ws:
        init_msg(ws)
        recv_window(ws)
        n_views = len(view_sessions(e.st))
        j = hist(e, sid=sid).json()  # default 2000
        assert "TAILMARK" in j["text"].splitlines()[-1] or "TAILMARK" in j["text"]
        assert "\x1b[31m" in j["text"] and "line-2599" in j["text"] and "line-0\n" not in j["text"]
        assert 1900 <= j["lines"] <= 2100
        fresh(e)
        big = hist(e, sid=sid, n=999999).json()  # clamped to 10000 -> all 2600 + screen
        assert big["lines"] >= 2600 and "line-0\n" in big["text"]
        fresh(e)
        small = hist(e, sid=sid, n=0).json()  # clamped to 1
        assert small["lines"] <= 40
        assert len(view_sessions(e.st)) == n_views  # history created no sessions
        assert len(attach_children(e.st)) == 1
        client_close(ws)
        wait_close_audit(e)
    assert wait_attach_gone(e.st)
    assert view_sessions(e.st) == []
    assert not any("line-" in json.dumps(a) for a in audit(e))


def test_history_pane_is_server_resolved_and_cap(env, monkeypatch):
    seen = {}
    real = tmuxctl.capture_history
    monkeypatch.setattr(tmuxctl, "capture_history",
                        lambda s, pane, n: (seen.update(pane=pane, n=n), real(s, pane, n))[1])
    with connect(env, mint(env)) as ws:
        init_msg(ws)
        recv_window(ws)
        r = env.c.get("/term/history", params={"sid": env.sid, "pane": "%999", "n": 50_000})
        assert r.status_code == 200
        assert seen["pane"] != "%999" and seen["n"] == 10000
        client_close(ws)
    assert tmuxctl.HISTORY_MAX_BYTES == 1_500_000
    with pytest.raises(tmuxctl.TmuxError):
        tmuxctl.capture_history(env.st, "bad;id", 10)


# ---- back / window parameters --------------------------------------------------------------
@pytest.mark.parametrize("back,want", [
    ("/a/claude-140", "/a/claude-140"), ("/p/my.proj_1", "/p/my.proj_1"), ("/tmux", "/tmux"),
    ("/servers", "/servers"), ("/", "/"),
    ("//evil.com", "/tmux"), ("https://x", "/tmux"), ("javascript:alert(1)", "/tmux"),
    ("/a/..%2f", "/tmux"), ("/a/..", "/tmux"), ("/a/" + "x" * 65, "/tmux"),
    ("/a/x?y=1", "/tmux"), ("/tmux?x=1", "/tmux"), ("/a/x/y", "/tmux"), ("", "/tmux"),
    ("/a/x#f", "/tmux"), ("/a/\u00e9", "/tmux"), ("\\evil", "/tmux"),
])
def test_back_validation(env, back, want):
    r = env.c.get(f"/term/{env.sid}", params={"back": back})
    assert r.status_code == 200
    assert f"back={want} " in r.text
    assert ("bl=Back to chat" in r.text) == want.startswith("/a/")
    if not want.startswith("/a/"):
        assert "bl=Back " in r.text + " "


def test_back_default_label(env):
    r = env.c.get(f"/term/{env.sid}")
    assert "back=/tmux bl=Back win=" in r.text


def _active(st, target):
    out = subprocess.run(["tmux", "-L", st.tmux_socket, "list-windows", "-t", target, "-F",
                          "#{window_id}:#{window_active}"], capture_output=True, text=True,
                         env={**os.environ, "TMUX": ""}).stdout.split()
    return next(x.split(":")[0] for x in out if x.endswith(":1"))


def _wids(st, target):
    out = subprocess.run(["tmux", "-L", st.tmux_socket, "list-windows", "-t", target, "-F",
                          "#{window_id}"], capture_output=True, text=True,
                         env={**os.environ, "TMUX": ""}).stdout.split()
    return out


def test_window_param_opens_requested_window(env):
    tmux(env.st, "new-window", "-d", "-t", "demo:", "-n", "second", "cat")
    tmux(env.st, "new-window", "-d", "-t", "demo:", "-n", "third", "cat")
    w1, w2, w3 = _wids(env.st, "demo")
    real = _active(env.st, "demo")
    assert real == w1
    wnum = "w" + w3[1:]
    r = env.c.get(f"/term/{env.sid}", params={"w": wnum})
    assert f"win={wnum}" in r.text
    t = env.c.post("/term/ticket", data={"csrf": env.csrf, "sid": env.sid, "w": wnum},
                   headers={"Origin": ORIGIN})
    assert t.status_code == 200
    with connect(env, t.json()["ticket"]) as ws:
        init_msg(ws)
        first = recv_window(ws)
        assert first["pos"] == 3 and first["name"] == "third" and first["count"] == 3
        assert _active(env.st, "demo") == w1  # the REAL session's window is untouched
        client_close(ws)
        wait_close_audit(env)
    assert wait_attach_gone(env.st)


def test_window_param_junk_and_foreign_ignored(env):
    tmux(env.st, "new-session", "-d", "-s", "other", "-x", "80", "-y", "24", "cat")
    foreign = _wids(env.st, "other")[0]
    mine = _wids(env.st, "demo")[0]
    for bad in ("w999999", "junk", "@5", "w1;x", "w" + foreign[1:], "", "w-1", "w" + "9" * 12):
        r = env.c.get(f"/term/{env.sid}", params={"w": bad})
        if bad == "w" + foreign[1:]:
            assert "win= " in r.text + " "  # other session's window refused
        elif bad in ("w999999", "junk", "@5", "w1;x", "", "w-1", "w" + "9" * 12):
            assert "win= " in r.text + " ", bad
        t = env.c.post("/term/ticket", data={"csrf": env.csrf, "sid": env.sid, "w": bad},
                       headers={"Origin": ORIGIN})
        assert t.status_code == 200
        with connect(env, t.json()["ticket"]) as ws:
            init_msg(ws)
            first = recv_window(ws)
            assert first["pos"] == 1 and first["count"] == 1
            client_close(ws)
            wait_close_audit(env)
        assert wait_attach_gone(env.st)
    assert mine != foreign


def test_term_csp_allows_self_fonts():
    from pm.web.routes.term import term_csp

    c = term_csp("https://pm.example.test")
    assert "font-src 'self'" in c
    assert "data:" not in c.split("font-src")[1].split(";")[0]
    assert "connect-src 'self' wss://pm.example.test" in c
    assert "connect-src 'self' ws://127.0.0.1:8096" in term_csp("http://127.0.0.1:8096")
