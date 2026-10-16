"""Project-scoped VM operations: snapshots, console info, start/stop, force off, delete.

A project addresses only the domains whose name matches one of its VM patterns (``projects.settings`` ``vms``,
case-sensitive fnmatch like ``libvirt.filter_info``) and that appear in a *fresh* listing. Every mutation also
needs ``vm_manage`` and refuses protected (configured / EVE-NG-looking) names; start/stop/force off/delete go
through ``libvirt.domain_action`` and keep all its guards (state, typed confirm). Console info is read-only
text (``virsh domdisplay`` / ``vncdisplay``); pm never proxies a console and never asks for its password.
"""

from __future__ import annotations

import fnmatch
import re
import time
from dataclasses import asdict, dataclass

from pm.core.config import Settings
from pm.infra import libvirt
from pm.infra.libvirt import Runner, VmError, check_name, clean, default_runner, invalidate, virsh_ro, virsh_rw

SNAP_TIMEOUT_S = 120.0  # a snapshot of a running domain saves its memory
MAX_SNAPSHOTS = 200
_DISPLAY = re.compile(r"^(vnc|spice|rdp)://[A-Za-z0-9.:\[\]_-]+(/[^\s]*)?$")
# project action -> libvirt.domain_action action
DOMAIN_ACTIONS = {"start": "start", "shutdown": "shutdown", "force-off": "destroy", "delete": "undefine"}


@dataclass(frozen=True)
class Snapshot:
    name: str
    created: str
    state: str
    current: bool = False


def in_project(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def cached_count(patterns: tuple[str, ...]) -> int | None:
    """How many VMs of the last cached listing match ``patterns``; None without patterns or a usable cached listing
    (a page render never calls virsh for it)."""
    info = libvirt.cached()
    if not patterns or info is None or not info.available:
        return None
    return sum(1 for d in info.domains if in_project(d.name, patterns))


def project_domain(settings: Settings, name: str, patterns: tuple[str, ...], *, mutate: bool,
                   run: Runner = default_runner) -> str:
    """The checked name of a project VM: valid, matches a pattern, in a fresh listing (and, to mutate: managed,
    not protected). Raises VmError with the HTTP status otherwise."""
    name = check_name(name)
    if not in_project(name, patterns):
        raise VmError("not a VM of this project", 404)
    if mutate and not settings.vm_manage:
        raise VmError("VM management disabled", 403)
    if name not in libvirt.list_names(settings, run):
        raise VmError("no such domain", 404)
    if mutate and libvirt.is_protected(name, settings):
        raise VmError("domain is protected", 403)
    return name


def _parse_snapshots(out: str, current: str) -> list[Snapshot]:
    snaps: list[Snapshot] = []
    seen = False
    for ln in out.splitlines():  # " Name  Creation Time  State"
        if ln.startswith("---"):
            seen = True
            continue
        parts = ln.split()
        if not seen or len(parts) < 2:
            continue
        snaps.append(Snapshot(clean(parts[0], 64), clean(" ".join(parts[1:-1]), 40), clean(parts[-1], 20),
                              parts[0] == current))
    return snaps[:MAX_SNAPSHOTS]


def snapshots(settings: Settings, name: str, patterns: tuple[str, ...], run: Runner = default_runner) -> list[Snapshot]:
    name = project_domain(settings, name, patterns, mutate=False, run=run)
    rc, out = run(virsh_ro(settings, "snapshot-list", "--", name), libvirt.READ_TIMEOUT_S)
    if rc != 0:
        raise VmError("virsh snapshot-list failed", 502)
    rc2, cur = run(virsh_ro(settings, "snapshot-current", "--name", "--", name), libvirt.READ_TIMEOUT_S)
    return _parse_snapshots(out, cur.strip() if rc2 == 0 else "")


def snapshots_view(settings: Settings, name: str, patterns: tuple[str, ...],
                   run: Runner = default_runner) -> dict:
    snaps = snapshots(settings, name, patterns, run)
    return {"vm": name, "snapshots": [asdict(s) for s in snaps],
            "protected": libvirt.is_protected(name, settings), "manage": settings.vm_manage}


def _snap_names(settings: Settings, name: str, run: Runner) -> set[str]:
    rc, out = run(virsh_ro(settings, "snapshot-list", "--name", "--", name), libvirt.READ_TIMEOUT_S)
    if rc != 0:
        raise VmError("virsh snapshot-list failed", 502)
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def _write(settings: Settings, verb: str, args: list[str], run: Runner, timeout: float) -> None:
    rc, out = run(virsh_rw(settings, verb, *args), timeout)
    invalidate()
    if rc != 0:
        raise VmError(f"virsh {verb} failed: {clean(out, 160).strip()}", 502)


def snapshot_create(settings: Settings, name: str, patterns: tuple[str, ...], snap: str = "",
                    run: Runner = default_runner) -> str:
    """Take a snapshot (``snapshot-create-as --atomic``); empty ``snap`` = ``pm-<UTC time>``."""
    name = project_domain(settings, name, patterns, mutate=True, run=run)
    snap = check_name(snap or time.strftime("pm-%Y%m%d-%H%M%S", time.gmtime()))
    if snap in _snap_names(settings, name, run):
        raise VmError("snapshot exists", 409)
    _write(settings, "snapshot-create-as", ["--atomic", "--", name, snap], run, SNAP_TIMEOUT_S)
    return f"Snapshot {snap} of {name} taken."


def snapshot_revert(settings: Settings, name: str, patterns: tuple[str, ...], snap: str,
                    run: Runner = default_runner) -> str:
    name = project_domain(settings, name, patterns, mutate=True, run=run)
    snap = check_name(snap)
    if snap not in _snap_names(settings, name, run):
        raise VmError("no such snapshot", 404)
    _write(settings, "snapshot-revert", ["--", name, snap], run, SNAP_TIMEOUT_S)
    return f"Reverted {name} to {snap}."


def snapshot_delete(settings: Settings, name: str, patterns: tuple[str, ...], snap: str,
                    run: Runner = default_runner) -> str:
    name = project_domain(settings, name, patterns, mutate=True, run=run)
    snap = check_name(snap)
    if snap not in _snap_names(settings, name, run):
        raise VmError("no such snapshot", 404)
    _write(settings, "snapshot-delete", ["--", name, snap], run, SNAP_TIMEOUT_S)
    return f"Deleted snapshot {snap} of {name}."


def domain_action(settings: Settings, action: str, name: str, patterns: tuple[str, ...], confirm: str = "",
                  run: Runner = default_runner) -> str:
    """start / shutdown / force-off (destroy, typed) / delete (undefine, typed, disks kept) of a project VM."""
    if action not in DOMAIN_ACTIONS:
        raise VmError("unknown action", 404)
    name = project_domain(settings, name, patterns, mutate=True, run=run)
    return libvirt.domain_action(settings, DOMAIN_ACTIONS[action], name, confirm, run)


def console(settings: Settings, name: str, patterns: tuple[str, ...], run: Runner = default_runner) -> dict:
    """Read-only console info: the display URI (``vnc://host:port``, ``spice://...``) or "" when none."""
    name = project_domain(settings, name, patterns, mutate=False, run=run)
    state = libvirt.domain_state(settings, name, run)
    url = ""
    if state == "running":
        rc, out = run(virsh_ro(settings, "domdisplay", "--", name), libvirt.READ_TIMEOUT_S)
        url = out.strip().splitlines()[0].strip() if rc == 0 and out.strip() else ""
        if not _DISPLAY.match(url):
            url = _vnc_url(settings, name, run)
    return {"vm": name, "state": clean(state, 30), "url": clean(url, 200), "read_only": True}


def _vnc_url(settings: Settings, name: str, run: Runner) -> str:
    """``vncdisplay`` prints ``host:N`` (display N = port 5900+N)."""
    rc, out = run(virsh_ro(settings, "vncdisplay", "--", name), libvirt.READ_TIMEOUT_S)
    m = re.match(r"^\s*([A-Za-z0-9.\[\]_-]*):(\d{1,4})\s*$", out.splitlines()[0] if rc == 0 and out.strip() else "")
    if not m:
        return ""
    return f"vnc://{m.group(1) or '127.0.0.1'}:{5900 + int(m.group(2))}"
