"""The dt Servers page, Resources and VMs tabs (PRD.md §6.9), and the URL / sort / meter helpers the whole
page shares (``pm.web.dt.servers`` builds the page on them).

Resources: the core's summary tiles as meters, one table at a time (Programs · tmux sessions · Containers · VMs,
``?r=``), sorted by a header click (``?so=``), the top 10 with "Show all" (``?all=1``), ⋯ per row: a program's
Stop all / Kill all (typed program name: the core's ``/servers/proc/program/{term|kill}`` checks ``confirm``), its
processes in the row details with their own ⋯ (Stop / Kill typed with the pid, Pause / Resume), Open in tmux, Show in
Services / VMs.
VMs: the whole host's libvirt guests (sortable), the image pool meter, images All / Unused (``?im=unused``), ⋯ Start,
Shut down, Force off (typed), Undefine (typed, shut off only), image Delete (typed). No IO: the route collected it all;
``pdesc`` (the core's process describer) is called only for the rows shown.
"""

from __future__ import annotations

from urllib.parse import quote, urlencode

from pm.infra.resources import fmt_cpu
from pm.infra.servers_model import fmt_bytes
from pm.web.dt import menus
from pm.web.dt.pjops import DOTS, disks, size, vm_why

KEYS = ("t", "f", "g", "r", "so", "all", "im", "q")
REFRESH = "#sv-panel"
VMS_REFRESH = "#sv-vms"
TOP = 10
GROUPS = (("programs", "Programs", "Program", "Procs"), ("tmux", "tmux sessions", "Session", "Panes / procs"),
          ("containers", "Containers", "Container", ""), ("vms", "VMs", "Guest", "Procs"))
POOL_WARN, POOL_CRIT = 80.0, 92.0


# ---------------------------------------------------------------- URL state, sort, meters (whole page)
def query(req) -> dict[str, str]:
    """The page's URL state: known keys only, each clipped."""
    qp = getattr(req, "query_params", None) or {}
    return {k: str(qp.get(k, ""))[:80] for k in KEYS if qp.get(k)}


def url(q: dict, path: str = "/servers", **change) -> str:
    """``path`` with the state ``q`` changed by ``change`` (None / "" drops a key)."""
    d = {**q, **change}
    qs = urlencode([(k, d[k]) for k in KEYS if d.get(k)])
    return path + ("?" + qs if qs else "")


def sort_cols(q: dict, cur: str, cols: list[dict]) -> list[dict]:
    """Header cells; ``"sort": (key, desc_first)`` adds the sort link. Clicking the current column reverses it."""
    out = []
    for c in cols:
        c = dict(c)
        s = c.pop("sort", None)
        if s:
            key, desc = s
            nxt = f"-{key}" if cur == key else key if cur == f"-{key}" else f"-{key}" if desc else key
            c.update(key=key, href=url(q, so=nxt))
        out.append(c)
    return out


def sorted_by(rows: list[dict], so: str, keys: dict) -> list[dict]:
    """``rows`` ordered by ``so`` (``key`` / ``-key``) through ``keys[key](row)``; an unknown key keeps the order."""
    fn = keys.get(so.lstrip("-"))
    return sorted(rows, key=fn, reverse=so.startswith("-")) if fn else rows


def meter(key: str, label: str, value: str, *, small: str = "", pct=None, level: str = "ok", sub: str = "",
          href: str = "", title: str = "") -> dict:
    """One cell of ``parts/_meter.html`` (docs/themes/dt/PARTS.md §16)."""
    return {"key": key, "label": label, "value": value, "small": small, "pct": pct, "level": level, "sub": sub,
            "href": href, "title": title}


# ---------------------------------------------------------------- Resources
def _group(q: dict) -> str:
    return q.get("r") if q.get("r") in {g[0] for g in GROUPS} else "programs"


def res_segs(r, q: dict) -> list[dict]:
    """Programs · tmux sessions · Containers · VMs with their row counts ("…" while docker stats run)."""
    cur = _group(q)
    out = []
    for key, label, _, _ in GROUPS:
        pending = key == "containers" and r is not None and r.docker_pending
        n = "…" if pending else (len(getattr(r, key)) if r is not None else None)
        out.append({"key": key, "label": label, "count": n, "cur": key == cur,
                    "title": "Counting: docker stats are still running" if pending else "",
                    "href": url({"t": "resources", "q": q.get("q", "")}, r="" if key == "programs" else key)})
    return out


def _sig(id: str, label: str, icon: str, url_: str, vals: dict, typed: str, what: str, body: str, **kw) -> dict:
    return menus.item(id, label, icon, post=url_, vals=vals, confirm=what, typed=typed, body=body,
                      ok=label.rstrip("…").split(" ")[0], refresh=REFRESH, **kw)


def program_menu(name: str, can: bool) -> list[dict]:
    """⋯ of a Programs row: Stop all / Kill all (typed program name), the processes, copy."""
    why = "" if can else "process control is off (proc_manage)"
    why = why or ("a VM: use the VMs tab" if name.startswith("qemu-system") else "")
    vals = {"program": name, "confirm": name}
    body = (f"Sends {{}} to every {name} process you own. pm, the tmux server, protected sessions and PID 1 are "
            "skipped.")
    if why:
        acts = [menus.item("term", "Stop all…", "stop", disabled=True, why=why),
                menus.item("kill", "Kill all…", "power", danger=True, disabled=True, why=why)]
    else:
        acts = [_sig("term", "Stop all…", "stop", "/servers/proc/program/term", vals, name,
                     f"Stop all {name} processes?", body.format("SIGTERM (asks them to quit)"),
                     done=f"Asked {name} to quit"),
                _sig("kill", "Kill all…", "power", "/servers/proc/program/kill", vals, name,
                     f"Kill all {name} processes?", body.format("SIGKILL (no time to save)"), danger=True,
                     done=f"Killed {name}")]
    return menus.menu(acts, menus.SEP, menus.item("det", "Processes", "down", key="X", det=True),
                      menus.item("copy", "Copy name", "copy", copy=name))


def proc_menu(prog: str, pid: int, start: int, paused: bool, can: bool) -> list[dict]:
    """⋯ of one process: Stop / Kill (typed pid), Pause or Resume (plain confirm)."""
    if not can:
        why = "process control is off (proc_manage)"
        return [menus.item("term", "Stop…", "stop", disabled=True, why=why),
                menus.item("kill", "Kill…", "power", danger=True, disabled=True, why=why)]
    vals, p = {"pid": str(pid), "start": str(start)}, str(pid)
    who = f"{prog} (pid {pid})"
    pause = (menus.item("cont", "Resume", "play", post="/servers/proc/cont", vals=vals, done=f"Resumed {who}",
                        refresh=REFRESH) if paused else
             menus.item("stop", "Pause", "clock", post="/servers/proc/stop", vals=vals, confirm=f"Pause {who}?",
                        body="Sends SIGSTOP; Resume sends SIGCONT.", ok="Pause", done=f"Paused {who}",
                        refresh=REFRESH))
    return menus.menu(
        _sig("term", "Stop…", "stop", "/servers/proc/term", vals, p, f"Stop {who}?",
             "Sends SIGTERM: asks the process to quit. Type the pid to confirm.", done=f"Asked {who} to quit"),
        pause, menus.SEP,
        _sig("kill", "Kill…", "power", "/servers/proc/kill", vals, p, f"Kill {who}?",
             "Sends SIGKILL: the process gets no time to save. Type the pid to confirm.", danger=True,
             done=f"Killed {who}"))


def _row_menu(grp: str, name: str, can: bool) -> list[dict]:
    if grp == "programs":
        return program_menu(name, can)
    copy = menus.item("copy", "Copy name", "copy", copy=name)
    if grp == "tmux":
        go = menus.item("open", "Open in tmux", "tmux", href="/tmux?" + urlencode({"q": name}))
    elif grp == "containers":
        go = menus.item("open", "Show in Services", "box", href=url({}, t="services", q=name))
    else:
        go = menus.item("open", "Show in VMs", "vm", href=url({}, t="vms", q=name))
    return menus.menu(go, copy)


def _procs(r, pdesc, prog: str, can: bool) -> list[dict]:
    out = []
    for pid, start, rss, cpu, state in r.items:
        out.append({"pid": pid, "desc": pdesc(pid, start) if pdesc else "", "cpu": fmt_cpu(cpu),
                    "ram": fmt_bytes(rss), "paused": state == "T",
                    "menu": proc_menu(prog, pid, start, state == "T", can)})
    return out


RES_SORT = {"name": lambda r: r.name.lower(), "procs": lambda r: (r.procs, r.extra), "cpu": lambda r: r.cpu,
            "ram": lambda r: r.rss}


def resources(ctx, q: dict) -> dict:
    """The Resources tab: meters, the chosen group's table (top 10 or all), its foot."""
    r = ctx.get("res")
    grp = _group(q)
    _, title, label, ncol = next(g for g in GROUPS if g[0] == grp)
    so = q.get("so", "") if q.get("so", "").lstrip("-") in RES_SORT else "-ram"
    key = RES_SORT[so.lstrip("-")]
    words = q.get("q", "").lower().split()
    rows = sorted((x for x in getattr(r, grp) if all(w in x.name.lower() for w in words)), key=key,
                  reverse=so.startswith("-"))
    total, mx = len(rows), max((x.rss for x in rows), default=0) or 1
    shown = rows if q.get("all") else rows[:TOP]
    can = bool(ctx.get("can_proc"))
    out = []
    for x in shown:
        n = f"{x.extra} / {x.procs}" if grp == "tmux" else "" if grp == "containers" else str(x.procs)
        out.append({"name": x.name, "n": n, "cpu": fmt_cpu(x.cpu), "ram": fmt_bytes(x.rss),
                    "share": x.rss * 100 / mx, "meta": " · ".join(s for s in (
                        f"{ncol.lower()} {n}" if n else "", f"CPU {fmt_cpu(x.cpu)}", fmt_bytes(x.rss)) if s),
                    "menu": _row_menu(grp, x.name, can),
                    "procs": _procs(x, ctx.get("pdesc"), x.name, can) if grp == "programs" and x.items else None,
                    "more": x.procs - len(x.items) if grp == "programs" and x.items else 0})
    cols = sort_cols(q, so, [{"label": label, "sort": ("name", False)}]
                     + ([{"label": ncol, "sort": ("procs", True), "num": True, "cls": "hide-s"}] if ncol else
                        [{"sr": "Processes", "cls": "hide-s"}])
                     + [{"label": "CPU", "sort": ("cpu", True), "num": True, "cls": "hide-s"},
                        {"label": "RAM", "sort": ("ram", True), "num": True}, {"sr": "Actions"}])
    pending = grp == "containers" and r.docker_pending
    off = (grp == "containers" and not r.docker_ok) or (grp == "tmux" and not r.tmux_ok)
    text = ("Collecting container stats…" if pending else f"No data: {title} could not be read." if off
            else f"No {title.lower()} match “{q['q']}”." if words else f"No {title.lower()} running.")
    return {"group": grp, "title": title, "rows": out, "cols": cols, "sort": so, "shown": len(out), "total": total,
            "all_href": url(q, all="1"), "text": text, "can": can,
            "meters": [meter(t.key, t.label, t.value, pct=t.pct, level=t.status, sub=t.detail)
                       for t in ctx.get("rtiles") or ()],
            "foot": f"CPU 100% = one core · sorted by {so.lstrip('-')} · stop and kill: your own processes only; "
                    "never pm, the tmux server, protected sessions or PID 1."}


# ---------------------------------------------------------------- VMs
def unused_images(vm) -> list:
    """File volumes no domain uses (also not as a backing file); [] without a listing."""
    if vm is None or not getattr(vm, "available", False):
        return []
    return [v for v in vm.volumes if not v.used_by and v.type == "file"]


def vol_sizes(v) -> str:
    on = f", {fmt_bytes(v.allocation)} on disk" if v.allocation is not None else ""
    return (fmt_bytes(v.capacity) if v.capacity else "size unknown") + on


def vm_menu(d, writable: bool) -> list[dict]:
    """⋯ of one VM (whole-host routes ``/servers/vms/{name}/…``)."""
    why = vm_why(writable, d.protected)
    base = f"/servers/vms/{quote(d.name, safe='')}"
    running, off = d.state == "running", d.state == "shut off"

    def act(id: str, label: str, icon: str, need: bool, need_why: str, **kw) -> dict:
        return menus.gated(id, label, icon, why=why, need=need, need_why=need_why, post=f"{base}/{id}",
                           refresh=VMS_REFRESH, **kw)

    return menus.menu(
        act("start", "Start", "play", off, "not shut off", done=f"Started {d.name}"),
        act("shutdown", "Shut down", "stop", running, "not running", confirm=f"Shut down {d.name}?", ok="Shut down",
            body="Sends an ACPI shutdown; the guest shuts down on its own.", done=f"Shutting down {d.name}"),
        act("destroy", "Force off…", "power", running, "not running", danger=True, vals={"confirm": d.name},
            confirm=f"Force off {d.name}?", typed=d.name, ok="Force off",
            body="Like pulling the plug: the guest gets no shutdown, unsaved data in it is lost.",
            done=f"{d.name} is off"),
        menus.SEP,
        act("undefine", "Undefine…", "trash", off, "shut it down first", danger=True, vals={"confirm": d.name},
            confirm=f"Undefine {d.name}?", typed=d.name, ok="Undefine",
            body="Removes the VM from libvirt (virsh undefine). Its disk images stay in the pool.",
            done=f"Undefined {d.name}"),
        menus.SEP, menus.details(), menus.item("copy", "Copy name", "copy", copy=d.name))


def image_menu(v, writable: bool) -> list[dict]:
    why = ("VM management is off (vm_manage)" if not writable else "" if v.deletable else
           f"used by {', '.join(v.used_by)}" if v.used_by else "not a deletable file volume")
    delete = (menus.item("delete", "Delete…", "trash", danger=True, disabled=True, why=why) if why else
              menus.item("delete", "Delete…", "trash", danger=True,
                         post=f"/servers/images/{quote(v.name, safe='')}/delete", vals={"confirm": v.name},
                         confirm=f"Delete {v.name}?", typed=v.name, ok="Delete",
                         body="Deletes the volume from the pool. No VM uses it.",
                         done=f"Deleted {v.name}", refresh=VMS_REFRESH))
    return menus.menu(delete, menus.SEP, menus.item("copy", "Copy path", "copy", copy=v.path or v.name))


VM_SORT = {"name": lambda r: r["name"].lower(), "cpu": lambda r: r["vcpus"], "ram": lambda r: r["mem"],
           "disk": lambda r: r["cap"], "state": lambda r: (r["state"] != "running", r["state"], r["name"])}


def _vm_row(d, writable: bool) -> dict:
    disk, full = disks(d)
    ram = size(d.mem_kib * 1024 if d.mem_kib else None)
    vcpu = str(d.vcpus) if d.vcpus is not None else "?"
    menu = vm_menu(d, writable)
    live = {i["id"] for i in menu if not i.get("sep") and not i.get("disabled")}
    vis = "start" if "start" in live else "shutdown" if "shutdown" in live else ""
    return {"name": d.name, "state": d.state, "dot": DOTS.get(d.state, "unknown"), "vcpu": vcpu, "ram": ram,
            "disk": disk, "protected": d.protected, "vcpus": d.vcpus or 0, "mem": d.mem_kib or 0,
            "cap": sum(k.capacity or 0 for k in d.disks), "q": d.name, "menu": menu,
            "vis": {"id": vis, "label": "Start" if vis == "start" else "Shut down",
                    "icon": "play" if vis == "start" else "stop"} if vis else None,
            "meta": f"{d.state} · {vcpu} vCPU · {ram}",
            "kv": [("State", d.state), ("Size", f"{vcpu} vCPU · {ram}"), ("Disks", full),
                   ("Autostart", "yes" if d.autostart else "no"), ("Persistent", "yes" if d.persistent else "no")]
            + ([("Protected", "yes: pm never changes it")] if d.protected else [])}


def _pool(vm) -> dict | None:
    pl = vm.pool
    if pl is None or not pl.capacity:
        return None
    pct = (pl.allocation or 0) * 100 / pl.capacity
    free = f" · {fmt_bytes(pl.available)} free" if pl.available is not None else ""
    return meter("pool", f"Pool {pl.name}", fmt_bytes(pl.allocation or 0), small=f"of {fmt_bytes(pl.capacity)}{free}",
                 pct=pct, level="crit" if pct >= POOL_CRIT else "warn" if pct >= POOL_WARN else "ok",
                 sub=f"{len(vm.volumes)} volumes", title="Image pool usage")


def vms(vm, q: dict) -> dict:
    """The VMs tab: guests (sortable), the pool meter, images All / Unused."""
    q = {k: v for k, v in q.items() if k in ("so", "im", "q")} | {"t": "vms"}
    if not getattr(vm, "available", False):
        err = getattr(vm, "error", "")
        return {"ok": False, "text": "libvirt is not available here" + (f": {err}." if err else ".")}
    writable = bool(vm.writable)
    so = q.get("so", "name") if q.get("so", "name").lstrip("-") in VM_SORT else "name"
    rows = sorted_by([_vm_row(d, writable) for d in vm.domains], so, VM_SORT)
    running = sum(1 for r in rows if r["state"] == "running")
    unused = {v.name for v in unused_images(vm)}
    im = "unused" if q.get("im") == "unused" else "all"
    imgs = [{"name": v.name, "size": fmt_bytes(v.capacity) if v.capacity else "–",
             "disk": fmt_bytes(v.allocation) if v.allocation is not None else "–",
             "used": ", ".join(v.used_by) or ("unused" if v.name in unused else "–"),
             "dot": "waiting" if v.name in unused else "idle", "word": "unused" if v.name in unused else "in use",
             "q": f"{v.name} {' '.join(v.used_by)}", "menu": image_menu(v, writable),
             "meta": f"{vol_sizes(v)} · " + (", ".join(v.used_by) or "unused")}
            for v in vm.volumes if im == "all" or v.name in unused]
    cols = sort_cols(q, so, [{"sr": "State"}, {"label": "Name", "sort": ("name", False)},
                             {"label": "vCPU", "sort": ("cpu", True), "num": True, "cls": "hide-n"},
                             {"label": "RAM", "sort": ("ram", True), "num": True, "cls": "hide-n"},
                             {"label": "Disk", "sort": ("disk", True), "num": True, "cls": "hide-s"},
                             {"label": "State", "sort": ("state", False), "cls": "hide-n"}, {"sr": "Actions"}])
    note = f"{running} running / {len(rows)} domains · VMs matching vm_protected are never changed"
    return {"ok": True, "rows": rows, "cols": cols, "sort": so, "note": note + ("" if writable else
            " · read-only (vm_manage off)"), "pool": _pool(vm), "images": imgs, "pool_name": getattr(
            vm.pool, "name", ""), "img_segs": [
            {"key": "all", "label": "All", "count": len(vm.volumes), "cur": im == "all", "href": url(q, im="")},
            {"key": "unused", "label": "Unused", "count": len(unused), "cur": im == "unused", "warn": bool(unused),
             "href": url(q, im="unused")}],
            "img_text": "No unused image." if im == "unused" else "No images listed."}
