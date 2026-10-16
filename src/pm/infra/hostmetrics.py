"""This host's metrics for the servers dashboard: /proc, statvfs, utmp, pm unit, tmux server.

Argv-only and read-only like ``servers``; missing sources are skipped. Imports the model,
never ``servers`` (which re-exports ``collect_host``).
"""

from __future__ import annotations

import os
import shutil
import socket
import time
from collections.abc import Callable
from pathlib import Path

from pm.core.config import Settings
from pm.infra.servers_model import (
    CMD_TIMEOUT_S,
    DISK_CRIT,
    DISK_WARN,
    LOAD_CRIT,
    LOAD_WARN,
    MEM_CRIT,
    MEM_WARN,
    SWAP_CRIT,
    SWAP_WARN,
    Metric,
    Runner,
    clean,
    default_runner,
    fmt_bytes,
    fmt_duration,
    level,
)
from pm.tmux import ctl as tmuxctl


def _read(p: Path) -> str:
    return p.read_text(errors="replace")


def _meminfo(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for ln in text.splitlines():
        k, _, rest = ln.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            out[k] = int(parts[0]) * 1024
    return out


def _logged_in(utmp: Path) -> int | None:
    try:
        data = utmp.read_bytes()[:1_000_000]
    except OSError:
        return None
    rec = 384  # struct utmp on 64-bit glibc
    n = 0
    for off in range(0, len(data) - rec + 1, rec):
        if int.from_bytes(data[off : off + 2], "little") == 7:  # USER_PROCESS
            n += 1
    return n


def _proc_age(proc: Path, uptime_s: float) -> float | None:
    try:
        stat = _read(proc / "self/stat")
        fields = stat[stat.rindex(")") + 2 :].split()
        start_ticks = int(fields[19])  # field 22 overall
        return uptime_s - start_ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None


def _disk(label: str, path: str, usage=shutil.disk_usage) -> Metric | None:
    try:
        u = usage(path)
    except OSError:
        return None
    pct = u.used / u.total * 100 if u.total else 0.0
    return Metric(
        label, f"{fmt_bytes(u.used)} / {fmt_bytes(u.total)} ({pct:.0f}%)", pct,
        level(pct, DISK_WARN, DISK_CRIT),
    )


def _same_mount(a: str, b: str) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return True


def _kernel_uptime(proc: Path) -> tuple[float, list[Metric]]:
    """Kernel always; Uptime when /proc/uptime reads. Returns (uptime_s, 0.0 = unknown)."""
    out = [Metric("Kernel", clean(os.uname().release, 64))]
    try:
        uptime_s = float(_read(proc / "uptime").split()[0])
        out.append(Metric("Uptime", fmt_duration(uptime_s)))
    except (OSError, ValueError, IndexError):
        return 0.0, out
    return uptime_s, out


def _load(proc: Path, cpus: int | None) -> list[Metric]:
    try:
        l1, l5, l15 = (float(x) for x in _read(proc / "loadavg").split()[:3])
        n = max(1, cpus or os.cpu_count() or 1)
        ratio = l1 / n
        return [Metric(
            "Load", f"{l1:.2f} {l5:.2f} {l15:.2f} on {n} CPU", min(100.0, ratio * 100),
            level(ratio, LOAD_WARN, LOAD_CRIT),
        )]
    except (OSError, ValueError):
        return []


def _memory_swap(proc: Path) -> list[Metric]:
    out: list[Metric] = []
    try:
        mi = _meminfo(_read(proc / "meminfo"))
        total = mi["MemTotal"]
        used = total - mi.get("MemAvailable", mi.get("MemFree", 0))
        pct = used / total * 100 if total else 0.0
        out.append(Metric(
            "Memory", f"{fmt_bytes(used)} / {fmt_bytes(total)} ({pct:.0f}%)", pct,
            level(pct, MEM_WARN, MEM_CRIT),
        ))
        st = mi.get("SwapTotal", 0)
        if st:
            su = st - mi.get("SwapFree", 0)
            sp = su / st * 100
            out.append(Metric(
                "Swap", f"{fmt_bytes(su)} / {fmt_bytes(st)} ({sp:.0f}%)", sp,
                level(sp, SWAP_WARN, SWAP_CRIT),
            ))
    except (OSError, KeyError):
        pass
    return out


def _disks(settings: Settings, usage, same: Callable[[str, str], bool]) -> list[Metric]:
    """/ and /home, plus dev_root when it is on neither mount."""
    found = [_disk(lbl, path, usage) for lbl, path in (("Disk /", "/"), ("Disk /home", "/home"))]
    dev = str(settings.dev_root)
    if not same(dev, "/home") and not same(dev, "/"):
        found.append(_disk(f"Disk {clean(dev, 60)}", dev, usage))
    return [m for m in found if m]


def _pm_service(run: Runner, proc: Path, uptime_s: float) -> Metric:
    rc, out = run(["systemctl", "--user", "is-active", "pm"], CMD_TIMEOUT_S)
    state = clean(out.strip().splitlines()[0], 20) if out.strip() else ("unknown" if rc else "active")
    age = _proc_age(proc, uptime_s) if uptime_s else None
    return Metric(
        "pm service", state + (f", up {fmt_duration(age)}" if age is not None else ""),
        None, "ok" if state == "active" else "crit",
    )


def _tmux_server(settings: Settings) -> Metric:
    try:
        out = tmuxctl.run(settings, ["display-message", "-p", "#{version} #{start_time}"],
                          timeout=CMD_TIMEOUT_S)
    except tmuxctl.TmuxError:
        return Metric("tmux server", "not running", None, "warn")
    ver, _, start = out.strip().partition(" ")
    text = f"tmux {clean(ver, 20)}"
    if start.strip().isdigit():
        text += f", up {fmt_duration(time.time() - int(start))}"
    return Metric("tmux server", text)


def collect_host(
    settings: Settings,
    *,
    proc: Path = Path("/proc"),
    utmp: Path = Path("/run/utmp"),
    run: Runner = default_runner,
    usage=shutil.disk_usage,
    cpus: int | None = None,
    same_mount: Callable[[str, str], bool] | None = None,
) -> tuple[str, tuple[Metric, ...]]:
    """Hostname + metrics from /proc, statvfs, utmp. Missing sources are skipped."""
    host = clean(socket.gethostname(), 64)
    uptime_s, metrics = _kernel_uptime(proc)
    metrics += _load(proc, cpus)
    metrics += _memory_swap(proc)
    metrics += _disks(settings, usage, same_mount or _same_mount)
    n_sessions = _logged_in(utmp)
    if n_sessions is not None:
        metrics.append(Metric("Logins", str(n_sessions)))
    metrics.append(_pm_service(run, proc, uptime_s))
    metrics.append(_tmux_server(settings))
    return host, tuple(metrics)
