"""Read-only browser terminal routes (P7.1).

Wiring: ``app.include_router(web.routes.term.router)``. Needs app.state.settings and
app.state.sessions; ticket store, broker, audit and csrf are created lazily.

GET  /term/{sid}?w=w34&back=/a/x  page (template term.html), 404 unless terminal_enabled;
                      w = window to open on (must belong to the session), back = Back target
                      (allow-listed path, default /tmux)
POST /term/ticket     form sid + csrf (+ optional w) -> {"ticket", "expires_in": 30}
GET  /term/history?sid=&n=  scrollback of the active pane of this login's live view
WS   /term/ws?ticket= read-only raw tmux attach stream

The stream is never logged or audited; only counters are.
"""

from __future__ import annotations

import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse

from pm.core import executors
from pm.core.models import AuditEntry, Tier
from pm.security.audit import AuditError
from pm.security.auth import require_session
from pm.security.csrf import origin_ok
from pm.security.guard import audit_result, client_of, get_audit, mutation_guard
from pm.security.ticket import TTL_S, TicketStore
from pm.tmux import ctl as tmuxctl
from pm.tmux import term
from pm.web.render import CSP, html

router = APIRouter()
_NOSTORE = {"Cache-Control": "no-store"}


def _tickets(app) -> TicketStore:
    st = app.state
    if getattr(st, "term_tickets", None) is None:
        st.term_tickets = TicketStore()
    return st.term_tickets


def _broker(app) -> term.Broker:
    st = app.state
    if getattr(st, "term_broker", None) is None:
        st.term_broker = term.Broker()
    return st.term_broker


def term_csp(base_url: str) -> str:
    u = urlsplit(base_url)
    host, ws = u.netloc, ("ws" if u.scheme == "http" else "wss")
    # xterm.js creates <style> elements at runtime (cell metrics, theme), so this
    # one page allows inline styles; scripts stay 'self' only.
    return (
        CSP.replace("connect-src 'self'", f"connect-src 'self' {ws}://{host}")
        .replace("style-src 'self'", "style-src 'self' 'unsafe-inline'")
        .replace("manifest-src 'self'", "manifest-src 'self'; font-src 'self'")  # Nerd Font symbols
    )


async def _require_enabled(request: Request) -> None:
    if not request.app.state.settings.terminal_enabled:
        raise HTTPException(404, "not found")


HIST_MIN_INTERVAL_S = 1.0
HIST_DEFAULT, HIST_MAX = 2000, 10000


@router.get("/term/history",
            dependencies=[Depends(_require_enabled), Depends(require_session)])
async def term_history(request: Request, sid: str = "", n: str = ""):
    """Raw scrollback (ANSI kept, never logged or cached). The pane is resolved server-side
    from this login's own ticket-validated view session; a pane id is never accepted."""
    app = request.app
    settings = app.state.settings
    if not origin_ok(request.headers, settings.allowed_origins, allow_missing=True):
        raise HTTPException(403, "bad origin")
    session = app.state.sessions.from_request(request)
    if session is None:
        raise HTTPException(401, "login required")
    try:
        count = HIST_DEFAULT if n == "" else int(n)
    except ValueError:
        raise HTTPException(400, "bad n") from None
    count = max(1, min(HIST_MAX, count))
    try:
        tmux_id, _name = await executors.run_tmux(term.resolve_session, settings, sid)
    except term.TermError as e:
        raise HTTPException(e.status, str(e)) from None
    _hist_rate(app, session.id)
    gid = _broker(app).view_for(session.id, tmux_id)
    if gid is None:
        raise HTTPException(409, "no live view for this session")
    try:
        text, window = await executors.run_tmux(_history, settings, gid, count)
    except tmuxctl.TmuxError:
        raise HTTPException(502, "tmux unavailable") from None
    try:
        audit_result(request, "terminal.history", tmux_id, "ok", {"n": count})
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None
    nlines = text.count("\n") + 1 if text else 0
    return JSONResponse({"lines": nlines, "text": text, "window": window}, headers=_NOSTORE)


def _hist_rate(app, session_id: str) -> None:
    """One history request per login session per HIST_MIN_INTERVAL_S, else 429."""
    last = getattr(app.state, "term_hist_last", None)
    if last is None:
        last = app.state.term_hist_last = {}
    now = time.monotonic()
    if now - last.get(session_id, -1e9) < HIST_MIN_INTERVAL_S:
        raise HTTPException(429, "slow down", headers={"Retry-After": "1"})
    last[session_id] = now
    if len(last) > 1024:
        for k in [k for k, t in last.items() if now - t > 60]:
            del last[k]


def _history(settings, gid: str, count: int):
    pane = tmuxctl.view_active_pane(settings, gid)
    text = tmuxctl.capture_history(settings, pane, count).rstrip("\n")
    return text, tmuxctl.view_select_window(settings, gid, None)


@router.get("/term/{sid}", response_class=HTMLResponse,
            dependencies=[Depends(_require_enabled), Depends(require_session)])
async def term_page(request: Request, sid: str, w: str = "", back: str = ""):
    settings = request.app.state.settings
    try:
        tmux_id, name = await executors.run_tmux(term.resolve_session, settings, sid)
    except term.TermError as e:
        raise HTTPException(e.status, str(e)) from None
    wid = await executors.run_tmux(term.window_in_session, settings, tmux_id,
                                  term.parse_window(w))
    back_to = term.valid_back(back)
    embed = request.query_params.get("embed") == "1"  # agent page Terminal tab (same-origin iframe)
    resp = html(request, "term.html", nav="term", embed=embed, session_name=name, sid=sid,
                 window=("w" + wid[1:]) if wid else "", back_url=back_to,
                 back_label=term.back_label(back_to),
                 ticket_path="/term/ticket", ws_path="/term/ws",
                 idle_s=settings.terminal_idle_s, max_s=settings.terminal_max_s)
    csp = term_csp(settings.base_url)
    resp.headers["Content-Security-Policy"] = csp.replace("frame-ancestors 'none'", "frame-ancestors 'self'") if embed else csp
    return resp


@router.post("/term/ticket", dependencies=[Depends(_require_enabled)])
async def term_ticket(request: Request,
                      session=Depends(mutation_guard("terminal.ticket", Tier.MESSAGE))):
    settings = request.app.state.settings
    form = await request.form()
    v = form.get("sid", "")
    sid = v if isinstance(v, str) else ""
    try:
        tmux_id, name = await executors.run_tmux(term.resolve_session, settings, sid)
    except term.TermError as e:
        try:
            audit_result(request, "terminal.ticket", sid[:16], "denied", {"reason": str(e)})
        except AuditError:
            raise HTTPException(503, "audit unavailable") from None
        raise HTTPException(e.status, str(e)) from None
    wv = form.get("w", "")
    wid = await executors.run_tmux(term.window_in_session, settings, tmux_id,
                                  term.parse_window(wv if isinstance(wv, str) else ""))
    token = _tickets(request.app).issue(session.id, tmux_id, wid)
    try:
        audit_result(request, "terminal.ticket", tmux_id, "ok", {})
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None
    return JSONResponse({"ticket": token, "expires_in": TTL_S}, headers=_NOSTORE)


def _write_audit(ws: WebSocket, action: str, target: str, result: str, detail: dict) -> None:
    get_audit(ws).write(AuditEntry(time.time(), client_of(ws), action, target, result, detail))


async def _ws_admit(ws: WebSocket, settings):
    """Pre-upgrade checks; every rejection here is HTTP 403 on the wire.

    Returns (session, ticket, tmux_id), or None after closing the socket.
    """
    app = ws.app
    if not settings.terminal_enabled:
        await ws.close(4404)
        return None
    if not origin_ok(ws.headers, settings.allowed_origins, require_present=True):
        await ws.close(4403)
        return None
    session = app.state.sessions.from_request(ws)
    if session is None:
        await ws.close(4401)
        return None
    ticket = _tickets(app).consume(ws.query_params.get("ticket"), session.id)
    if ticket is None:
        await ws.close(4401)
        return None
    try:  # the session may have been renamed/removed since the ticket was minted
        tmux_id, _name = await executors.run_tmux(
            term.resolve_session, settings, "s" + ticket.tmux_id[1:])
    except term.TermError:
        await ws.close(4404)
        return None
    return session, ticket, tmux_id


async def _ws_finish(ws: WebSocket, tmux_id: str, result: term.Result) -> None:
    d = {"duration_s": result.duration_s, "bytes_out": result.bytes_out,
         "dropped_in": result.dropped_in, "reason": result.reason}
    try:
        # synchronous on purpose (before any await): must land even if this task is being cancelled
        _write_audit(ws, "terminal.close", tmux_id, "ok", d)
    except AuditError:
        pass
    try:
        await ws.close(result.code)
    except Exception:  # noqa: BLE001 - already closed
        pass


@router.websocket("/term/ws")
async def term_ws(ws: WebSocket):
    app = ws.app
    settings = app.state.settings
    admitted = await _ws_admit(ws, settings)
    if admitted is None:
        return
    session, ticket, tmux_id = admitted
    broker = _broker(app)
    await executors.run_collectors(broker.cleanup_stale, settings)
    if not broker.reserve(settings.terminal_max):
        await ws.accept()
        await ws.close(4429)
        return
    result = term.Result(code=1011, reason="error")  # filled in place by serve()
    opened = False
    try:
        try:
            await executors.run_collectors(_write_audit, ws, "terminal.open", tmux_id, "ok",
                                           {"mode": "view"})
        except AuditError:
            await ws.close(4503)  # fail closed: no audit line, no stream
            return
        opened = True
        await ws.accept()
        result = await term.serve(
            ws, settings, tmux_id, term.Limits.from_settings(settings),
            still_valid=lambda: app.state.sessions.get(session.id) is not None, res=result, broker=broker,
            owner=session.id, window_id=ticket.window_id)
    finally:
        broker.release()
        if opened:
            await _ws_finish(ws, tmux_id, result)
