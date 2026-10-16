"""Per-process resource sampling from /proc (injectable proc_root for tests)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

PROC = Path("/proc")


def _clk_tck() -> int:
    try:
        return int(os.sysconf("SC_CLK_TCK"))
    except (ValueError, OSError):
        return 100


def _page_size() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE"))
    except (ValueError, OSError):
        return 4096


@dataclass(frozen=True)
class ProcStat:
    pid: int
    ppid: int
    comm: str
    utime: int  # clock ticks
    stime: int
    starttime: int  # clock ticks since boot (stat field 22)
    rss_pages: int


def parse_stat(raw: str) -> ProcStat | None:
    """Parse /proc/<pid>/stat; comm may contain spaces/parens, so split on the last ')'."""
    try:
        lp = raw.index("(")
        rp = raw.rindex(")")
        pid = int(raw[:lp].strip())
        comm = raw[lp + 1 : rp]
        f = raw[rp + 2 :].split()
        # f[0] is field 3 (state)
        return ProcStat(
            pid=pid,
            ppid=int(f[1]),
            comm=comm,
            utime=int(f[11]),
            stime=int(f[12]),
            starttime=int(f[19]),
            rss_pages=int(f[21]),
        )
    except (ValueError, IndexError):
        return None


def read_proc_stat(pid: int, proc_root: Path = PROC) -> ProcStat | None:
    try:
        return parse_stat((proc_root / str(pid) / "stat").read_text(errors="replace"))
    except OSError:
        return None


def _uptime(proc_root: Path) -> float | None:
    try:
        return float((proc_root / "uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def sample_once(
    pid: int,
    *,
    proc_root: Path = PROC,
    clk_tck: int | None = None,
    page_size: int | None = None,
) -> tuple[float, float, float] | None:
    """Return (cpu_pct since start, rss_mb, age_s) or None if the process is gone."""
    st = read_proc_stat(pid, proc_root)
    if st is None:
        return None
    tck = clk_tck or _clk_tck()
    ps = page_size or _page_size()
    up = _uptime(proc_root)
    age = max(0.0, up - st.starttime / tck) if up is not None else 0.0
    cpu = ((st.utime + st.stime) / tck) / age * 100.0 if age > 0 else 0.0
    return round(cpu, 1), round(st.rss_pages * ps / 1048576, 1), round(age, 1)


class ResourceSampler:
    """Keeps the previous (cpu ticks, time) per pid so cpu_pct is a delta between polls.

    The first sample of a pid falls back to the since-start average.
    """

    def __init__(
        self,
        *,
        proc_root: Path = PROC,
        clk_tck: int | None = None,
        page_size: int | None = None,
        clock=time.monotonic,
    ) -> None:
        self.proc_root = proc_root
        self.clk_tck = clk_tck or _clk_tck()
        self.page_size = page_size or _page_size()
        self._clock = clock
        self._prev: dict[int, tuple[int, int, float]] = {}  # pid -> (ticks, starttime, t)

    def sample(self, pid: int) -> tuple[float, float, float] | None:
        st = read_proc_stat(pid, self.proc_root)
        if st is None:
            self._prev.pop(pid, None)
            return None
        now = self._clock()
        ticks = st.utime + st.stime
        prev = self._prev.get(pid)
        self._prev[pid] = (ticks, st.starttime, now)
        up = _uptime(self.proc_root)
        age = max(0.0, up - st.starttime / self.clk_tck) if up is not None else 0.0
        if prev and prev[1] == st.starttime and now > prev[2] and ticks >= prev[0]:
            cpu = (ticks - prev[0]) / self.clk_tck / (now - prev[2]) * 100.0
        else:
            cpu = (ticks / self.clk_tck) / age * 100.0 if age > 0 else 0.0
        rss = st.rss_pages * self.page_size / 1048576
        return round(cpu, 1), round(rss, 1), round(age, 1)

    def forget_except(self, pids: set[int]) -> None:
        for p in list(self._prev):
            if p not in pids:
                del self._prev[p]
