"""ntfy publisher.

Only the headers Title / Tags / Click / Priority are sent, plus a short body
built from agent state + project name + agent id. Never pane text, tokens,
secrets or the hook secret. No `Actions` header (no http action buttons).
Failures are logged by exception type only and never raised.
"""

from __future__ import annotations

import logging
import re
import ssl
import threading
import time

import httpx

from pm.agents import labels
from pm.core.config import Settings
from pm.core.models import Agent, AgentState

log = logging.getLogger("pm.notify")

DEFAULT_URL = ""  # = Settings.ntfy_url; set a `pm` topic, not `agent` (agent-written messages only)
TIMEOUT_S = 3.0
MAX_MESSAGE = 120
DEBOUNCE_S = 30.0  # min gap between any two notifications for one agent
DEDUPE_S = 600.0  # same (agent, state) suppressed for this long

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_NAME_BAD = re.compile(r"[^A-Za-z0-9._-]")
_PRIORITY = {"min", "low", "default", "high", "max", "1", "2", "3", "4", "5"}

_lock = threading.Lock()
_last_any: dict[str, float] = {}
_last_state: dict[tuple[str, str], float] = {}


def reset() -> None:
    with _lock:
        _last_any.clear()
        _last_state.clear()


def agent_link(settings: Settings, agent_id: str) -> str:
    if not _ID_RE.fullmatch(agent_id or ""):
        raise ValueError("bad agent id")
    return f"{settings.base_url.rstrip('/')}/a/{agent_id}"


def _ascii(text: str, limit: int) -> str:
    return "".join(c for c in text if 32 <= ord(c) < 127)[:limit]


def _validate_message(message: str) -> str:
    if not isinstance(message, str) or not message or len(message) > MAX_MESSAGE \
            or "\n" in message or not message.isascii() or not message.isprintable():
        raise ValueError("message must be short single-line printable ASCII")
    return message


def publish(settings: Settings, title: str, message: str, *, click: str | None = None,
            tags=(), priority: str | None = None, transport=None) -> bool:
    """POST to ntfy. Returns True on 2xx; never raises."""
    try:
        message = _validate_message(message)
        headers = {"Title": _ascii(title, 80) or "pm"}
        tags = [t for t in (_ascii(str(t), 32) for t in tags) if t and "," not in t]
        if tags:
            headers["Tags"] = ",".join(tags)
        if click:
            headers["Click"] = _ascii(click, 200)
        if priority is not None:
            if str(priority) not in _PRIORITY:
                raise ValueError("bad priority")
            headers["Priority"] = str(priority)
        url = settings.ntfy_url
        if not url:
            return False
        ca = settings.ntfy_ca
        kw: dict = {"timeout": TIMEOUT_S, "follow_redirects": False}
        if transport is not None:
            kw["transport"] = transport
        else:
            kw["verify"] = _ssl_context(str(ca) if ca else None)
        with httpx.Client(**kw) as c:
            r = c.post(url, content=message.encode("ascii"), headers=headers)
        return r.is_success
    except Exception as exc:  # noqa: BLE001 - by type only; never leak details
        log.warning("ntfy publish failed: %s", exc.__class__.__name__)
        return False


def _ssl_context(ca: str | None = None) -> ssl.SSLContext:
    """System store (or `ca` file), full chain + hostname checks.

    Python 3.13 enables VERIFY_X509_STRICT, which rejects the internal CA
    (Basic Constraints not marked critical) that the Go ntfy CLI accepts.
    Only that strict-profile flag is cleared; verification stays on.
    """
    ctx = ssl.create_default_context(cafile=ca)
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx


def should_notify(old: AgentState | None, new: AgentState) -> bool:
    if new == old:
        return False
    # Idle ("finished, at prompt") is noise: never pushed. The inbox still lists it.
    return new in (AgentState.WAITING, AgentState.FAILED)


def notify_state_change(settings: Settings, agent: Agent, old: AgentState | None,
                        new: AgentState, *, now: float | None = None, transport=None) -> bool:
    """True if a notification was sent successfully."""
    try:
        if not should_notify(old, new):
            return False
        now = time.monotonic() if now is None else now
        deb = float(settings.ntfy_debounce_s)
        dup = float(settings.ntfy_dedupe_s)
        with _lock:
            if now - _last_any.get(agent.id, -1e12) < deb:
                return False
            if now - _last_state.get((agent.id, new.value), -1e12) < dup:
                return False
            _last_any[agent.id] = now
            _last_state[(agent.id, new.value)] = now
        proj = _NAME_BAD.sub("", agent.project or "")[:40] or agent.id
        verb = {AgentState.WAITING: "is waiting", AgentState.FAILED: "failed"}[new]
        prio = "high" if new == AgentState.FAILED else None
        wn = labels.clean_window_name(_NAME_BAD.sub("", getattr(agent, "window_name", "") or ""))
        who = _NAME_BAD.sub("", agent.label or agent.id)[:64] + (f" [{wn}]" if wn else "")
        return publish(settings, f"{proj} {verb}" + (f" ({wn})" if wn else ""), f"{who} ({proj}): {new.value}",
                       click=agent_link(settings, agent.id), tags=("pm", new.value),
                       priority=prio, transport=transport)
    except Exception as exc:  # noqa: BLE001
        log.warning("ntfy notify failed: %s", exc.__class__.__name__)
        return False
