"""Server health monitor: samples this host, evaluates with hysteresis, publishes to ntfy.

`collect` reads the live host, `evaluate` is a pure state machine (readings in,
decisions out), `run_cycle` glues them with publishing, `start`/`stop` own the
background task. Messages carry a metric name, value and threshold only: no env,
command lines, file names (other than mount points) or pane text. Failures are
logged by exception type and never escape the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pm.core import executors
from pm.core.config import Settings
from pm.core.fsutil import JsonStore
from pm.infra import servers
from pm.notify import ntfy as notify
from pm.tmux import ctl as tmuxctl

log = logging.getLogger(__name__)

MAX_PER_CYCLE = 5
CLEAR_MARGIN = 3.0  # percentage points below the trigger before a metric counts as recovered
LOAD_CLEAR_FACTOR = 0.85
INODE_PCT = 95.0
PRIO_CRIT = "4"
PRIO_RECOVERY = "3"
FAILED = "_failed"  # readings key: frozenset of groups whose collector failed this sample
_SAFE = re.compile(r"[^A-Za-z0-9._/:@ %-]")
_BAD_STATES = {"dead", "restarting", "removing"}
_EXIT_RE = re.compile(r"Exited \((\d+)\)")


def _safe(s: object, limit: int = 60) -> str:
    return _SAFE.sub("", str(s))[:limit]


@dataclass(frozen=True)
class Reading:
    key: str
    label: str  # "disk /home"
    value: float  # bad when >= trigger, recovered when < clear_at
    trigger: float
    clear_at: float
    text: str  # "93%"
    limit: str  # "92%"

    @property
    def group(self) -> str:
        return self.key.split(":", 1)[0]


@dataclass
class KeyState:
    bad_n: int = 0
    good_n: int = 0
    alerted: bool = False
    last_sent: float = 0.0
    last: Reading | None = None


@dataclass(frozen=True)
class Alert:
    key: str
    kind: str  # "alert" | "recovery" | "summary"
    title: str
    message: str
    priority: str
    tags: tuple[str, ...]


@dataclass
class AlertState:
    keys: dict[str, KeyState] = field(default_factory=dict)

    def alerted(self) -> dict[str, float]:
        return {k: s.last_sent for k, s in self.keys.items() if s.alerted}


# ---------------------------------------------------------------- persistence
def state_path(settings: Settings) -> Path:
    return settings.state_dir / "alerts.json"


def _store(settings: Settings) -> JsonStore:
    return JsonStore(state_path(settings), dict, sort_keys=True, mode=0o600,
                     validate=lambda d: isinstance(d, dict) and isinstance(d.get("alerted", {}), dict))


def load_state(settings: Settings) -> AlertState:
    st = AlertState()
    for k, ts in _store(settings).load().get("alerted", {}).items():
        if isinstance(k, str) and isinstance(ts, (int, float)) and not isinstance(ts, bool):
            st.keys[k[:120]] = KeyState(alerted=True, last_sent=float(ts))
    return st


def save_state(settings: Settings, state: AlertState) -> None:
    state_path(settings).parent.mkdir(parents=True, exist_ok=True)
    _store(settings).save({"version": 1, "alerted": state.alerted()})


# ---------------------------------------------------------------- evaluation
def _hostname(settings: Settings) -> str:
    name = settings.alerts_host or socket.gethostname().split(".")[0]
    return _safe(name, 40) or "host"


ALERT_HEAD = "pm alert -- "  # "pm alert -- <host>: <label> <value>" (_limit)
RECOVERED_HEAD = "pm recovered -- "  # "pm recovered -- <host>: <label>"


def _limit(settings: Settings, r: Reading, host: str, kind: str) -> Alert:
    link = f"{settings.base_url.rstrip('/')}/servers"
    if kind == "recovery":  # recovered_after pairs the two titles
        return Alert(r.key, kind, f"{RECOVERED_HEAD}{host}: {r.label}"[:80],
                     f"{r.label} is back to normal ({r.text}). See {link}"[:120],
                     PRIO_RECOVERY, (str(settings.dev_root / "pm"), "white_check_mark"))
    return Alert(r.key, kind, f"{ALERT_HEAD}{host}: {r.label} {r.text}"[:80],
                 (f"{r.label} {r.text}, limit {r.limit}. See {link}" if r.limit != "ok"
                      else f"{r.label} {r.text}. See {link}")[:120],
                 PRIO_CRIT, (str(settings.dev_root / "pm"), "warning"))


def recovered_after(items, ids=None) -> dict[str, float]:
    """{id of a "pm recovered -- <host>: <label>" ntfy item: seconds since the first "pm alert -- <host>: <label> ..."
    of that incident (after the previous recovery of the same subject)}. Recoveries without an alert in ``items``
    are left out; ``ids`` (optional) limits the answer to those items (nothing to pair: no scan of the rest)."""
    want = None if ids is None else set(ids)
    rel = [i for i in items if (i.title or "").startswith((ALERT_HEAD, RECOVERED_HEAD))]
    if want is not None and not any(i.id in want and i.title.startswith(RECOVERED_HEAD) for i in rel):
        return {}
    out: dict[str, float] = {}
    alerts: list[tuple[float, str]] = []  # (time, "<host>: <label> <value>") of the alerts seen so far
    since: dict[str, float] = {}  # subject -> time of its previous recovery
    for it in sorted(rel, key=lambda i: i.time):
        title = it.title or ""
        if title.startswith(ALERT_HEAD):
            alerts.append((it.time, title[len(ALERT_HEAD):]))
        elif title.startswith(RECOVERED_HEAD):
            subj = title[len(RECOVERED_HEAD):]
            after = since.get(subj, float("-inf"))
            first = min((t for t, rest in alerts if after < t <= it.time and rest.startswith(subj + " ")), default=None)
            if first is not None and (want is None or it.id in want):
                out[it.id] = it.time - first
            since[subj] = it.time
    return out


def evaluate(settings: Settings, readings: dict, state: AlertState, now: float) -> list[Alert]:
    """One sample: update `state` in place, return notifications to send (uncapped)."""
    host = _hostname(settings)
    sustain = max(1, settings.alerts_sustain)
    failed = readings.get(FAILED, frozenset())
    out: list[Alert] = []
    for key in readings:
        if key != FAILED:
            state.keys.setdefault(key, KeyState())
    for key in list(state.keys):
        s = state.keys[key]
        r = readings.get(key)
        group = key.split(":", 1)[0]
        if r is None and group in failed:
            continue  # collector failed: no information, hold state
        if r is not None:
            s.last = r
            bad, good = r.value >= r.trigger, r.value < r.clear_at
        else:
            bad, good = False, True  # vanished (container removed, host unlisted)
        if bad:
            s.bad_n, s.good_n = s.bad_n + 1, 0
            if s.bad_n >= sustain and (not s.alerted or now - s.last_sent >= settings.alerts_repeat_s):
                s.alerted, s.last_sent = True, now
                out.append(_limit(settings, r, host, "alert"))
        elif good:
            s.good_n, s.bad_n = s.good_n + 1, 0
            if s.alerted and s.good_n >= sustain:
                s.alerted = False
                if s.last is not None:
                    out.append(_limit(settings, s.last, host, "recovery"))
            if not s.alerted and r is None:
                del state.keys[key]
        else:  # dead band between clear and trigger: hold, do not count
            s.bad_n = s.good_n = 0
    return out


def cap(settings: Settings, alerts: list[Alert], limit: int = MAX_PER_CYCLE) -> list[Alert]:
    if len(alerts) <= limit:
        return alerts
    extra = len(alerts) - limit
    link = f"{settings.base_url.rstrip('/')}/servers"
    summary = Alert("_summary", "summary", f"{ALERT_HEAD}{_hostname(settings)}: {extra} more alerts",
                    f"{extra} more alerts or recoveries. See {link}"[:120], PRIO_CRIT,
                    (str(settings.dev_root / "pm"), "warning"))
    return [*alerts[:limit], summary]


# ---------------------------------------------------------------- collection
def _pct(label, key, value, trigger, unit="%"):
    clear = trigger - CLEAR_MARGIN
    return Reading(key, label, value, trigger, clear, f"{value:.0f}{unit}", f"{trigger:.0f}{unit}")


def _flag(key, label, bad: bool, text: str) -> Reading:
    return Reading(key, label, 1.0 if bad else 0.0, 1.0, 1.0, text, "ok")


def _mounts(settings: Settings) -> list[str]:
    paths = ["/", "/home", str(settings.dev_root), "/var/lib/docker"]
    seen: set[int] = set()
    out = []
    for p in paths:
        try:
            dev = os.stat(p).st_dev
        except OSError:
            continue
        if dev not in seen:
            seen.add(dev)
            out.append(p)
    return out


def _collect_system(settings: Settings, proc: Path, cpus: int | None) -> dict:
    r: dict = {}
    try:
        l5 = float((proc / "loadavg").read_text().split()[1])
        n = max(1, cpus or os.cpu_count() or 1)
        t = settings.alerts_load_per_cpu
        v = l5 / n
        r["load"] = Reading("load", "load (5m)", v, t, t * LOAD_CLEAR_FACTOR, f"{v:.2f}/cpu", f"{t:.2f}/cpu")
    except (OSError, ValueError, IndexError):
        pass
    try:
        mi: dict[str, int] = {}
        for ln in (proc / "meminfo").read_text().splitlines():
            k, _, rest = ln.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                mi[k] = int(parts[0])
        total = mi["MemTotal"]
        if total:
            used = total - mi.get("MemAvailable", mi.get("MemFree", 0))
            r["mem"] = _pct("memory", "mem", used / total * 100, settings.alerts_mem_pct)
        if mi.get("SwapTotal"):
            su = mi["SwapTotal"] - mi.get("SwapFree", 0)
            r["swap"] = _pct("swap", "swap", su / mi["SwapTotal"] * 100, settings.alerts_swap_pct)
    except (OSError, KeyError):
        pass
    for path in _mounts(settings):
        try:
            sv = os.statvfs(path)
        except OSError:
            continue
        if sv.f_blocks:
            used = (sv.f_blocks - sv.f_bfree) / (sv.f_blocks - sv.f_bfree + sv.f_bavail) * 100 \
                if (sv.f_blocks - sv.f_bfree + sv.f_bavail) else 0.0
            r[f"disk:{path}"] = _pct(f"disk {_safe(path)}", f"disk:{path}", used, settings.alerts_disk_pct)
        if sv.f_files:
            iu = (sv.f_files - sv.f_ffree) / sv.f_files * 100
            r[f"inode:{path}"] = Reading(f"inode:{path}", f"inodes {_safe(path)}", iu, INODE_PCT,
                                         INODE_PCT - CLEAR_MARGIN, f"{iu:.0f}%", f"{INODE_PCT:.0f}%")
    return r


def _container_bad(c) -> bool:
    if c.health == "unhealthy" or c.state in _BAD_STATES:
        return True
    if c.state == "exited":
        m = _EXIT_RE.search(c.status)
        return not m or m.group(1) != "0"  # clean exits (batch jobs) are not alerts
    return False


def _collect_containers(settings: Settings, docker) -> dict:
    out: dict = {}
    for c in docker.containers:
        name = _safe(c.name, 60)
        if not name:
            continue
        what = "unhealthy" if c.health == "unhealthy" else (c.state or "down")
        out[f"container:{name}"] = _flag(f"container:{name}", f"container {name}",
                                         _container_bad(c), _safe(what, 20))
    return out


def collect(settings: Settings, *, proc: Path = Path("/proc"), cpus: int | None = None,
            docker_fn: Callable = servers.collect_docker, hosts_fn: Callable = servers.check_hosts,
            tmux_fn: Callable | None = None) -> dict:
    """Live reading: {key: Reading}; FAILED holds groups whose collector could not run."""
    readings: dict = {}
    failed: set[str] = set()
    try:
        readings.update(_collect_system(settings, proc, cpus))
    except Exception as exc:  # noqa: BLE001
        log.warning("alerts collect system failed: %s", type(exc).__name__)
        failed.update({"load", "mem", "swap", "disk", "inode"})
    if settings.alerts_containers:
        try:
            info = docker_fn()
            if info.available:
                readings.update(_collect_containers(settings, info))
            else:
                failed.add("container")
        except Exception as exc:  # noqa: BLE001
            log.warning("alerts collect docker failed: %s", type(exc).__name__)
            failed.add("container")
    if settings.alerts_hosts:
        try:
            for h in hosts_fn(servers.host_targets(settings)):
                name = _safe(h.name, 40)
                readings[f"host:{name}"] = _flag(f"host:{name}", f"host {name}", not h.up, "unreachable")
        except Exception as exc:  # noqa: BLE001
            log.warning("alerts collect hosts failed: %s", type(exc).__name__)
            failed.add("host")
    try:
        (tmux_fn or (lambda: tmuxctl.run(settings, ["display-message", "-p", "#{version}"],
                                         timeout=servers.CMD_TIMEOUT_S)))()
        readings["tmux"] = _flag("tmux", "tmux server", False, "down")
    except tmuxctl.TmuxError:
        readings["tmux"] = _flag("tmux", "tmux server", True, "down")
    except Exception as exc:  # noqa: BLE001
        log.warning("alerts collect tmux failed: %s", type(exc).__name__)
        failed.add("tmux")
    readings[FAILED] = frozenset(failed)
    return readings


# ---------------------------------------------------------------- loop
async def run_cycle(settings: Settings, state: AlertState, *, collector=collect,
                    publisher=notify.publish, now: Callable[[], float] = time.time) -> int:
    """One sample + publish. Returns the number of notifications attempted."""
    readings = await executors.run_collectors(collector, settings)
    before = state.alerted()
    alerts = cap(settings, evaluate(settings, readings, state, now()))
    link = f"{settings.base_url.rstrip('/')}/servers"
    for a in alerts:
        try:
            await executors.run_collectors(publisher, settings, a.title, a.message, click=link,
                                    tags=a.tags, priority=a.priority)
        except Exception as exc:  # noqa: BLE001
            log.warning("alert publish failed: %s", type(exc).__name__)
    if state.alerted() != before:
        try:
            await executors.run_collectors(save_state, settings, state)
        except OSError as exc:
            log.warning("alert state save failed: %s", type(exc).__name__)
    return len(alerts)


def enabled(settings: Settings) -> bool:
    return bool(settings.alerts_enabled) and not settings.disable_poll


async def alert_loop(settings: Settings, state: AlertState, **kw) -> None:
    while True:
        if enabled(settings):
            try:
                await run_cycle(settings, state, **kw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - never crash; type only
                log.warning("alerts cycle failed: %s", type(exc).__name__)
        await asyncio.sleep(max(1, settings.alerts_interval_s))


def start(app, settings: Settings | None = None) -> asyncio.Task:
    """Call from the app lifespan. Sets app.state.alerts_state / alerts_task."""
    settings = settings or app.state.settings
    st = app.state
    st.alerts_state = load_state(settings)
    st.alerts_task = asyncio.create_task(alert_loop(settings, st.alerts_state), name="pm-alerts")
    return st.alerts_task


async def stop(app) -> None:
    task = getattr(app.state, "alerts_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        app.state.alerts_task = None
