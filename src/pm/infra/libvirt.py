"""libvirt (qemu:///system) VM and image management for the Servers tab.

Reads use ``virsh -r`` (the world-rw read-only socket; pm's service lacks the libvirt
group). Mutations need the write socket, so they run through ``sg libvirt -c`` when the
process is not in the group. Everything is a fixed-binary argv list with a timeout, no
shell for pm's own values (the only shell string is the ``sg -c`` payload, built from
``shlex.quote`` of a validated name). A domain / image is only ever addressed by a name
that appears in a *fresh* listing; EVE-NG-looking and configured-protected domains are
never mutated; images referenced by any domain are never deleted.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import shutil
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

from pm.core.config import Settings
from pm.core.proc import ProcError, run_argv

CACHE_TTL_S = 10.0
READ_TIMEOUT_S = 4.0
WRITE_TIMEOUT_S = 30.0
MAX_DOMAINS = 100
MAX_VOLUMES = 500

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")

Runner = Callable[[list[str], float], tuple[int, str]]

ACTIONS = {  # action -> (virsh verb, required state, tier2 typed confirm)
    "start": ("start", "shut off", False),
    "shutdown": ("shutdown", "running", False),
    "destroy": ("destroy", "running", True),
    "undefine": ("undefine", "shut off", True),
}


class VmError(Exception):
    """Refused or failed; ``status`` is the HTTP status the route should use."""

    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


@dataclass(frozen=True)
class Disk:
    target: str
    device: str
    source: str
    capacity: int | None = None  # virtual size, bytes
    allocation: int | None = None  # bytes used on the host


@dataclass(frozen=True)
class Domain:
    name: str
    state: str
    vcpus: int | None
    mem_kib: int | None
    autostart: bool
    persistent: bool
    protected: bool
    disks: tuple[Disk, ...] = ()


@dataclass(frozen=True)
class Volume:
    name: str
    path: str
    type: str
    capacity: int | None
    allocation: int | None
    used_by: tuple[str, ...] = ()
    deletable: bool = False


@dataclass(frozen=True)
class Pool:
    name: str
    capacity: int | None = None
    allocation: int | None = None
    available: int | None = None


@dataclass(frozen=True)
class VmInfo:
    ts: float
    domains: tuple[Domain, ...] = ()
    volumes: tuple[Volume, ...] = ()
    pool: Pool | None = None
    available: bool = True
    error: str = ""
    writable: bool = True


def filter_info(info: VmInfo, patterns: tuple[str, ...]) -> VmInfo:
    """Keep the domains whose name matches a pattern, and the volumes those domains use (no pool usage)."""
    doms = tuple(d for d in info.domains if any(fnmatch.fnmatchcase(d.name, p) for p in patterns))
    kept = {d.name for d in doms}
    vols = tuple(v for v in info.volumes if kept.intersection(v.used_by))
    return replace(info, domains=doms, volumes=vols, pool=None)  # pool usage describes the whole pool


def clean(s: object, limit: int = 200) -> str:
    return _CTRL.sub("", str(s))[:limit]


def default_runner(argv: list[str], timeout: float) -> tuple[int, str]:
    try:
        p = run_argv(argv, timeout=timeout)
    except ProcError:
        return -1, ""
    out = p.stdout[:2_000_000].decode("utf-8", "replace")
    if p.returncode != 0 and not out:
        out = p.stderr[:2000].decode("utf-8", "replace")
    return p.returncode, out


def is_protected(name: str, settings: Settings) -> bool:
    return any(fnmatch.fnmatchcase(name.lower(), p.lower()) for p in settings.vm_protected)


def virsh_ro(settings: Settings, *args: str) -> list[str]:
    """Read-only virsh argv (``-r``)."""
    return [settings.virsh_bin, "-r", "-c", settings.virsh_uri, *args]


def virsh_rw(settings: Settings, *args: str) -> list[str]:
    """Read-write virsh argv; through ``sg libvirt`` when ``virsh_write_sg`` and pm is not in that group yet."""
    base = [settings.virsh_bin, "-c", settings.virsh_uri, *args]
    if settings.virsh_write_sg and not _in_libvirt_group():
        return ["sg", "libvirt", "-c", " ".join(shlex.quote(a) for a in base)]
    return base


def _in_libvirt_group() -> bool:
    try:
        import grp
        return grp.getgrnam("libvirt").gr_gid in os.getgroups()
    except (KeyError, OSError):
        return False


def _int(s: str) -> int | None:
    m = re.match(r"\s*(\d+)", s or "")
    return int(m.group(1)) if m else None


def _kv(text: str) -> dict[str, str]:
    d: dict[str, str] = {}
    for ln in text.splitlines():
        k, sep, v = ln.partition(":")
        if sep:
            d[k.strip()] = v.strip()
    return d


def _table(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    seen = False
    for ln in text.splitlines():
        if ln.startswith("---"):
            seen = True
            continue
        if seen and ln.strip():
            rows.append(ln.split(None, 4))
    return rows


def list_names(settings: Settings, run: Runner = default_runner) -> list[str]:
    rc, out = run(virsh_ro(settings, "list", "--all", "--name"), READ_TIMEOUT_S)
    if rc != 0:
        raise VmError("virsh list failed", 502)
    return [ln.strip() for ln in out.splitlines() if ln.strip()][:MAX_DOMAINS]


def domain_state(settings: Settings, name: str, run: Runner = default_runner) -> str:
    rc, out = run(virsh_ro(settings, "domstate", "--", name), READ_TIMEOUT_S)
    return out.strip().splitlines()[0].strip() if rc == 0 and out.strip() else ""


def _disks(settings: Settings, name: str, run: Runner) -> tuple[Disk, ...]:
    rc, out = run(virsh_ro(settings, "domblklist", "--details", "--", name), READ_TIMEOUT_S)
    if rc != 0:
        return ()
    sizes: dict[str, tuple[int | None, int | None]] = {}
    rc2, out2 = run(virsh_ro(settings, "domblkinfo", name, "--all"), READ_TIMEOUT_S)
    if rc2 == 0:
        for r in _table(out2):
            if len(r) >= 3:
                sizes[r[0]] = (_int(r[1]), _int(r[2]))
    disks = []
    for r in _table(out):  # Type Device Target Source
        if len(r) < 3:
            continue
        src = r[3].strip() if len(r) > 3 else "-"
        cap, alloc = sizes.get(r[2], (None, None))
        if cap is None and src.startswith("/"):
            cap, alloc = qemu_img_sizes(src, run)
        disks.append(Disk(clean(r[2], 20), clean(r[1], 20), clean(src, 300), cap, alloc))
    return tuple(disks)


def qemu_img_sizes(path: str, run: Runner = default_runner) -> tuple[int | None, int | None]:
    """Fallback when domblkinfo has nothing: ``qemu-img info`` (needs read access to the file)."""
    if not shutil.which("qemu-img") or not path.startswith("/"):
        return None, None
    rc, out = run(["qemu-img", "info", "--force-share", "--output=json", "--", path], READ_TIMEOUT_S)
    if rc != 0:
        return None, None
    try:
        j = json.loads(out)
        return int(j.get("virtual-size")), int(j.get("actual-size"))
    except (ValueError, TypeError):
        return None, None


def read_domain(settings: Settings, name: str, run: Runner = default_runner) -> Domain:
    rc, out = run(virsh_ro(settings, "dominfo", "--", name), READ_TIMEOUT_S)
    d = _kv(out) if rc == 0 else {}
    return Domain(
        name=clean(name, 64), state=clean(d.get("State", "unknown"), 30),
        vcpus=_int(d.get("CPU(s)", "")), mem_kib=_int(d.get("Max memory", "")),
        autostart=d.get("Autostart", "").startswith("enable"),
        persistent=d.get("Persistent", "yes") == "yes",
        protected=is_protected(name, settings), disks=_disks(settings, name, run))


def read_pool(settings: Settings, run: Runner = default_runner) -> Pool | None:
    rc, out = run(virsh_ro(settings, "pool-info", "--", settings.vm_pool), READ_TIMEOUT_S)
    if rc != 0:
        return None
    d = _kv(out)
    return Pool(clean(settings.vm_pool, 40), *(_size(d.get(k, "")) for k in ("Capacity", "Allocation", "Available")))


_UNITS = {"B": 1, "bytes": 1, "KiB": 1 << 10, "MiB": 1 << 20, "GiB": 1 << 30, "TiB": 1 << 40}


def _size(s: str) -> int | None:
    m = re.match(r"\s*([\d.]+)\s*(\w+)", s or "")
    if not m or m.group(2) not in _UNITS:
        return None
    try:
        return int(float(m.group(1)) * _UNITS[m.group(2)])
    except ValueError:
        return None


def read_volumes(settings: Settings, domains: tuple[Domain, ...], run: Runner = default_runner) -> tuple[Volume, ...]:
    rc, out = run(virsh_ro(settings, "vol-list", "--details", "--pool", settings.vm_pool), READ_TIMEOUT_S)
    if rc != 0:
        return ()
    used: dict[str, list[str]] = {}
    for dom in domains:
        for dk in dom.disks:
            used.setdefault(dk.source, []).append(dom.name)
    vols = []
    for ln in out.splitlines():
        if ln.startswith("---"):
            vols = []
            continue
        # Name Path Type Capacity(2 tokens) Allocation(2 tokens)
        parts = ln.split()
        if len(parts) < 7 or parts[0] == "Name":
            continue
        name, path, typ = parts[0], parts[1], parts[2]
        cap, alloc = _size(" ".join(parts[3:5])), _size(" ".join(parts[5:7]))
        users = tuple(sorted(set(used.get(path, ()))))
        vols.append(Volume(clean(name, 120), clean(path, 300), clean(typ, 20), cap, alloc, users,
                           deletable=typ == "file" and not users and bool(NAME_RE.match(name))))
    vols = vols[:MAX_VOLUMES]
    backing = _backing_map(settings, [v for v in vols if v.type == "file" and not v.name.endswith(".iso")], run)
    out_v = []
    for v in vols:
        kids = backing.get(v.path, ())
        if kids:
            v = replace(v, used_by=tuple(sorted(set(v.used_by) | {f"backing of {k}" for k in kids})),
                        deletable=False)
        out_v.append(v)
    return tuple(out_v)


_BACKING = re.compile(r"<backingStore>\s*<path>([^<]+)</path>")


def _backing_map(settings: Settings, vols: list[Volume], run: Runner) -> dict[str, list[str]]:
    """backing-file path -> names of volumes layered on it (deleting it would break them)."""
    def one(v: Volume) -> tuple[str, str | None]:
        rc, xml = run(virsh_ro(settings, "vol-dumpxml", "--pool", settings.vm_pool, "--", v.name), READ_TIMEOUT_S)
        m = _BACKING.search(xml) if rc == 0 else None
        return v.name, (m.group(1).strip() if m else None)
    res: dict[str, list[str]] = {}
    if not vols:
        return res
    with ThreadPoolExecutor(min(6, len(vols))) as ex:
        for name, base in ex.map(one, vols):
            if base:
                res.setdefault(base, []).append(name)
    return res


def collect(settings: Settings, run: Runner = default_runner, which=shutil.which) -> VmInfo:
    now = time.time()
    if not which(settings.virsh_bin):
        return VmInfo(now, available=False, error="virsh not installed")
    try:
        names = list_names(settings, run)
    except VmError as e:
        return VmInfo(now, available=False, error=str(e))
    with ThreadPoolExecutor(min(6, max(1, len(names)))) as ex:
        doms = tuple(ex.map(lambda n: read_domain(settings, n, run), names))
    return VmInfo(now, doms, read_volumes(settings, doms, run), read_pool(settings, run),
                  True, "", writable=settings.vm_manage)


_cache: tuple[float, VmInfo] | None = None
_lock = threading.Lock()


def get(settings: Settings, *, force: bool = False) -> VmInfo:
    """Cached for CACHE_TTL_S; one collector at a time."""
    global _cache
    with _lock:
        if not force and _cache and time.time() - _cache[0] < CACHE_TTL_S:
            return _cache[1]
        info = collect(settings)
        _cache = (time.time(), info)
        return info


def cached() -> VmInfo | None:
    """The last listing ``get`` collected (any age), never a virsh call; None when there is none yet."""
    hit = _cache
    return hit[1] if hit else None


def invalidate() -> None:
    global _cache
    _cache = None


# ------------------------------------------------------------------ mutations
def check_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.fullmatch(name):
        raise VmError("invalid name", 400)
    return name


def needs_confirm(action: str) -> bool:
    return action == "image_delete" or ACTIONS[action][2]


def domain_action(settings: Settings, action: str, name: str, confirm: str = "",
                  run: Runner = default_runner) -> str:
    """Run start/shutdown/destroy/undefine on a domain from a fresh listing. Returns a message."""
    if action not in ACTIONS:
        raise VmError("unknown action", 400)
    if not settings.vm_manage:
        raise VmError("VM management disabled", 403)
    name = check_name(name)
    if name not in list_names(settings, run):
        raise VmError("no such domain", 404)
    if is_protected(name, settings):
        raise VmError("domain is protected", 403)
    verb, need, typed = ACTIONS[action]
    if typed and confirm != name:
        raise VmError("confirm mismatch", 403)
    state = domain_state(settings, name, run)
    if state != need:
        raise VmError(f"{action} needs state '{need}', domain is '{state or 'unknown'}'", 409)
    rc, out = run(virsh_rw(settings, verb, "--", name), WRITE_TIMEOUT_S)
    invalidate()
    if rc != 0:
        raise VmError(f"virsh {verb} failed: {clean(out, 160).strip()}", 502)
    return {"start": f"Started {name}.", "shutdown": f"Shutdown signal sent to {name}.",
            "destroy": f"Forced off {name}.", "undefine": f"Undefined {name} (disks kept)."}[action]


def image_delete(settings: Settings, name: str, confirm: str = "", run: Runner = default_runner) -> str:
    if not settings.vm_manage:
        raise VmError("VM management disabled", 403)
    name = check_name(name)
    names = list_names(settings, run)  # fresh, so a just-added domain protects its disk
    doms = tuple(read_domain(settings, n, run) for n in names)
    vol = next((v for v in read_volumes(settings, doms, run) if v.name == name), None)
    if vol is None:
        raise VmError("no such image", 404)
    if vol.type != "file":
        raise VmError("not a file volume", 403)
    if vol.used_by:
        raise VmError("image is attached to " + ", ".join(vol.used_by), 409)
    if confirm != name:
        raise VmError("confirm mismatch", 403)
    rc, out = run(virsh_rw(settings, "vol-delete", "--pool", settings.vm_pool, "--", name), WRITE_TIMEOUT_S)
    invalidate()
    if rc != 0:
        raise VmError(f"virsh vol-delete failed: {clean(out, 160).strip()}", 502)
    return f"Deleted image {name}."
