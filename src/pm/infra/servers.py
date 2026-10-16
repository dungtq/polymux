"""Read-only servers dashboard data: this host, docker, tmux summary, host reachability.

Everything is argv-only (fixed binaries, timeouts, no shell) and read-only. Results are
cached for ``CACHE_TTL_S`` so concurrent viewers do not multiply work. Strings that come
from docker/tmux are control-char stripped and bounded here; templates autoescape them.
Dataclasses and helpers live in ``servers_model``, this host's metrics in ``hostmetrics``;
both are re-exported here.

Thresholds (level ``ok`` / ``warn`` / ``crit``):

* load: load1 / cpu count   warn >= 0.70, crit >= 1.00
* memory used (MemTotal - MemAvailable)   warn >= 80 %, crit >= 92 %
* swap used                               warn >= 25 %, crit >= 60 %
* disk used                               warn >= 80 %, crit >= 92 %
* tcp latency                             warn >= 250 ms (up), down is crit
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import socket
import threading
import time
from dataclasses import dataclass, replace
from typing import NamedTuple

from pm.core import executors
from pm.core.config import Settings
from pm.core.models import AgentState
from pm.infra import snapshot as services
from pm.infra.hostmetrics import _logged_in, _meminfo, collect_host  # noqa: F401 - re-export (tests, resources)
from pm.infra.servers_model import (  # noqa: F401 - re-exported: servers.X stays the public surface
    CMD_TIMEOUT_S,
    DISK_CRIT,
    DISK_WARN,
    LAT_WARN_MS,
    LOAD_CRIT,
    LOAD_WARN,
    MEM_CRIT,
    MEM_WARN,
    SWAP_CRIT,
    SWAP_WARN,
    AgentChip,
    Container,
    DockerInfo,
    HostCheck,
    Libvirt,
    Metric,
    Panel,
    Runner,
    SessionRow,
    TcpCheck,
    TmuxSummary,
    clean,
    compose_labels,
    default_runner,
    fmt_bytes,
    fmt_duration,
    level,
)
from pm.tmux import ctl as tmuxctl

CACHE_TTL_S = 5.0
VIRSH_TIMEOUT_S = 2.0
TCP_TIMEOUT_S = 1.0
MAX_CONTAINERS = 200
MAX_EXTRA_HOSTS = 16


# ---------------------------------------------------------------- docker


def _health(status: str) -> str:
    s = status.lower()
    for h in ("unhealthy", "healthy", "starting"):  # unhealthy before healthy
        if f"({h}" in s or f"({h})" in s:
            return h
    return ""


def _trim_ports(ports: str) -> str:
    parts = [p.strip().replace("0.0.0.0:", "").replace("[::]:", "") for p in ports.split(",") if p.strip()]
    seen: dict[str, None] = dict.fromkeys(parts)
    items = list(seen)
    s = ", ".join(items[:3])
    if len(items) > 3:
        s += f" +{len(items) - 3}"
    return s[:80]


def _container_rank(c: Container) -> int:
    if c.health == "unhealthy":
        return 0
    if c.problem:
        return 1
    if c.clean_exit:
        return 3
    return 2


def collect_docker(run: Runner = default_runner) -> DockerInfo:
    rc, out = run(["docker", "ps", "-a", "--format", "{{json .}}"], CMD_TIMEOUT_S)
    if rc != 0:
        return DockerInfo(False)
    rows: list[Container] = []
    for ln in out.splitlines()[:MAX_CONTAINERS]:
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict):
            continue
        status, (stack, workdir) = clean(d.get("Status", ""), 80), compose_labels(d.get("Labels"))
        rows.append(Container(
            name=clean(d.get("Names", ""), 80), image=clean(d.get("Image", ""), 100),
            state=clean(d.get("State", ""), 20).lower(), status=status,
            health=_health(status), ports=_trim_ports(clean(d.get("Ports", ""), 400)), stack=stack, workdir=workdir,
        ))
    rows.sort(key=lambda c: (_container_rank(c), c.name))
    return DockerInfo(
        True, tuple(rows), sum(1 for c in rows if c.state == "running"), len(rows),
        sum(1 for c in rows if c.health == "unhealthy"),
    )


# ---------------------------------------------------------------- tmux


def summarize_tmux(snap, activity: dict[str, float]) -> TmuxSummary:
    sessions = [s for s in snap.sessions if not tmuxctl.VIEW_RE.fullmatch(s.name)]
    by_sid: dict[str, list] = {}
    for a in snap.agents:
        by_sid.setdefault(a.session_id, []).append(a)
    rows: list[SessionRow] = []
    counts: dict[str, int] = {}
    for s in sessions:
        ags = by_sid.get(s.id, [])
        for a in ags:
            counts[a.state.value] = counts.get(a.state.value, 0) + 1
        proj = next((a.project for a in ags if a.project), None)
        if proj is None:
            for p in snap.projects:
                if s.id in p.sessions:
                    proj = p.name
                    break
        rows.append(SessionRow(
            name=clean(s.name, 64), windows=len(s.windows),
            panes=sum(len(w.panes) for w in s.windows), attached=s.attached,
            project=clean(proj, 64) if proj else None,
            agents=tuple(AgentChip(clean(a.id, 40), clean(a.label or a.id, 60), a.state.value,
                                  clean(a.window_name, 48))
                         for a in ags),
            last_activity=max((activity.get(w.id, 0.0) for w in s.windows), default=0.0),
            protected=s.protected,
            waiting=sum(1 for a in ags if a.state is AgentState.WAITING),
        ))
    rows.sort(key=lambda r: (r.waiting == 0, -r.last_activity, r.name))
    order = [s.value for s in AgentState]
    return TmuxSummary(
        True, len(rows), sum(r.windows for r in rows), sum(r.panes for r in rows),
        sum(r.attached for r in rows),
        tuple(sorted(counts.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 9)),
        tuple(rows),
    )


# ---------------------------------------------------------------- hosts / virsh


def tcp_check(host: str, port: int, timeout: float = TCP_TIMEOUT_S) -> float | None:
    """Connect and close at once; no data sent. Latency in ms, None when unreachable."""
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError:
        return None
    return (time.monotonic() - t0) * 1000


def host_targets(settings: Settings) -> list[tuple[str, str, int, str]]:
    targets: list[tuple[str, str, int, str]] = []
    extra = settings.servers
    if isinstance(extra, list):
        for e in extra[:MAX_EXTRA_HOSTS]:
            if not isinstance(e, dict):
                continue
            name, host, port = e.get("name"), e.get("host"), e.get("port")
            if isinstance(name, str) and isinstance(host, str) and isinstance(port, int) \
                    and not isinstance(port, bool) and 0 < port < 65536 and host:
                targets.append((clean(name, 40), clean(host, 100), port, clean(e.get("note", ""), 100)))
    return targets


def check_hosts(targets, check: TcpCheck = tcp_check) -> tuple[HostCheck, ...]:
    lats = list(executors.hosts.map(lambda t: check(t[1], t[2], TCP_TIMEOUT_S), targets))
    return tuple(
        HostCheck(n, h, p, note, lat is not None, lat)
        for (n, h, p, note), lat in zip(targets, lats)
    )


def collect_libvirt(run: Runner = default_runner, which=shutil.which) -> Libvirt | None:
    if not which("virsh"):
        return None
    rc, out = run(  # -r: world-rw read-only socket (the service lacks the libvirt group)
        ["virsh", "-r", "-c", "qemu:///system", "list", "--all"], VIRSH_TIMEOUT_S)
    if rc != 0:
        return None
    running = total = 0
    seen_rule = False
    for ln in out.splitlines():
        if ln.startswith("---"):
            seen_rule = True
            continue
        parts = ln.split(None, 2)
        if seen_rule and len(parts) == 3:
            total += 1
            running += parts[2].strip() == "running"
    return Libvirt(running, total)


# ---------------------------------------------------------------- summary strip

_RANK = {"ok": 0, "warn": 1, "crit": 2}
_LOAD_RE = re.compile(r"^([\d.]+) ([\d.]+) ([\d.]+) on (\d+) CPU")


class Tile(NamedTuple):
    key: str
    label: str
    value: str
    status: str  # ok | warn | crit | na (muted)
    detail: str = ""
    pct: float | None = None  # 0..100 -> bar


@dataclass(frozen=True)
class Summary:
    status: str  # ok | warn | crit: worst tile (n/a tiles ignored)
    tiles: tuple[Tile, ...] = ()


def _na(key: str, label: str) -> Tile:
    return Tile(key, label, "n/a", "na", "no data")


def _names(items, n: int = 3) -> str:
    names = [clean(x, 60) for x in items]
    return ", ".join(names[:n]) + (f" +{len(names) - n}" if len(names) > n else "")


def _tiles(p: Panel) -> list[Tile]:
    by = {m.label: m for m in p.metrics}
    out: list[Tile] = []
    m = by.get("Load")
    lm = _LOAD_RE.match(m.text) if m else None
    if m and lm:
        l1, l5, l15, n = float(lm[1]), float(lm[2]), float(lm[3]), int(lm[4])
        out.append(Tile("load", "Load", f"{l1:.2f} {l5:.2f} {l15:.2f}", m.level,
                        f"{l1 / max(1, n):.2f} per CPU, {n} CPUs", m.pct))
    else:
        out.append(_na("load", "Load"))
    for key, label in (("memory", "Memory"), ("swap", "Swap")):
        m = by.get(label)
        out.append(Tile(key, label, f"{m.pct:.0f}%", m.level, m.text, m.pct)
                   if m and m.pct is not None else _na(key, label))
    disks = [x for x in p.metrics if x.label.startswith("Disk ") and x.pct is not None]
    if disks:
        w = max(disks, key=lambda x: x.pct)
        out.append(Tile("disk", "Disk", f"{w.pct:.0f}%", w.level, w.label[5:] + ": " + w.text, w.pct))
    else:
        out.append(_na("disk", "Disk"))
    m = by.get("Uptime")
    out.append(Tile("uptime", "Uptime", m.text, "ok") if m else _na("uptime", "Uptime"))
    m = by.get("Logins")
    out.append(Tile("logins", "Sessions", m.text, "ok", "logged in") if m else _na("logins", "Sessions"))

    d = p.docker
    if d.available:
        exited = [c for c in d.containers if c.problem and c.health != "unhealthy"]
        bad = [c.name for c in d.containers if c.health == "unhealthy"] + [c.name for c in exited]
        oneshot = sum(1 for c in d.containers if c.clean_exit)
        st = "crit" if d.unhealthy else "warn" if exited else "ok"
        det = f"{d.unhealthy} unhealthy, {len(exited)} exited non-zero" + (
            f", {oneshot} finished (exit 0, ignored)" if oneshot else "")
        okn = d.total - sum(1 for c in d.containers if c.problem)
        out.append(Tile("containers", "Containers", f"{okn}/{d.total}", st,
                        det + (f": {_names(bad)}" if bad else "")))
    else:
        out.append(_na("containers", "Containers"))

    t = p.tmux
    if t.ok:
        ag = ", ".join(f"{n} {s}" for s, n in t.agents_by_state) or "no agents"
        out.append(Tile("tmux", "tmux", f"{t.sessions} sess", "ok",
                        f"{t.windows} windows, {t.panes} panes, {t.attached} attached; {ag}"))
    else:
        out.append(_na("tmux", "tmux"))

    if p.hosts:
        down = [h.name for h in p.hosts if not h.up]
        slow = [h.name for h in p.hosts if h.up and h.latency_ms is not None and h.latency_ms >= LAT_WARN_MS]
        up = len(p.hosts) - len(down)
        st = "crit" if down else "warn" if slow else "ok"
        out.append(Tile("hosts", "Hosts", f"{up}/{len(p.hosts)}", st,
                        ("down: " + _names(down)) if down else ("slow: " + _names(slow)) if slow else "all reachable"))
    else:
        out.append(_na("hosts", "Hosts"))

    m = by.get("pm service")
    if m:
        state, _, up = m.text.partition(", up ")
        out.append(Tile("pm", "pm", state, m.level, f"up {up}" if up else ""))
    else:
        out.append(_na("pm", "pm"))
    if p.ntfy_ok is not None:
        out.append(Tile("ntfy", "ntfy", "up" if p.ntfy_ok else "down", "ok" if p.ntfy_ok else "warn", "mirror"))
        if p.ntfy_24h is not None:
            out.append(Tile("ntfy24", "ntfy 24h", str(p.ntfy_24h), "ok", "messages in the last 24 h"))
    if p.collecting:
        waiting = set(p.collecting)
        out = [Tile(t.key, t.label, "…", "na", "collecting…")
               if t.status == "na" and _TILE_SECTION.get(t.key) in waiting else t for t in out]
    return out


def summarize(p: Panel) -> Summary:
    """Pure: tiles + overall status (worst tile) from already-collected data. Never raises."""
    try:
        tiles = _tiles(p)
    except Exception:  # noqa: BLE001
        return Summary("ok", ())
    worst = max((_RANK.get(t.status, 0) for t in tiles), default=0)
    return Summary(("ok", "warn", "crit")[worst], tuple(tiles))


# ---------------------------------------------------------------- assembly + cache


SECTIONS = ("host", "docker", "tmux", "hosts", "virsh", "ntfy")
FIRST_WAIT_S = 2.0  # first-ever load waits this long, then returns what finished
STALE_AFTER_S = 30.0  # a cached panel older than this is marked stale (refresh is stuck or failing)
# tile key -> section that fills it (for the "collecting…" placeholder)
_TILE_SECTION = {"load": "host", "memory": "host", "swap": "host", "disk": "host", "uptime": "host",
                 "logins": "host", "pm": "host", "containers": "docker", "tmux": "tmux",
                 "hosts": "hosts", "ntfy": "ntfy", "ntfy24": "ntfy"}


def _section_host(settings, run, host_kw):
    return collect_host(settings, run=run, **host_kw)


def _section_docker(settings, run):
    return collect_docker(run)


def _section_tmux(settings, tracker, snapshot_fn):
    if snapshot_fn is not None:
        snap = snapshot_fn(settings)
    else:  # never build for the panel when the poll loop already published one
        snap = services.published(settings) or services.latest_snapshot(settings)
    snap = services.merge_tracker(snap, tracker)
    activity = snap.activity or services.window_activity(settings)
    return summarize_tmux(snap, activity), bool(snap.errors)


def collect_panel(settings: Settings, tracker=None, *, run: Runner = default_runner,
                  check: TcpCheck = tcp_check, snapshot_fn=None, ntfy_fn=None,
                  partial: dict | None = None, pool=None, **host_kw) -> Panel:
    """All sections run CONCURRENTLY (worst case = the slowest, not the sum). Each finished
    section is also stored in ``partial`` so a waiting request can answer with what is ready."""
    pool = pool or executors.panel
    parts: dict = partial if partial is not None else {}

    def go(name, fn, *a):
        def work():
            try:
                parts[name] = ("ok", fn(*a))
            except Exception:  # noqa: BLE001 - a section must not blank the page
                parts[name] = ("fail", None)
        return pool.submit(work)

    futs = [
        go("host", _section_host, settings, run, host_kw),
        go("docker", _section_docker, settings, run),
        go("tmux", _section_tmux, settings, tracker, snapshot_fn),
        go("hosts", lambda: check_hosts(host_targets(settings), check)),
        go("virsh", collect_libvirt, run),
        go("ntfy", lambda: ntfy_fn(settings) if ntfy_fn is not None else (None, None)),
    ]
    for f in futs:
        f.result()
    return assemble(parts)


def assemble(parts: dict, collecting: tuple[str, ...] = ()) -> Panel:
    """Panel from whatever sections finished; ``collecting`` names the ones still running."""
    failed: list[str] = []

    def get(name):
        r = parts.get(name)
        return r[1] if r and r[0] == "ok" else None

    def bad(name) -> bool:
        r = parts.get(name)
        return bool(r and r[0] == "fail")

    hostname, metrics = get("host") or ("", ())
    if bad("host"):
        failed.append("host")
    docker = get("docker") or DockerInfo(False)
    if bad("docker") or ("docker" in parts and not docker.available):
        failed.append("docker")
    tmux, tmux_err = get("tmux") or (TmuxSummary(False), False)
    if bad("tmux") or tmux_err:
        failed.append("tmux")
    hosts = get("hosts") or ()
    if bad("hosts"):
        failed.append("hosts")
    ntfy_ok, ntfy_24h = get("ntfy") or (None, None)
    return Panel(time.time(), hostname, tuple(metrics), docker, tmux, tuple(hosts), get("virsh"),
                 tuple(failed), ntfy_ok, ntfy_24h, tuple(collecting))


_lock = threading.Lock()
_cache: dict[int, tuple[Settings, Panel]] = {}


def get_panel(settings: Settings, tracker=None, *, force: bool = False, ntfy_fn=None) -> Panel:
    """Blocking, cached for CACHE_TTL_S; the lock makes concurrent callers share one collection.
    Handlers use ``get_panel_swr`` instead (never waits for a refresh once a panel exists).
    ``ntfy_fn(settings)`` -> (reachable, messages in 24 h) fills the ntfy section (None: unknown)."""
    with _lock:
        hit = _cache.get(id(settings))
        now = time.time()
        if hit and hit[0] is settings and not force and now - hit[1].ts < CACHE_TTL_S:
            return hit[1]
        panel = collect_panel(settings, tracker, ntfy_fn=ntfy_fn)
        _cache[id(settings)] = (settings, panel)
        return panel


# ---------------------------------------------------------------- stale-while-revalidate
class _Refresh:
    def __init__(self):
        self.parts: dict = {}
        self.future = None


_refreshing: dict[int, _Refresh] = {}
_rlock = threading.Lock()


def _run_refresh(settings: Settings, tracker, job: _Refresh, ntfy_fn=None) -> None:
    try:
        panel = collect_panel(settings, tracker, partial=job.parts, ntfy_fn=ntfy_fn)
        with _lock:
            _cache[id(settings)] = (settings, panel)
    except Exception:  # noqa: BLE001 - keep serving the last good panel
        pass
    finally:
        with _rlock:
            if _refreshing.get(id(settings)) is job:
                del _refreshing[id(settings)]


def start_refresh(settings: Settings, tracker=None, ntfy_fn=None) -> _Refresh:
    """Single flight: the in-progress refresh, or a new one on the collectors pool."""
    with _rlock:
        job = _refreshing.get(id(settings))
        if job is None:
            job = _Refresh()
            _refreshing[id(settings)] = job
            job.future = executors.collectors.submit(_run_refresh, settings, tracker, job, ntfy_fn)
        return job


def invalidate_panel(settings: Settings) -> None:
    """Age the cached panel so the next poll refreshes it (after a container stop / kill)."""
    with _lock:
        hit = _cache.get(id(settings))
        if hit and hit[0] is settings:
            _cache[id(settings)] = (settings, replace(hit[1], ts=0.0))


def cached_panel(settings: Settings) -> Panel | None:
    hit = _cache.get(id(settings))
    return hit[1] if hit and hit[0] is settings else None


async def get_panel_swr(settings: Settings, tracker=None, first_wait_s: float = FIRST_WAIT_S, *,
                        ntfy_fn=None) -> Panel:
    """Serve the last good panel at once; refresh in the background when it is older than
    CACHE_TTL_S. The first-ever load waits at most ``first_wait_s`` and then returns the
    sections that finished, the others marked ``collecting``."""
    panel = cached_panel(settings)
    now = time.time()
    if panel is not None and now - panel.ts < CACHE_TTL_S:
        return panel
    job = start_refresh(settings, tracker, ntfy_fn)
    if panel is not None:
        if now - panel.ts > STALE_AFTER_S and "refresh" not in panel.failed:
            panel = replace(panel, failed=panel.failed + ("refresh",))
        return panel
    try:
        await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(job.future)), first_wait_s)
    except TimeoutError:
        pass
    panel = cached_panel(settings)
    if panel is not None:
        return panel
    parts = dict(job.parts)
    return assemble(parts, tuple(n for n in SECTIONS if n not in parts))
