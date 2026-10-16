"""Servers dashboard data model: dataclasses, thresholds and small pure helpers.

Shared by ``servers`` (assembly, cache, summary) and ``hostmetrics`` (this host's metrics),
so neither imports the other's internals. ``servers`` re-exports every name here.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from pm.core.proc import ProcError, run_argv

CMD_TIMEOUT_S = 3.0

LOAD_WARN, LOAD_CRIT = 0.70, 1.00
MEM_WARN, MEM_CRIT = 80.0, 92.0
SWAP_WARN, SWAP_CRIT = 25.0, 60.0
DISK_WARN, DISK_CRIT = 80.0, 92.0
LAT_WARN_MS = 250.0

_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# (rc, stdout); rc -1 means the binary is missing or timed out
Runner = Callable[[list[str], float], tuple[int, str]]
TcpCheck = Callable[[str, int, float], float | None]


def clean(s: object, limit: int = 200) -> str:
    return _CTRL.sub("", str(s))[:limit]


def level(value: float, warn: float, crit: float) -> str:
    return "crit" if value >= crit else "warn" if value >= warn else "ok"


def fmt_duration(sec: float) -> str:
    sec = max(0, int(sec))
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m" if m else f"{sec}s"


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def default_runner(argv: list[str], timeout: float) -> tuple[int, str]:
    try:
        p = run_argv(argv, timeout=timeout)
    except ProcError:
        return -1, ""
    return p.returncode, p.stdout[:2_000_000].decode("utf-8", "replace")


@dataclass(frozen=True)
class Metric:
    label: str
    text: str
    pct: float | None = None  # 0..100 -> bar
    level: str = "ok"
    note: str = ""


_EXIT0_RE = re.compile(r"^Exited \(0\)")
STACK_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")  # a docker compose project name
_COMPOSE = "com.docker.compose.project"


def compose_labels(raw: object) -> tuple[str, str]:
    """``docker ps`` ``Labels`` text ("k=v,k=v") -> (compose project, its working dir); ("", "") outside compose.
    A project name that is not a valid compose name is dropped (it is used in routes and filters)."""
    labels = dict(kv.partition("=")[::2] for kv in str(raw or "").split(",") if "=" in kv)
    stack = clean(labels.get(_COMPOSE, ""), 100)
    if not STACK_RE.fullmatch(stack):
        return "", ""
    return stack, clean(labels.get(_COMPOSE + ".working_dir", ""), 300)


@dataclass(frozen=True)
class Container:
    name: str
    image: str
    state: str
    status: str
    health: str  # healthy | unhealthy | starting | ""
    ports: str
    stack: str = ""  # docker compose project ("" = not started by compose)
    workdir: str = ""  # the compose project's working dir

    @property
    def clean_exit(self) -> bool:
        """Exited with code 0 (a finished one-shot job): fine, not a health problem."""
        return self.state == "exited" and _EXIT0_RE.match(self.status) is not None

    @property
    def problem(self) -> bool:
        return self.health == "unhealthy" or self.state in ("dead", "restarting", "removing") or (
            self.state == "exited" and not self.clean_exit)


@dataclass(frozen=True)
class DockerInfo:
    available: bool
    containers: tuple[Container, ...] = ()
    running: int = 0
    total: int = 0
    unhealthy: int = 0


@dataclass(frozen=True)
class AgentChip:
    id: str
    label: str
    state: str
    window: str = ""


@dataclass(frozen=True)
class SessionRow:
    name: str
    windows: int
    panes: int
    attached: int
    project: str | None
    agents: tuple[AgentChip, ...]
    last_activity: float
    protected: bool
    waiting: int


@dataclass(frozen=True)
class TmuxSummary:
    ok: bool
    sessions: int = 0
    windows: int = 0
    panes: int = 0
    attached: int = 0
    agents_by_state: tuple[tuple[str, int], ...] = ()
    rows: tuple[SessionRow, ...] = ()


@dataclass(frozen=True)
class HostCheck:
    name: str
    host: str
    port: int
    note: str
    up: bool
    latency_ms: float | None


@dataclass(frozen=True)
class Libvirt:
    running: int
    total: int


@dataclass(frozen=True)
class Panel:
    ts: float
    hostname: str = ""
    metrics: tuple[Metric, ...] = ()
    docker: DockerInfo = field(default_factory=lambda: DockerInfo(False))
    tmux: TmuxSummary = field(default_factory=lambda: TmuxSummary(False))
    hosts: tuple[HostCheck, ...] = ()
    libvirt: Libvirt | None = None
    failed: tuple[str, ...] = ()  # collector names that failed -> stale marker
    ntfy_ok: bool | None = None  # ntfy mirror reachable; None = unknown / unavailable
    ntfy_24h: int | None = None  # ntfy messages in the last 24 h; None = unknown
    collecting: tuple[str, ...] = ()  # sections still running on a first-ever load

    @property
    def stale(self) -> bool:
        return bool(self.failed)
