"""Stop / kill a process or a container from Servers > Resources and Services.

Guarded like ``tmuxkill``: fresh lookup by id (pid + starttime, container name), same-uid only,
never pm itself or its ancestors, never the tmux server or anything inside a protected tmux
session, never PID 1 / kernel threads / the login chain. Signals go through ``os.kill`` and
docker through an argv list (no shell, timeout). The route owns CSRF, tier and audit.
"""

from __future__ import annotations

import fnmatch
import os
import re
import signal
from pathlib import Path

from pm.core.config import Settings
from pm.infra.resources import Proc, scan
from pm.infra.servers import Runner, clean, default_runner
from pm.infra.servers_model import STACK_RE
from pm.tmux import ctl as tmuxctl

WRITE_TIMEOUT_S = 25.0

# action -> (signal, past tense)
SIGNALS = {
    "term": (signal.SIGTERM, "Asked to quit (SIGTERM)"),
    "kill": (signal.SIGKILL, "Killed (SIGKILL)"),
    "stop": (signal.SIGSTOP, "Paused (SIGSTOP)"),
    "cont": (signal.SIGCONT, "Resumed (SIGCONT)"),
}
# Programs that hold the machine, the login or pm's own plumbing together.
PROTECTED_COMM = frozenset({"systemd", "(sd-pam)", "sshd", "dbus-daemon", "tmux: server", "tmux: client",
                            "login", "init"})
CT_ACTIONS = {"start": "Started", "stop": "Stopped", "restart": "Restarted", "kill": "Killed"}
_CT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")
MAX_PROGRAM_PROCS = 64  # signal_program refuses a bigger fan-out
MAX_STACK = 50  # containers one restart_stack call may restart


class CtlError(Exception):
    def __init__(self, msg: str, status: int = 403):
        super().__init__(msg)
        self.status = status


def _ancestors(procs: dict[int, Proc], pid: int) -> set[int]:
    out: set[int] = set()
    while pid > 0 and pid not in out:
        out.add(pid)
        p = procs.get(pid)
        pid = p.ppid if p else 0
    return out


def _protected_tree(settings: Settings, procs: dict[int, Proc]) -> set[int]:
    """Pids under a pane of a protected tmux session (agents, shells the user depends on)."""
    try:
        out = tmuxctl.run(settings, ["list-panes", "-a", "-F", "#{session_name}\t#{pane_pid}"], timeout=3.0)
    except tmuxctl.TmuxError:
        raise CtlError("cannot read tmux state, refusing", 503) from None
    roots = [int(pid) for name, _, pid in (ln.rpartition("\t") for ln in out.splitlines())
             if pid.isdigit() and tmuxctl.is_protected(name, settings)]
    kids: dict[int, list[int]] = {}
    for p in procs.values():
        kids.setdefault(p.ppid, []).append(p.pid)
    seen: set[int] = set()
    stack = list(roots)
    while stack:
        q = stack.pop()
        if q not in seen:
            seen.add(q)
            stack.extend(kids.get(q, ()))
    return seen


def _refusal(p: Proc, mine: set[int], uid: int, proc: Path) -> CtlError | None:
    """Why ``p`` must not be signalled (None = it may be); the protected tmux tree is checked by the caller."""
    if p.pid <= 1:
        return CtlError("refusing pid <= 1")
    if p.pid in mine:
        return CtlError("refusing pm itself or its parents")
    try:
        owner = (proc / str(p.pid)).stat().st_uid
    except OSError:
        return CtlError("no such process (already gone?)", 404)
    if owner != uid:
        return CtlError("not owned by pm's user")
    if p.comm in PROTECTED_COMM or p.comm.startswith("qemu-system"):
        return CtlError(f"{p.comm} is protected" + (" (use the VMs tab)" if p.comm.startswith("qemu") else ""))
    if p.ppid == 2 or p.rss == 0:
        return CtlError("kernel thread")
    return None


def _send(pid: int, sig: signal.Signals) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        raise CtlError("no such process (already gone?)", 404) from None
    except PermissionError:
        raise CtlError("operation not permitted") from None


def _check(settings: Settings, action: str) -> None:
    if action not in SIGNALS:
        raise CtlError("unknown action", 400)
    if not settings.proc_manage:
        raise CtlError("process control disabled")


def signal_process(settings: Settings, action: str, pid: int, start: int, *,
                   proc: Path = Path("/proc"), me: int | None = None, uid: int | None = None) -> str:
    """Send ``action``'s signal to ``pid`` after re-checking that it is still the process shown."""
    _check(settings, action)
    if pid <= 1:
        raise CtlError("refusing pid <= 1")
    procs = scan(proc)
    p = procs.get(pid)
    if p is None:
        raise CtlError("no such process (already gone?)", 404)
    if p.start != start:
        raise CtlError("pid was reused by another process; reload the page", 409)
    me = os.getpid() if me is None else me
    err = _refusal(p, _ancestors(procs, me), os.getuid() if uid is None else uid, proc)
    if err is not None:
        raise err
    if pid in _protected_tree(settings, procs):
        raise CtlError("process runs inside a protected tmux session")
    sig, msg = SIGNALS[action]
    _send(pid, sig)
    return f"{msg}: {p.comm} (pid {pid})."


def signal_program(settings: Settings, action: str, program: str, confirm: str, *,
                   proc: Path = Path("/proc"), me: int | None = None, uid: int | None = None) -> tuple[str, int]:
    """Signal every process named ``program`` (a Resources > Programs row) -> (message, processes signalled).

    One fresh scan; the same per-process checks as ``signal_process``; refused processes are skipped, not
    fatal. ``confirm`` must repeat the program name. More than MAX_PROGRAM_PROCS processes is refused."""
    _check(settings, action)
    if not program or program != clean(program, 40).strip():
        raise CtlError("bad program name", 400)
    if confirm != program:
        raise CtlError("confirm mismatch", 403)
    procs = scan(proc)
    targets = [p for p in procs.values() if p.comm == program]
    if not targets:
        raise CtlError("no such program (already gone?)", 404)
    if len(targets) > MAX_PROGRAM_PROCS:
        raise CtlError(f"{len(targets)} processes; more than {MAX_PROGRAM_PROCS}, stop them one by one", 409)
    mine, owner = _ancestors(procs, os.getpid() if me is None else me), os.getuid() if uid is None else uid
    prot = _protected_tree(settings, procs)
    sig, msg = SIGNALS[action]
    sent = 0
    for p in targets:
        if p.pid in prot or _refusal(p, mine, owner, proc) is not None:
            continue
        try:
            _send(p.pid, sig)
        except CtlError:
            continue
        sent += 1
    if not sent:
        raise CtlError(f"no process of {program} may be signalled (protected or gone)")
    skipped = len(targets) - sent
    return (f"{msg}: {program}, {sent} process{'es' if sent != 1 else ''}"
            + (f", {skipped} skipped (protected or gone)." if skipped else "."), sent)


# ---------------------------------------------------------------- containers
def container_protected(name: str, settings: Settings) -> bool:
    return any(fnmatch.fnmatchcase(name, g) for g in settings.container_protected)


def container_action(settings: Settings, action: str, name: str, run: Runner = default_runner) -> str:
    if action not in CT_ACTIONS:
        raise CtlError("unknown action", 400)
    if not settings.container_manage:
        raise CtlError("container control disabled")
    if not _CT_RE.fullmatch(name):
        raise CtlError("bad container name", 400)
    rc, out = run(["docker", "ps", "-a", "--format", "{{.Names}}"], 5.0)
    if rc != 0:
        raise CtlError("docker unavailable", 503)
    if name not in out.split():
        raise CtlError("no such container", 404)
    if container_protected(name, settings):
        raise CtlError("container is protected")
    argv = {"start": ["docker", "start", "--", name],
            "stop": ["docker", "stop", "--time", "10", "--", name],
            "restart": ["docker", "restart", "--time", "10", "--", name],
            "kill": ["docker", "kill", "--", name]}[action]
    rc, out = run(argv, WRITE_TIMEOUT_S)
    if rc != 0:
        raise CtlError(f"docker {action} failed: {clean(out, 160).strip()}", 502)
    return f"{CT_ACTIONS[action]} {name}."


def restart_stack(settings: Settings, stack: str, run: Runner = default_runner) -> tuple[str, int]:
    """Restart the containers of docker compose project ``stack`` -> (message, containers restarted).

    The members come from docker itself (label filter), never from the client; protected names are
    skipped; one ``docker restart`` call for the rest."""
    if not settings.container_manage:
        raise CtlError("container control disabled")
    if not STACK_RE.fullmatch(stack):
        raise CtlError("bad stack name", 400)
    rc, out = run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={stack}",
                   "--format", "{{.Names}}"], 5.0)
    if rc != 0:
        raise CtlError("docker unavailable", 503)
    names = sorted({n for n in out.split() if _CT_RE.fullmatch(n)})
    if not names:
        raise CtlError("no such stack", 404)
    todo = [n for n in names if not container_protected(n, settings)]
    if not todo:
        raise CtlError("every container of the stack is protected")
    if len(todo) > MAX_STACK:
        raise CtlError(f"{len(todo)} containers; more than {MAX_STACK}", 409)
    rc, out = run(["docker", "restart", "--time", "10", "--", *todo], min(120.0, WRITE_TIMEOUT_S + 10.0 * len(todo)))
    if rc != 0:
        raise CtlError(f"docker restart failed: {clean(out, 160).strip()}", 502)
    skipped = len(names) - len(todo)
    return (f"Restarted stack {stack}: {len(todo)} container{'s' if len(todo) != 1 else ''}"
            + (f", {skipped} protected skipped." if skipped else "."), len(todo))
