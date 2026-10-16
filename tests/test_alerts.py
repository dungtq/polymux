import asyncio
import json
import re
import stat
from types import SimpleNamespace

from pm.core.config import Settings
from pm.infra import servers
from pm.notify import alerts
from pm.notify.alerts import AlertState, Reading, evaluate
from pm.tmux import ctl as tmuxctl


def S(tmp_path, **kw):
    kw.setdefault("alerts_sustain", 3)
    kw.setdefault("alerts_repeat_s", 600)
    return Settings(tmux_socket="pmtest-unused", state_dir=tmp_path, **kw)


def disk(v, key="disk:/home", trig=92.0):
    return {key: Reading(key, "disk /home", v, trig, trig - 3, f"{v:.0f}%", f"{trig:.0f}%")}


def run(s, st, seq, t0=1000.0, step=60.0):
    out = []
    for i, v in enumerate(seq):
        out.append(evaluate(s, disk(v), st, t0 + i * step))
    return out


def test_sustain_before_first_alert(tmp_path):
    s, st = S(tmp_path), AlertState()
    res = run(s, st, [95, 95, 95])
    assert [len(r) for r in res] == [0, 0, 1]
    a = res[2][0]
    assert a.title.startswith("pm alert -- ") and "disk /home 95%" in a.title
    assert a.priority == "4" and "warning" in a.tags
    assert "limit 92%" in a.message and a.message.endswith("https://pm.example.test/servers")


def test_blip_resets_and_no_alert(tmp_path):
    s, st = S(tmp_path), AlertState()
    assert [len(r) for r in run(s, st, [95, 95, 50, 95, 95])] == [0] * 5


def test_hysteresis_no_flapping(tmp_path):
    s, st = S(tmp_path), AlertState()
    res = run(s, st, [95, 95, 95, 90, 95, 90, 95, 90, 91, 91, 91])  # 90/91 in dead band
    assert sum(len(r) for r in res) == 1
    assert st.keys["disk:/home"].alerted


def test_repeat_interval_and_recovery(tmp_path):
    s, st = S(tmp_path), AlertState()
    res = run(s, st, [95] * 3, step=60)
    assert len(res[2]) == 1
    assert evaluate(s, disk(95), st, 1000 + 300) == []  # inside repeat window
    rep = evaluate(s, disk(95), st, 1000 + 120 + 700)
    assert len(rep) == 1 and rep[0].kind == "alert"
    out = [evaluate(s, disk(50), st, 3000 + i) for i in range(3)]
    assert [len(o) for o in out] == [0, 0, 1]
    rec = out[2][0]
    assert rec.kind == "recovery" and rec.priority == "3" and "white_check_mark" in rec.tags
    assert rec.title.startswith("pm recovered -- ")
    assert not st.keys["disk:/home"].alerted


def test_persisted_state_prevents_duplicate_after_restart(tmp_path):
    s = S(tmp_path)
    st = AlertState()
    run(s, st, [95] * 3, t0=1000)
    alerts.save_state(s, st)
    p = tmp_path / "alerts.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    data = json.loads(p.read_text())
    assert data["version"] == 1 and "disk:/home" in data["alerted"]
    st2 = alerts.load_state(s)
    res = [evaluate(s, disk(95), st2, 1200 + i) for i in range(5)]
    assert sum(len(r) for r in res) == 0
    assert len(evaluate(s, disk(95), st2, 1000 + 800)) == 1  # repeat window elapsed
    st3 = alerts.load_state(s)
    out = [evaluate(s, disk(10), st3, 1300 + i) for i in range(3)]
    assert out[2] and out[2][0].kind == "recovery"


def test_corrupt_state_file(tmp_path):
    (tmp_path / "alerts.json").write_text("{nope")
    assert alerts.load_state(S(tmp_path)).keys == {}
    for bad in ("[1]", '{"alerted": [1]}', '{"alerted": {"k": true, "j": "x"}}'):
        (tmp_path / "alerts.json").write_text(bad)
        assert alerts.load_state(S(tmp_path)).keys == {}


def test_cap_and_summary(tmp_path):
    s, st = S(tmp_path, alerts_sustain=1), AlertState()
    rd = {}
    for i in range(8):
        rd.update(disk(99, key=f"disk:/m{i}"))
    out = alerts.cap(s, evaluate(s, rd, st, 1.0))
    assert len(out) == 6 and out[-1].kind == "summary" and "3 more alerts" in out[-1].title


def test_load_mem_swap_collect(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "loadavg").write_text("9.0 4.0 1.0 1/100 5\n")
    (proc / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 40 kB\nSwapTotal: 100 kB\nSwapFree: 20 kB\n")
    s = S(tmp_path, alerts_containers=False, alerts_hosts=False)
    r = alerts.collect(s, proc=proc, cpus=4, tmux_fn=lambda: "")
    assert r["load"].value == 1.0 and r["load"].value >= r["load"].trigger  # 5-minute avg used
    assert round(r["mem"].value) == 96 and r["mem"].value >= s.alerts_mem_pct
    assert round(r["swap"].value) == 80 and r["swap"].value >= s.alerts_swap_pct
    assert r["tmux"].value == 0 and any(k.startswith("disk:") for k in r)
    assert r[alerts.FAILED] == frozenset()


def test_container_problems(tmp_path):
    C = servers.Container
    info = servers.DockerInfo(True, (
        C("web", "i", "running", "Up 1h (unhealthy)", "unhealthy", ""),
        C("db", "i", "running", "Up 1h (healthy)", "healthy", ""),
        C("job", "i", "exited", "Exited (0) 1h ago", "", ""),
        C("crash", "i", "exited", "Exited (137) 1h ago", "", ""),
        C("loop", "i", "restarting", "Restarting (1) 5s ago", "", ""),
    ), 2, 5, 1)
    s = S(tmp_path, alerts_hosts=False)
    r = alerts.collect(s, proc=tmp_path, docker_fn=lambda: info, tmux_fn=lambda: "")
    bad = {k for k, v in r.items() if k.startswith("container:") and v.value >= v.trigger}
    assert bad == {"container:web", "container:crash", "container:loop"}
    s1 = S(tmp_path, alerts_sustain=1)
    out = evaluate(s1, {k: v for k, v in r.items() if k != alerts.FAILED}, AlertState(), 1.0)
    assert any("container web unhealthy" in a.title for a in out)


def test_docker_unavailable_holds_state(tmp_path):
    s = S(tmp_path, alerts_hosts=False)
    r = alerts.collect(s, proc=tmp_path, docker_fn=lambda: servers.DockerInfo(False), tmux_fn=lambda: "")
    assert "container" in r[alerts.FAILED]
    st = AlertState()
    st.keys["container:x"] = alerts.KeyState(alerted=True, last_sent=1.0)
    for i in range(5):
        assert evaluate(s, r, st, 10.0 + i) == []
    assert st.keys["container:x"].alerted


def test_host_down_and_tmux(tmp_path):
    s = S(tmp_path, alerts_containers=False, alerts_sustain=1)
    hc = lambda targets: (servers.HostCheck("rebox", "h", 22, "", False, None),
                          servers.HostCheck("uatbox", "h", 22, "", True, 1.0))

    def boom():
        raise tmuxctl.TmuxError("x")
    r = alerts.collect(s, proc=tmp_path, hosts_fn=hc, tmux_fn=boom)
    out = evaluate(s, r, AlertState(), 1.0)
    titles = " | ".join(a.title for a in out)
    assert "host rebox unreachable" in titles and "tmux server down" in titles
    assert "uatbox" not in titles


def test_vanished_key_recovers(tmp_path):
    s, st = S(tmp_path, alerts_sustain=1), AlertState()
    evaluate(s, disk(99), st, 1.0)
    out = evaluate(s, {}, st, 2.0)
    assert out and out[0].kind == "recovery" and "disk:/home" not in st.keys


def test_no_secrets_in_messages(tmp_path):
    s = S(tmp_path, alerts_sustain=1, alerts_hosts=False)
    C = servers.Container
    info = servers.DockerInfo(True, (C("we/b$(x) y;`id`", "img", "exited", "Exited (1) 1h", "", ""),), 0, 1, 0)
    r = alerts.collect(s, proc=tmp_path, docker_fn=lambda: info, tmux_fn=lambda: "")
    r.update(disk(99))
    out = evaluate(s, r, AlertState(), 1.0)
    assert len(out) == 2
    for a in out:
        for text in (a.title, a.message):
            assert re.fullmatch(r"[A-Za-z0-9 .,/:%_@-]*", text), text
        assert len(a.message) <= 120 and len(a.title) <= 80


def test_publish_via_cycle_and_failure_swallowed(tmp_path):
    s = S(tmp_path, alerts_sustain=1)
    sent = []

    def pub(settings, title, message, **kw):
        sent.append((title, kw))
        raise RuntimeError("down")
    st = AlertState()
    n = asyncio.run(alerts.run_cycle(s, st, collector=lambda _s: disk(99), publisher=pub, now=lambda: 5.0))
    assert n == 1 and sent[0][1]["click"] == "https://pm.example.test/servers" and sent[0][1]["priority"] == "4"
    assert "disk:/home" in json.loads((tmp_path / "alerts.json").read_text())["alerted"]


def test_loop_survives_collector_errors_and_disabled(tmp_path):
    calls = []

    def col(_s):
        calls.append(1)
        raise OSError("x")

    async def go(settings):
        t = asyncio.create_task(alerts.alert_loop(settings, AlertState(), collector=col))
        await asyncio.sleep(0.15)
        alive = not t.done()
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
        return alive
    assert asyncio.run(go(S(tmp_path, alerts_interval_s=0))) is True
    assert len(calls) >= 1
    calls.clear()
    assert asyncio.run(go(S(tmp_path, alerts_enabled=False))) is True
    s = S(tmp_path, disable_poll=True)
    assert asyncio.run(go(s)) is True
    assert calls == []


def test_start_stop_leave_no_task(tmp_path):
    async def go():
        s = S(tmp_path, alerts_enabled=False)
        app = SimpleNamespace(state=SimpleNamespace(settings=s))
        t = alerts.start(app)
        await asyncio.sleep(0.01)
        assert not t.done()
        await alerts.stop(app)
        assert t.done() and app.state.alerts_task is None
        await alerts.stop(app)
        return [x for x in asyncio.all_tasks() if x is not asyncio.current_task()]
    assert asyncio.run(go()) == []


def test_recovered_after_pairs_each_recovery_with_its_incident():
    from pm.core.models import NtfyItem

    def n(i, t, title):
        return NtfyItem(id=i, time=t, title=title, message="")
    items = [
        n("a1", 100.0, "pm alert -- h: disk /home 93%"),
        n("a2", 400.0, "pm alert -- h: disk /home 95%"),  # repeat of the same incident
        n("x1", 150.0, "pm alert -- h: disk / 99%"),  # another subject: "disk /home" must not match "disk /"
        n("r1", 1000.0, "pm recovered -- h: disk /home"),
        n("a3", 2000.0, "pm alert -- h: disk /home 92%"),
        n("r2", 2600.0, "pm recovered -- h: disk /home"),
        n("r3", 3000.0, "pm recovered -- h: load"),  # no alert in the window
    ]
    assert alerts.recovered_after(list(reversed(items))) == {"r1": 900.0, "r2": 600.0}
    assert alerts.recovered_after([]) == {}


def test_recovered_after_limited_to_ids():
    from pm.core.models import NtfyItem

    items = [NtfyItem("a1", 100.0, "pm alert -- h: cpu 99%", ""), NtfyItem("r1", 160.0, "pm recovered -- h: cpu", ""),
             NtfyItem("a2", 200.0, "pm alert -- h: load 9", ""), NtfyItem("r2", 260.0, "pm recovered -- h: load", "")]
    assert alerts.recovered_after(items, {"r2", "x"}) == {"r2": 60.0}
    assert alerts.recovered_after(items, {"a1", "zzz"}) == {}  # no recovery among them: nothing to pair
    assert alerts.recovered_after(items) == {"r1": 60.0, "r2": 60.0}
