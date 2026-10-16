"""Shared test helpers: settings, logged-in clients and the common fake-app fixtures.

Import what you need (``from helpers import login, make_settings``). Fixtures defined here are
picked up by pytest under the name they are bound to in the test module, so a module that wants
the pages app as its ``env`` writes ``from helpers import pages_env as env``.

Never point anything here at the user's default tmux server: settings are built from the
``fake_tmux`` fixture (stub binary, ``pmtest-fake`` socket) or ``tmux_socket="pmtest-*"``.
"""

import base64
import contextlib
import dataclasses
import os
import re
import socket
import subprocess
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from pm.agents import message as msg
from pm.chat import strip as chat_strip
from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, AgentState, ChatView, NtfyItem, Tier, TmuxPane, TmuxSession, TmuxWindow
from pm.infra import servers
from pm.infra import snapshot as services
from pm.notify import feed as ntfy_feed
from pm.notify.events import Hub
from pm.security import auth
from pm.security.csrf import Csrf
from pm.tmux import ctl as tmuxctl
from pm.tmux.ctl import SEP
from pm.web.app import create_app
from pm.web.render import STATIC_DIR
from pm.web.routes import agents as routes_agents
from pm.web.routes import chat as routes_chat
from pm.web.routes import ntfy as routes_ntfy
from pm.web.routes import search as routes_search
from pm.web.routes.pages import router as pages_router
from pm.web.routes.servers import router as servers_router

TESTSERVER = "https://testserver"
ORIGIN = "https://pm.example.test"
# a fake GitHub token: masking tests check it never reaches a page
TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


# ------------------------------------------------------------------ site defaults
# The app defaults are host-neutral (loopback, ntfy/git links off). Tests were written against a
# full site, so the whole run uses these values as Settings defaults; NEUTRAL_DEFAULTS keeps the real ones.
SITE = dict(
    base_url="https://pm.example.test", allowed_origins=("https://pm.example.test",),
    git_allowed_hosts=("git.example.test",),
    builder_frame_hosts=("https://mock.example.test",), ntfy_url="https://ntfy.example.test/pm",
    ntfy_topic_url="https://ntfy.example.test/agent", ntfy_pm_topic_url="https://ntfy.example.test/pm",
    display_utc_offset_h=7.0, vm_protected=("*eve*",),
)
_INIT_FIELDS = [f.name for f in dataclasses.fields(Settings) if f.init]
NEUTRAL_DEFAULTS = {k: Settings.__init__.__defaults__[_INIT_FIELDS.index(k)] for k in SITE}


def apply_site_defaults() -> None:
    d = list(Settings.__init__.__defaults__)
    for k, v in SITE.items():
        d[_INIT_FIELDS.index(k)] = v
    Settings.__init__.__defaults__ = tuple(d)


# ------------------------------------------------------------------ test isolation (no network, no stray sockets)
# Hosts under example.test never resolve; the session fixture in conftest.py answers them with an empty 200
# so background ntfy threads do not log ConnectError warnings.
FAKE_HTTP_DOMAIN = "example.test"


def is_fake_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return host == FAKE_HTTP_DOMAIN or host.endswith("." + FAKE_HTTP_DOMAIN)


# ``private_tmux`` names its server ``pmtest-<pid>-<hex6>``. Only these names are ever swept;
# ``pmtest-dev-*`` (a live dev instance), ``pmtest-fake`` and the like never match.
PMTEST_SOCK_RE = re.compile(r"^pmtest-(\d+)-[0-9a-f]{6}$")


def tmux_socket_dir() -> Path:
    return Path(os.environ.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{os.getuid()}"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def _server_listening(path: Path) -> bool:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
        c.settimeout(1.0)
        try:
            c.connect(str(path))
        except OSError:
            return False
    return True


def kill_private_tmux(name: str, path: Path | None = None) -> None:
    """``tmux -S <socket> kill-server`` (it may already be gone), then remove the socket file."""
    assert PMTEST_SOCK_RE.match(name), name
    path = path or tmux_socket_dir() / name
    with contextlib.suppress(subprocess.TimeoutExpired, OSError):
        subprocess.run(["tmux", "-S", str(path), "kill-server"], timeout=10, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def sweep_pmtest_sockets(sock_dir: Path | None = None) -> int:
    """Remove ``pmtest-<pid>-<hex6>`` sockets left by this run or by a pytest process that is gone.

    A live other pid (another pytest in another worktree) is left alone. A dead server's socket is just
    unlinked; ``kill-server`` only runs when something still listens on it. Returns how many were removed.
    """
    sock_dir = sock_dir or tmux_socket_dir()
    try:
        names = os.listdir(sock_dir)
    except OSError:
        return 0
    me, alive, n = os.getpid(), {}, 0
    for name in names:
        m = PMTEST_SOCK_RE.match(name)
        if not m:
            continue
        pid = int(m.group(1))
        if pid != me:
            if pid not in alive:
                alive[pid] = _pid_alive(pid)
            if alive[pid]:
                continue
        path = sock_dir / name
        if _server_listening(path):
            kill_private_tmux(name, path)
        else:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
        n += 1
    return n


# ------------------------------------------------------------------ settings / app / login
def make_settings(fake_tmux, tmp_path, *, state="state", **overrides):
    """``fake_tmux`` settings rooted in ``tmp_path``: dev_root ``dev/``, no extra projects,
    state in ``tmp_path/<state>``, token/totp under ``cfg/``. ``overrides`` win."""
    fields = dict(dev_root=tmp_path / "dev", extra_projects=(), state_dir=tmp_path / state,
                  token_file=tmp_path / "cfg/token", totp_file=tmp_path / "cfg/totp")
    fields.update(overrides)
    return dataclasses.replace(fake_tmux, **fields)


def login(s, c):
    """Log ``c`` in with the token pm wrote to ``s.token_file``; asserts the 303."""
    r = c.post("/login", data={"token": s.token_file.read_text().strip()}, follow_redirects=False)
    assert r.status_code == 303
    return r


def make_client(s, *routers, static=False, base_url=TESTSERVER, logged_in=False):
    """``create_app(s)`` + extra routers (+ /static) wrapped in a TestClient; optionally logged in."""
    app = create_app(s)
    for r in routers:
        app.include_router(r)
    if static:
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    c = TestClient(app, base_url=base_url)
    if logged_in:
        login(s, c)
    return c


def pane_line(sid, sname, wid, pid, cwd):
    """One fake ``list-panes`` row: window 1 "main", pane pid 4242 running claude."""
    f = [sid, sname, "1", wid, "0", "main", "1", pid, "4242", "claude", cwd, "1", "t"]
    return SEP.join(f) + "\n"


# ------------------------------------------------------------------ page fixtures
@pytest.fixture
def pages_env(fake_tmux, tmp_path, monkeypatch):
    """Pages app, not logged in: running claude-12 in dev/alpha (with a TODO.md); its pane shows
    ANSI, a secret (TOKEN) and a <script>. Returns ``(settings, client)``."""
    (tmp_path / "dev" / "alpha").mkdir(parents=True)
    (tmp_path / "dev" / "alpha" / "TODO.md").write_text("- [x] one\n- [ ] open <b>thing</b>\n")
    capture = f"\x1b[31mhello\x1b[0m\nexport GH={TOKEN}\n<script>x</script>"
    dev = tmp_path / "dev"
    s = make_settings(fake_tmux, tmp_path)
    monkeypatch.setenv("FAKE_TMUX_OUT", pane_line("$1", "work", "@1", "%12", str(dev / "alpha")))
    agent = Agent(id="claude-12", kind=AgentKind.CLAUDE, pane_id="%12", session_id="$1", pid=4242,
                  cwd=str(dev / "alpha"), state=AgentState.RUNNING, state_since=1.0, session_uuid="abc-123")
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes: [agent])
    monkeypatch.setattr(tmuxctl, "capture_pane", lambda settings, pane, lines=200: capture)
    return s, make_client(s, pages_router, static=True)


def _idle_agent_app(fake_tmux, tmp_path, monkeypatch, *routers):
    dev = tmp_path / "dev"
    (dev / "alpha").mkdir(parents=True)
    s = make_settings(fake_tmux, tmp_path)
    monkeypatch.setenv("FAKE_TMUX_OUT", pane_line("$1", "work", "@1", "%12", str(dev / "alpha")))
    agent = Agent(id="claude-12", kind=AgentKind.CLAUDE, pane_id="%12", session_id="$1", pid=4242,
                  cwd=str(dev / "alpha"), state=AgentState.IDLE, state_since=1.0)
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes: [agent])
    monkeypatch.setattr(tmuxctl, "capture_pane", lambda settings, pane, lines=200: "x")
    return s, make_client(s, pages_router, *routers, static=True), dev


@pytest.fixture
def chat_env(fake_tmux, tmp_path, monkeypatch):
    """Pages + chat routes, not logged in: idle claude-12 in dev/alpha. Returns ``(settings, client)``."""
    s, c, _dev = _idle_agent_app(fake_tmux, tmp_path, monkeypatch, routes_chat.router)
    return s, c


@pytest.fixture
def builder_env(fake_tmux, tmp_path, monkeypatch):
    """Pages app, not logged in: idle claude-12 in dev/alpha.
    Returns ``(settings, client, project_dir, tmp_path)``."""
    s, c, dev = _idle_agent_app(fake_tmux, tmp_path, monkeypatch)
    return s, c, dev / "alpha", tmp_path


# ------------------------------------------------------------------ chat view helpers
def set_chat_view(monkeypatch, view):
    """Make the chat route render ``view`` for any agent."""
    monkeypatch.setattr(chat_strip, "chat_view", lambda settings, agent, pane_text=None: view)


def chat_view(**kw):
    base = dict(agent_id="claude-12", state=AgentState.IDLE, last_role="assistant",
                last_text="hello", last_ts=1.0, source="transcript")
    base.update(kw)
    return ChatView(**base)


def agent_page_csrf(c):
    """The form CSRF token from the claude-12 agent page."""
    m = re.search(r'name="csrf" value="([^"]+)"', c.get("/a/claude-12").text)
    assert m
    return m.group(1)


def png_bytes():
    return b"\x89PNG\r\n\x1a\n" + b"\0" * 32


# ------------------------------------------------------------------ agent mutation routes (bare app)
def mk_client(st, unlocked=True, ceiling=Tier.MESSAGE):
    """Bare FastAPI app with only the agents router and a ready session cookie.
    Returns ``(app, client, session, csrf_token, settings)``."""
    st = dataclasses.replace(st, tier_ceiling=ceiling, state_dir=st.state_dir)
    app = FastAPI()
    app.state.settings = st
    auth.install(app, st)
    app.include_router(routes_agents.router)
    app.state.hub = Hub()
    sess = app.state.sessions.create("1.2.3.4")
    if unlocked:
        sess.unlocked_until = time.time() + 300
    c = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    c.cookies.set(auth.COOKIE_NAME, app.state.sessions.cookie_value(sess))
    tok = Csrf(st.state_dir, st.allowed_origins).token(sess.id)
    return app, c, sess, tok, st


def post(c, tok, url, origin=ORIGIN, csrf=True, **data):
    h = {"Origin": origin} if origin else {}
    if csrf:
        data = {"csrf": tok, **data}
    return c.post(url, data=data, headers=h)


def audit_text(st):
    return st.audit_file.read_text() if st.audit_file.exists() else ""


def snap_with(monkeypatch, agents=(), sessions=()):
    snap = services.Snapshot(ts=time.time(), agents=tuple(agents), sessions=tuple(sessions))
    # handlers read the published snapshot; a real build here would touch the real tmux
    monkeypatch.setattr(services, "published", lambda s: snap)
    monkeypatch.setattr(services, "_build", lambda *a, **k: pytest.fail("handler triggered a build"))


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """Fake tmux layer: records run() calls; agent claude-1 on pane %1."""
    st = Settings(state_dir=tmp_path / "st", token_file=tmp_path / "tok",
                  totp_file=tmp_path / "totp", dev_root=tmp_path / "dev", extra_projects=(),
                  unprotected_sessions=("free*",), tmux_socket="pmtest-fake")
    calls = []

    def pane(i, sid):
        return TmuxPane(id=f"%{i}", window_id=f"@{i}", session_id=sid, pid=1, command="x", cwd="/")

    def sess(i, name):
        return TmuxSession(id=f"${i}", name=name, windows=(
            TmuxWindow(id=f"@{i}", session_id=f"${i}", index=0, name="w",
                       panes=(pane(i, f"${i}"),)),))

    sessions = (sess(1, "prot"), sess(2, "free1"))
    monkeypatch.setattr(tmuxctl, "run", lambda s, a, timeout=0, **kw: calls.append(a) or "")
    monkeypatch.setattr(tmuxctl, "list_sessions", lambda s: sessions)
    monkeypatch.setattr(services, "build_snapshot", lambda *a, **k: None)  # tmux mutations force one (tmux_changed)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    for k in ("ENTER_DELAY_S", "PASTE_DELAY_S", "RETRY_DELAY_S", "CONFIRM_DELAY_S"):
        monkeypatch.setattr(msg, k, 0)
    agent = Agent(id="claude-1", kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=1,
                  cwd="/")
    return st, calls, agent


# ------------------------------------------------------------------ servers / search / ntfy apps
def servers_panel():
    row = servers.SessionRow("main", 1, 1, 0, None, (), 0.0, True, 0)
    return servers.Panel(
        time.time(), "devbox",
        (servers.Metric("Load", "1 on 4 CPU", 25.0, "ok"),),
        servers.DockerInfo(True, (servers.Container("<script>alert(1)</script>", "img", "running", "Up", "", ""),), 1, 1, 0),
        servers.TmuxSummary(True, 1, 1, 1, 0, (), (row,)),
        (servers.HostCheck("rebox", "198.51.100.55", 22, "RE only", False, None),),
        None, ("docker",),
    )


def patch_panel(monkeypatch, make):
    async def swr(settings, tracker=None, first_wait_s=2.0, **kw):
        return make()
    monkeypatch.setattr(servers, "get_panel_swr", swr)


def servers_client(fake_tmux, tmp_path, monkeypatch, login=True):
    """App + servers router (poll off) serving ``servers_panel()``; logged in unless ``login=False``."""
    s = make_settings(fake_tmux, tmp_path, state="st", disable_poll=True)
    (tmp_path / "dev").mkdir(exist_ok=True)
    patch_panel(monkeypatch, lambda: servers_panel())
    return make_client(s, servers_router, logged_in=login)


def search_client(fake_tmux, tmp_path):
    """Pages + search routes + /static, not logged in. Returns ``(client, settings)``."""
    s = make_settings(fake_tmux, tmp_path, state="st")
    (tmp_path / "dev").mkdir(exist_ok=True)
    return make_client(s, pages_router, routes_search.router, static=True), s


@pytest.fixture
def ntfy_env(tmp_path, monkeypatch):
    """Pages + ntfy routes over a stubbed ntfy feed (no fake tmux binary), not logged in.
    Returns ``(settings, client, feed_state, dev_root)``; set ``feed_state["items"]``."""
    dev = tmp_path / "dev"
    (dev / "alpha").mkdir(parents=True)
    (dev / "beta").mkdir()
    s = Settings(dev_root=dev, extra_projects=(), state_dir=tmp_path / "state",
                 token_file=tmp_path / "cfg/token", totp_file=tmp_path / "cfg/totp",
                 tmux_socket="pmtest-unused")
    state = {"items": [], "ok": True}
    monkeypatch.setattr(ntfy_feed, "_snapshot", lambda settings: (list(state["items"]), state["ok"]))
    return s, make_client(s, pages_router, routes_ntfy.router), state, dev


def ntfy_item(n, path, **kw):
    base = dict(id=str(n), time=1_700_000_000.0 + n, title=f"T{n}", message=f"M{n}", tags=(path,))
    base.update(kw)
    return NtfyItem(**base)


# --------------------------------------------------------------------------
# Claude JSONL builders: background tasks and narration thinking (synthetic shapes, invented text)
# --------------------------------------------------------------------------


def thinking_sig(narration: bool) -> str:
    """A signature shaped like Claude's (base64 of a small protobuf); "narration" in its head or not."""
    tag = b"narration" if narration else b"reasoning"
    head = b"\x08\x04\x12\xb0\x07\n\x11\x08\x12\x18\x02\x38\x01\x42\x09" + tag + b"\x12\x0c"
    return base64.b64encode(head + bytes(range(7, 67))).decode()


def bg_result(uid: str, tool_id: str, task_id: str, ts: str, *, shape: str = "bash") -> dict:
    """The immediate tool_result of a background launch. ``shape``: "bash" (structured backgroundTaskId),
    "agent" (structured isAsync + agentId), "bash-text" / "agent-text" (text only, no toolUseResult)."""
    if shape.startswith("bash"):
        out = f"Command running in background with ID: {task_id}. Output is being written to: /tmp/x/{task_id}.out"
        tur = {"stdout": "", "stderr": "", "interrupted": False, "backgroundTaskId": task_id}
        content: object = out
    else:
        out = (f"Async agent launched successfully.\nagentId: {task_id} (internal id)\n"
               "The agent is working in the background.")
        tur = {"isAsync": True, "status": "async_launched", "agentId": task_id, "description": "SECRET desc",
               "prompt": "SECRET prompt", "outputFile": f"/tmp/x/{task_id}.output"}
        content = [{"type": "text", "text": out}]
    e = {"type": "user", "uuid": uid, "timestamp": ts,
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id,
                                                   "content": content, "is_error": False}]}}
    if not shape.endswith("-text"):
        e["toolUseResult"] = tur
    return e


def task_xml(task_id: str, status: str = "completed", tool_id: str | None = None) -> str:
    tid = f"<tool-use-id>{tool_id}</tool-use-id>\n" if tool_id else ""
    return (f"<task-notification>\n<task-id>{task_id}</task-id>\n{tid}"
            f"<output-file>/tmp/x/{task_id}.output</output-file>\n<status>{status}</status>\n"
            "<summary>SECRET summary of the task</summary>\n<result>SECRET result text</result>\n"
            "<usage><tool_uses>3</tool_uses></usage>\n</task-notification>")


def task_note(uid: str, task_id: str, ts: str, *, status: str = "completed", tool_id: str | None = None,
              shape: str = "user") -> dict:
    """A task notification: a user entry with a string content (``shape`` "user") or a queued_command
    attachment with origin.kind "task-notification" (``shape`` "attachment")."""
    body = task_xml(task_id, status, tool_id)
    if shape == "user":
        return {"type": "user", "uuid": uid, "timestamp": ts, "message": {"role": "user", "content": body},
                "origin": {"kind": "task-notification"}, "promptSource": "system"}
    return {"type": "attachment", "uuid": uid, "timestamp": ts,
            "attachment": {"type": "queued_command", "prompt": body, "commandMode": "task-notification",
                           "origin": {"kind": "task-notification", "producer": "session-task"}}}


def new_window_call(calls: list[list[str]]) -> list[str]:
    """The first recorded ``new-window`` as one command: ``tmuxctl.new_agent_window`` sends one tmux sequence
    (``new-window ... ; set-option -w ... remain-on-exit on ; rename-window ... -- <name>``). Returns the
    new-window words up to the first ``;`` with its temporary ``-n`` replaced by the final window name."""
    call = next(x for x in calls if x and x[0] == "new-window")
    if ";" not in call:
        return list(call)
    nw = list(call[:call.index(";")])
    tail = call[call.index(";"):]
    assert tail[:2] == [";", "set-option"] and "remain-on-exit" in tail
    nw[nw.index("-n") + 1] = tail[-1]  # rename-window ... -- <name>
    return nw
