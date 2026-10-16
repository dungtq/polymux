import json
import socket
import time
from dataclasses import replace
from pathlib import Path

from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, AgentState, TmuxPane, TmuxSession, TmuxWindow
from pm.infra import servers
from pm.infra.snapshot import Snapshot
from pm.web.render import TEMPLATE_DIR


def test_level_thresholds():
    assert servers.level(0.5, servers.LOAD_WARN, servers.LOAD_CRIT) == "ok"
    assert servers.level(0.7, servers.LOAD_WARN, servers.LOAD_CRIT) == "warn"
    assert servers.level(1.0, servers.LOAD_WARN, servers.LOAD_CRIT) == "crit"
    assert servers.level(91.9, servers.DISK_WARN, servers.DISK_CRIT) == "warn"
    assert servers.level(92, servers.DISK_WARN, servers.DISK_CRIT) == "crit"


def _fake_proc(tmp_path, load="3.00 1.00 0.50 1/2 3", avail=1000, total=10000):
    p = tmp_path / "proc"
    (p / "self").mkdir(parents=True)
    (p / "uptime").write_text("90061.5 100.0\n")
    (p / "loadavg").write_text(load)
    (p / "meminfo").write_text(
        f"MemTotal: {total} kB\nMemAvailable: {avail} kB\nSwapTotal: 1000 kB\nSwapFree: 500 kB\n"
    )
    (p / "self/stat").write_text("1 (pm x) S " + " ".join(["0"] * 17) + " 100 0\n")
    return p


def _run(argv, timeout):
    if argv[0] == "systemctl":
        return 0, "active\n"
    return -1, ""


def test_collect_host_fake_proc(tmp_path, settings):
    class U:
        def __init__(self, used, total): self.used, self.total = used, total

    host, ms = servers.collect_host(
        settings, proc=_fake_proc(tmp_path, avail=500), utmp=tmp_path / "none", run=_run,
        usage=lambda path: U(95, 100), cpus=4, same_mount=lambda a, b: True,
    )
    by = {m.label: m for m in ms}
    assert by["Load"].level == "warn" and by["Load"].pct == 75.0  # 3.0 / 4 cpus
    assert by["Memory"].level == "crit"  # 95% used
    assert by["Swap"].pct == 50.0 and by["Swap"].level == "warn"
    assert by["Disk /"].level == "crit"
    assert "1d 1h" in by["Uptime"].text
    assert by["pm service"].text.startswith("active") and by["pm service"].level == "ok"
    assert "Logins" not in by and "Disk /home" in by
    assert not any(l.startswith("Disk /home/") for l in by)


def test_collect_host_metric_order(tmp_path, settings):
    class U:
        def __init__(self, used, total): self.used, self.total = used, total

    _, ms = servers.collect_host(
        settings, proc=_fake_proc(tmp_path), utmp=tmp_path / "none", run=_run,
        usage=lambda path: U(1, 100), cpus=4, same_mount=lambda a, b: False,
    )
    labels = [m.label for m in ms]
    assert labels[:7] == ["Kernel", "Uptime", "Load", "Memory", "Swap", "Disk /", "Disk /home"]
    assert labels[7].startswith("Disk /") and labels[8:] == ["pm service", "tmux server"]


def test_collect_host_missing_proc_is_tolerated(tmp_path, settings):
    _, ms = servers.collect_host(settings, proc=tmp_path / "nope", utmp=tmp_path / "x", run=_run)
    assert any(m.label == "Kernel" for m in ms)


def test_logged_in_utmp(tmp_path):
    rec = lambda t: t.to_bytes(2, "little") + bytes(382)
    f = tmp_path / "utmp"
    f.write_bytes(rec(7) + rec(8) + rec(7))
    assert servers._logged_in(f) == 2


def _ps(*rows):
    return lambda argv, t: (0, "\n".join(json.dumps(r) for r in rows) + "\n")


def test_docker_sorting_and_counts():
    run = _ps(
        {"Names": "ok", "Image": "i", "State": "running", "Status": "Up 1h (healthy)", "Ports": "0.0.0.0:80->80/tcp"},
        {"Names": "dead", "Image": "i", "State": "exited", "Status": "Exited (1) 2h ago", "Ports": ""},
        {"Names": "bad", "Image": "i", "State": "running", "Status": "Up 1h (unhealthy)", "Ports": ""},
    )
    d = servers.collect_docker(run)
    assert [c.name for c in d.containers] == ["bad", "dead", "ok"]
    assert (d.running, d.total, d.unhealthy) == (2, 3, 1)
    assert d.containers[2].health == "healthy" and d.containers[2].ports == "80->80/tcp"


def test_docker_unavailable():
    assert servers.collect_docker(lambda a, t: (-1, "")).available is False


def test_docker_strips_control_chars():
    d = servers.collect_docker(_ps({"Names": "a\x1b[31mb", "State": "running"}))
    assert "\x1b" not in d.containers[0].name


def test_docker_compose_stack_and_workdir():
    lab = ("com.docker.compose.config-hash=ab,com.docker.compose.project=pm-dev,"
           "com.docker.compose.project.working_dir=/srv/pm-dev,com.docker.compose.service=web")
    d = servers.collect_docker(_ps({"Names": "web", "State": "running", "Labels": lab},
                                   {"Names": "solo", "State": "running", "Labels": "maintainer=x"},
                                   {"Names": "nolabels", "State": "running"}))
    by = {c.name: c for c in d.containers}
    assert (by["web"].stack, by["web"].workdir) == ("pm-dev", "/srv/pm-dev")
    assert (by["solo"].stack, by["solo"].workdir) == ("", "") == (by["nolabels"].stack, by["nolabels"].workdir)


def test_compose_labels_rejects_bad_project_names():
    assert servers.compose_labels(None) == ("", "")
    for bad in ("-x", "Upper", "a b", "a;rm", "x" * 64, ""):
        assert servers.compose_labels(f"com.docker.compose.project={bad},"
                                      "com.docker.compose.project.working_dir=/w") == ("", ""), bad
    assert servers.compose_labels("com.docker.compose.project=a_1-b") == ("a_1-b", "")


def test_ports_trimmed():
    assert servers._trim_ports("1->1/tcp, 2->2/tcp, 3->3/tcp, 4->4/tcp").endswith("+1")


def _pane(n, w, s): return TmuxPane(f"%{n}", w, s, 1, "bash", "/x")


def _sess(sid, name, wid, att=0, prot=True):
    return TmuxSession(sid, name, att, prot, (TmuxWindow(wid, sid, 0, "w", True, (_pane(int(sid[1:]), wid, sid),)),))


def test_summarize_tmux_sort_hide_and_chips():
    sess = (_sess("$1", "old", "@1"), _sess("$2", "wait", "@2", 1), _sess("$3", "new", "@3"),
            _sess("$4", "pm-view-0123456789ab", "@4"))
    ag = (Agent("claude-2", AgentKind.CLAUDE, "%2", "$2", 1, "/x", project="proj",
                state=AgentState.WAITING, label="wait-0-claude"),)
    snap = Snapshot(time.time(), sess, ag)
    t = servers.summarize_tmux(snap, {"@1": 10, "@2": 5, "@3": 50, "@4": 99})
    assert [r.name for r in t.rows] == ["wait", "new", "old"]
    assert t.sessions == 3 and t.attached == 1 and t.agents_by_state == (("waiting", 1),)
    r = t.rows[0]
    assert r.project == "proj" and r.agents[0].label == "wait-0-claude" and r.last_activity == 5


def test_tcp_check_open_and_closed():
    ls = socket.socket()
    ls.bind(("127.0.0.1", 0))
    ls.listen(1)
    port = ls.getsockname()[1]
    try:
        assert servers.tcp_check("127.0.0.1", port, 1) is not None
    finally:
        ls.close()
    assert servers.tcp_check("127.0.0.1", port, 1) is None


def test_host_targets_extra_servers(settings):
    s = replace(settings, servers=[{"name": "x", "host": "h", "port": 5, "note": "n"},
                                   {"name": "bad", "host": "h", "port": "z"}, 3])
    t = servers.host_targets(s)
    assert t == [("x", "h", 5, "n")]  # no built-in hosts


def test_check_hosts_concurrent_results():
    res = servers.check_hosts([("a", "h1", 1, ""), ("b", "h2", 2, "n")],
                              lambda h, p, t: 5.0 if h == "h1" else None)
    assert [(r.name, r.up) for r in res] == [("a", True), ("b", False)]


def test_libvirt_counts_and_absent():
    out = " Id Name State\n-----------\n 1 a running\n - b shut off\n"
    assert servers.collect_libvirt(lambda a, t: (0, out), lambda n: "/x") == servers.Libvirt(1, 2)
    assert servers.collect_libvirt(lambda a, t: (0, out), lambda n: None) is None
    assert servers.collect_libvirt(lambda a, t: (1, ""), lambda n: "/x") is None


def test_collect_panel_stale_and_cache(monkeypatch, settings):
    calls = []
    monkeypatch.setattr(servers, "collect_docker", lambda run: servers.DockerInfo(False))
    monkeypatch.setattr(servers, "check_hosts", lambda t, c=None: ())
    monkeypatch.setattr(servers, "collect_libvirt", lambda run: None)
    snap = Snapshot(time.time())
    p = servers.collect_panel(settings, snapshot_fn=lambda s: snap, ntfy_fn=lambda s: (None, None))
    assert p.stale and "docker" in p.failed

    def fake(s, tracker=None, **kw):
        calls.append(1)
        return servers.Panel(time.time())

    monkeypatch.setattr(servers, "collect_panel", fake)
    servers._cache.clear()
    servers.get_panel(settings)
    servers.get_panel(settings)
    assert len(calls) == 1
    servers.get_panel(settings, force=True)
    assert len(calls) == 2


def _m(label, text, pct=None, lvl="ok"):
    return servers.Metric(label, text, pct, lvl)


def _panel(**kw):
    base = dict(
        ts=time.time(), hostname="h",
        metrics=(
            _m("Uptime", "1d 2h"), _m("Load", "0.40 0.30 0.20 on 4 CPU", 10.0),
            _m("Memory", "1.0 GiB / 8.0 GiB (12%)", 12.0), _m("Swap", "0 B / 1 GiB (0%)", 0.0),
            _m("Disk /", "1 GiB / 10 GiB (10%)", 10.0), _m("Disk /home", "9 GiB / 10 GiB (90%)", 90.0, "warn"),
            _m("Logins", "2"), _m("pm service", "active, up 3h 1m"),
        ),
        docker=servers.DockerInfo(True, (servers.Container("a", "i", "running", "Up", "", ""),), 1, 1, 0),
        tmux=servers.TmuxSummary(True, 2, 3, 4, 1, (("waiting", 1), ("idle", 2))),
        hosts=(servers.HostCheck("rebox", "h", 22, "", True, 5.0),),
    )
    base.update(kw)
    return servers.Panel(**base)


def _by(s):
    return {t.key: t for t in s.tiles}


def test_summarize_ok_and_tile_shape():
    s = servers.summarize(_panel(ntfy_ok=True, ntfy_24h=7))
    t = _by(s)
    assert s.status == "warn"  # worst disk 90% -> warn
    assert t["disk"].value == "90%" and "/home" in t["disk"].detail and t["disk"].status == "warn"
    assert t["load"].value == "0.40 0.30 0.20" and "0.10 per CPU" in t["load"].detail
    assert t["containers"].value == "1/1" and t["hosts"].value == "1/1"
    assert t["tmux"].value == "2 sess" and "1 waiting" in t["tmux"].detail
    assert t["pm"].value == "active" and t["ntfy"].value == "up" and t["ntfy24"].value == "7"
    key, label, value, status, detail = t["pm"][:5]
    assert status == "ok"
    ok = servers.summarize(_panel(metrics=tuple(m for m in _panel().metrics if m.label != "Disk /home")))
    assert ok.status == "ok"


def test_summarize_worst_is_crit_and_boundaries():
    assert servers.level(80, servers.DISK_WARN, servers.DISK_CRIT) == "warn"
    p = _panel(metrics=(_m("Memory", "x (95%)", 95.0, servers.level(95.0, servers.MEM_WARN, servers.MEM_CRIT)),))
    s = servers.summarize(p)
    assert s.status == "crit" and _by(s)["memory"].status == "crit"


def test_summarize_docker_unavailable_and_missing_is_na():
    s = servers.summarize(servers.Panel(time.time()))
    t = _by(s)
    assert s.status == "ok"
    for k in ("load", "memory", "swap", "disk", "uptime", "logins", "containers", "tmux", "hosts", "pm"):
        assert t[k].value == "n/a" and t[k].status == "na", k
    assert "ntfy" not in t and "ntfy24" not in t


def test_summarize_containers_and_hosts_problems():
    cs = (
        servers.Container("bad", "i", "running", "Up (unhealthy)", "unhealthy", ""),
        servers.Container("gone", "i", "exited", "Exited (1) 2 hours ago", "", ""),
    )
    hosts = (servers.HostCheck("rebox", "h", 22, "", False, None), servers.HostCheck("uatbox", "h", 22, "", True, 1.0))
    s = servers.summarize(_panel(docker=servers.DockerInfo(True, cs, 1, 2, 1), hosts=hosts))
    t = _by(s)
    assert s.status == "crit"
    assert t["containers"].status == "crit" and "bad" in t["containers"].detail and "gone" in t["containers"].detail
    assert t["hosts"].value == "1/2" and "rebox" in t["hosts"].detail and t["hosts"].status == "crit"
    exited = servers.DockerInfo(True, cs[1:], 0, 1, 0)
    assert _by(servers.summarize(_panel(docker=exited)))["containers"].status == "warn"


def test_summarize_never_raises():
    bad = servers.Panel(time.time(), metrics=(_m("Load", "garbage"),))
    assert _by(servers.summarize(bad))["load"].status == "na"


def test_libvirt_uses_system_connection_not_per_user_session():
    # `virsh` defaults to qemu:///session for this user, which lists 0 guests while the
    # lab guests live under qemu:///system (regression: Servers tab showed 0 guests).
    from pm.infra import servers

    seen = []

    def run(argv, timeout):
        seen.append(argv)
        return 0, " Id   Name   State\n------------------\n 1    a      running\n -    b      shut off\n"

    lv = servers.collect_libvirt(run=run, which=lambda n: "/usr/bin/virsh")
    assert seen[0][:4] == ["virsh", "-r", "-c", "qemu:///system"]
    assert (lv.running, lv.total) == (1, 2)


def test_clean_exit_container_is_not_a_health_problem():
    done = servers.Container("init", "i", "exited", "Exited (0) 3 days ago", "", "")
    up = servers.Container("web", "i", "running", "Up 1 hour", "", "")
    s = servers.summarize(_panel(docker=servers.DockerInfo(True, (up, done), 1, 2, 0)))
    t = _by(s)
    assert t["containers"].status == "ok" and t["containers"].value == "2/2"
    assert "ignored" in t["containers"].detail
    assert done.clean_exit and not done.problem
    assert servers._container_rank(done) > servers._container_rank(up)


def test_filter_info():
    from pm.infra import libvirt as lv
    d = lambda n: lv.Domain(n, "running", 1, 1, False, True, False)  # noqa: E731
    v = lambda n, u: lv.Volume(n, "/x/" + n, "file", 1, 1, u)  # noqa: E731
    info = lv.VmInfo(1.0, domains=(d("lab-1"), d("Lab-2"), d("prod")),
                     volumes=(v("a", ("lab-1", "prod")), v("b", ("prod",)), v("c", ())),
                     pool=lv.Pool("images", 10, 5, 5))
    r = lv.filter_info(info, ("lab-*",))
    assert [x.name for x in r.domains] == ["lab-1"] and [x.name for x in r.volumes] == ["a"]
    assert r.pool is None and r.ts == 1.0
    assert [x.name for x in lv.filter_info(info, ("pr?d", "[Ll]ab-2")).domains] == ["Lab-2", "prod"]
    assert lv.filter_info(info, ()).domains == () and lv.filter_info(info, ("zz",)).volumes == ()


def test_summary_tiles_and_level_bars_have_one_css_copy():
    import re

    css = Path(__file__).resolve().parents[1] / "src/pm/static/css"
    for name in ("chrome.css", "data.css", "base.css", "rows.css", "nav.css"):
        t = (css / name).read_text()
        rules = re.findall(r"([^{}]+)\{", re.sub(r"/\*.*?\*/", "", t, flags=re.S))
        assert not [r for r in rules if ".sum-" in r or ".srv-bar" in r], name
    own = (css / "servers.css").read_text()
    assert ".sum-tile {" in own and ".sum-badge::before" in own and ".srv-bar::-webkit-progress-value" in own
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", own)  # tokens only (--lvl-ok, --waiting, --failed)
    assert "var(--lvl-ok)" in own


def test_tile_markup_is_one_macro():
    tpl = TEMPLATE_DIR
    for name in ("_servers.html", "_resources.html"):
        t = (tpl / name).read_text()
        assert "sum_tile(t," in t and 'class="sum-tile' not in t, name
