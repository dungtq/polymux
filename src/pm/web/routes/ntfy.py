"""GET /ntfy/panel: HTML fragment with the ntfy mirror (Inbox + project page)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from pm.agents import inbox
from pm.core import executors
from pm.core.errors import NotFound, PmError
from pm.core.models import Tier
from pm.notify import feed as ntfy_feed
from pm.notify import mutes as ntfy_mutes
from pm.notify import pins as ntfy_pins
from pm.notify import sidebadges
from pm.security.audit import AuditError
from pm.security.guard import audit_result, mutation_guard
from pm.web import deps
from pm.web.agentsidebar import agent_dots
from pm.web.deps import AUTH, snapshot
from pm.web.ntfypanel import BELL_MAX_ROWS, BELL_ROWS, build_bell, build_panel
from pm.web.render import fragment, html

router = APIRouter()


@router.get("/ntfy/panel", dependencies=AUTH)
async def ntfy_panel(request: Request, scope: str = "inbox",
                     n: str | None = None, all: str | None = None) -> Response:
    markup, _ = await build_panel(request, scope, n, all)
    return fragment(request, markup)


@router.get("/ntfy/bell", dependencies=AUTH)
async def ntfy_bell(request: Request, n: int = Query(BELL_ROWS, ge=1, le=BELL_MAX_ROWS),
                    unread_first: int = Query(0, ge=0, le=1)) -> Response:
    """Topbar bell dropdown: last ``n`` (default 5) system messages (no agent), loaded when it opens;
    ``unread_first=1`` lists the unread ones first (classic uses neither parameter)."""
    markup, _ = await build_bell(request, n, bool(unread_first))
    return fragment(request, markup)


@router.get("/ntfy", response_class=HTMLResponse, dependencies=AUTH)
async def ntfy_page(request: Request) -> Response:
    """All ntfy messages, agent and system."""
    markup, stats = await build_panel(request, "all")
    return html(request, "ntfy.html", nav="ntfy", ntfy_html=markup, stats=stats, drawer=True)


@router.get("/notifications", response_class=HTMLResponse, dependencies=AUTH)
async def notifications_page(request: Request) -> Response:
    """All system messages ("More" in the bell): the ntfy panel filtered to messages without an agent."""
    markup, _ = await build_panel(request, "system")
    return html(request, "notifications.html", nav="notifications", ntfy_html=markup)


@router.get("/inbox/unread", dependencies=AUTH)
async def inbox_unread(request: Request) -> Response:
    settings = deps.settings(request)
    snap = await snapshot(request)
    inbox.ensure_baseline(settings.state_dir)
    items = await executors.run_collectors(lambda: ntfy_feed.recent(settings))
    n = inbox.unread_count(snap.agents, inbox.group_ntfy(items), inbox.load(settings.state_dir),
                           window_s=inbox.idle_window(settings))
    sysn = await executors.run_collectors(sidebadges.system_unread, settings)
    return JSONResponse({"unread": n, "system": sysn}, headers={"Cache-Control": "no-store"})


@router.get("/side/unread", dependencies=AUTH)
async def side_unread(request: Request, kind: str = "projects") -> Response:
    """Unread ntfy counts per sidebar row key (project name) for the live badge refresh."""
    from pm.web.deps import side_unread

    if kind != "projects":
        raise HTTPException(404, "unknown kind")
    snap = await snapshot(request)
    return JSONResponse(await side_unread(request, snap, kind), headers={"Cache-Control": "no-store"})


@router.get("/side/dots", dependencies=AUTH)
async def side_dots(request: Request, kind: str = "projects", agent: str = "") -> Response:
    """Status dot per sidebar row key (project name / agent id) for the live dot refresh."""
    snap = await snapshot(request)
    if kind == "projects":
        dots = deps.project_dots(snap)
    elif kind == "agents":
        dots = await executors.run_collectors(agent_dots, snap, deps.agent_or_404(snap, agent))
    else:
        raise HTTPException(404, "unknown kind")
    return JSONResponse(dots, headers={"Cache-Control": "no-store"})


def _done(request: Request, action: str, target: str, result: str) -> None:
    try:
        audit_result(request, action, target, result, {})
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None


def _hub_ping(request: Request) -> None:
    hub = getattr(request.app.state, "hub", None)
    try:
        if hub is not None:
            hub.publish("inbox", {"read": 1})
    except Exception:  # noqa: BLE001 - best effort
        pass


async def _mark(request: Request, action: str, dismiss: bool, unread: bool = False) -> Response:
    raw = (await request.form()).get("key", "")
    if not isinstance(raw, str) or not inbox.KEY_RE.fullmatch(raw):
        _done(request, action, "", "denied")
        raise HTTPException(400, "bad key")
    settings = deps.settings(request)
    try:
        if unread:
            inbox.mark_unread(settings.state_dir, raw)
        else:
            inbox.mark(settings.state_dir, raw, dismiss=dismiss)
    except OSError:
        _done(request, action, raw, "error")
        raise HTTPException(500, "cannot save") from None
    _done(request, action, raw, "ok")
    _hub_ping(request)
    return Response(status_code=200, headers={"Cache-Control": "no-store", "HX-Trigger": "pm:refresh"})


@router.post("/inbox/read")
async def inbox_read(request: Request, _=Depends(mutation_guard("inbox_read", Tier.SAFE))):
    return await _mark(request, "inbox_read", False)


@router.post("/inbox/dismiss")
async def inbox_dismiss(request: Request, _=Depends(mutation_guard("inbox_dismiss", Tier.SAFE))):
    return await _mark(request, "inbox_dismiss", True)


@router.post("/inbox/mark-unread")
async def inbox_mark_unread(request: Request, _=Depends(mutation_guard("inbox_mark_unread", Tier.SAFE))):
    """Mark one key (agent row / ntfy group) unread until it is read again."""
    return await _mark(request, "inbox_mark_unread", False, unread=True)


@router.post("/inbox/read-all")
async def inbox_read_all(request: Request, _=Depends(mutation_guard("inbox_read_all", Tier.SAFE))):
    try:
        inbox.mark_all(deps.settings(request).state_dir)
    except OSError:
        _done(request, "inbox_read_all", "", "error")
        raise HTTPException(500, "cannot save") from None
    _done(request, "inbox_read_all", "", "ok")
    _hub_ping(request)
    return Response(status_code=200, headers={"Cache-Control": "no-store", "HX-Trigger": "pm:refresh"})


@router.post("/inbox/read-system")
async def inbox_read_system(request: Request, _=Depends(mutation_guard("inbox_read_system", Tier.SAFE))):
    """The bell's "Mark all read": only the system messages, agent rows stay unread."""
    settings = deps.settings(request)
    items = await executors.run_collectors(lambda: ntfy_feed.recent(settings))
    keys = [g.key for g in inbox.group_ntfy(items) if inbox.is_system(g.item)]
    try:
        inbox.mark_many(settings.state_dir, keys)
    except OSError:
        _done(request, "inbox_read_system", "", "error")
        raise HTTPException(500, "cannot save") from None
    _done(request, "inbox_read_system", "", "ok")
    _hub_ping(request)
    return Response(status_code=200, headers={"Cache-Control": "no-store", "HX-Trigger": "pm:refresh"})


async def _feed_op(request: Request, action: str, field: str, valid, op, ping: bool = False) -> Response:
    """One form value (``field``, checked by ``valid``) -> ``await op(settings, value)``; domain errors map to
    their status, OSError to 500; audited; ``ping`` also tells the hub the read state changed."""
    raw = (await request.form()).get(field, "")
    if not isinstance(raw, str) or not valid(raw):
        _done(request, action, "", "denied")
        raise HTTPException(400, f"bad {field}")
    settings = deps.settings(request)
    try:
        await op(settings, raw)
    except PmError as e:
        _done(request, action, raw, "denied")
        raise HTTPException(e.status, str(e)) from None
    except OSError:
        _done(request, action, raw, "error")
        raise HTTPException(500, "cannot save") from None
    _done(request, action, raw, "ok")
    if ping:
        _hub_ping(request)
    return Response(status_code=200, headers={"Cache-Control": "no-store", "HX-Trigger": "pm:refresh"})


def _pin_id(raw: str) -> bool:
    return bool(ntfy_pins.ID_RE.fullmatch(raw))


async def _pin_op(settings, raw: str) -> None:
    items = await executors.run_collectors(lambda: ntfy_feed.recent(settings))  # the snapshot comes from the server's
    item = next((i for i in items if i.id == raw), None)  # feed, never from the client
    if item is None:
        raise NotFound("message not in the feed")
    await executors.run_collectors(ntfy_pins.pin, settings, item)


async def _unpin_op(settings, raw: str) -> None:
    await executors.run_collectors(ntfy_pins.unpin, settings, raw)


async def _mute_op(settings, raw: str) -> None:
    await executors.run_collectors(ntfy_mutes.mute, settings, raw)


async def _unmute_op(settings, raw: str) -> None:
    await executors.run_collectors(ntfy_mutes.unmute, settings, raw)


@router.post("/ntfy/pin")
async def ntfy_pin(request: Request, _=Depends(mutation_guard("ntfy_pin", Tier.SAFE))):
    return await _feed_op(request, "ntfy_pin", "id", _pin_id, _pin_op)


@router.post("/ntfy/unpin")
async def ntfy_unpin(request: Request, _=Depends(mutation_guard("ntfy_unpin", Tier.SAFE))):
    return await _feed_op(request, "ntfy_unpin", "id", _pin_id, _unpin_op)


@router.post("/ntfy/mute")
async def ntfy_mute(request: Request, _=Depends(mutation_guard("ntfy_mute", Tier.SAFE))):
    """Mute a title for 24 h: its messages leave every ntfy list and count (``notify.mutes``)."""
    return await _feed_op(request, "ntfy_mute", "title", ntfy_mutes.valid_title, _mute_op, ping=True)


@router.post("/ntfy/unmute")
async def ntfy_unmute(request: Request, _=Depends(mutation_guard("ntfy_unmute", Tier.SAFE))):
    return await _feed_op(request, "ntfy_unmute", "title", ntfy_mutes.valid_title, _unmute_op, ping=True)
