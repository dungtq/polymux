import asyncio
import json
import stat
import time
from types import SimpleNamespace as NS

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pm.core.models import Agent, AgentKind, AgentState
from pm.notify import events
from pm.notify.events import AgentTracker, Hub, HubFull
from pm.security import auth
from pm.web.routes import events as routes_events


def mk(i="claude-1", state=AgentState.RUNNING, **kw):
    kw.setdefault("cwd", "/w/p")
    return Agent(id=i, kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=1,
                 project="p", state=state, **kw)


# ---------------------------------------------------------------- hub
def test_hub_ids_replay_and_ring():
    h = Hub(ring=3)
    ids = [h.publish("inbox", {"n": i}).id for i in range(5)]
    assert ids == [1, 2, 3, 4, 5]
    assert [e.id for e in h.replay(3)] == [4, 5]
    assert [e.id for e in h.replay(0)] == [3, 4, 5]  # ring dropped 1,2
    assert h.replay(None) == []


def test_ui_event_type_is_known_and_published():
    assert events.UI == "ui" and events.UI in events.EVENT_TYPES
    h = Hub()
    ev = h.publish(events.UI, {"area": "tabs", "rev": 3})
    assert (ev.type, ev.data) == ("ui", {"area": "tabs", "rev": 3})
    assert routes_events._frame(ev) == 'id: 1\nevent: ui\ndata: {"area":"tabs","rev":3}\n\n'  # SSE as is


def test_pm_code_event_type_is_known():
    assert events.PM_CODE == "pm_code" and events.PM_CODE in events.EVENT_TYPES
    assert events.EVENT_TYPES == {"agent_state", "agent_added", "agent_removed", "inbox", "ui", "pm_code"}
    ev = Hub().publish(events.PM_CODE, {"need": "restart", "msg": "m", "head": "h"})
    assert routes_events._frame(ev) == 'id: 1\nevent: pm_code\ndata: {"need":"restart","msg":"m","head":"h"}\n\n'


def test_hub_max_subscribers_and_drop_slow():
    h = Hub(queue_size=2, max_subscribers=2)
    s1, _ = h.subscribe()
    s2, _ = h.subscribe()
    with pytest.raises(HubFull):
        h.subscribe()
    for i in range(3):
        h.publish("inbox", {"n": i})
    assert s1.dropped and s2.dropped
    assert h.subscriber_count == 0
    h.subscribe()  # slot freed


def test_hub_fast_consumer_receives():
    async def go():
        h = Hub()
        sub, _ = h.subscribe()
        h.publish("inbox", {"a": 1})
        assert (await sub.get(1)).id == 1
        assert await sub.get(0.01) is None
    asyncio.run(go())


# ---------------------------------------------------------------- tracker
def test_tracker_emits_only_on_change():
    t = [100.0]
    tr = AgentTracker(clock=lambda: t[0])
    ev = tr.update([mk()])
    assert [e[0] for e in ev] == ["agent_added"]
    assert tr.update([mk()]) == []
    t[0] = 110
    ev = tr.update([mk(state=AgentState.IDLE)])
    assert ev[0][0] == "agent_state" and tr.agents[0].state_since == 110
    assert [e[0] for e in tr.update([])] == ["agent_removed"]


def test_tracker_hook_wins_then_expires():
    t = [100.0]
    tr = AgentTracker(clock=lambda: t[0], hold_s=5)
    tr.update([mk(session_uuid="abc")])
    ev = tr.apply_hook("Notification", "abc", None)
    assert [e[0] for e in ev] == ["agent_state", "inbox"]
    assert ev[1][1]["waiting"] == 1
    t[0] = 103
    assert tr.update([mk(session_uuid="abc", state=AgentState.RUNNING)]) == []
    assert tr.agents[0].state == AgentState.WAITING
    t[0] = 106
    ev = tr.update([mk(session_uuid="abc", state=AgentState.RUNNING)])
    assert tr.agents[0].state == AgentState.RUNNING
    assert [e[0] for e in ev] == ["agent_state", "inbox"]


def test_tracker_hook_by_cwd_and_unknown():
    tr = AgentTracker(clock=lambda: 1.0)
    tr.update([mk()])
    assert tr.apply_hook("Bogus", "x", None) == []
    assert tr.apply_hook("Stop", None, "/other") == []
    ev = tr.apply_hook("Stop", None, "/w/p")
    assert ev[0][1]["state"] == "idle"


def test_poll_loop_survives_errors(monkeypatch, settings):
    calls = []

    def boom(s):
        calls.append(1)
        raise RuntimeError("secret pane text")

    monkeypatch.setattr(events.services, "_build", lambda s, now: boom(s))
    s = settings.__class__(**{**settings.__dict__, "poll_interval_s": 0.01})

    async def go():
        task = asyncio.create_task(events.poll_loop(s, Hub(), AgentTracker()))
        await asyncio.sleep(0.1)
        assert not task.done()
        task.cancel()
    asyncio.run(go())
    assert len(calls) >= 2


# ---------------------------------------------------------------- http
@pytest.fixture
def app(settings):
    a = FastAPI()
    a.state.settings = settings
    auth.install(a, settings)
    a.state.hub = Hub()
    a.state.tracker = AgentTracker()
    a.state.sse_heartbeat_s = 0.05
    a.state.sse_max_s = 0.3
    a.include_router(routes_events.router)
    return a


@pytest.fixture
def local(app):
    return TestClient(app, client=("127.0.0.1", 5000))


def secret(settings):
    return events.ensure_hook_secret(settings).decode()


def login(app, c):
    s = app.state.sessions.create("t")
    c.cookies.set(auth.COOKIE_NAME, app.state.sessions.cookie_value(s))


def post(c, settings, body=None, **hdr):
    h = {"X-PM-Hook-Secret": secret(settings), **hdr}
    return c.post("/hooks/claude", content=body if body is not None else
                  json.dumps({"hook_event_name": "Stop", "session_id": "s1"}), headers=h)


def test_secret_file_perms(settings):
    secret(settings)
    p = events.hook_secret_path(settings)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_hook_ok_and_event(app, local, settings):
    app.state.tracker.update([mk(session_uuid="s1", state=AgentState.RUNNING)])
    assert post(local, settings).status_code == 204
    assert app.state.hub.last_id == 1
    assert app.state.tracker.agents[0].state == AgentState.IDLE


def test_hook_wrong_or_missing_secret(local, settings):
    assert post(local, settings, **{"X-PM-Hook-Secret": "nope"}).status_code == 401
    r = local.post("/hooks/claude", content="{}")
    assert r.status_code == 401


@pytest.mark.parametrize("h", ["X-Forwarded-For", "X-Real-IP"])
def test_hook_rejects_proxy_headers(local, settings, h):
    assert post(local, settings, **{h: "1.2.3.4"}).status_code == 403


def test_hook_non_local_peer(app, settings):
    c = TestClient(app, client=("172.19.0.1", 1234))
    assert post(c, settings).status_code == 403


def test_hook_oversize_and_bad_input(local, settings):
    assert post(local, settings, body="x" * 20000).status_code == 413
    assert post(local, settings, body="notjson").status_code == 400
    assert post(local, settings, body="[]").status_code == 400
    bad = json.dumps({"hook_event_name": "Stop", "session_id": "a b;"})
    assert post(local, settings, body=bad).status_code == 400
    bad = json.dumps({"hook_event_name": "Stop", "cwd": "rel"})
    assert post(local, settings, body=bad).status_code == 400
    unk = json.dumps({"hook_event_name": "PreToolUse", "session_id": "s"})
    assert post(local, settings, body=unk).status_code == 204


def test_sse_requires_login(app, local):
    assert local.get("/events", follow_redirects=False).status_code in (303, 401)


def test_sse_stream_replay_heartbeat_no_text(app, local, settings):
    login(app, local)
    hub = app.state.hub
    app.state.tracker.update([mk(session_uuid="s1")])
    hub.publish("agent_added", {"id": "claude-1", "state": "running", "project": "p"})
    hub.publish("agent_state", {"id": "claude-1", "state": "waiting", "project": "p"})
    note = json.dumps({"hook_event_name": "Notification", "session_id": "s1",
                       "message": "SECRET-PANE-TEXT sk-abc"})
    assert post(local, settings, body=note).status_code == 204
    with local.stream("GET", "/events", headers={"Last-Event-ID": "1"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["x-accel-buffering"] == "no"
        body = "".join(r.iter_text())
    assert "id: 1\n" not in body and "id: 2\n" in body
    assert "event: agent_state" in body
    assert ": hb" in body
    assert "SECRET-PANE-TEXT" not in body


def test_sse_max_streams(app, local):
    login(app, local)
    app.state.hub.max_subscribers = 0
    assert local.get("/events").status_code == 429


def test_sse_per_client_cap_is_429(app, local):
    login(app, local)
    app.state.hub.max_per_client = 0
    r = local.get("/events")
    assert r.status_code == 429 and r.headers["retry-after"]


# ---------------------------------------------------------------- stream lifecycle (raw ASGI)
def _scope(app, ip="9.9.9.9"):
    s = app.state.sessions.create("t")
    cookie = f"{auth.COOKIE_NAME}={app.state.sessions.cookie_value(s)}"
    return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
            "path": "/events", "raw_path": b"/events", "query_string": b"", "scheme": "http",
            "root_path": "", "server": ("t", 80), "client": ("127.0.0.1", 1),
            "headers": [(b"cookie", cookie.encode()), (b"x-real-ip", ip.encode()), (b"host", b"t")]}


class _Conn:
    """One fake ASGI HTTP connection: collects the body, can hang up."""

    def __init__(self, app, **kw):
        self.app, self.kw = app, kw
        self.body = b""
        self.status = None
        self.gone = asyncio.Event()
        self.task = None

    async def receive(self):
        await self.gone.wait()
        return {"type": "http.disconnect"}

    async def send(self, m):
        if m["type"] == "http.response.start":
            self.status = m["status"]
        elif m["type"] == "http.response.body":
            self.body += m.get("body", b"")

    def start(self):
        self.task = asyncio.create_task(self.app(_scope(self.app, **self.kw), self.receive, self.send))
        return self


async def _until(pred, t=3.0):
    end = time.monotonic() + t
    while time.monotonic() < end:
        if pred():
            return True
        await asyncio.sleep(0.01)
    return pred()


def test_sse_cancel_mid_stream_releases_count(app):
    app.state.sse_max_s = None
    app.state.sse_heartbeat_s = 5

    async def go():
        hub = app.state.hub
        c = _Conn(app).start()
        assert await _until(lambda: hub.subscriber_count == 1)
        c.task.cancel()
        await c.task  # shutdown cancel ends quietly: no CancelledError escapes to uvicorn's log
        assert hub.subscriber_count == 0 and hub._per_client == {}
    asyncio.run(go())


def test_sse_cancel_before_generator_starts_releases_count(app):
    async def go():
        hub = app.state.hub
        sub, _ = hub.subscribe(client="9.9.9.9")
        resp = routes_events._SSEResponse(iter(()), sub)  # never iterated

        async def boom(*a, **k):
            raise asyncio.CancelledError

        resp.listen_for_disconnect = boom
        await resp({"type": "http", "asgi": {"spec_version": "2.4"}}, None, boom)  # cancel ends quietly
        assert hub.subscriber_count == 0
    asyncio.run(go())


def test_sse_client_hangup_releases(app):
    app.state.sse_max_s = None
    app.state.sse_heartbeat_s = 0.05

    async def go():
        hub = app.state.hub
        c = _Conn(app).start()
        assert await _until(lambda: hub.subscriber_count == 1)
        c.gone.set()  # http.disconnect: is_disconnected() / heartbeat tick notices within a beat
        assert await _until(lambda: hub.subscriber_count == 0)
        await asyncio.wait_for(c.task, 2)
        assert hub._per_client == {}
    asyncio.run(go())


def test_sse_max_lifetime_recycles_cleanly(app):
    app.state.sse_max_s = 0.2
    app.state.sse_heartbeat_s = 0.05

    async def go():
        c = _Conn(app).start()
        await asyncio.wait_for(c.task, 3)
        assert c.status == 200 and b"event: recycle" in c.body and b"event: bye" not in c.body
        assert app.state.hub.subscriber_count == 0
    asyncio.run(go())


def test_sse_newest_wins_oldest_gets_bye(app):
    app.state.sse_max_s = None
    app.state.sse_heartbeat_s = 5
    app.state.hub.max_per_client = 2

    async def go():
        hub = app.state.hub
        a = _Conn(app).start()
        assert await _until(lambda: hub.subscriber_count == 1)
        b = _Conn(app).start()
        assert await _until(lambda: hub.subscriber_count == 2)
        other = _Conn(app, ip="8.8.8.8").start()  # other client never evicted
        assert await _until(lambda: hub.subscriber_count == 3)
        c = _Conn(app).start()
        await asyncio.wait_for(a.task, 3)  # oldest ends cleanly with a final bye
        assert b"event: bye" in a.body and a.status == 200
        assert await _until(lambda: c.status == 200 and hub.subscriber_count == 3)
        assert b.status == 200 and not b.task.done() and not other.task.done()
        for x in (b, c, other):
            x.task.cancel()
        await asyncio.gather(b.task, c.task, other.task, return_exceptions=True)
        assert hub.subscriber_count == 0 and hub._per_client == {}
    asyncio.run(go())


def test_hub_reaps_stale_subscriptions():
    h = Hub()
    s, _ = h.subscribe(client="x")
    s.seen -= 1000  # generator never ran / stopped answering
    h.subscribe(client="y")
    assert s.closed and h.subscriber_count == 1 and "x" not in h._per_client


# ---------------------------------------------------------------- live extras (agentlive)
def _live(monkeypatch, fn):
    monkeypatch.setattr(events.agentlive, "build", fn)


def test_with_live_adds_unread_and_last_for_state_events(monkeypatch, settings):
    hub = Hub()
    sub, _ = hub.subscribe()
    a = mk(state=AgentState.IDLE)
    _live(monkeypatch, lambda s, agents: {x.id: {"unread": 2, "last": {"text": "t", "src": "user", "ts": 1.0}}
                                         for x in agents})
    evs = [(events.AGENT_STATE, events._public(a)), (events.AGENT_REMOVED, {"id": "x"}),
           (events.INBOX, {"waiting": 0, "ids": []})]
    got = asyncio.run(events.with_live(settings, hub, [a], evs))
    assert got[0][1]["unread"] == 2 and got[0][1]["last"]["src"] == "user" and got[0][1]["state"] == "idle"
    assert got[1:] == evs[1:]
    sub.close()


def test_with_live_unchanged_without_listener_error_or_timeout(monkeypatch, settings):
    a = mk()
    evs = [(events.AGENT_ADDED, events._public(a))]
    calls = []
    _live(monkeypatch, lambda s, agents: calls.append(1) or {})
    assert asyncio.run(events.with_live(settings, Hub(), [a], evs)) == evs and calls == []  # nobody listens
    hub = Hub()
    hub.subscribe()

    def boom(s, agents):
        raise RuntimeError("x")
    _live(monkeypatch, boom)
    assert asyncio.run(events.with_live(settings, hub, [a], evs)) == evs

    def slow(s, agents):
        time.sleep(0.3)
        return {a.id: {"unread": 1, "last": None}}
    _live(monkeypatch, slow)
    monkeypatch.setattr(events.agentlive, "TIMEOUT_S", 0.05)
    got = asyncio.run(events.with_live(settings, hub, [a], evs))
    assert got == evs and "unread" not in got[0][1]  # timeout: the plain payload


def test_poll_once_publishes_extras(monkeypatch, settings):
    a = mk(state=AgentState.WAITING)
    monkeypatch.setattr(events.executors, "run_snapshot",
                        lambda fn, s: asyncio.sleep(0, result=NS(agents=[a])))
    _live(monkeypatch, lambda s, agents: {x.id: {"unread": 0, "last": None} for x in agents})
    hub = Hub()
    sub, _ = hub.subscribe()
    asyncio.run(events.poll_once(settings, hub, AgentTracker(), seeded=False))
    added = [e for e in hub.replay(0) if e.type == events.AGENT_ADDED]
    assert added and added[0].data["unread"] == 0 and added[0].data["last"] is None
    sub.close()


def test_hook_event_carries_extras(app, local, settings, monkeypatch):
    _live(monkeypatch, lambda s, agents: {x.id: {"unread": 4, "last": None} for x in agents})
    app.state.tracker.update([mk(session_uuid="s1", state=AgentState.RUNNING)])
    sub, _ = app.state.hub.subscribe()
    assert post(local, settings).status_code == 204
    ev = app.state.hub.replay(0)[-1]
    assert ev.type == events.AGENT_STATE and ev.data["unread"] == 4
    sub.close()


def test_hook_answers_before_the_live_extras(app, settings, monkeypatch):
    """The 204 goes out first; the enriched event is published after it (BackgroundTask)."""
    _live(monkeypatch, lambda s, agents: {x.id: {"unread": 1, "last": None} for x in agents})
    app.state.tracker.update([mk(session_uuid="s1", state=AgentState.RUNNING)])
    sub, _ = app.state.hub.subscribe()
    body = json.dumps({"hook_event_name": "Stop", "session_id": "s1"}).encode()
    seen = {}

    async def go():
        scope = {"type": "http", "method": "POST", "path": "/hooks/claude", "raw_path": b"/hooks/claude",
                 "query_string": b"", "client": ("127.0.0.1", 5000), "server": ("testserver", 80), "scheme": "http",
                 "http_version": "1.1", "root_path": "",
                 "headers": [(b"x-pm-hook-secret", secret(settings).encode()),
                             (b"content-length", str(len(body)).encode())]}
        sent = []

        async def receive():
            if sent:
                await asyncio.sleep(1)
                return {"type": "http.disconnect"}
            sent.append(1)
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(msg):
            if msg["type"] == "http.response.start":
                seen["status"] = msg["status"]
                seen["published_at_start"] = len(app.state.hub.replay(0))
        await app(scope, receive, send)
    asyncio.run(go())
    assert seen == {"status": 204, "published_at_start": 0}
    ev = app.state.hub.replay(0)[-1]
    assert ev.type == events.AGENT_STATE and ev.data["unread"] == 1 and ev.data["state"] == "idle"
    sub.close()
