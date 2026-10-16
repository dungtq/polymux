"""Per-program / tmux / container / VM CPU and RAM for the Servers "Resources" tab.

Read-only: /proc for processes, ``tmux list-panes`` for the session -> process mapping and
``docker stats --no-stream`` for containers (argv lists, fixed binaries, timeouts, no shell).
CPU% is a delta between two samples of utime+stime (100 % = one core).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from pm.core import executors
from pm.core.config import Settings
from pm.infra.servers import CMD_TIMEOUT_S, Runner, Tile, _meminfo, clean, default_runner, level
from pm.tmux import ctl as tmuxctl

CACHE_TTL_S = 8.0
DOCKER_TTL_S = 15.0  # `docker stats --no-stream` takes ~1.5 s: refreshed in the background
BASELINE_GAP_S = 0.5  # first sample waits this long to get a CPU delta
BASELINE_MAX_AGE_S = 60.0  # an older previous sample is no longer a useful baseline
MAX_PROCS = 6000
TOP_PROGRAMS = 15
DOCKER_STATS_TIMEOUT_S = 8.0
_CLK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
_PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
_GUEST_RE = re.compile(rb"guest=([^,\x00]{1,64})")
_UNITS = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12,
          "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4}


class Proc(NamedTuple):
    pid: int
    ppid: int
    comm: str
    ticks: int
    rss: int  # bytes
    state: str = ""  # R S D T Z ...; T = paused (SIGSTOP)
    start: int = 0  # starttime in clock ticks since boot: pid + start identify a process (pid reuse)


@dataclass(frozen=True)
class Row:
    name: str
    procs: int  # processes (programs, tmux, vms); 0 for containers
    cpu: float  # percent of one core
    rss: int  # bytes
    extra: int = 0  # tmux: panes
    items: tuple = ()  # programs: (pid, start, rss, cpu, state) of the biggest processes, for stop/kill


@dataclass(frozen=True)
class Resources:
    ts: float
    ncpu: int
    mem_total: int
    mem_used: int
    cpu_total: float  # all processes, percent of one core
    programs: tuple[Row, ...] = ()
    tmux: tuple[Row, ...] = ()
    containers: tuple[Row, ...] = ()
    vms: tuple[Row, ...] = ()
    docker_ok: bool = True
    tmux_ok: bool = True
    docker_pending: bool = False  # first docker stats still running; containers fill in next poll


# comm values that name a thread or an interpreter, not the program: node's main thread is called
# "MainThread" (set by its runtime), so every Node tool in the list looked like one program.
_GENERIC_COMM = frozenset({"MainThread"})


def _alias(pid: int, comm: str, proc: Path) -> str:
    """"MainThread" -> "node exa-mcp-server" (interpreter + script basename from cmdline)."""
    try:
        argv = [a for a in (proc / str(pid) / "cmdline").read_bytes()[:4096].split(b"\0") if a]
    except OSError:
        return comm
    if not argv:
        return comm
    exe = os.path.basename(argv[0].decode("utf-8", "replace"))
    script = next((os.path.basename(a.decode("utf-8", "replace")) for a in argv[1:] if not a.startswith(b"-")), "")
    return clean(f"{exe} {script}".strip() if script else exe, 40) or comm


def scan(proc: Path = Path("/proc")) -> dict[int, Proc]:
    out: dict[int, Proc] = {}
    try:
        names = os.listdir(proc)
    except OSError:
        return out
    for n in names:
        if not n.isdigit():
            continue
        try:
            raw = (proc / n / "stat").read_text(errors="replace")
        except OSError:
            continue  # exited meanwhile
        lp, rp = raw.find("("), raw.rfind(")")
        if lp < 0 or rp < lp:
            continue
        f = raw[rp + 2:].split()
        try:
            comm = clean(raw[lp + 1:rp], 40)
            if comm in _GENERIC_COMM:
                comm = _alias(int(n), comm, proc)
            out[int(n)] = Proc(int(n), int(f[1]), comm, int(f[11]) + int(f[12]), int(f[21]) * _PAGE,
                               f[0], int(f[19]))
        except (IndexError, ValueError):
            continue
        if len(out) >= MAX_PROCS:
            break
    return out


def _guest(pid: int, proc: Path) -> str:
    try:
        m = _GUEST_RE.search((proc / str(pid) / "cmdline").read_bytes()[:4096])
    except OSError:
        return ""
    return clean(m[1].decode("utf-8", "replace"), 64) if m else ""


def _cpu(cur: Proc, prev: dict[int, Proc], dt: float) -> float:
    p = prev.get(cur.pid)
    if p is None or p.comm != cur.comm or dt <= 0:
        return 0.0
    return max(0, cur.ticks - p.ticks) / _CLK / dt * 100


def _tree(root: int, kids: dict[int, list[int]]) -> list[int]:
    seen, stack = [], [root]
    while stack and len(seen) < MAX_PROCS:
        pid = stack.pop()
        seen.append(pid)
        stack.extend(kids.get(pid, ()))
    return seen


ITEMS_PER_PROGRAM = 12


def programs_rows(cur: dict[int, Proc], cpu: dict[int, float]) -> list[Row]:
    agg: dict[str, list] = defaultdict(lambda: [0, 0.0, 0, []])
    for p in cur.values():
        a = agg[p.comm]
        a[0] += 1
        a[1] += cpu.get(p.pid, 0.0)
        a[2] += p.rss
        a[3].append((p.pid, p.start, p.rss, cpu.get(p.pid, 0.0), p.state))
    return [Row(k, v[0], v[1], v[2], 0, tuple(sorted(v[3], key=lambda i: -i[2])[:ITEMS_PER_PROGRAM]))
            for k, v in agg.items()]


def describe(pid: int, start: int, proc: Path = Path("/proc")) -> str:
    """"node exa-mcp-server, 2d": what a pid is (exe + script, no flags: they can carry keys) and its age."""
    what = _alias(pid, "", proc)
    try:
        up = float((proc / "uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return what
    age = max(0, int(up - start / _CLK))
    n, u = (age // 86400, "d") if age >= 86400 else (age // 3600, "h") if age >= 3600 else (age // 60, "m")
    return f"{what}, {n}{u}" if what else f"{n}{u}"


def tmux_rows(cur: dict[int, Proc], cpu: dict[int, float], panes: list[tuple[str, int]]) -> list[Row]:
    kids: dict[int, list[int]] = defaultdict(list)
    for p in cur.values():
        kids[p.ppid].append(p.pid)
    agg: dict[str, list] = defaultdict(lambda: [0, 0.0, 0, 0])
    for name, pid in panes:
        a = agg[name]
        a[3] += 1
        for q in _tree(pid, kids):
            pr = cur.get(q)
            if pr:
                a[0] += 1
                a[1] += cpu.get(q, 0.0)
                a[2] += pr.rss
    return [Row(k, v[0], v[1], v[2], v[3]) for k, v in agg.items()]


def vm_rows(cur: dict[int, Proc], cpu: dict[int, float], proc: Path) -> list[Row]:
    return [Row(_guest(p.pid, proc) or f"qemu-{p.pid}", 1, cpu.get(p.pid, 0.0), p.rss)
            for p in cur.values() if p.comm.startswith("qemu-system")]


def _bytes(s: str) -> int:
    m = re.match(r"^\s*([\d.]+)\s*([A-Za-z]*)", s)
    if not m:
        return 0
    try:
        return int(float(m[1]) * _UNITS.get(m[2].upper() or "B", 1))
    except ValueError:
        return 0


def docker_rows(run: Runner) -> list[Row] | None:
    rc, out = run(["docker", "stats", "--no-stream", "--format", "{{json .}}"], DOCKER_STATS_TIMEOUT_S)
    if rc != 0:
        return None
    rows: list[Row] = []
    for ln in out.splitlines()[:200]:
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict):
            continue
        try:
            c = float(str(d.get("CPUPerc", "0")).rstrip("%") or 0)
        except ValueError:
            c = 0.0
        rows.append(Row(clean(d.get("Name", ""), 80), 0, c, _bytes(str(d.get("MemUsage", "")).split("/")[0])))
    return rows


def _mem(proc: Path) -> tuple[int, int]:
    try:
        mi = _meminfo((proc / "meminfo").read_text())
        total = mi["MemTotal"]
        return total, total - mi.get("MemAvailable", mi.get("MemFree", 0))
    except (OSError, KeyError):
        return 0, 0


def collect(settings: Settings, *, run: Runner = default_runner, proc: Path = Path("/proc"),
            prev: tuple[float, dict[int, Proc]] | None = None, sleep=time.sleep,
            now=time.monotonic, docker_fn=None) -> tuple[Resources, tuple[float, dict[int, Proc]]]:
    """Returns the snapshot and the (time, procs) sample to use as the next baseline."""
    if prev is None or now() - prev[0] > BASELINE_MAX_AGE_S:
        prev = (now(), scan(proc))
        sleep(BASELINE_GAP_S)
    t1 = now()
    cur = scan(proc)
    dt = t1 - prev[0]
    cpu = {pid: _cpu(p, prev[1], dt) for pid, p in cur.items()}

    panes: list[tuple[str, int]] = []
    tmux_ok = True
    try:
        out = tmuxctl.run(settings, ["list-panes", "-a", "-F", "#{session_name}\t#{pane_pid}"],
                          timeout=CMD_TIMEOUT_S)
        for ln in out.splitlines():
            name, _, pid = ln.rpartition("\t")
            if pid.isdigit() and not tmuxctl.VIEW_RE.fullmatch(name):
                panes.append((clean(name, 64), int(pid)))
    except tmuxctl.TmuxError:
        tmux_ok = False
    pending = False
    if docker_fn is None:
        docker = docker_rows(run)
    else:
        docker, pending = docker_fn()
    total, used = _mem(proc)
    key = lambda r: (-r.rss, r.name)  # noqa: E731
    res = Resources(
        time.time(), max(1, os.cpu_count() or 1), total, used, sum(cpu.values()),
        tuple(sorted(programs_rows(cur, cpu), key=key)), tuple(sorted(tmux_rows(cur, cpu, panes), key=key)),
        tuple(sorted(docker or (), key=key)), tuple(sorted(vm_rows(cur, cpu, proc), key=key)),
        docker is not None or pending, tmux_ok, pending)
    return res, ((t1, cur) if dt >= 1.0 else prev)


# ---------------------------------------------------------------- cache


_lock = threading.Lock()
_state: dict = {"res": None, "at": 0.0, "sample": None}
_dlock = threading.Lock()
_docker: dict = {"rows": None, "at": 0.0, "busy": False}


def _docker_refresh() -> None:
    try:
        rows = docker_rows(default_runner)
    except Exception:  # noqa: BLE001 - keep the last good rows
        rows = None
    with _dlock:
        _docker.update(rows=rows, at=time.monotonic(), busy=False, seen=True)


def docker_swr() -> tuple[list[Row] | None, bool]:
    """(rows, pending): the last docker stats at once; a stale one starts a background refresh,
    so the Resources tab never waits ~1.5 s for docker. pending = nothing collected yet."""
    with _dlock:
        rows, seen = _docker["rows"], _docker.get("seen", False)
        if not _docker["busy"] and (not seen or time.monotonic() - _docker["at"] >= DOCKER_TTL_S):
            _docker["busy"] = True
            executors.collectors.submit(_docker_refresh)
        return rows, not seen


def get(settings: Settings, *, force: bool = False) -> Resources:
    """Cached for CACHE_TTL_S; the lock makes concurrent callers share one collection."""
    with _lock:
        if not force and _state["res"] is not None and time.monotonic() - _state["at"] < CACHE_TTL_S:
            return _state["res"]
        res, sample = collect(settings, prev=_state["sample"], docker_fn=docker_swr)
        _state.update(res=res, at=time.monotonic(), sample=sample)
        return res


def invalidate() -> None:
    """Drop the caches so the next poll shows the effect of a kill / stop."""
    with _dlock:
        _docker["at"] = 0.0
    _state["at"] = 0.0


def sort_rows(rows, key: str):
    if key == "cpu":
        return sorted(rows, key=lambda r: (-r.cpu, r.name))
    if key == "name":
        return sorted(rows, key=lambda r: r.name.lower())
    return sorted(rows, key=lambda r: (-r.rss, r.name))


def fmt_cpu(v: float) -> str:
    return f"{v:.0f}%" if v >= 10 else f"{v:.1f}%"


def _sum(rows) -> tuple[float, int]:
    return sum(r.cpu for r in rows), sum(r.rss for r in rows)


def summarize(r: Resources) -> tuple[Tile, ...]:
    """Pure: summary tiles. CPU is % of the whole machine, RAM is share of total RAM."""
    from pm.infra.servers import fmt_bytes

    def share(cpu: float, rss: int) -> str:
        mp = rss / r.mem_total * 100 if r.mem_total else 0.0
        return f"{fmt_bytes(rss)} RAM ({mp:.0f}%), {fmt_cpu(cpu / r.ncpu)} CPU"

    tiles: list[Tile] = []
    cp = min(100.0, r.cpu_total / r.ncpu)
    tiles.append(Tile("cpu", "CPU", f"{cp:.0f}%", level(cp / 100, 0.7, 0.9),
                      f"{fmt_cpu(r.cpu_total)} of {r.ncpu * 100}% ({r.ncpu} cores)", cp))
    mp = r.mem_used / r.mem_total * 100 if r.mem_total else 0.0
    tiles.append(Tile("mem", "Memory", f"{mp:.0f}%", level(mp / 100, 0.8, 0.92),
                      f"{fmt_bytes(r.mem_used)} / {fmt_bytes(r.mem_total)}", mp))
    if r.tmux_ok:
        c, m = _sum(r.tmux)
        tiles.append(Tile("tmux", "tmux", fmt_bytes(m), "ok",
                          f"{len(r.tmux)} sessions: " + share(c, m)))
    if r.docker_ok and not r.docker_pending:
        c, m = _sum(r.containers)
        tiles.append(Tile("containers", "Containers", fmt_bytes(m), "ok",
                          f"{len(r.containers)} running: " + share(c, m)))
    if r.vms:
        c, m = _sum(r.vms)
        tiles.append(Tile("vms", "VMs", fmt_bytes(m), "ok", f"{len(r.vms)} qemu: " + share(c, m)))
    if r.programs:
        top = r.programs[0]
        tiles.append(Tile("top-ram", "Top RAM", top.name, "ok",
                          f"{fmt_bytes(top.rss)}, {top.procs} proc"))
        tc = max(r.programs, key=lambda x: x.cpu)
        tiles.append(Tile("top-cpu", "Top CPU", tc.name, "ok", f"{fmt_cpu(tc.cpu)} of one core"))
    return tuple(tiles)
