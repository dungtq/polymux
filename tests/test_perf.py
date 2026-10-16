"""Architecture of the performance work: single producer, latest snapshot, bounded pools,
stale-while-revalidate servers panel, SSE caps, event-loop watchdog."""

from __future__ import annotations

import asyncio
import logging
import pathlib
import sys
import threading
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from pm.core import executors
from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, AgentState, Tier
from pm.infra import servers
from pm.infra import snapshot as services
from pm.notify import events, watchdog
from pm.notify.events import AgentTracker, Hub, HubFull
from pm.web.app import create_app


# ---------------------------------------------------------------- latest snapshot
@pytest.fixture
def builds(monkeypatch):
    """Replace the real builder by a slow counting one."""
    calls: list[float] = []

    def fake(settings, now):
        calls.append(now)
        time.sleep(0.15)
        return services.Snapshot(time.time())

    monkeypatch.setattr(services, "_build", fake)
    return calls


def test_single_flight_async_readers_share_one_build(settings, builds):
    async def go():
        return await asyncio.gather(*(services.snapshot_async(settings) for _ in range(20)))

    res = asyncio.run(go())
    assert len(builds) == 1
    assert len({id(r) for r in res}) == 1


def test_single_flight_threads_share_one_build(settings, builds):
    out: list = []
    ts = [threading.Thread(target=lambda: out.append(services.latest_snapshot(settings)))
          for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(builds) == 1 and len(out) == 8


def test_latest_snapshot_staleness_rules(settings, builds, monkeypatch):
    monkeypatch.setattr(services, "is_idle", lambda: False)
    fresh = services.Snapshot(time.time() - 1)
    services.publish(settings, fresh)
    assert services.latest_snapshot(settings) is fresh  # fresh: never builds
    assert asyncio.run(services.snapshot_async(settings)) is fresh
    assert builds == []
    services.publish(settings, services.Snapshot(time.time() - 14))
    services.latest_snapshot(settings)  # < 15 s: still served
    assert builds == []
    services.publish(settings, services.Snapshot(time.time() - 16))
    snap = services.latest_snapshot(settings)  # > 15 s: one build
    assert len(builds) == 1 and time.time() - snap.ts < 1
    services.publish(settings, services.Snapshot(time.time() - 3))
    services.latest_snapshot(settings, max_age_s=2)  # caller-chosen limit
    assert len(builds) == 2


def test_no_snapshot_yet_builds_once(settings, builds):
    assert services.published(settings) is None
    services.latest_snapshot(settings)
    services.latest_snapshot(settings)
    assert len(builds) == 1


def test_idle_tightens_page_staleness(settings, builds, monkeypatch):
    services.publish(settings, services.Snapshot(time.time() - 5))
    monkeypatch.setattr(services, "is_idle", lambda: True)
    services.latest_snapshot(settings)  # 5 s old is too old once the poller sleeps
    assert len(builds) == 1


def test_build_snapshot_reuses_recent_and_force_rebuilds(settings, builds):
    a = services.build_snapshot(settings)
    assert services.build_snapshot(settings) is a
    assert services.build_snapshot(settings, force=True) is not a
    assert len(builds) == 2


def test_poll_loop_is_the_producer(settings, builds):
    s = replace(settings, poll_interval_s=0.01)
    assert services.published(s) is None

    async def go():
        t = asyncio.create_task(events.poll_loop(s, Hub(), AgentTracker()))
        await asyncio.sleep(0.5)
        t.cancel()

    asyncio.run(go())
    assert services.published(s) is not None
    assert len(builds) >= 2  # never overlapping: the fake would not tolerate it (single thread)


def test_next_interval_adaptive(settings, monkeypatch):
    s = replace(settings, poll_interval_s=2.0)
    hub, tr = Hub(), AgentTracker()
    monkeypatch.setattr(services, "is_idle", lambda: False)
    assert events.next_interval(s, hub, tr) == 2.0
    monkeypatch.setattr(services, "is_idle", lambda: True)
    assert events.next_interval(s, hub, tr) == events.IDLE_POLL_S
    hub.subscribe()  # a connected SSE client keeps the fast cadence
    assert events.next_interval(s, hub, tr) == 2.0
    hub.close()
    hub2 = Hub()
    tr.update([Agent(id="claude-1", kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=1,
                     cwd="/", state=AgentState.WAITING)])
    assert events.next_interval(s, hub2, tr) == 2.0  # an agent is waiting
    assert events.next_interval(replace(s, poll_interval_s=8.0), hub2, AgentTracker()) == 8.0


def test_touch_wakes_from_idle(monkeypatch):
    monkeypatch.setattr(services, "_demand", [time.monotonic() - 1000])
    assert services.is_idle()
    services.touch()
    assert not services.is_idle()


# ---------------------------------------------------------------- executors
def test_executors_are_bounded_and_named():
    sizes = executors.sizes()
    assert sizes["snapshot"] == 1
    assert 1 <= sizes["collectors"] <= 4 and 1 <= sizes["tmux"] <= 4
    assert all(0 < n <= 8 for n in sizes.values())
    assert executors.snapshot._thread_name_prefix == "pm-snapshot"


def test_no_blocking_to_thread_in_routes():
    root = pathlib.Path(__file__).parent.parent / "src/pm"
    for f in [*root.glob("routes_*.py"), root / "tmux/term.py", root / "notify/alerts.py", root / "notify/events.py"]:
        assert "asyncio.to_thread" not in f.read_text(), f.name


def test_default_executor_is_bounded(tmp_path):
    s = Settings(state_dir=tmp_path / "st", token_file=tmp_path / "t", totp_file=tmp_path / "o",
                 tier_ceiling=Tier.SAFE, disable_poll=True)
    seen = {}
    app = create_app(s)

    @app.get("/_pool")
    async def pool():
        seen["n"] = asyncio.get_running_loop()._default_executor._max_workers
        return {}

    with TestClient(app) as c:
        c.get("/_pool")
    assert seen["n"] == 8


# ---------------------------------------------------------------- SSE caps
def test_hub_per_client_cap_and_release():
    h = Hub(max_per_client=2, max_subscribers=5)
    a, _ = h.subscribe(client="1.1.1.1")
    h.subscribe(client="1.1.1.1")
    c, _ = h.subscribe(client="1.1.1.1")  # newest wins: the OLDEST of this client is evicted
    assert a.evicted and a.done and not c.done
    assert h._per_client["1.1.1.1"] == 2 and h.subscriber_count == 2
    h.subscribe(client="2.2.2.2")  # another client is unaffected
    assert not c.evicted
    c.close()
    h.subscribe(client="1.1.1.1")  # slot released
    a.close()  # double close never double-counts
    assert h._per_client["1.1.1.1"] == 2
    z = Hub(max_per_client=0)
    with pytest.raises(HubFull) as e:
        z.subscribe(client="x")
    assert e.value.scope == "client"


def test_hub_total_cap_scope():
    h = Hub(max_per_client=9, max_subscribers=2)
    h.subscribe(client="a")
    h.subscribe(client="b")
    with pytest.raises(HubFull) as e:
        h.subscribe(client="c")
    assert e.value.scope == "total"


def test_hub_defaults():
    h = Hub()
    assert h.max_per_client == 12 and h.max_subscribers == 50


# ---------------------------------------------------------------- servers panel (SWR)
def _slow_collect(delay, calls):
    def fake(settings, tracker=None, partial=None, **kw):
        calls.append(1)
        time.sleep(delay)
        return servers.Panel(time.time(), hostname="h")
    return fake


def test_swr_first_load_returns_partial_within_budget(settings, monkeypatch):
    calls: list = []
    monkeypatch.setattr(servers, "collect_panel", _slow_collect(1.0, calls))
    servers._cache.clear()

    async def go():
        t0 = time.monotonic()
        p = await servers.get_panel_swr(settings, first_wait_s=0.1)
        return p, time.monotonic() - t0

    p, dt = asyncio.run(go())
    assert dt < 0.6
    assert set(p.collecting) == set(servers.SECTIONS)
    tiles = {t.key: t for t in servers.summarize(p).tiles}
    assert tiles["load"].detail == "collecting…" and tiles["containers"].value == "…"
    servers._refreshing[id(settings)].future.result(5)  # let the background refresh land
    assert servers.cached_panel(settings).hostname == "h"


def test_swr_stale_panel_is_served_instantly_and_refreshed_once(settings, monkeypatch):
    calls: list = []
    monkeypatch.setattr(servers, "collect_panel", _slow_collect(0.6, calls))
    old = servers.Panel(time.time() - 10, hostname="old")
    servers._cache[id(settings)] = (settings, old)

    async def go():
        t0 = time.monotonic()
        res = await asyncio.gather(*(servers.get_panel_swr(settings) for _ in range(10)))
        return res, time.monotonic() - t0

    res, dt = asyncio.run(go())
    assert dt < 0.3  # nobody waited for the 0.6 s collection
    assert all(p.hostname == "old" for p in res)
    servers._refreshing[id(settings)].future.result(5)
    assert len(calls) == 1  # single flight
    assert servers.cached_panel(settings).hostname == "h"
    assert asyncio.run(servers.get_panel_swr(settings)).hostname == "h"  # fresh: no new refresh
    assert len(calls) == 1


def test_swr_marks_very_old_panel_stale(settings, monkeypatch):
    monkeypatch.setattr(servers, "collect_panel", _slow_collect(0.3, []))
    servers._cache[id(settings)] = (settings, servers.Panel(time.time() - 100))
    p = asyncio.run(servers.get_panel_swr(settings))
    assert p.stale and "refresh" in p.failed
    servers._refreshing[id(settings)].future.result(5)


def test_collectors_run_concurrently(settings, monkeypatch):
    def slow(ret):
        def f(*a, **k):
            time.sleep(0.3)
            return ret
        return f

    monkeypatch.setattr(servers, "collect_host", slow(("h", ())))
    monkeypatch.setattr(servers, "collect_docker", slow(servers.DockerInfo(True)))
    monkeypatch.setattr(servers, "collect_libvirt", slow(None))
    monkeypatch.setattr(servers, "check_hosts", slow(()))
    snap = services.Snapshot(time.time())
    t0 = time.monotonic()
    p = servers.collect_panel(settings, snapshot_fn=lambda s: (time.sleep(0.3), snap)[1],
                              ntfy_fn=lambda s: (time.sleep(0.3), (True, 3))[1])
    dt = time.monotonic() - t0
    assert dt < 0.9  # six 0.3 s sections: ~0.3 s, not 1.8 s
    assert p.hostname == "h" and p.ntfy_ok is True and not p.stale


def test_collect_panel_section_failure_is_isolated(settings, monkeypatch):
    monkeypatch.setattr(servers, "collect_host", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(servers, "collect_docker", lambda run: servers.DockerInfo(True))
    monkeypatch.setattr(servers, "collect_libvirt", lambda run: None)
    monkeypatch.setattr(servers, "check_hosts", lambda t, c=None: ())
    p = servers.collect_panel(settings, snapshot_fn=lambda s: services.Snapshot(time.time()),
                              ntfy_fn=lambda s: (None, None))
    assert p.failed == ("host",)


# ---------------------------------------------------------------- watchdog
class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def _wd(stall=2.0, exit_s=30.0):
    clock, exits = Clock(), []
    records: list[str] = []
    logger = logging.getLogger("pm.watchdog.test")
    logger.setLevel(logging.DEBUG)
    logger.handlers = [type("H", (logging.Handler,), {"emit": lambda self, r: records.append(r.getMessage())})()]
    logger.propagate = False
    wd = watchdog.Watchdog(stall, exit_s, clock=clock, exit_fn=exits.append, logger=logger,
                           frames_fn=lambda: {1: sys._getframe(0), 2: sys._getframe(0)},
                           names_fn=lambda: {1: "main", 2: "pm-snapshot_0"})
    wd.loop_ident = 1
    return wd, clock, exits, records


def test_watchdog_logs_once_per_window_with_stack_without_locals():
    wd, clock, exits, records = _wd()
    secret = "TOPSECRET-LOCAL-VALUE"  # noqa: F841 - must never show up in the log
    clock.t = 1.0
    assert wd.check() == "ok" and not records
    clock.t = 2.6  # stalled 2.35 s >= 2.0
    assert wd.check() == "log"
    assert len(records) == 1 and "event loop stalled 2." in records[0]
    assert "test_perf.py" in records[0]
    assert "test_watchdog_logs_once_per_window_with_stack_without_locals" in records[0]
    assert "TOPSECRET" not in records[0]
    assert "pm-snapshot_0=" in records[0]  # worker thread's top frame listed
    clock.t = 8.0
    wd.check()
    assert len(records) == 1  # rate limited to one line / 10 s
    clock.t = 13.0
    wd.check()
    assert len(records) == 2
    assert not exits


def test_watchdog_exit_decision_and_disabled():
    wd, clock, exits, records = _wd(exit_s=30.0)
    clock.t = 29.0
    assert wd.check() == "log" and not exits
    clock.t = 31.0
    assert wd.check() == "exit" and exits == [3]
    assert "exiting" in records[-1]
    wd2, clock2, exits2, _ = _wd(exit_s=0)  # 0 disables the exit
    clock2.t = 10_000.0
    assert wd2.check() == "log" and not exits2


def test_watchdog_recovery_and_lag():
    wd, clock, exits, records = _wd()
    clock.t = 5.0
    wd.check()
    assert wd.loop_lag_ms >= 4000
    wd.beat(0.01)  # loop is alive again
    clock.t = 5.1
    assert wd.check() == "ok"
    assert any("recovered" in r for r in records)
    assert wd.loop_lag_ms == pytest.approx(10.0, abs=1)


def test_watchdog_real_stall_is_logged_and_thread_is_daemon(caplog):
    caplog.set_level(logging.WARNING, logger="pm.watchdog")
    exits: list[int] = []

    async def go():
        wd = watchdog.Watchdog(0.3, 0, exit_fn=exits.append)
        wd.start()
        await asyncio.sleep(0.6)
        assert wd._thread.daemon
        time.sleep(1.3)  # injected stall: blocks the loop
        await asyncio.sleep(0.4)
        await wd.stop()
        return wd

    wd = asyncio.run(go())
    msgs = [r.getMessage() for r in caplog.records if r.name == "pm.watchdog"]
    assert any("event loop stalled" in m and "test_watchdog_real_stall" in m for m in msgs)
    assert not exits
    assert not any(t.name == "pm-watchdog" for t in threading.enumerate())
    assert wd._thread is None


def test_watchdog_exits_on_long_stall():
    exits: list[int] = []

    async def go():
        wd = watchdog.Watchdog(0.2, 0.8, exit_fn=exits.append)
        wd.start()
        await asyncio.sleep(0.3)
        time.sleep(1.6)
        await wd.stop()

    asyncio.run(go())
    assert exits and exits[0] == 3


def test_healthz_reports_loop_lag_and_snapshot_age(tmp_path):
    s = Settings(state_dir=tmp_path / "st", token_file=tmp_path / "t", totp_file=tmp_path / "o",
                 tier_ceiling=Tier.SAFE, disable_poll=True, watchdog=True)
    app = create_app(s)
    with TestClient(app) as c:
        body = c.get("/healthz").json()
        assert body["ok"] is True and isinstance(body["loop_lag_ms"], float)
        assert body["snapshot_age_s"] is None
        services.publish(s, services.Snapshot(time.time() - 3))
        assert 2.5 <= c.get("/healthz").json()["snapshot_age_s"] < 6
    assert app.state.watchdog is None


# ---------------------------------------------------------------- detection fast paths
def test_lazy_proc_view_walks_children_without_full_scan():
    import os
    import subprocess

    from pm.agents import detect

    child = subprocess.Popen(["sleep", "5"], stdin=subprocess.DEVNULL)
    try:
        view = detect._LazyView(detect.PROC)
        assert child.pid in view.children(os.getpid())
        st = view.stat(child.pid)
        assert st is not None and st.ppid == os.getpid() and st.comm == "sleep"
        full = detect._FullView(detect.PROC)
        assert child.pid in full.children(os.getpid())
    finally:
        child.kill()
        child.wait()


# ---------------------------------------------------------------- SSE client key / 429
def test_sse_client_key_uses_real_ip_only_from_trusted_proxy():
    from types import SimpleNamespace

    from pm.web.routes.events import sse_client

    def req(peer, **h):
        return SimpleNamespace(client=SimpleNamespace(host=peer), headers=h)

    assert sse_client(req("172.19.0.3", **{"x-real-ip": "192.0.2.50"})) == "192.0.2.50"  # nginx
    assert sse_client(req("127.0.0.1", **{"x-real-ip": "192.0.2.51"})) == "192.0.2.51"
    assert sse_client(req("172.19.0.3")) == "172.19.0.3"
    assert sse_client(req("8.8.8.8", **{"x-real-ip": "1.2.3.4"})) == "8.8.8.8"  # spoof ignored
