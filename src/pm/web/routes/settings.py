"""Settings: sessions, passkeys, default launch options, notify rules (+ GET /api/prefs).

Mutations answer through ``settings_panel.reply`` (HTML + ``HX-Trigger`` pm:notice, or JSON ``{ok, notice}``)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from markupsafe import escape

from pm.core import executors, prefs
from pm.core.models import Tier
from pm.projects import projects
from pm.qchat import cfg as qchatcfg
from pm.qchat import db as qchatdb
from pm.qchat import run as qchat
from pm.security import sessions_store as sstore
from pm.security.auth import COOKIE_NAME, require_session
from pm.security.guard import audit_result, mutation_guard
from pm.security.passkey import manager_of
from pm.web.render import fragment, get_template, page_ctx
from pm.web.settings_panel import AGENTS, html, reply, wants_json
from pm.web.settings_panel import render as render_panel

router = APIRouter()
_AUTH = [Depends(require_session)]
@router.get("/settings/panel", response_class=HTMLResponse, dependencies=_AUTH)
async def panel(request: Request) -> Response:
    """The panel reads prefs, servers, keys and passkeys from disk: rendered in the collectors pool, never on the
    event loop (same as GET /settings)."""
    return fragment(request, await executors.run_collectors(render_panel, request))


@router.post("/settings/sessions/revoke",
             dependencies=[Depends(mutation_guard("session_revoke", Tier.SAFE))])
async def revoke_session(request: Request) -> Response:
    form = await request.form()  # (Form() params would consume the body before the guard)
    h, confirm = str(form.get("h", "")), str(form.get("confirm", ""))
    if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
        raise HTTPException(400, "bad session id")
    cur = request.state.session
    is_current = sstore.sid_hash(cur.id) == h
    if is_current and confirm != "1":
        audit_result(request, "session_revoke", h[:8], "denied", {"reason": "confirm"})
        return html(request, "Revoking this session logs you out. Tick the confirm box and retry.", ok=False)
    ok = request.app.state.sessions.revoke_hash(h)
    audit_result(request, "session_revoke", h[:8], "ok" if ok else "denied",
                 {"current": int(is_current)} if ok else {"reason": "unknown"})
    if ok and is_current:
        resp = (JSONResponse({"ok": True, "notice": "Logged out.", "redirect": "/login"}) if wants_json(request)
                else Response(status_code=200, headers={"HX-Redirect": "/login"}))
        resp.delete_cookie(COOKIE_NAME)
        return resp
    return html(request, "Session revoked." if ok else "Session not found.", ok=ok)


@router.post("/settings/sessions/revoke-others",
             dependencies=[Depends(mutation_guard("session_revoke", Tier.SAFE))])
async def revoke_other_sessions(request: Request) -> Response:
    """Log out every session but this one (one save)."""
    n = request.app.state.sessions.revoke_others(sstore.sid_hash(request.state.session.id))
    audit_result(request, "session_revoke", "others", "ok", {"n": n})
    return html(request, f"Revoked {n} other session{'s' if n != 1 else ''}." if n else "No other sessions.")


@router.post("/settings/passkeys/revoke",
             dependencies=[Depends(mutation_guard("passkey_revoke", Tier.SAFE))])
async def revoke_passkey(request: Request) -> Response:
    id = str((await request.form()).get("id", ""))
    if not id or len(id) > 512:
        raise HTTPException(400, "bad passkey id")
    ok = await executors.run_collectors(manager_of(request.app).delete_credential, id)
    audit_result(request, "passkey_revoke", id[:8], "ok" if ok else "denied",
                 {} if ok else {"reason": "unknown"})
    return html(request, "Passkey removed." if ok else "Passkey not found.", ok=ok)


@router.post("/settings/prefs", dependencies=[Depends(mutation_guard("prefs_save", Tier.SAFE))])
async def save_prefs(request: Request) -> Response:
    form = await request.form()
    new = {
        "launch": {k: str(form.get("launch_" + k, "")) for k in ("agent", "model", "mode")},
        "notify": {k: form.get("notify_" + k) == "1" for k, _ in prefs.NOTIFY_RULES},
    }
    if new["launch"]["agent"] and new["launch"]["agent"] not in AGENTS:
        raise HTTPException(400, "unknown agent")
    prefs.save(request.app.state.settings.prefs_file, new)
    audit_result(request, "prefs_save", "", "ok")
    return html(request, "Preferences saved.")


@router.post("/settings/composer", dependencies=[Depends(mutation_guard("composer_save", Tier.SAFE))])
async def save_composer(request: Request) -> Response:
    form = await request.form()
    raw_n = str(form.get("paste_lines", "")).strip()
    if not raw_n.lstrip("-").isdigit() or len(raw_n) > 6:
        audit_result(request, "composer_save", "", "denied", {"why": "paste_lines"})
        return html(request, "Paste threshold must be a number (0 = off).", ok=False)
    raw_e = str(form.get("exts", ""))[:600]
    exts = prefs._clean_exts(raw_e)
    if not exts:
        audit_result(request, "composer_save", "", "denied", {"why": "exts"})
        return html(request, "Give at least one file type (letters/digits, up to 8 each).", ok=False)
    send_key = str(form.get("send_key", ""))  # missing = keep the stored one
    if send_key and send_key not in prefs.SEND_KEYS:
        audit_result(request, "composer_save", "", "denied", {"why": "send_key"})
        return html(request, "Send with must be Enter or Ctrl+Enter.", ok=False)
    comp = prefs.save_composer_prefs(request.app.state.settings, int(raw_n), exts, send_key or None)
    audit_result(request, "composer_save", "", "ok",
                 {"paste_lines": comp["paste_lines"], "n_exts": len(comp["exts"]),
                  "exts": ",".join(comp["exts"]), "send_key": comp["send_key"]})
    return html(request, "Composer settings saved.")


@router.get("/api/prefs", dependencies=_AUTH)
async def api_prefs(request: Request) -> Response:
    """Stored defaults for other pages (launch form, alerts)."""
    return JSONResponse(prefs.load(request.app.state.settings.prefs_file),
                        headers={"Cache-Control": "no-store"})


# ---- Quick chat settings (qchat.json; history stats from qchat.db). The block loads lazily into
# #qchat-settings; every mutation re-renders it. MCP header / env values are never rendered.

_QC_NAME = re.compile(r"^[A-Za-z0-9_.:\- ]{1,80}$")


def _qc_ctx(settings) -> dict:
    cfg = qchatcfg.load(settings)
    return {"qc": cfg, "qc_mcp": qchatcfg.discover_mcp(settings),
            "qc_skills": qchatcfg.discover_skills(settings, cfg["default_folder"]),
            "qc_folders": [p.name for p in projects.discover(settings)], "qc_profiles": qchat.PROFILES,
            "qc_modes": qchatcfg.PERMISSION_MODES, "qc_stats": qchatdb.stats(settings)}


async def _qc_html(request: Request, notice: str = "", ok: bool = True) -> Response:
    if request.method == "POST" and wants_json(request):
        return reply(request, notice, ok)
    ctx = await run_in_threadpool(_qc_ctx, request.app.state.settings)
    body = get_template("_settings_qchat.html").render(**page_ctx(request), **ctx, notice=notice)
    return reply(request, notice, ok, lambda: body)


def _qc_name(raw: object) -> str:
    name = str(raw or "").strip()
    if not _QC_NAME.fullmatch(name):
        raise HTTPException(400, "bad name")
    return name


def _qc_audit(request: Request, action: str, target: str, result: str, detail: dict | None = None) -> None:
    audit_result(request, action, target[:80], result, detail)


@router.get("/settings/qchat", response_class=HTMLResponse, dependencies=_AUTH)
async def qchat_block(request: Request) -> Response:
    return await _qc_html(request)


def _qc_patch(part: str, form) -> dict:
    def has(k: str) -> bool:
        return form.get(k) == "1"

    if part == "tools":
        return {"profiles": {"read": has("profile_read"), "full": has("profile_full")},
                "default_profile": str(form.get("default_profile", "none")),
                "default_folder": str(form.get("default_folder", "")),
                "read_tools": str(form.get("read_tools", ""))[:1000],
                "full_permission_mode": str(form.get("full_permission_mode", "")),
                "max_turns": str(form.get("max_turns", "")), "timeout_s": str(form.get("timeout_s", ""))}
    if part == "skills":
        return {"skills_in_none": has("skills_in_none")}
    if part == "history":
        return {"keep_convs": str(form.get("keep_convs", "")), "keep_days": str(form.get("keep_days", "")),
                "nav_search": has("nav_search")}
    raise HTTPException(400, "unknown part")


@router.post("/settings/qchat", dependencies=[Depends(mutation_guard("qchat_settings", Tier.SAFE))])
async def qchat_save(request: Request) -> Response:
    form = await request.form()
    part = str(form.get("part", ""))
    patch = _qc_patch(part, form)
    try:
        await run_in_threadpool(qchatcfg.save, request.app.state.settings, patch)
    except ValueError as e:
        _qc_audit(request, "qchat_settings", part, "denied", {"why": "invalid"})
        return await _qc_html(request, str(e)[:160], ok=False)
    _qc_audit(request, "qchat_settings", part, "ok")
    return await _qc_html(request, "Quick chat settings saved.")


@router.post("/settings/qchat/mcp/add",
             dependencies=[Depends(mutation_guard("qchat_mcp_add", Tier.MESSAGE))])
async def qchat_mcp_add(request: Request) -> Response:
    form = await request.form()
    name, typ = str(form.get("name", "")).strip(), str(form.get("type", ""))
    target, headers = str(form.get("target", "")), str(form.get("headers", ""))
    try:
        await run_in_threadpool(lambda: qchatcfg.add_mcp(request.app.state.settings, name=name, type=typ,
                                                         target=target, headers=headers))
    except ValueError as e:
        _qc_audit(request, "qchat_mcp_add", name, "denied", {"why": "invalid"})
        return await _qc_html(request, str(e)[:160], ok=False)
    _qc_audit(request, "qchat_mcp_add", name, "ok", {"type": typ, "headers_len": len(headers)})
    return await _qc_html(request, f"Added MCP server {name}.")


@router.post("/settings/qchat/mcp/remove",
             dependencies=[Depends(mutation_guard("qchat_mcp_remove", Tier.SAFE))])
async def qchat_mcp_remove(request: Request) -> Response:
    name = _qc_name((await request.form()).get("name"))
    ok = await run_in_threadpool(qchatcfg.remove_mcp, request.app.state.settings, name)
    _qc_audit(request, "qchat_mcp_remove", name, "ok" if ok else "denied", {} if ok else {"why": "unknown"})
    return await _qc_html(request, f"Removed {name}." if ok else "Only manual servers can be removed here.", ok=ok)


@router.post("/settings/qchat/mcp/test",
             dependencies=[Depends(mutation_guard("qchat_mcp_test", Tier.MESSAGE))])
async def qchat_mcp_test(request: Request) -> Response:
    name = _qc_name((await request.form()).get("name"))
    known = {m["name"] for m in await run_in_threadpool(qchatcfg.discover_mcp, request.app.state.settings)}
    if name not in known:
        _qc_audit(request, "qchat_mcp_test", name, "denied", {"why": "unknown"})
        return await _qc_html(request, "Unknown MCP server.", ok=False)
    ok, msg = await run_in_threadpool(qchatcfg.test_mcp, request.app.state.settings, name)
    _qc_audit(request, "qchat_mcp_test", name, "ok" if ok else "error")
    return await _qc_html(request, f"{name}: {'ok' if ok else 'failed'} - {msg}"[:200], ok=ok)


async def _qc_toggle(request: Request, action: str, key: str, known_of) -> Response:
    form = await request.form()
    name, on = _qc_name(form.get("name")), form.get("on") == "1"
    st = request.app.state.settings
    if name not in {x["name"] for x in await run_in_threadpool(known_of, st)}:
        _qc_audit(request, action, name, "denied", {"why": "unknown"})
        raise HTTPException(404, "unknown name")
    await run_in_threadpool(qchatcfg.save, st, {key: {name: on}})
    _qc_audit(request, action, name, "ok", {"on": int(on)})
    return Response(status_code=204)


@router.post("/settings/qchat/mcp/toggle",
             dependencies=[Depends(mutation_guard("qchat_mcp_toggle", Tier.SAFE))])
async def qchat_mcp_toggle(request: Request) -> Response:
    return await _qc_toggle(request, "qchat_mcp_toggle", "mcp_enabled", qchatcfg.discover_mcp)


@router.post("/settings/qchat/skills/toggle",
             dependencies=[Depends(mutation_guard("qchat_skill_toggle", Tier.SAFE))])
async def qchat_skill_toggle(request: Request) -> Response:
    cfg = await run_in_threadpool(qchatcfg.load, request.app.state.settings)
    return await _qc_toggle(request, "qchat_skill_toggle", "skills_enabled",
                            lambda st: qchatcfg.discover_skills(st, cfg["default_folder"]))


@router.post("/settings/qchat/mcp/rescan", dependencies=[Depends(mutation_guard("qchat_rescan", Tier.SAFE))])
@router.post("/settings/qchat/skills/rescan", dependencies=[Depends(mutation_guard("qchat_rescan", Tier.SAFE))])
async def qchat_rescan(request: Request) -> Response:
    _qc_audit(request, "qchat_rescan", "", "ok")
    return await _qc_html(request, "Re-scanned.")


@router.post("/settings/qchat/history/delete-all",
             dependencies=[Depends(mutation_guard("qchat_delete_all", Tier.MESSAGE))])
async def qchat_history_delete_all(request: Request) -> Response:
    n = await run_in_threadpool(qchatdb.delete_all, request.app.state.settings)
    _qc_audit(request, "qchat_delete_all", "", "ok", {"deleted": n})
    return await _qc_html(request, f"Deleted {n} conversation{'s' if n != 1 else ''}.")


@router.get("/settings/qchat/skills/{name}", dependencies=_AUTH)
async def qchat_skill_view(request: Request, name: str, folder: str = "") -> Response:
    if not _QC_NAME.fullmatch(name):
        raise HTTPException(404, "no such skill")
    st = request.app.state.settings
    cfg = await run_in_threadpool(qchatcfg.load, st)
    text = await run_in_threadpool(qchatcfg.skill_text, st, name, folder or cfg["default_folder"])
    if text is None:
        raise HTTPException(404, "no such skill")
    return HTMLResponse(f'<pre class="qcs-view mono small" tabindex="0">{escape(text)}</pre>',
                        headers={"Cache-Control": "no-store"})  # escaped: htmx swaps it as markup
