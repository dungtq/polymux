"""Docker compose stack status and operations via argv-only calls. Inject `run` for tests.

A stack is the compose file in a project's directory (compose's own discovery order); every call runs
with that directory as cwd. ``down`` never removes volumes or images. Log text is ANSI-stripped and masked.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from pm.core.errors import NotFound, PmError
from pm.core.models import StackStatus
from pm.core.proc import ProcError, run_argv
from pm.security.mask import render_pane

DOCKER = "docker"
TIMEOUT_S = 30.0
COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
# web hosts a stack publishes: Traefik Host(`h`) rules, https://h URLs, VIRTUAL_HOST=h (nginx-proxy)
HOST_RE = re.compile(r"(?:Host\(`|https://|VIRTUAL_HOST[=:]\s*)((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})\b")
MAX_LOG_LINES = 1000
OP_TIMEOUT_S = 120.0  # up may pull images
MAX_SERVICES = 100
SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# compose verb argv per operation; down keeps volumes and images (no -v / --rmi, ever)
OPS = {"up": ("up", "-d"), "down": ("down",)}  # restart: ``restart`` (the control route)
# "Add a stack" templates: no catalogue yet, so the view offers none
TEMPLATES: tuple[str, ...] = ()

Run = Callable[[list[str], Path], "subprocess.CompletedProcess[str]"]


def _default_run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return run_argv(argv, cwd=cwd, text=True, timeout=TIMEOUT_S)


def _op_run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return run_argv(argv, cwd=cwd, text=True, timeout=OP_TIMEOUT_S)


def compose_file(path: str | Path) -> Path | None:
    for n in COMPOSE_FILES:
        f = Path(path) / n
        if f.is_file():
            return f
    return None


def urls_for(path: str | Path) -> tuple[str, ...]:
    f = compose_file(path)
    if f is None:
        return ()
    try:
        text = f.read_text(errors="replace")[:200_000]
    except OSError:
        return ()
    seen: dict[str, None] = {}
    for m in HOST_RE.finditer(text):
        seen[f"https://{m.group(1)}"] = None
    return tuple(seen)


def _parse_ps(out: str) -> list[dict]:
    out = out.strip()
    if not out:
        return []
    try:
        data = json.loads(out)
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        rows = []
        for ln in out.splitlines():
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return rows


def stack_status(path: str | Path, run: Run = _default_run) -> StackStatus | None:
    if compose_file(path) is None:
        return None
    try:
        cp = run([DOCKER, "compose", "ps", "--all", "--format", "json"], Path(path))
    except ProcError:
        return StackStatus(0, 0, urls_for(path))
    rows = _parse_ps(cp.stdout) if cp.returncode == 0 else []
    running = sum(1 for r in rows if str(r.get("State", "")).lower() == "running")
    return StackStatus(running, len(rows), urls_for(path))


def restart(path: str | Path, run: Run = _default_run) -> bool:
    if compose_file(path) is None:
        return False
    try:
        return run([DOCKER, "compose", "restart"], Path(path)).returncode == 0
    except ProcError:
        return False


def logs(path: str | Path, tail: int = 200, run: Run = _default_run, service: str | None = None) -> str:
    """Last ``tail`` lines of the stack (or one service of it), ANSI-stripped and masked.

    ``service`` must be a service of a fresh ``compose ps`` (NotFound otherwise)."""
    if compose_file(path) is None:
        return ""
    tail = max(1, min(int(tail), MAX_LOG_LINES))
    argv = [DOCKER, "compose", "logs", "--no-color", "--tail", str(tail)]
    if service:
        if not SERVICE_RE.fullmatch(service) or service not in {s.name for s in services(path, run)}:
            raise NotFound("no such service")
        argv += ["--", service]
    try:
        cp = run(argv, Path(path))
    except ProcError:
        return ""
    return render_pane((cp.stdout + cp.stderr)[-200_000:])


@dataclass(frozen=True)
class Service:
    name: str  # compose service name (the logs filter)
    container: str
    state: str
    health: str
    ports: str
    image: str


def _clean(v: object, limit: int = 120) -> str:
    return _CTRL.sub("", str(v or ""))[:limit]


def _ports(r: dict) -> str:
    pubs = r.get("Publishers")
    if isinstance(pubs, list):
        seen: dict[str, None] = {}
        for p in pubs:
            if isinstance(p, dict) and p.get("PublishedPort"):
                seen[f"{p.get('PublishedPort')}->{p.get('TargetPort')}/{p.get('Protocol') or 'tcp'}"] = None
        return _clean(", ".join(seen), 200)
    return _clean(r.get("Ports"), 200)


def services(path: str | Path, run: Run = _default_run) -> list[Service]:
    """Containers of the stack (``compose ps --all``): service, container, state, health, ports, image.

    No compose file: []. compose failed: PmError 502."""
    if compose_file(path) is None:
        return []
    try:
        cp = run([DOCKER, "compose", "ps", "--all", "--format", "json"], Path(path))
    except ProcError:
        raise PmError("docker compose ps failed", status=502) from None
    if cp.returncode != 0:
        raise PmError("docker compose ps failed", status=502)
    rows = [r for r in _parse_ps(cp.stdout) if isinstance(r, dict)][:MAX_SERVICES]
    return [Service(_clean(r.get("Service") or r.get("Name"), 64), _clean(r.get("Name"), 128),
                    _clean(r.get("State"), 20).lower(), _clean(r.get("Health"), 20).lower(), _ports(r),
                    _clean(r.get("Image"), 200)) for r in rows]


def view(path: str | Path, run: Run = _default_run) -> dict:
    """JSON view model of a project's stack (theme-neutral): file, services, counts, URLs, add templates."""
    f = compose_file(path)
    out: dict = {"compose_file": f.name if f else None, "services": [], "running": 0, "total": 0,
                 "urls": list(urls_for(path)), "error": "", "templates": list(TEMPLATES)}
    if f is None:
        return out
    try:
        svcs = services(path, run)
    except PmError as e:
        out["error"] = str(e)
        return out
    out["services"] = [asdict(s) for s in svcs]
    out["running"] = sum(1 for s in svcs if s.state == "running")
    out["total"] = len(svcs)
    return out


def operate(path: str | Path, action: str, run: Run = _op_run) -> str:
    """Run ``docker compose up -d | down`` in the project dir. Returns a message.

    Unknown action / no compose file: NotFound. compose failed: PmError 502 (masked tail of its output)."""
    if action not in OPS:
        raise NotFound("unknown stack action")
    if compose_file(path) is None:
        raise NotFound("no compose file")
    try:
        cp = run([DOCKER, "compose", *OPS[action]], Path(path))
    except ProcError as e:
        raise PmError(f"docker compose {action} failed: {e}", status=502) from None
    if cp.returncode != 0:
        tail = render_pane((cp.stderr or cp.stdout or "").strip()[-300:])
        raise PmError(f"docker compose {action} failed (exit {cp.returncode}): {tail}", status=502)
    return {"up": "Stack up.", "down": "Stack down (volumes kept)."}[action]
