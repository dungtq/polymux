"""SSE stream (cookie auth) and the Claude Code hook receiver (loopback + secret)."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import random
import re
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from pm.infra import snapshot as services
from pm.notify import events
from pm.security.auth import require_session

router = APIRouter()

HEARTBEAT_S = 15.0
MAX_LIFETIME_S = 600.0  # a stream ends cleanly after ~10 min; the browser reopens it
MAX_HOOK_BODY = 16 * 1024
LOCAL_PEERS = {"127.0.0.1", "::1"}
PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "x-forwarded-host", "x-forwarded-proto", "forwarded")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def sse_client(request: Request) -> str:
    """Key for the per-client stream cap. Behind nginx every request arrives from the proxy's
    address, so a trusted peer (loopback / private: pm binds only loopback + the docker gateway)
    is replaced by the X-Real-IP that nginx sets from the real socket."""
    peer = request.client.host if request.client else "?"
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if ip.is_loopback or ip.is_private:
        real = request.headers.get("x-real-ip", "").strip()
        if real and len(real) <= 64:
            return real
    return peer


def _frame(ev: events.Event) -> str:
    data = json.dumps(ev.data, separators=(",", ":"))
    return f"id: {ev.id}\nevent: {ev.type}\ndata: {data}\n\n"


class _SSEResponse(StreamingResponse):
    """Releases the subscription (and the per-client slot) however the response ends,
    including a cancel before the generator ever started (its own finally would not run)."""

    def __init__(self, gen, sub: events.Subscription, **kw):
        super().__init__(gen, **kw)
        self._sub = sub

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        except asyncio.CancelledError:
            pass  # server shutdown cancels open streams: end quietly, no traceback in the log
        finally:
            self._sub.close()


def _stream_limits(request: Request) -> tuple[float, float | None]:
    """(heartbeat seconds, jittered max stream lifetime or None) from app.state."""
    hb = float(getattr(request.app.state, "sse_heartbeat_s", HEARTBEAT_S))
    max_s = getattr(request.app.state, "sse_max_s", MAX_LIFETIME_S)
    if max_s is not None:
        max_s = float(max_s) * random.uniform(0.9, 1.1)  # de-synchronise the recycles
    return hb, max_s


async def _gen(request: Request, hub: events.Hub, sub: events.Subscription, backlog, last: int | None,
               hb: float, max_s: float | None):
    """The SSE frames of one stream: retry, optional reset, backlog, then events / heartbeats."""
    start = time.monotonic()
    try:
        yield "retry: 5000\n\n"
        if last is not None and last > hub.last_id:
            yield "event: reset\ndata: {}\n\n"  # server restarted; client should refetch
        for ev in backlog:
            yield _frame(ev)
        while not sub.done:
            sub.seen = time.monotonic()
            if max_s is not None and sub.seen - start >= max_s:
                yield "event: recycle\ndata: {}\n\n"  # clean end: the client reopens at once
                break
            if await request.is_disconnected():
                break
            ev = await sub.get(hb)
            if ev is not None:
                yield _frame(ev)
            elif sub.done:
                break
            else:
                yield ": hb\n\n"  # a dead peer makes this write fail -> finally below
        if sub.evicted:
            yield "event: bye\ndata: {}\n\n"  # a newer stream of this client took the slot
    except asyncio.CancelledError:
        return  # shutdown / hangup cancel: the finally below releases the slot
    finally:
        sub.close()


@router.get("/events", dependencies=[Depends(require_session)])
async def stream(request: Request) -> Response:
    hub: events.Hub = request.app.state.hub
    hb, max_s = _stream_limits(request)
    raw = request.headers.get("last-event-id", "")
    last = int(raw) if raw.isdigit() and len(raw) < 12 else None
    services.touch()
    try:
        sub, backlog = hub.subscribe(last, client=sse_client(request))
    except events.HubFull as full:
        msg = "too many streams from this client" if full.scope == "client" else "too many streams"
        return Response(msg, status_code=429, headers={"Retry-After": "10", "Cache-Control": "no-store"})
    gen = _gen(request, hub, sub, backlog, last, hb, max_s)
    return _SSEResponse(gen, sub, media_type="text/event-stream", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@router.post("/hooks/claude")
async def claude_hook(request: Request) -> Response:
    settings = request.app.state.settings
    peer = request.client.host if request.client else ""
    if peer not in LOCAL_PEERS or any(h in request.headers for h in PROXY_HEADERS):
        return Response(status_code=403)
    if not events.verify_hook_secret(request.headers.get("x-pm-hook-secret"), settings):
        return Response(status_code=401)
    cl = request.headers.get("content-length")
    if cl is not None and (not cl.isdigit() or int(cl) > MAX_HOOK_BODY):
        return Response(status_code=413)
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_HOOK_BODY:
            return Response(status_code=413)
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"error": "bad json"}, status_code=400)
    if not isinstance(doc, dict):
        return JSONResponse({"error": "bad json"}, status_code=400)
    name = doc.get("hook_event_name")
    if not isinstance(name, str) or name not in events.HOOK_STATES:
        return Response(status_code=204)  # unknown/ignored event
    sid = doc.get("session_id")
    cwd = doc.get("cwd")
    if sid is not None and not (isinstance(sid, str) and _ID_RE.match(sid)):
        return JSONResponse({"error": "bad session_id"}, status_code=400)
    if cwd is not None and not (isinstance(cwd, str) and cwd.startswith("/") and len(cwd) <= 4096
                                and "\x00" not in cwd):
        return JSONResponse({"error": "bad cwd"}, status_code=400)
    if not (sid or cwd):
        return JSONResponse({"error": "session_id or cwd required"}, status_code=400)
    # "message" / notification text is deliberately never read.
    return _hook_answer(settings, request.app.state.hub, request.app.state.tracker,
                        request.app.state.tracker.apply_hook(name, sid, cwd))


def _hook_answer(settings, hub: events.Hub, tracker: events.AgentTracker, evs) -> Response:
    """The hook's 204. The hook never waits for the live extras (UserPromptSubmit would delay the prompt): with a
    client listening the events are enriched and published after the answer, else published now."""
    if not evs or hub.subscriber_count == 0:
        events.publish_all(hub, evs)
        return Response(status_code=204)
    return Response(status_code=204, background=BackgroundTask(_publish_live, settings, hub, tracker, evs))


async def _publish_live(settings, hub: events.Hub, tracker: events.AgentTracker, evs) -> None:
    events.publish_all(hub, await events.with_live(settings, hub, tracker.agents, evs))
