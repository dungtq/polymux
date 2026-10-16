"""The dt Servers page's view model (PRD.md §6.9, LAYOUT.md §5 Servers): tabs Overview · Services · Resources ·
VMs, the head's stats line, the controls row of each tab, and the Overview and Services content. Resources and VMs:
``pm.web.dt.servers_res``.

``dt_servers()`` is a Jinja global (``template_globals()``) read by ``servers.html`` and the fragments the core returns
(``_servers.html`` for ``GET /servers/panel``, ``_vms.html`` for ``GET /servers/vms``). It reads only the route's
context (``p`` Panel, ``summary``, ``stamp``, ``tab``, ``can_ct``, ``ct_protected``, ``vm``, ``res``, ``rtiles``,
``can_proc``, ``pdesc``, ``request``) and the URL; no IO. URL state: ``t`` tab, ``f`` Services filter, ``g=stack`` By
stack, ``so`` sort (``key`` ascending, ``-key`` descending), ``r`` Resources group, ``all=1`` Show all, ``im=unused``
images, ``q`` the filter box's first text. The panel's poll URL carries the same state (README A21).

Mutations are the core's routes, run from ⋯ items (``menus.item(post=...)``, refresh ``#sv-panel``): container
start / restart / stop / kill, compose stack restart (protected members skipped by the core). Protected containers
(``container_protected``) and a page without container control show disabled items that say why.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from jinja2 import pass_context

from pm.infra.servers_model import LAT_WARN_MS
from pm.web.dt import menus
from pm.web.dt import servers_res as res
from pm.web.dt.servers_res import REFRESH, meter, query, sort_cols, sorted_by, url

TABS = (("overview", "Overview"), ("services", "Services"), ("resources", "Resources"), ("vms", "VMs"))
FILTERS = (("all", "All"), ("running", "Running"), ("unhealthy", "Unhealthy"), ("exited", "Exited"))
STATUS = {"ok": ("running", "All OK", "ok"), "warn": ("waiting", "Worth a look", "warn"),
          "crit": ("failed", "Needs attention", "bad")}
_UP = re.compile(r"^Up (?:About )?(an?|\d+|Less than a(?:n)?) (second|minute|hour|day|week|month|year)")
_UNIT_S = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2592000,
           "year": 31536000}


# ---------------------------------------------------------------- meters and status (Overview)
def _pct_meter(m, key: str) -> dict:
    sub = re.sub(r" \(\d+%\)$", "", m.text)
    return meter(key, m.label, f"{m.pct:.0f}%", pct=m.pct, level=m.level, sub=sub, title=f"{m.label}: {m.text}")


def _load_meter(m) -> dict:
    nums, _, n = m.text.partition(" on ")
    vals = nums.split()
    cpus = int(n.split()[0]) if n.split() and n.split()[0].isdigit() else 0
    per = f"{float(vals[0]) / cpus:.2f} per CPU · {cpus} CPUs" if vals and cpus else m.text
    return meter("load", "Load", vals[0] if vals else m.text, small=" ".join(vals[1:]), pct=m.pct, level=m.level,
                 sub=per, title=f"Load average 1 / 5 / 15 min: {m.text}")


def _docker_meter(p) -> dict:
    d = p.docker
    if not d.available:
        word = "collecting…" if "docker" in p.collecting else "docker unavailable"
        return meter("containers", "Containers", "–", level="na", sub=word)
    exited = sum(1 for c in d.containers if c.problem and c.health != "unhealthy")
    done = sum(1 for c in d.containers if c.clean_exit)
    bits = [f"{d.unhealthy} unhealthy"] + ([f"{exited} exited"] if exited else []) + (
        [f"{done} one-shot done"] if done else [])
    lv = "crit" if d.unhealthy else "warn" if exited else "ok"
    return meter("containers", "Containers", str(d.running), small=f"/ {d.total}", level=lv, sub=" · ".join(bits),
                 href=url({}, t="services"))


def meters(p) -> list[dict]:
    """Overview meters: load, memory, swap, every disk, containers, VMs, tmux, uptime, pm."""
    by = {m.label: m for m in p.metrics}
    out = [_load_meter(by["Load"])] if "Load" in by else []
    out += [_pct_meter(m, m.label.lower().replace(" ", "-")) for m in p.metrics
            if m.pct is not None and (m.label in ("Memory", "Swap") or m.label.startswith("Disk "))]
    out.append(_docker_meter(p))
    if p.libvirt is not None:
        out.append(meter("vms", "VMs", str(p.libvirt.running), small=f"/ {p.libvirt.total}", sub="libvirt guests",
                         href=url({}, t="vms")))
    t = p.tmux
    if t.ok:
        out.append(meter("tmux", "tmux", str(t.sessions), small="sessions",
                         sub=f"{t.windows} windows · {t.panes} panes · {t.attached} attached", href="/tmux"))
    if "Uptime" in by:
        logins = by.get("Logins")
        out.append(meter("uptime", "Uptime", by["Uptime"].text, sub=f"{logins.text} logins" if logins else ""))
    pm = by.get("pm service")
    if pm:
        state, _, up = pm.text.partition(", up ")
        ntfy = ("ntfy up" if p.ntfy_ok else "ntfy down") if p.ntfy_ok is not None else ""
        n24 = f"{p.ntfy_24h} in 24 h" if p.ntfy_24h is not None else ""
        out.append(meter("pm", "pm", state, level=pm.level, sub=" · ".join(x for x in (
            f"up {up}" if up else "", ntfy, n24) if x)))
    return out


def _names(xs, n: int = 3) -> str:
    xs = list(xs)
    return ", ".join(xs[:n]) + (f" +{len(xs) - n}" if len(xs) > n else "")


def _attn(tone: str, icon: str, text: str, go: str = "", href: str = "") -> dict:
    return {"tone": tone, "icon": icon, "text": text, "go": go, "href": href}


def _attn_docker(p) -> list[dict]:
    d = p.docker
    if not d.available:
        return []
    out = []
    unh = [c.name for c in d.containers if c.health == "unhealthy"]
    bad = [c.name for c in d.containers if c.problem and c.health != "unhealthy"]
    done = [c.name for c in d.containers if c.clean_exit]
    if unh:
        out.append(_attn("bad", "box", f"{len(unh)} unhealthy: {_names(unh)}", "Services",
                         url({}, t="services", f="unhealthy")))
    if bad:
        out.append(_attn("bad", "box", f"{len(bad)} exited with an error or restarting: {_names(bad)}", "Services",
                         url({}, t="services", f="exited")))
    if done:
        out.append(_attn("muted", "box", f"{len(done)} one-shot container{'s' if len(done) != 1 else ''} finished "
                         f"(exit 0): {_names(done)}", "Services", url({}, t="services", f="exited")))
    return out


def attention(p, vm) -> list[dict]:
    """The "worth a look" list: what is wrong or unusual, each linking to the tab that shows it."""
    out = _attn_docker(p)
    for h in p.hosts:
        if not h.up:
            out.append(_attn("bad", "server", f"{h.name} ({h.host}:{h.port}) does not answer", "Hosts", "#sv-hosts"))
        elif h.latency_ms is not None and h.latency_ms >= LAT_WARN_MS:
            out.append(_attn("warn", "server", f"{h.name} is slow: {h.latency_ms:.0f} ms", "Hosts", "#sv-hosts"))
    for m in p.metrics:
        if m.level in ("warn", "crit"):
            go = (("tmux", "/tmux") if m.label == "tmux server" else ("Resources", url({}, t="resources"))
                  if m.label in ("Load", "Memory", "Swap") else ("", ""))
            out.append(_attn("bad" if m.level == "crit" else "warn", "cpu", f"{m.label}: {m.text}", *go))
    if p.stale:
        out.append(_attn("warn", "clock", f"Stale data: {', '.join(p.failed)} did not answer"))
    if p.ntfy_ok is False:
        out.append(_attn("warn", "bell", "The ntfy mirror does not answer", "Notifications", "/ntfy"))
    unused = res.unused_images(vm)
    if unused:
        text = (f"{unused[0].name} is not used by any VM ({res.vol_sizes(unused[0])})" if len(unused) == 1
                else f"{len(unused)} images are not used by any VM: {_names(v.name for v in unused)}")
        out.append(_attn("warn", "vm", text, "VMs › Images", url({}, t="vms", im="unused")))
    return out


def status(p, summary, stamp: str) -> dict:
    """The one status line: dot, word, and what it is based on."""
    st = getattr(summary, "status", "ok") if summary is not None else "ok"
    dot, word, cls = STATUS.get(st, STATUS["ok"])
    up = sum(1 for h in p.hosts if h.up)
    bits = [f"{up} of {len(p.hosts)} hosts reachable"] if p.hosts else []
    if p.collecting:
        bits.append(f"collecting {', '.join(p.collecting)}…")
    bits.append(f"updated {stamp}")
    return {"dot": dot, "word": word, "cls": cls, "note": " · ".join(bits)}


def host_rows(p) -> list[dict]:
    out = []
    for h in p.hosts:
        addr = f"{h.host}:{h.port}"
        lat = f"{h.latency_ms:.0f} ms" if h.up and h.latency_ms is not None else "–"
        slow = h.up and h.latency_ms is not None and h.latency_ms >= LAT_WARN_MS
        out.append({"name": h.name, "addr": addr, "lat": lat, "note": h.note, "dot": "running" if h.up else "failed",
                    "word": ("up, slow" if slow else "up") if h.up else "down", "lat_cls": "warn" if slow else "",
                    "meta": " · ".join(x for x in (addr, lat, h.note) if x),
                    "menu": menus.menu(menus.item("copy", "Copy address", "copy", copy=addr))})
    return out


def this_host(p) -> list[tuple]:
    by = {m.label: m for m in p.metrics}
    rows = [(k, by[k].text, "warn" if by[k].level in ("warn", "crit") else "")
            for k in ("Kernel", "Uptime", "Logins", "pm service", "tmux server") if k in by]
    if p.libvirt is not None:
        rows.append(("libvirt", f"{p.libvirt.running} running / {p.libvirt.total} guests", ""))
    if p.ntfy_ok is not None:
        n24 = f" · {p.ntfy_24h} messages in 24 h" if p.ntfy_24h is not None else ""
        rows.append(("ntfy", ("up" if p.ntfy_ok else "down") + n24, "" if p.ntfy_ok else "warn"))
    return rows


def overview(ctx) -> dict:
    p = ctx.get("p")
    return {"status": status(p, ctx.get("summary"), str(ctx.get("stamp") or "")), "attn": attention(p, ctx.get("vm")),
            "meters": meters(p), "hosts": host_rows(p), "host": this_host(p), "hostname": p.hostname}


# ---------------------------------------------------------------- Services
def uptime(status: str) -> tuple[str, int]:
    """docker's "Up 3 days (healthy)" -> ("3d", seconds); ("–", 0) when not up."""
    m = _UP.match(status or "")
    if not m:
        return "–", 0
    n = 1 if not m[1][0].isdigit() else int(m[1])
    unit = m[2]
    short = "<1m" if unit == "second" else f"{n}{'mo' if unit == 'month' else unit[0]}"
    return short, n * _UNIT_S[unit]


def bucket(c) -> str:
    """The segment a container falls in: running | unhealthy | exited."""
    if c.state in ("exited", "dead", "created"):
        return "exited"
    if c.health == "unhealthy" or c.state in ("restarting", "removing"):
        return "unhealthy"
    return "running"


def health(c) -> tuple[str, str, str]:
    """(dot, word, cell class) of a container."""
    if c.health == "unhealthy":
        return "failed", "unhealthy", "bad"
    if c.clean_exit:
        return "idle", "exited (0)", "muted"
    if c.problem:
        code = re.match(r"^Exited \((\d+)\)", c.status or "")
        return "failed", f"exited ({code[1]})" if code else c.state, "bad"
    if c.health == "starting" or c.state != "running":
        return "waiting", c.health or c.state, "warn"
    return "running", c.health or "running", "ok" if c.health == "healthy" else "muted"


def ct_menu(c, can: bool, protected: bool, url_base: str = "/servers/containers") -> list[dict]:
    """⋯ of one container: Start (exited) or Restart / Stop / Kill, the stack's restart, details, copy."""
    why = "" if can else "container control is off (container_manage)"
    why = why or ("protected name: pm never stops it" if protected else "")
    base = f"{url_base}/{quote(c.name, safe='')}"
    up = c.state == "running"

    def act(id: str, label: str, icon: str, need: bool, need_why: str, **kw) -> dict:
        return menus.gated(id, label, icon, why=why, need=need, need_why=need_why, post=f"{base}/{id}",
                           refresh=REFRESH, **kw)

    stack = menus.gated("stack", f"Restart stack {c.stack}…", "refresh", why="" if can else why,
                        post=f"/servers/stacks/{quote(c.stack, safe='')}/restart", confirm=f"Restart stack {c.stack}?",
                        body=f"Runs docker restart for every container of the compose project {c.stack}. Protected "
                        "containers in it are skipped.", ok="Restart stack", done=f"Restarted stack {c.stack}",
                        refresh=REFRESH) if c.stack else None
    return menus.menu(
        act("start", "Start", "play", not up, "already running", done=f"Started {c.name}"),
        act("restart", "Restart", "refresh", up, "not running", confirm=f"Restart {c.name}?", ok="Restart",
            body=f"Runs docker restart {c.name}.", done=f"Restarted {c.name}"),
        act("stop", "Stop", "stop", up, "not running", confirm=f"Stop {c.name}?", ok="Stop",
            body=f"Runs docker stop {c.name} (10 s to shut down).", done=f"Stopped {c.name}"),
        act("kill", "Kill", "power", up, "not running", danger=True, confirm=f"Kill {c.name}?", ok="Kill",
            body="The container gets SIGKILL and no time to shut down.", done=f"Killed {c.name}"),
        menus.SEP, stack, menus.SEP, menus.details(), menus.item("copy", "Copy name", "copy", copy=c.name))


def ct_row(c, can: bool, protected: bool) -> dict:
    dot, word, cls = health(c)
    up, secs = uptime(c.status)
    ports = [x.strip() for x in (c.ports or "").split(",") if x.strip()]
    menu = ct_menu(c, can, protected)
    ids = {i["id"] for i in menu if not i.get("sep") and not i.get("disabled")}
    vis = "start" if "start" in ids else "restart" if "restart" in ids else ""
    acts = [a for a in (("start", "Start", "play"), ("restart", "Restart", "refresh"), ("stop", "Stop", "stop"),
                        ("kill", "Kill", "power")) if a[0] in ids]
    return {"name": c.name, "image": c.image, "stack": c.stack, "dot": dot, "word": word, "cls": cls, "up": up,
            "secs": secs, "ports": len(ports), "bucket": bucket(c), "protected": protected,
            "q": " ".join((c.name, c.image, c.stack)), "meta": " · ".join(x for x in (word, c.stack, c.image) if x),
            "vis": {"id": vis, "label": vis.capitalize(), "icon": "play" if vis == "start" else "refresh"}
            if vis else None, "acts": acts, "menu": menu,
            "kv": [("Image", c.image), ("Status", c.status), ("Health", word),
                   ("Stack", f"{c.stack} · {c.workdir}" if c.workdir else c.stack or "not a compose project"),
                   ("Ports", ", ".join(ports) or "none published")]
            + ([("Protected", "yes: can be looked at, never stopped from here")] if protected else [])}


SV_SORT = {"name": lambda r: r["name"].lower(), "stack": lambda r: (r["stack"] or "~", r["name"]),
           "image": lambda r: r["image"].lower(), "health": lambda r: ({"unhealthy": 0, "exited": 1}.get(
               r["bucket"], 2), r["name"]), "up": lambda r: r["secs"]}


def services(ctx, q: dict) -> dict:
    p = ctx.get("p")
    d = p.docker
    if not d.available:
        return {"ok": False, "text": "Collecting container data…" if "docker" in p.collecting else
                "Docker is not available on this host.", "rows": [], "groups": []}
    can = bool(ctx.get("can_ct"))
    prot = ctx.get("ct_protected") or (lambda n: False)
    rows = [ct_row(c, can, bool(prot(c.name))) for c in d.containers]
    f = q.get("f") if q.get("f") in dict(FILTERS) else "all"
    shown = [r for r in rows if f == "all" or r["bucket"] == f]
    so = q.get("so", "name")
    shown = sorted_by(shown, so, SV_SORT)
    groups = []
    if q.get("g") == "stack":
        by: dict[str, list] = {}
        for r in shown:
            by.setdefault(r["stack"], []).append(r)
        for s in sorted(by, key=lambda k: (not k, k)):
            members = by[s]
            run = sum(1 for r in members if r["bucket"] == "running")
            bad = any(r["bucket"] == "unhealthy" for r in members)
            groups.append({"key": s or "-", "label": s or "No stack", "count": f"{run} / {len(members)} running",
                           "cls": "bad" if bad else "", "rows": members,
                           "actions": [{"label": "Restart stack", "icon": "refresh", "title": f"Restart stack {s}",
                                        "attrs": {"data-sv-stack": s}}] if s and can else None})
    cols = sort_cols(q, so, [{"sr": "State"}, {"label": "Name", "sort": ("name", False)},
                             {"label": "Stack", "sort": ("stack", False), "cls": "hide-n"},
                             {"label": "Image", "sort": ("image", False), "cls": "hide-s"},
                             {"label": "Health", "sort": ("health", False), "cls": "hide-s"},
                             {"label": "Uptime", "sort": ("up", True), "num": True},
                             {"label": "Ports", "num": True, "cls": "hide-n"}, {"sr": "Actions"}])
    empty = "No container matches." if rows else "No containers on this host."
    first = groups[0]["rows"] if groups else shown
    return {"ok": True, "rows": shown, "groups": groups, "cols": cols, "sort": so, "text": empty,
            "cur": first[0]["name"] if first else "",
            "foot": f"Docker on {p.hostname or 'this host'} · updated {ctx.get('stamp') or ''} · protected names "
                    "can be looked at, never stopped from here.", "can": can}


def service_segs(p, q: dict) -> list[dict]:
    rows = list(p.docker.containers) if p.docker.available else []
    n = {k: 0 for k, _ in FILTERS}
    for c in rows:
        n[bucket(c)] += 1
    n["all"] = len(rows)
    cur = q.get("f") if q.get("f") in dict(FILTERS) else "all"
    return [{"key": k, "label": lab, "count": n[k], "cur": k == cur, "bad": k == "unhealthy" and n[k] > 0,
             "href": url(q, f="" if k == "all" else k)} for k, lab in FILTERS]


# ---------------------------------------------------------------- the page
def tabs(p, tab: str) -> list[dict]:
    d = p.docker
    out = []
    for k, lab in TABS:
        it = {"key": k, "label": lab, "href": url({}, t="" if k == "overview" else k), "cur": k == tab}
        if k == "services" and d.available:
            it.update(count=f"{d.running}/{d.total}", bad=bool(d.unhealthy),
                      title=f"{d.running} running of {d.total}" + (f", {d.unhealthy} unhealthy" if d.unhealthy else ""))
        if k == "vms" and p.libvirt is not None:
            it.update(count=f"{p.libvirt.running}/{p.libvirt.total}",
                      title=f"{p.libvirt.running} running of {p.libvirt.total}")
        out.append(it)
    return out


def stats(p, summary, stamp: str) -> list:
    st = getattr(summary, "status", None) if summary is not None else None
    out: list = [p.hostname or "this host"]
    if st:
        _, word, cls = STATUS.get(st, STATUS["ok"])
        out.append({"text": word, "cls": cls})
    if p.stale:
        out.append({"text": f"stale: {', '.join(p.failed)}", "cls": "warn"})
    out.append(f"updated {stamp}")
    return out


def tools(ctx, p, tab: str, q: dict) -> dict | None:
    """The controls row of a tab: filter box (client rows), segments, the List / By stack toggle."""
    if tab == "services":
        stack = q.get("g") == "stack"
        return {"name": "sv", "ph": "Filter containers, images, stacks", "rows": "#sv-panel",
                "segs": service_segs(p, q), "seg_label": "Show",
                "toggle": [{"key": "list", "label": "List", "href": url(q, g=""), "cur": not stack},
                           {"key": "stack", "label": "By stack", "href": url(q, g="stack"), "cur": stack}]}
    if tab == "resources":
        return {"name": "sv", "ph": "Filter by name", "rows": "#sv-panel", "segs": res.res_segs(ctx.get("res"), q),
                "url": url({**q, "q": ""}, "/servers/panel"),  # server mode too: the top 10 of every match
                "seg_label": "Group", "toggle": None}
    if tab == "vms":
        return {"name": "sv", "ph": "Filter VMs and images", "rows": "#sv-vms", "segs": None, "toggle": None}
    return None


def page(ctx) -> dict:
    """Everything the page, the panel fragment and the VMs fragment show."""
    req = ctx.get("request")
    q = query(req)
    path = getattr(getattr(req, "url", None), "path", "") or ""
    tab = str(ctx.get("tab") or "") or ("vms" if path == "/servers/vms" else "overview")
    p = ctx.get("p")
    v: dict = {"tab": tab, "q": q.get("q", ""), "fragment": path == "/servers/panel",
               "panel_url": url({**q, "q": "", "t": "" if tab == "overview" else tab}, "/servers/panel"),
               "vms_url": url({"so": q.get("so", ""), "im": q.get("im", "")}, "/servers/vms")}
    if p is not None:
        v.update(tabs=tabs(p, tab), stats=stats(p, ctx.get("summary"), str(ctx.get("stamp") or "")),
                 tools=tools(ctx, p, tab, q))
        if tab == "overview":
            v["ov"] = overview(ctx)
        elif tab == "services":
            v["sv"] = services(ctx, q)
        elif tab == "resources" and ctx.get("res") is not None:
            v["res"] = res.resources(ctx, q)
    if tab == "vms" and ctx.get("vm") is not None:
        v["vms"] = res.vms(ctx.get("vm"), q)
    return v


@pass_context
def dt_servers(ctx) -> dict:
    """The Servers view model of the page or fragment being rendered (``page()``)."""
    return page(ctx)
