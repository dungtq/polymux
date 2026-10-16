"""Servers dashboard: GET /servers (page), GET /servers/panel (HTMX fragment), process / program / container /
compose stack / VM actions, and the project-scoped VM routes under /p/{name}/vms (the project's VM patterns only)."""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from pm.core import executors, timefmt
from pm.core.models import Tier
from pm.infra import libvirt, procctl, resources, servers, vmops
from pm.infra import snapshot as services
from pm.notify import feed as ntfy_feed
from pm.projects import settings as pset
from pm.security.audit import AuditError
from pm.security.auth import require_session
from pm.security.guard import audit_result, mutation_guard
from pm.web.deps import snapshot
from pm.web.render import fragment, get_template, html, page_ctx

router = APIRouter()
_AUTH = [Depends(require_session)]


async def _panel(request: Request) -> servers.Panel:
    st = request.app.state
    services.touch()
    return await servers.get_panel_swr(st.settings, getattr(st, "tracker", None), ntfy_fn=ntfy_feed.collect_ntfy)


def _stamp(p: servers.Panel) -> str:
    return timefmt.fmt_time(p.ts)


_TABS = ("overview", "services", "resources", "vms")


def _tab(t: str) -> str:
    return t if t in _TABS else "overview"


def _can(request: Request, enabled: bool) -> bool:
    return enabled and request.app.state.settings.tier_ceiling >= Tier.MESSAGE


async def _res_ctx(request: Request, tab: str, s: str) -> dict:
    """Template context for the Resources tab (empty for the others)."""
    if tab != "resources":
        return {}
    res = await executors.run_collectors(resources.get, request.app.state.settings)
    rs = s if s in ("ram", "cpu", "name") else "ram"
    return {"res": res, "rs": rs, "rtiles": resources.summarize(res), "rsort": resources.sort_rows,
            "fb": servers.fmt_bytes, "fcpu": resources.fmt_cpu, "top": resources.TOP_PROGRAMS,
            "pdesc": resources.describe, "can_proc": _can(request, request.app.state.settings.proc_manage)}


def _ct_ctx(request: Request) -> dict:
    st = request.app.state.settings
    return {"can_ct": _can(request, st.container_manage),
            "ct_protected": lambda n: procctl.container_protected(n, st)}


@router.get("/servers", response_class=HTMLResponse, dependencies=_AUTH)
async def servers_page(request: Request, t: str = "overview", ct: str = "all", s: str = "ram") -> Response:
    tab = _tab(t)
    p = await _panel(request)
    rc = await _res_ctx(request, tab, s)
    vm = (await executors.run_collectors(libvirt.get, request.app.state.settings)
          if tab == "vms" else libvirt.cached() if tab == "overview" else None)  # overview: last listing, no IO
    return html(request, "servers.html", nav="servers", p=p, stamp=_stamp(p), **_ct_ctx(request),
                 summary=servers.summarize(p) if tab == "overview" else None,
                 ct="bad" if ct == "bad" else "all", tab=tab, vm=vm, **{"fb": servers.fmt_bytes, **rc})


@router.get("/servers/panel", response_class=HTMLResponse, dependencies=_AUTH)
async def servers_panel(request: Request, t: str = "overview", ct: str = "all", s: str = "ram") -> Response:
    tab = _tab(t)
    p = await _panel(request)
    rc = await _res_ctx(request, tab, s)
    html = get_template("_servers.html").render(
        **page_ctx(request), **_ct_ctx(request), p=p, stamp=_stamp(p),
        summary=servers.summarize(p) if tab == "overview" else None,
        ct="bad" if ct == "bad" else "all", tab=tab, **rc)
    return fragment(request, html)


# ------------------------------------------------------------------ VMs (libvirt)
@router.get("/servers/vms", response_class=HTMLResponse, dependencies=_AUTH)
async def servers_vms(request: Request) -> Response:
    vm = await executors.run_collectors(libvirt.get, request.app.state.settings)
    html = get_template("_vms.html").render(**page_ctx(request), vm=vm, fb=servers.fmt_bytes)
    return fragment(request, html)


@router.get("/p/{name}/vms", response_class=HTMLResponse, dependencies=_AUTH)
async def project_vms(request: Request, name: str) -> Response:
    """VM tab of the project page: the /servers VM table filtered by the project's VM patterns (lazy)."""
    if (await snapshot(request)).project(name) is None:
        raise HTTPException(404, "unknown project")
    st = request.app.state.settings
    pats = pset.load(st, name).vms
    vm = libvirt.filter_info(await executors.run_collectors(libvirt.get, st), pats) if pats else None
    html = get_template("_project_vms.html").render(
        **page_ctx(request), vm=vm, patterns=pats, fb=servers.fmt_bytes)
    return fragment(request, html)


async def _form_int(request: Request, key: str) -> int:
    v = (await request.form()).get(key, "")
    if not isinstance(v, str) or not v.isdigit() or len(v) > 12:
        raise HTTPException(400, f"bad {key}")
    return int(v)


async def _ctl(request: Request, action: str, target: str, fn, *args, detail: dict | None = None) -> HTMLResponse:
    """Run a procctl call off the loop; audit the outcome; refusals become 4xx with the reason.
    ``fn`` returns the message, or (message, count) for a fan-out (the count goes into the audit as ``n``)."""
    detail = dict(detail or {})
    try:
        msg = await executors.run_collectors(fn, request.app.state.settings, *args)
    except procctl.CtlError as e:
        _done(request, action, target, "error" if e.status >= 500 else "denied", {**detail, "reason": str(e)[:160]})
        raise HTTPException(e.status, str(e)) from None
    if isinstance(msg, tuple):
        msg, detail["n"] = msg
    _done(request, action, target, "ok", detail or None)
    resources.invalidate()
    servers.invalidate_panel(request.app.state.settings)
    return HTMLResponse(f'<p class="ok">{escape(msg)}</p>',
                        headers={"Cache-Control": "no-store", "HX-Trigger": "res-changed"})


def _proc_signal(sig: str):
    async def proc_signal(request: Request) -> HTMLResponse:
        pid, start = await _form_int(request, "pid"), await _form_int(request, "start")
        return await _ctl(request, f"proc_{sig}", f"pid {pid}", procctl.signal_process, sig, pid, start)
    return proc_signal


def _container_ctl(act: str):
    async def container_ctl(request: Request, name: str) -> HTMLResponse:
        return await _ctl(request, f"container_{act}", name[:80], procctl.container_action, act, name)
    return container_ctl


def _proc_program_signal(sig: str):
    async def proc_program_signal(request: Request) -> HTMLResponse:
        """Every process of one Resources > Programs row; ``program`` and a typed ``confirm`` repeating it."""
        program = (await request.form()).get("program", "")
        program = program[:80] if isinstance(program, str) else ""
        return await _ctl(request, f"proc_prog_{sig}", program, procctl.signal_program, sig, program,
                          await _confirm(request))
    return proc_program_signal


def _post(path: str, endpoint, audit: str) -> None:
    router.add_api_route(path, endpoint, methods=["POST"], response_class=HTMLResponse,
                         dependencies=[Depends(mutation_guard(audit, Tier.MESSAGE))])


# One route per action, so the guard's attempt line and the outcome line carry the same audit action (the audit
# page folds the pair); an unknown action matches no route (404).
for _a in procctl.SIGNALS:
    _post(f"/servers/proc/{_a}", _proc_signal(_a), f"proc_{_a}")
    _post(f"/servers/proc/program/{_a}", _proc_program_signal(_a), f"proc_prog_{_a}")
for _a in procctl.CT_ACTIONS:
    _post(f"/servers/containers/{{name}}/{_a}", _container_ctl(_a), f"container_{_a}")


@router.post("/servers/stacks/{stack}/restart", response_class=HTMLResponse)
async def stack_restart(request: Request, stack: str, _=Depends(mutation_guard("stack_restart", Tier.MESSAGE))):
    """A docker compose project's containers (members found by docker's label, protected names skipped)."""
    return await _ctl(request, "stack_restart", stack[:80], procctl.restart_stack, stack, detail={"scope": "docker"})


async def _confirm(request: Request) -> str:
    v = (await request.form()).get("confirm", "")
    return v if isinstance(v, str) else ""


def _done(request: Request, action: str, target: str, result: str, detail: dict | None = None) -> None:
    try:
        audit_result(request, action, target, result, detail)
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None


async def _vm_mutation(request: Request, action: str, name: str, fn, *args, slow: bool = False) -> HTMLResponse:
    """``slow``: long virsh work (snapshots, up to SNAP_TIMEOUT_S) runs on the jobs pool, not the page pool."""
    settings = request.app.state.settings
    target = name[:80]
    run = executors.run_jobs if slow else executors.run_collectors
    try:
        msg = await run(fn, settings, *args)
    except libvirt.VmError as e:
        res = "error" if e.status >= 500 else "denied"
        _done(request, action, target, res, {"reason": str(e)[:160]})
        raise HTTPException(e.status, str(e)) from None
    _done(request, action, target, "ok")
    return HTMLResponse(f'<p class="ok">{escape(msg)}</p>',
                        headers={"Cache-Control": "no-store", "HX-Trigger": "vms-changed"})


@router.post("/servers/vms/{name}/start", response_class=HTMLResponse)
async def vm_start(request: Request, name: str, _=Depends(mutation_guard("vm_start", Tier.SAFE))):
    return await _vm_mutation(request, "vm_start", name, libvirt.domain_action, "start", name)


@router.post("/servers/vms/{name}/shutdown", response_class=HTMLResponse)
async def vm_shutdown(request: Request, name: str, _=Depends(mutation_guard("vm_shutdown", Tier.SAFE))):
    return await _vm_mutation(request, "vm_shutdown", name, libvirt.domain_action, "shutdown", name)


@router.post("/servers/vms/{name}/destroy", response_class=HTMLResponse)
async def vm_destroy(request: Request, name: str, _=Depends(mutation_guard("vm_destroy", Tier.MESSAGE))):
    return await _vm_mutation(request, "vm_destroy", name, libvirt.domain_action, "destroy", name,
                              await _confirm(request))


@router.post("/servers/vms/{name}/undefine", response_class=HTMLResponse)
async def vm_undefine(request: Request, name: str, _=Depends(mutation_guard("vm_undefine", Tier.MESSAGE))):
    return await _vm_mutation(request, "vm_undefine", name, libvirt.domain_action, "undefine", name,
                              await _confirm(request))


@router.post("/servers/images/{name}/delete", response_class=HTMLResponse)
async def image_delete(request: Request, name: str, _=Depends(mutation_guard("vm_image_delete", Tier.MESSAGE))):
    return await _vm_mutation(request, "vm_image_delete", name, libvirt.image_delete, name,
                              await _confirm(request))


# ------------------------------------------------------------------ project VMs (scoped to the project's patterns)
async def _pvm(request: Request, name: str, action: str = "", target: str = "") -> tuple[str, ...]:
    """The project's VM patterns; unknown project = 404 (audited when ``action`` is a mutation)."""
    if (await snapshot(request)).project(name) is None:
        if action:
            _done(request, action, target, "denied", {"reason": "unknown project"})
        raise HTTPException(404, "unknown project")
    return pset.load(request.app.state.settings, name).vms


async def _pvm_read(request: Request, name: str, fn, vm: str) -> JSONResponse:
    pats = await _pvm(request, name)
    try:
        data = await executors.run_collectors(fn, request.app.state.settings, vm, pats)
    except libvirt.VmError as e:
        raise HTTPException(e.status, str(e)) from None
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.get("/p/{name}/vms/{vm}/snapshots", dependencies=_AUTH)
async def project_vm_snapshots(request: Request, name: str, vm: str) -> JSONResponse:
    return await _pvm_read(request, name, vmops.snapshots_view, vm)


@router.get("/p/{name}/vms/{vm}/console", dependencies=_AUTH)
async def project_vm_console(request: Request, name: str, vm: str) -> JSONResponse:
    """Read-only console info (display URI as text); pm does not proxy the console."""
    return await _pvm_read(request, name, vmops.console, vm)


async def _pvm_action(request: Request, name: str, vm: str, action: str, typed: bool) -> HTMLResponse:
    audit = f"pvm_{action}".replace("-", "_")
    pats = await _pvm(request, name, audit, vm[:80])
    confirm = await _confirm(request) if typed else ""
    return await _vm_mutation(request, audit, vm, vmops.domain_action, action, vm, pats, confirm)


@router.post("/p/{name}/vms/{vm}/start", response_class=HTMLResponse)
async def project_vm_start(request: Request, name: str, vm: str,
                           _=Depends(mutation_guard("pvm_start", Tier.SAFE))):
    return await _pvm_action(request, name, vm, "start", False)


@router.post("/p/{name}/vms/{vm}/shutdown", response_class=HTMLResponse)
async def project_vm_shutdown(request: Request, name: str, vm: str,
                              _=Depends(mutation_guard("pvm_shutdown", Tier.SAFE))):
    return await _pvm_action(request, name, vm, "shutdown", False)


@router.post("/p/{name}/vms/{vm}/force-off", response_class=HTMLResponse)
async def project_vm_force_off(request: Request, name: str, vm: str,
                               _=Depends(mutation_guard("pvm_force_off", Tier.MESSAGE))):
    return await _pvm_action(request, name, vm, "force-off", True)


@router.post("/p/{name}/vms/{vm}/delete", response_class=HTMLResponse)
async def project_vm_delete(request: Request, name: str, vm: str,
                            _=Depends(mutation_guard("pvm_delete", Tier.MESSAGE))):
    return await _pvm_action(request, name, vm, "delete", True)


@router.post("/p/{name}/vms/{vm}/snapshots", response_class=HTMLResponse)
async def project_vm_snapshot_create(request: Request, name: str, vm: str,
                                     _=Depends(mutation_guard("pvm_snapshot", Tier.SAFE))):
    pats = await _pvm(request, name, "pvm_snapshot", vm[:80])
    snap = (await request.form()).get("snap", "")
    return await _vm_mutation(request, "pvm_snapshot", vm, vmops.snapshot_create, vm, pats,
                              snap if isinstance(snap, str) else "", slow=True)


@router.post("/p/{name}/vms/{vm}/snapshots/{snap}/revert", response_class=HTMLResponse)
async def project_vm_snapshot_revert(request: Request, name: str, vm: str, snap: str,
                                     _=Depends(mutation_guard("pvm_snapshot_revert", Tier.MESSAGE))):
    pats = await _pvm(request, name, "pvm_snapshot_revert", f"{vm[:64]}:{snap[:64]}")
    return await _vm_mutation(request, "pvm_snapshot_revert", f"{vm[:64]}:{snap[:64]}",
                              vmops.snapshot_revert, vm, pats, snap, slow=True)


@router.post("/p/{name}/vms/{vm}/snapshots/{snap}/delete", response_class=HTMLResponse)
async def project_vm_snapshot_delete(request: Request, name: str, vm: str, snap: str,
                                     _=Depends(mutation_guard("pvm_snapshot_delete", Tier.MESSAGE))):
    pats = await _pvm(request, name, "pvm_snapshot_delete", f"{vm[:64]}:{snap[:64]}")
    return await _vm_mutation(request, "pvm_snapshot_delete", f"{vm[:64]}:{snap[:64]}",
                              vmops.snapshot_delete, vm, pats, snap)
