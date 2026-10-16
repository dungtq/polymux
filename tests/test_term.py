import asyncio
import json
import os
import signal
import subprocess
import time
from dataclasses import replace

import pytest

from pm.tmux import ctl as tmuxctl
from pm.tmux import term


def tmux(settings, *args):
    subprocess.run(["tmux", "-L", settings.tmux_socket, *args], check=True, timeout=10,
                   env={**os.environ, "TMUX": ""}, capture_output=True)


def test_clamp_size():
    assert term.clamp_size(1000, 1000) == (300, 120)
    assert term.clamp_size(1, 1) == (20, 5)
    assert term.clamp_size(80, 24) == (80, 24)
    for bad in [("80", 24), (80.5, 24), (None, 1), (True, 10)]:
        assert term.clamp_size(*bad) is None


def test_attach_argv(settings):
    a = term.attach_argv(replace(settings, tmux_socket="pmtest-x"), "$4")
    assert a == ["tmux", "-L", "pmtest-x", "attach-session", "-t", "$4", "-r"]
    with pytest.raises(tmuxctl.TmuxError):
        term.attach_argv(settings, "demo; rm -rf /")
    with pytest.raises(tmuxctl.TmuxError):
        term.attach_argv(settings, "-t")


def test_resolve_session(private_tmux):
    tmux(private_tmux, "new-session", "-d", "-s", "demo")
    sess = {s.name: s for s in tmuxctl.list_sessions(private_tmux)}
    assert term.resolve_session(private_tmux, "s" + sess["demo"].id[1:]) == (
        sess["demo"].id, "demo")  # protected-by-default sessions are still viewable
    for bad in ["s" + sess["pmtest-base"].id[1:], "s9999", "demo", "$1", "s1; x", ""]:
        with pytest.raises(term.TermError) as e:
            term.resolve_session(private_tmux, bad)
        assert e.value.status == 404


def test_resolve_refuses_own_session(private_tmux, monkeypatch):
    tmux(private_tmux, "new-session", "-d", "-s", "ownsess")
    s = next(s for s in tmuxctl.list_sessions(private_tmux) if s.name == "ownsess")
    monkeypatch.setenv("TMUX_PANE", s.windows[0].panes[0].id)
    with pytest.raises(term.TermError):
        term.resolve_session(private_tmux, "s" + s.id[1:])


def test_outbox_coalesce_and_size():
    async def go():
        o = term.Outbox()
        o.push_bytes(b"a" * 10)
        o.push_bytes(b"b" * 10)
        o.push_text("t")
        o.push_bytes(b"c")
        assert o.size == 21
        assert o.pop() == ("b", b"a" * 10 + b"b" * 10)
        assert o.pop() == ("t", "t")
        assert o.pop() == ("b", b"c")
        assert o.size == 0 and o.pop() is None
    asyncio.run(go())


def test_broker_cap():
    b = term.Broker()
    assert b.reserve(2) and b.reserve(2) and not b.reserve(2)
    b.release()
    assert b.reserve(2)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return not open(f"/proc/{pid}/stat").read().split(")")[1].split()[0] == "Z"


def test_attach_close_escalates_to_sigkill_and_reaps():
    async def go():
        a = term.Attach(["sh", "-c", "trap '' HUP; while :; do sleep 1; done"], 80, 24)
        pid = a.proc.pid
        await asyncio.sleep(0.2)
        fd = a.master
        t0 = time.time()
        await a.close()
        assert a.proc.returncode == -signal.SIGKILL  # ignored SIGHUP -> killed, reaped
        assert time.time() - t0 < 3
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)  # reaped: not even a zombie
        with pytest.raises(OSError):
            os.fstat(fd)
        await a.close()  # idempotent
    asyncio.run(go())


def test_attach_winsize_applied():
    async def go():
        a = term.Attach(["sh", "-c", "sleep 0.3; stty size; sleep 1"], 123, 45)
        await asyncio.sleep(0.2)
        a.resize(100, 33)
        await asyncio.sleep(0.4)
        data = os.read(a.master, 4096)
        await a.close()
        assert b"33 100" in data
    asyncio.run(go())


class FakeWS:
    def __init__(self, first=None, stall=False):
        self.inbox = asyncio.Queue()
        if first is not None:
            self.inbox.put_nowait({"type": "websocket.receive", "text": json.dumps(first)})
        self.sent = []
        self.stall = stall

    async def receive(self):
        item = await self.inbox.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def send_bytes(self, b):
        if self.stall:
            await asyncio.sleep(3600)
        self.sent.append(b)

    async def send_text(self, t):
        self.sent.append(t)


def children_of_tmux(settings):
    out = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            argv = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
        except OSError:
            continue
        if b"attach-session" in argv and settings.tmux_socket.encode() in argv:
            out.append(pid)
    return out


def test_slow_client_dropped_with_4413_and_bounded(private_tmux):
    tmux(private_tmux, "new-session", "-d", "-s", "spew", "-x", "100", "-y", "30",
         "yes 0123456789abcdef")
    sid = next(s.id for s in tmuxctl.list_sessions(private_tmux) if s.name == "spew")

    async def go():
        ws = FakeWS({"type": "init", "cols": 100, "rows": 30}, stall=True)
        lim = term.Limits(idle_s=60, max_s=60, send_timeout_s=0.5)
        res = term.Result()
        r = await asyncio.wait_for(term.serve(ws, private_tmux, sid, lim, res=res), 10)
        assert (r.code, r.reason) == (4413, "slow client")
    asyncio.run(go())
    assert children_of_tmux(private_tmux) == []


def test_cancel_kills_child(private_tmux):
    tmux(private_tmux, "new-session", "-d", "-s", "c1", "-x", "100", "-y", "30", "cat")
    sid = next(s.id for s in tmuxctl.list_sessions(private_tmux) if s.name == "c1")

    async def go():
        ws = FakeWS({"type": "init", "cols": 100, "rows": 30})
        t = asyncio.create_task(term.serve(ws, private_tmux, sid, term.Limits(60, 60)))
        await asyncio.sleep(0.8)
        assert len(children_of_tmux(private_tmux)) == 1
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    asyncio.run(go())
    assert children_of_tmux(private_tmux) == []
    assert any(s.name == "c1" for s in tmuxctl.list_sessions(private_tmux))


def test_cancel_tears_down_view_session_and_reaps(private_tmux, monkeypatch):
    sid = three_windows(private_tmux, "c2")
    broker = term.Broker()
    res = term.Result()
    procs = []
    real_attach = term.Attach

    def spy(*a, **kw):
        procs.append(real_attach(*a, **kw))
        return procs[-1]

    async def go():
        ws = FakeWS({"type": "init", "cols": 100, "rows": 30})
        t = asyncio.create_task(term.serve(ws, private_tmux, sid, term.Limits(60, 60), res=res,
                                           broker=broker, owner="o1"))
        await asyncio.sleep(0.8)
        assert len(view_sessions(private_tmux)) == 1 and len(broker.views) == 1
        assert broker.owners and len(children_of_tmux(private_tmux)) == 1
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    monkeypatch.setattr(term, "Attach", spy)
    asyncio.run(go())
    assert view_sessions(private_tmux) == []  # grouped session killed
    assert broker.views == set() and broker.owners == {}
    assert children_of_tmux(private_tmux) == []
    (a,) = procs
    assert a.master == -1 and a.proc.returncode is not None  # child reaped, fd closed
    with pytest.raises(ProcessLookupError):
        os.kill(a.proc.pid, 0)  # not even a zombie
    assert (res.code, res.reason) == (1001, "cancelled") and res.duration_s > 0
    assert any(s.name == "c2" for s in tmuxctl.list_sessions(private_tmux))


def test_other_clients_size_unchanged(private_tmux):
    """A read-only attach at a different PTY size never resizes the window."""
    tmux(private_tmux, "new-session", "-d", "-s", "sz", "-x", "120", "-y", "30", "cat")
    sid = next(s.id for s in tmuxctl.list_sessions(private_tmux) if s.name == "sz")

    def size():
        return subprocess.run(
            ["tmux", "-L", private_tmux.tmux_socket, "display-message", "-p", "-t", "sz",
             "#{window_width}x#{window_height}"], capture_output=True, text=True,
            env={**os.environ, "TMUX": ""}).stdout.strip()

    # a real (writable) second client at 120x30 on its own PTY
    other = term.Attach(term.attach_argv(private_tmux, sid)[:-1], 120, 30)
    time.sleep(0.8)
    before = size()
    assert before.startswith("120x")

    async def go():
        ws = FakeWS({"type": "init", "cols": 40, "rows": 10})
        t = asyncio.create_task(term.serve(ws, private_tmux, sid, term.Limits(60, 60)))
        await asyncio.sleep(0.8)
        assert size() == before
        ws.inbox.put_nowait({"type": "websocket.receive",
                             "text": json.dumps({"type": "resize", "cols": 25, "rows": 8})})
        await asyncio.sleep(0.4)
        assert size() == before
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    try:
        asyncio.run(go())
    finally:
        asyncio.run(other.close())


# ---- grouped view session / window navigation ----

def tmux_out(settings, *args):
    return subprocess.run(["tmux", "-L", settings.tmux_socket, *args], check=True, timeout=10,
                          env={**os.environ, "TMUX": ""}, capture_output=True, text=True).stdout


def view_sessions(settings):
    return [n for n in tmux_out(settings, "list-sessions", "-F", "#{session_name}").split()
            if n.startswith("pm-view-")]


def active_flags(settings, target):
    return tmux_out(settings, "list-windows", "-t", target, "-F", "#{window_active}").split()


def three_windows(settings, name="w3"):
    tmux(settings, "new-session", "-d", "-s", name, "-x", "100", "-y", "30", "-n", "one", "cat")
    tmux(settings, "new-window", "-d", "-t", name + ":", "-n", "two", "cat")
    tmux(settings, "new-window", "-d", "-t", name + ":", "-n", "three", "cat")
    tmux(settings, "select-window", "-t", name + ":1")
    return next(s.id for s in tmuxctl.list_sessions(settings) if s.name == name)


def window_msgs(ws):
    return [json.loads(x) for x in ws.sent if isinstance(x, str) and '"window"' in x]


def wmsg(ws, d):
    ws.inbox.put_nowait({"type": "websocket.receive", "text": json.dumps(d)})


def test_window_next_prev_wrap_isolated_from_target(private_tmux):
    sid = three_windows(private_tmux)
    before = active_flags(private_tmux, "w3")
    assert before.count("1") == 1
    broker = term.Broker()

    async def go():
        ws = FakeWS({"type": "init", "cols": 100, "rows": 30})
        t = asyncio.create_task(term.serve(ws, private_tmux, sid, term.Limits(60, 60),
                                           broker=broker))
        await asyncio.sleep(0.8)
        first = window_msgs(ws)
        assert len(first) == 1 and set(first[0]) == {"type", "index", "pos", "count", "name"}
        assert first[0]["count"] == 3 and 1 <= first[0]["pos"] <= 3
        assert len(view_sessions(private_tmux)) == 1 and len(broker.views) == 1
        base = first[0]["index"]
        seen = []
        for d in ("next", "next", "next", "prev"):
            wmsg(ws, {"type": "window", "dir": d})
            await asyncio.sleep(0.3)
            seen.append(window_msgs(ws)[-1])
        idx = [m["index"] for m in seen]
        n0 = (base + 1) % 3
        assert idx == [n0, (n0 + 1) % 3, base, (base + 2) % 3]  # wrapped around
        assert [m["pos"] for m in seen] == [i + 1 for i in idx]
        assert seen[0]["name"] in ("one", "two", "three")
        assert active_flags(private_tmux, "w3") == before  # real session untouched
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    asyncio.run(go())
    assert view_sessions(private_tmux) == [] and broker.views == set()
    assert children_of_tmux(private_tmux) == []
    assert active_flags(private_tmux, "w3") == before


def test_window_invalid_and_rate_limited(private_tmux):
    sid = three_windows(private_tmux, "w4")

    async def go():
        ws = FakeWS({"type": "init", "cols": 100, "rows": 30})
        res = term.Result()
        t = asyncio.create_task(term.serve(ws, private_tmux, sid, term.Limits(60, 60), res=res))
        await asyncio.sleep(0.8)
        n0 = len(window_msgs(ws))
        for bad in ({"type": "window", "dir": "up"}, {"type": "window"},
                    {"type": "window", "dir": ["next"]}, {"type": "window", "dir": "NEXT"},
                    {"type": "window", "dir": "next", "extra": 1}, {"type": "windows", "dir": "next"}):
            wmsg(ws, bad)
        await asyncio.sleep(0.4)
        assert len(window_msgs(ws)) == n0 and res.dropped_in == 6
        for _ in range(30):  # burst: only ~10 per second may switch
            wmsg(ws, {"type": "window", "dir": "next"})
        await asyncio.sleep(1.0)
        done = len(window_msgs(ws)) - n0
        assert 10 <= done <= 12, done
        assert res.dropped_in >= 6 + 18
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    asyncio.run(go())
    assert view_sessions(private_tmux) == []


def test_view_session_removed_on_normal_close_and_error(private_tmux):
    sid = three_windows(private_tmux, "w5")

    async def go():
        ws = FakeWS({"type": "init", "cols": 100, "rows": 30})
        t = asyncio.create_task(term.serve(ws, private_tmux, sid, term.Limits(60, 60)))
        await asyncio.sleep(0.6)
        assert len(view_sessions(private_tmux)) == 1
        ws.inbox.put_nowait({"type": "websocket.disconnect"})
        r = await asyncio.wait_for(t, 5)
        assert r.reason == "client closed"
        assert view_sessions(private_tmux) == []
        # abnormal: receive() raises
        ws2 = FakeWS({"type": "init", "cols": 100, "rows": 30})
        t = asyncio.create_task(term.serve(ws2, private_tmux, sid, term.Limits(60, 60)))
        await asyncio.sleep(0.6)
        assert len(view_sessions(private_tmux)) == 1
        ws2.inbox.put_nowait(RuntimeError("socket gone"))
        await asyncio.wait_for(t, 5)
        assert view_sessions(private_tmux) == []
        # killed tmux attach child (abnormal) -> still cleaned
        ws3 = FakeWS({"type": "init", "cols": 100, "rows": 30})
        t = asyncio.create_task(term.serve(ws3, private_tmux, sid, term.Limits(60, 60)))
        await asyncio.sleep(0.6)
        for pid in children_of_tmux(private_tmux):
            os.kill(int(pid), signal.SIGKILL)
        await asyncio.wait_for(t, 5)
        assert view_sessions(private_tmux) == []
    asyncio.run(go())
    assert children_of_tmux(private_tmux) == []
    assert any(s.name == "w5" and len(s.windows) == 3 for s in tmuxctl.list_sessions(private_tmux)
               if not s.name.startswith("pm-view-"))


def test_init_timeout_creates_no_view_session(private_tmux):
    sid = three_windows(private_tmux, "w6")

    async def go():
        ws = FakeWS()
        r = await term.serve(ws, private_tmux, sid, term.Limits(60, 60, init_timeout_s=0.2))
        assert r.code == 4400
    asyncio.run(go())
    assert view_sessions(private_tmux) == []


def test_kill_view_session_refuses_other_names(private_tmux):
    sid = three_windows(private_tmux, "w7")
    assert tmuxctl.kill_view_session(private_tmux, sid) is False
    assert any(s.name == "w7" for s in tmuxctl.list_sessions(private_tmux))
    with pytest.raises(tmuxctl.TmuxError):
        tmuxctl.new_view_session(private_tmux, sid, "not-a-view")


def test_stale_cleanup_only_pm_view(private_tmux):
    sid = three_windows(private_tmux, "w8")
    tmux(private_tmux, "new-session", "-d", "-s", "pm-viewer", "cat")        # other pm*
    tmux(private_tmux, "new-session", "-d", "-s", "pm-view-notahexvalue", "cat")
    tmux(private_tmux, "new-session", "-d", "-s", "keepme", "cat")
    stale = tmuxctl.new_view_session(private_tmux, sid, "pm-view-" + "a" * 12)
    live = tmuxctl.new_view_session(private_tmux, sid, "pm-view-" + "b" * 12)
    fresh = tmuxctl.new_view_session(private_tmux, sid, "pm-view-" + "c" * 12)
    b = term.Broker()
    b.views.add(live)
    # too young: nothing killed
    assert b.cleanup_stale(private_tmux) == []
    b._last_cleanup = 0.0
    killed = b.cleanup_stale(private_tmux, now=time.time() + 120)
    assert sorted(killed) == sorted([stale, fresh])  # `live` is owned by this broker
    names = tmux_out(private_tmux, "list-sessions", "-F", "#{session_name}").split()
    assert "pm-view-" + "b" * 12 in names
    for keep in ("w8", "pm-viewer", "pm-view-notahexvalue", "keepme", "pmtest-base"):
        assert keep in names
    assert "pm-view-" + "a" * 12 not in names


def test_stale_cleanup_skips_attached(private_tmux):
    sid = three_windows(private_tmux, "w9")
    g = tmuxctl.new_view_session(private_tmux, sid, "pm-view-" + "d" * 12)
    a = term.Attach(term.attach_argv(private_tmux, g), 80, 24)
    time.sleep(0.6)
    try:
        assert term.Broker().cleanup_stale(private_tmux, now=time.time() + 120) == []
    finally:
        asyncio.run(a.close())
        tmuxctl.kill_view_session(private_tmux, g)


def test_grouped_readonly_client_keeps_other_size(private_tmux):
    sid = three_windows(private_tmux, "w10")
    other = term.Attach(term.attach_argv(private_tmux, sid)[:-1], 100, 30)
    time.sleep(0.6)

    def size():
        return tmux_out(private_tmux, "display-message", "-p", "-t", "w10:",
                        "#{window_width}x#{window_height}").strip()
    before = size()

    async def go():
        ws = FakeWS({"type": "init", "cols": 40, "rows": 10})
        t = asyncio.create_task(term.serve(ws, private_tmux, sid, term.Limits(60, 60)))
        await asyncio.sleep(0.8)
        wmsg(ws, {"type": "window", "dir": "next"})
        await asyncio.sleep(0.4)
        assert size() == before
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    try:
        asyncio.run(go())
    finally:
        asyncio.run(other.close())
