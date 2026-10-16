"""The dt project view's VMs tab view model (PRD.md §6.4): rows, words and ⋯ menus for the project's libvirt
guests, read by the dt ``_project_vms.html`` (the fragment of ``GET /p/{name}/vms``).

``dt_pjops()`` is a Jinja global (``template_globals()``) that reads the route's context and returns
``view(vm, patterns, project)``: ``vm`` = the route's ``VmInfo`` (already filtered to the project's patterns; ``None``
when the project has none), ``patterns`` = the project's VM name patterns, ``project`` = the project name (the request's
``path_params``). No IO: the route collected everything.

Mutations stay the core's project routes (``/p/{name}/vms/{vm}/...``): start / shutdown (SAFE), force-off and delete
(typed ``confirm`` = the VM name, V7-SEC-6), snapshots (create SAFE, revert / delete after an in-page question).
Items are disabled with a reason when VM management is off, the VM is protected or its state does not allow it.
"""

from __future__ import annotations

from urllib.parse import quote

from jinja2 import pass_context

from pm.infra.servers_model import fmt_bytes
from pm.web.dt import menus

DOTS = {"running": "running", "shut off": "idle", "paused": "waiting", "pmsuspended": "waiting",
        "crashed": "failed", "in shutdown": "waiting"}
REFRESH = "#pj-vms"


def size(n: float | None) -> str:
    """A byte count as text (fmt_bytes), "?" when unknown or 0."""
    return fmt_bytes(n) if n else "?"


def disks(d) -> tuple[str, str]:
    """(short cell text, full text) of a domain's disks: first disk's size (+N more); every source with sizes."""
    ds = list(getattr(d, "disks", ()) or ())
    if not ds:
        return "–", "no disks"
    first = ds[0]
    short = size(first.capacity) if first.capacity else first.target
    if len(ds) > 1:
        short += f" +{len(ds) - 1}"
    parts = []
    for k in ds:
        cap = f" {size(k.capacity)}" if k.capacity else ""
        used = f" ({size(k.allocation)} used)" if k.capacity and k.allocation is not None else ""
        parts.append(f"{k.target}: {k.source or '?'}{cap}{used}")
    return short, "; ".join(parts)


def vm_why(writable: bool, protected: bool) -> str:
    """Why no VM action is offered: VM management off, or a protected VM ("" = allowed)."""
    if not writable:
        return "VM management is off (vm_manage)"
    return "protected VM" if protected else ""


def vm_menu(name: str, state: str, base: str, writable: bool, protected: bool) -> list[dict]:
    """⋯ of one VM: details (console, snapshots), start / shut down, snapshot, revert, force off, delete."""
    url = f"{base}/{quote(name, safe='')}"
    why = vm_why(writable, protected)
    running, off = state == "running", state == "shut off"

    def act(id: str, label: str, icon: str, need: bool, need_why: str, **kw) -> dict:
        return menus.gated(id, label, icon, why=why, need=need, need_why=need_why, **kw)

    return menus.menu(
        menus.item("det", "Console and snapshots", "term", key="X", det=True),
        act("start", "Start", "play", off, "not shut off", click="[data-pjv-start]"),
        act("stop", "Shut down", "stop", running, "not running", click="[data-pjv-stop]"),
        menus.SEP,
        act("snap", "Take a snapshot", "clock", running or off, f"VM is {state}", post=f"{url}/snapshots",
            done=f"Snapshot of {name} taken", refresh=REFRESH),
        act("revert", "Revert to a snapshot…", "refresh", True, "", event="pjv:revert"),
        menus.SEP,
        act("off", "Force off…", "power", running, "not running", danger=True, post=f"{url}/force-off",
            vals={"confirm": name}, confirm=f"Force off {name}?", typed=name, ok="Force off",
            body="Like pulling the plug: the guest gets no shutdown, unsaved data in it is lost.",
            done=f"{name} is off", refresh=REFRESH),
        act("del", "Delete VM…", "trash", off, "shut it down first", danger=True, post=f"{url}/delete",
            vals={"confirm": name}, confirm=f"Delete VM {name}?", typed=name, ok="Delete",
            body="Removes the VM from libvirt (virsh undefine). Its disk images stay on disk.",
            done=f"Deleted {name}", refresh=REFRESH),
    )


def _row(d, base: str, writable: bool) -> dict:
    state = str(getattr(d, "state", "") or "unknown")
    protected = bool(getattr(d, "protected", False))
    disk, full = disks(d)
    vcpu = str(d.vcpus) if d.vcpus is not None else "?"
    ram = size(d.mem_kib * 1024 if d.mem_kib else None)
    url = f"{base}/{quote(d.name, safe='')}"
    can = writable and not protected
    act = None
    if can and state == "shut off":
        act = {"label": "Start", "icon": "play", "url": f"{url}/start", "hook": "data-pjv-start"}
    elif can and state == "running":
        act = {"label": "Shut down", "icon": "stop", "url": f"{url}/shutdown", "hook": "data-pjv-stop"}
    return {
        "name": d.name, "state": state, "dot": DOTS.get(state, "unknown"), "vcpu": vcpu, "ram": ram, "disk": disk,
        "protected": protected, "act": act,
        "meta": f"{state} · {vcpu} vCPU · {ram}",
        "kv": [("State", state), ("Size", f"{vcpu} vCPU · {ram}"), ("Disks", full),
               ("Autostart", "yes" if d.autostart else "no")] + ([("Protected", "yes: pm never changes it")]
                                                                  if protected else []),
        "menu": vm_menu(d.name, state, base, writable, protected),
    }


def view(vm, patterns, project: str) -> dict:
    """The VMs tab: ``state`` none (no patterns) | off (libvirt unavailable) | empty (no match) | ok, the rows, the
    stats line and the empty-state text."""
    pats = tuple(patterns or ())
    base = f"/p/{quote(str(project or ''), safe='')}/vms"
    if not pats:
        return {"state": "none", "rows": [], "stats": "", "text": "No VMs for this project: add VM name patterns "
                "(vms) to its project settings."}
    if vm is None or not getattr(vm, "available", False):
        err = getattr(vm, "error", "") if vm is not None else ""
        return {"state": "off", "rows": [], "stats": "", "text": "libvirt is not available here" +
                (f": {err}." if err else ".")}
    writable = bool(getattr(vm, "writable", False))
    rows = [_row(d, base, writable) for d in vm.domains]
    running = sum(1 for r in rows if r["state"] == "running")
    stats = f"{running} of {len(rows)} running · patterns {', '.join(pats)}"
    if not writable:
        stats += " · read-only (vm_manage off)"
    text = "" if rows else f"No VM matches this project's patterns ({', '.join(pats)})."
    return {"state": "ok" if rows else "empty", "rows": rows, "stats": stats, "text": text, "writable": writable}


@pass_context
def dt_pjops(ctx) -> dict:
    """The VMs tab of the page being rendered: ``view()`` of the route's ``vm``, ``patterns`` and project name."""
    req = ctx.get("request")
    name = str((getattr(req, "path_params", None) or {}).get("name", "")) if req is not None else ""
    return view(ctx.get("vm"), ctx.get("patterns"), name)
