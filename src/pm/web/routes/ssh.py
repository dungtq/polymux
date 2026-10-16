"""Settings > SSH: generate pm's own key, and the remote servers card. Listing is part of the settings
panel (web.routes.settings).

Mutations: POST + CSRF + Origin + session + tier + audit (``ssh_keygen``, ``ssh_server_add|remove``,
``ssh_hostkey_scan|pin|repin``, ``ssh_test``, ``ssh_key_install``). Private key material is never read, rendered,
logged or audited; the audit detail carries only a fingerprint prefix. The install password is read from the
POST body, handed to ssh through a private FIFO and zeroed; it is never audited (not even its length),
logged or echoed, and ssh stderr is reduced to a result class.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from pm.core import executors
from pm.core.models import Tier
from pm.infra import servers_store as ss
from pm.infra import sshkeys
from pm.security.audit import AuditError
from pm.security.guard import audit_result, mutation_guard
from pm.web.settings_panel import html

router = APIRouter()


def _done(request: Request, result: str, detail: dict) -> None:
    try:
        audit_result(request, "ssh_keygen", sshkeys.KEY_NAME, result, detail)
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None


@router.post("/settings/ssh/keygen", dependencies=[Depends(mutation_guard("ssh_keygen", Tier.SAFE))])
async def keygen(request: Request) -> Response:
    settings = request.app.state.settings
    try:
        key = await executors.run_collectors(sshkeys.generate, settings)
    except sshkeys.SshKeyError as e:
        _done(request, "denied", {"reason": str(e)[:80]})
        return html(request, f"Key not generated: {e}.", ok=False)
    _done(request, "ok", {"fp": key.fingerprint[:19], "type": key.type})
    return html(request, "SSH key generated. Copy the public key below to the git server.")


# ------------------------------------------------------------------ remote servers
def _srv_done(request: Request, action: str, target: str, result: str, detail: dict) -> None:
    try:
        audit_result(request, action, target, result, detail)
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None


def _d(s: dict, **kw) -> dict:
    return {"host": s["host"][:80], "port": s["port"], "user": s["user"], **kw}


@router.post("/settings/servers/add", dependencies=[Depends(mutation_guard("ssh_server_add", Tier.SAFE))])
async def server_add(request: Request) -> Response:
    f = await request.form()
    name, host, port, user = (str(f.get(k) or "") for k in ("name", "host", "port", "user"))
    try:
        s = await executors.run_collectors(ss.add, request.app.state.settings, name, host, port, user)
    except ss.ServerError as e:
        _srv_done(request, "ssh_server_add", "", "denied", {"reason": str(e)[:80]})
        return html(request, f"Server not added: {e}.", ok=False)
    _srv_done(request, "ssh_server_add", s["id"], "ok", _d(s))
    return html(request, f"Server {s['name']} added. Pin its host key next.")


@router.post("/settings/servers/{sid}/remove", dependencies=[Depends(mutation_guard("ssh_server_remove", Tier.SAFE))])
async def server_remove(request: Request, sid: str) -> Response:
    try:
        s = await executors.run_collectors(ss.remove, request.app.state.settings, sid)
    except ss.ServerError as e:
        _srv_done(request, "ssh_server_remove", "", "denied", {"reason": str(e)[:80]})
        return html(request, f"Server not removed: {e}.", ok=False)
    _srv_done(request, "ssh_server_remove", s["id"], "ok", _d(s))
    return html(request, f"Server {s['name']} removed.")


@router.post("/settings/servers/{sid}/scan", dependencies=[Depends(mutation_guard("ssh_hostkey_scan", Tier.SAFE))])
async def server_scan(request: Request, sid: str) -> Response:
    """Read-only: fetch the server's host key fingerprint so the page can ask the user to confirm it."""
    st = request.app.state.settings
    try:
        s = await executors.run_collectors(ss.get, st, sid)
        _, fp = await executors.run_collectors(ss.scan_hostkey, s)
    except ss.ServerError as e:
        _srv_done(request, "ssh_hostkey_scan", sid if ss.valid_id(sid) else "", "denied", {"reason": str(e)[:80]})
        return JSONResponse({"error": str(e)}, headers={"Cache-Control": "no-store"})
    _srv_done(request, "ssh_hostkey_scan", s["id"], "ok", _d(s, fp=fp[:19]))
    return JSONResponse({"fp": fp, "host": s["host"], "port": s["port"], "pinned": s["hostkey_fp"]},
                        headers={"Cache-Control": "no-store"})


@router.post("/settings/servers/{sid}/pin", dependencies=[Depends(mutation_guard("ssh_hostkey_pin", Tier.SAFE))])
async def server_pin(request: Request, sid: str) -> Response:
    f = await request.form()
    fp = str(f.get("fp") or "")[:80]
    try:
        s = await executors.run_collectors(ss.pin_hostkey, request.app.state.settings, sid, fp)
    except ss.ServerError as e:
        _srv_done(request, "ssh_hostkey_pin", sid if ss.valid_id(sid) else "", "denied", {"reason": str(e)[:80]})
        return html(request, f"Host key not pinned: {e}.", ok=False)
    _srv_done(request, "ssh_hostkey_pin", s["id"], "ok", _d(s, fp=s["hostkey_fp"][:19]))
    return html(request, f"Host key of {s['name']} pinned.")


@router.post("/settings/servers/{sid}/repin",
             dependencies=[Depends(mutation_guard("ssh_hostkey_repin", Tier.MESSAGE))])
async def server_repin(request: Request, sid: str) -> Response:
    """Replace a pinned host key: re-scan, the user confirmed ``fp`` and typed the server name in ``confirm``."""
    f = await request.form()
    fp, confirm = str(f.get("fp") or "")[:80], str(f.get("confirm") or "")[:80]
    st = request.app.state.settings
    try:
        old = await executors.run_collectors(ss.get, st, sid)
        if confirm != old["name"]:
            raise ss.ServerError("type the server name to confirm")
        s = await executors.run_collectors(lambda: ss.pin_hostkey(st, sid, fp, replace=True))
    except ss.ServerError as e:
        _srv_done(request, "ssh_hostkey_repin", sid if ss.valid_id(sid) else "", "denied", {"reason": str(e)[:80]})
        return html(request, f"Host key not replaced: {e}.", ok=False)
    _srv_done(request, "ssh_hostkey_repin", s["id"], "ok",
              _d(s, old_fp=old["hostkey_fp"][:19], fp=s["hostkey_fp"][:19]))
    return html(request, f"Host key of {s['name']} replaced.")


@router.post("/settings/servers/{sid}/test", dependencies=[Depends(mutation_guard("ssh_test", Tier.SAFE))])
async def server_test(request: Request, sid: str) -> Response:
    try:
        s, res = await executors.run_collectors(ss.probe, request.app.state.settings, sid)
    except ss.ServerError as e:
        _srv_done(request, "ssh_test", "", "denied", {"reason": str(e)[:80]})
        return html(request, f"Test not run: {e}.", ok=False)
    _srv_done(request, "ssh_test", s["id"], "ok" if res == "ok" else "denied", _d(s, result=res))
    return html(request, f"{s['name']}: {ss.RESULTS.get(res, res)}.", ok=res == "ok")


@router.post("/settings/servers/{sid}/install", dependencies=[Depends(mutation_guard("ssh_key_install", Tier.MESSAGE))])
async def server_install(request: Request, sid: str) -> Response:
    f = await request.form()
    user = str(f.get("user") or "").strip()
    pw = bytearray(str(f.get("password") or "").encode("utf-8"))
    del f  # the password is only handed on; it is never stored, logged or audited
    try:
        s, res = await executors.run_collectors(ss.install_key, request.app.state.settings, sid, user, pw)
    except ss.ServerError as e:
        _srv_done(request, "ssh_key_install", sid if ss.valid_id(sid) else "", "denied", {"reason": str(e)[:80]})
        return html(request, f"Key not installed: {e}.", ok=False)
    _srv_done(request, "ssh_key_install", s["id"], "ok" if res == "ok" else "denied",
              _d(s, result=res, install_user=user))
    if res == "ok":
        return html(request, f"pm key installed on {s['name']} and key login works.")
    return html(request, f"Key not installed on {s['name']}: {ss.RESULTS.get(res, res)}.", ok=False)
