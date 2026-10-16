import time
from pathlib import Path

from pm.infra import resources as rs
from pm.infra import servers
from pm.infra.resources import Proc, Resources, Row


def _stat(pid, comm, ppid, ticks, rss_pages):
    f = ["S", str(ppid)] + ["0"] * 9 + [str(ticks), "0"] + ["0"] * 8 + [str(rss_pages)]
    return f"{pid} ({comm}) " + " ".join(f)


def _proc(tmp_path, procs):
    for pid, comm, ppid, ticks, rss in procs:
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "stat").write_text(_stat(pid, comm, ppid, ticks, rss))
    return tmp_path


def test_scan_parses_comm_with_spaces_and_parens(tmp_path):
    _proc(tmp_path, [(5, "my (odd) prog", 1, 40, 10)])
    p = rs.scan(tmp_path)[5]
    assert p.comm == "my (odd) prog" and p.ppid == 1 and p.ticks == 40 and p.rss == 10 * rs._PAGE


def test_programs_and_tmux_aggregate():
    cur = {1: Proc(1, 0, "bash", 0, 100), 2: Proc(2, 1, "claude", 0, 300),
           3: Proc(3, 1, "claude", 0, 200), 9: Proc(9, 0, "other", 0, 50)}
    cpu = {2: 10.0, 3: 5.0}
    progs = {r.name: r for r in rs.programs_rows(cur, cpu)}
    assert progs["claude"].procs == 2 and progs["claude"].rss == 500 and progs["claude"].cpu == 15.0
    t = rs.tmux_rows(cur, cpu, [("main", 1), ("main", 9)])
    assert len(t) == 1 and t[0].extra == 2 and t[0].procs == 4 and t[0].rss == 650


def test_docker_rows_units_and_failure():
    out = '{"Name":"a","CPUPerc":"1.50%","MemUsage":"12MiB / 31GiB"}\nnot json\n{"Name":"b","CPUPerc":"x","MemUsage":"2GB / 3GB"}'
    rows = rs.docker_rows(lambda a, t: (0, out))
    assert [(r.name, r.cpu, r.rss) for r in rows] == [("a", 1.5, 12 * 1024**2), ("b", 0.0, 2 * 10**9)]
    assert rs.docker_rows(lambda a, t: (1, "")) is None


def test_collect_cpu_delta_and_tmux(fake_tmux, monkeypatch, tmp_path):
    _proc(tmp_path, [(10, "bash", 1, 0, 5), (11, "vim", 10, 0, 5)])
    (tmp_path / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 400 kB\n")
    monkeypatch.setenv("FAKE_TMUX_OUT", "main\t10\n")
    prev = (0.0, rs.scan(tmp_path))
    (tmp_path / "11" / "stat").write_text(_stat(11, "vim", 10, 100, 5))  # 1 s of CPU
    res, _ = rs.collect(fake_tmux, run=lambda a, t: (1, ""), proc=tmp_path, prev=prev,
                        now=lambda: 2.0, sleep=lambda s: None)
    assert res.tmux[0].name == "main" and res.tmux[0].procs == 2
    assert abs(res.tmux[0].cpu - 50.0) < 0.1  # 1 s over 2 s
    assert res.mem_total == 1000 * 1024 and res.mem_used == 600 * 1024
    assert res.docker_ok is False


def test_summarize_tiles():
    r = Resources(time.time(), 4, 1000, 500, 100.0,
                  (Row("claude", 3, 80.0, 300), Row("x", 1, 20.0, 100)),
                  (Row("main", 4, 50.0, 200, 2),), (Row("c", 0, 10.0, 50),), ())
    t = {x.key: x for x in rs.summarize(r)}
    assert t["cpu"].value == "25%" and t["mem"].value == "50%"
    assert t["top-ram"].value == "claude" and t["top-cpu"].value == "claude"
    assert "vms" not in t and "tmux" in t and "containers" in t


def test_docker_swr_never_blocks_and_refreshes(monkeypatch):
    import threading
    gate = threading.Event()

    def slow(run):
        gate.wait(5)
        return [Row("a", 0, 1.0, 1)]

    monkeypatch.setattr(rs, "docker_rows", slow)
    monkeypatch.setattr(rs, "_docker", {"rows": None, "at": 0.0, "busy": False})
    t0 = time.monotonic()
    assert rs.docker_swr() == (None, True)  # first call: pending, returns at once
    assert time.monotonic() - t0 < 1
    gate.set()
    for _ in range(100):
        rows, pending = rs.docker_swr()
        if rows is not None:
            break
        time.sleep(0.05)
    assert rows[0].name == "a" and pending is False
