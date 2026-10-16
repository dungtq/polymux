"""Auto-exit of long-idle agents (background sweeper; no extra units, no per-request work).

Every ``autoexit_interval_s`` the sweeper reads the already published snapshot (it never triggers a
build) and exits Claude agents that are ``idle`` and whose last response (``Agent.done_ts``) is older
than the timeout. Exit uses the normal safe mechanism (``stop.exit_agent``: Ctrl-C twice, tmux ids,
argv only, timeout). Only sessions matching ``autoexit_sessions`` (never ``pm*``) are touched: the
protection checks use ``tmuxctl.autoexit_settings``. Afterwards the tmux window stays (like after any agent
quits); with ``autoexit_close_window`` it is closed, but only when it is not protected, has no attached client and
holds nothing but the shell left behind by that agent (no child processes).

State lives in ``state_dir/autoexit.json``: the timeout override (settings page) and one info record per
auto-exit (cwd, kind, session, model, project, window) for the ended-agent page. Nothing here ever holds pane text.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pm.agents import head as agenthead
from pm.agents import registry
from pm.agents import stop as stop_mod
from pm.core import executors
from pm.core.config import Settings
from pm.core.fsutil import JsonStore
from pm.core.models import Agent, AgentKind, AgentState, AuditEntry
from pm.security.audit import AuditError, AuditLog
from pm.tmux import ctl as tmuxctl
from pm.tmux import kill as tmuxkill

log = logging.getLogger(__name__)

MAX_PER_CYCLE = 3
FIRST_DELAY_S = 60.0
MAX_IDLE_S = 365 * 86400
INFO_MAX = 300
ID_RE = re.compile(r"[a-z][a-z-]*-[0-9a-z]{6}")
_SHELLS = stop_mod._SHELLS
_LOCK = threading.Lock()


def _file(settings: Settings) -> Path:
    return settings.state_dir / "autoexit.json"


def _store(settings: Settings) -> JsonStore:
    return JsonStore(_file(settings), dict, validate=lambda d: isinstance(d, dict), indent=1, lock=_LOCK, fsync_dir=True)


def _load(settings: Settings) -> dict:
    raw = _store(settings).load()
    if not isinstance(raw.get("info"), dict):
        raw["info"] = {}
    return raw


def _save(settings: Settings, data: dict) -> None:
    info = dict(sorted(data["info"].items(), key=lambda kv: kv[1].get("ts", 0))[-INFO_MAX:])
    out = {"info": info}
    if isinstance(data.get("idle_s"), int):
        out["idle_s"] = data["idle_s"]
    settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _store(settings).save(out)


def idle_timeout_s(settings: Settings) -> int:
    """Seconds an agent may stay idle before auto-exit; 0 = disabled."""
    v = _load(settings).get("idle_s")
    if not isinstance(v, int) or isinstance(v, bool):
        v = int(getattr(settings, "autoexit_idle_s", 86400) or 0)
    return max(0, min(MAX_IDLE_S, v))


def set_idle_timeout_s(settings: Settings, seconds: int) -> int:
    seconds = max(0, min(MAX_IDLE_S, int(seconds)))
    with _LOCK:
        d = _load(settings)
        d["idle_s"] = seconds
        _save(settings, d)
    return seconds


def info_for(settings: Settings, agent_id: str) -> dict | None:
    row = _load(settings)["info"].get(agent_id)
    return row if isinstance(row, dict) else None


def record_manual_exit(settings: Settings, a, window: str = "", now: float | None = None) -> None:
    """Remember what a manual Exit (``/exit`` sent as a message) needs for the ended-agent page,
    before it is sent. Metadata only, never pane text. Best effort: the registry row keeps the id."""
    now = time.time() if now is None else now
    try:
        with _LOCK:
            d = _load(settings)
            d["info"][a.id] = {"ts": now, "cwd": a.cwd, "kind": a.kind.value,
                               "session": a.ident or a.session_uuid or "",
                               "model": agenthead.model_from_transcript(a.transcript),
                               "project": a.project or "", "window": window, "manual": True}
            _save(settings, d)
    except OSError:
        pass


def human(seconds: float) -> str:
    s = int(max(0, seconds))
    if s >= 86400:
        d, h = divmod(s, 86400)
        return f"{d}d {h // 3600}h" if h >= 3600 else f"{d}d"
    if s >= 3600:
        return f"{s // 3600}h"
    return f"{max(1, s // 60)}m"


@dataclass(frozen=True)
class Candidate:
    agent: Agent
    idle_s: float
    window: str
    session: str


def plan(settings: Settings, snap, now: float | None = None, *, timeout_s: int | None = None,
         own_session: str | None = None) -> list[Candidate]:
    """Agents that would be exited now .
    Pure: only reads the snapshot and the autoexit state file."""
    now = time.time() if now is None else now
    timeout_s = idle_timeout_s(settings) if timeout_s is None else timeout_s
    if timeout_s <= 0:
        return []
    sess = {s.id: s for s in snap.sessions}
    s2 = tmuxctl.autoexit_settings(settings)
    out: list[Candidate] = []
    for a in snap.agents:
        if a.kind is not AgentKind.CLAUDE or a.state is not AgentState.IDLE:
            continue
        if not registry.UUID_RE.fullmatch(a.ident or a.session_uuid or ""):
            continue  # cannot be resumed: never exit it automatically
        done = a.done_ts()
        if not done or now - done < timeout_s:
            continue  # unknown activity counts as active
        s = sess.get(a.session_id)
        if s is None or tmuxctl.is_protected(s.name, s2) or s.id == own_session:
            continue
        out.append(Candidate(a, now - done, a.window_name, s.name))
    out.sort(key=lambda c: -c.idle_s)
    return out


def _children(pid: int) -> bool | None:
    """True if the process has child processes, None if that cannot be determined."""
    try:
        return bool(Path(f"/proc/{int(pid)}/task/{int(pid)}/children").read_text().split())
    except (OSError, ValueError):
        return None


def close_window_if_empty(settings: Settings, agent: Agent) -> str:
    """After the agent exited: kill its tmux window when that holds only the idle shell left behind.
    Returns "closed" or a short reason it was kept. Never raises."""
    try:
        sessions = tmuxctl.list_sessions(settings)
    except tmuxctl.TmuxError:
        return "tmux unavailable"
    s2 = tmuxctl.autoexit_settings(settings)
    for s in sessions:
        for w in s.windows:
            if not any(p.id == agent.pane_id for p in w.panes):
                continue
            if tmuxctl.is_protected(s.name, s2):
                return "session protected"
            if s.attached > 0:
                return "session attached"
            if len(w.panes) != 1:
                return "window has other panes"
            p = w.panes[0]
            if not p.dead and p.command not in _SHELLS:
                return "pane still running"
            if not p.dead and _children(p.pid) is not False:
                return "shell has child processes"
            try:
                tmuxkill.kill_window(s2, w.id, w.name)
            except (tmuxkill.KillRefused, tmuxctl.TmuxError) as e:
                return str(e)[:60]
            return "closed"
    return "pane gone"


def _audit(audit: AuditLog, action: str, target: str, result: str, detail: dict) -> None:
    audit.write(AuditEntry(time.time(), "autoexit", action, target, result, detail))


def exit_one(settings: Settings, audit: AuditLog, cand: Candidate, now: float | None = None) -> str:
    """Exit one candidate. Fails closed: no audit line, no exit. Returns a status word."""
    a = cand.agent
    now = time.time() if now is None else now
    ident = a.ident or a.session_uuid or ""
    model = agenthead.model_from_transcript(a.transcript)
    base = {"kind": a.kind.value, "session": ident, "project": a.project or "", "window": cand.window,
            "cwd": a.cwd[:200], "idle_s": int(cand.idle_s)}
    try:
        _audit(audit, "agent_autoexit", a.id, "attempt", base)
    except AuditError:
        return "audit unavailable"
    try:
        res = stop_mod.exit_agent(tmuxctl.autoexit_settings(settings), a)
    except Exception:  # noqa: BLE001
        res = stop_mod.StopResult(a.id, "error")
    closed = ""
    if res.status in ("stopped", "gone"):
        registry.mark_ended(settings, a.id, ident, now=now)
        with _LOCK:
            d = _load(settings)
            d["info"][a.id] = {"ts": now, "cwd": a.cwd, "kind": a.kind.value, "session": ident,
                               "model": model, "project": a.project or "", "window": cand.window,
                               "tmux_session": cand.session, "idle_s": int(cand.idle_s)}
            _save(settings, d)
        if res.status == "stopped":
            closed = close_window_if_empty(settings, a) if settings.autoexit_close_window else "kept"
    try:
        _audit(audit, "agent_autoexit", a.id, "ok" if res.status in ("stopped", "gone") else "error",
               {**base, "status": res.status, "model": model, "window_close": closed})
    except AuditError:
        log.warning("autoexit: audit result write failed")
    return res.status


def sweep(settings: Settings, audit: AuditLog, snap, now: float | None = None) -> list[tuple[str, str]]:
    """One synchronous pass (runs on the tmux pool). Returns [(agent id, status)]."""
    if snap is None or snap.errors:
        return []
    own = None
    try:
        own = tmuxkill._self_session_id(settings, snap.sessions)
    except tmuxkill.KillRefused:
        return []
    cands = plan(settings, snap, now, own_session=own)
    done: list[tuple[str, str]] = []
    for c in cands[:MAX_PER_CYCLE]:
        done.append((c.agent.id, exit_one(settings, audit, c, now)))
    return done


def _audit_of(app) -> AuditLog:
    st = app.state
    if getattr(st, "audit", None) is None:
        st.audit = AuditLog(st.settings.audit_file)
    return st.audit


async def loop(app, settings: Settings, published: Callable[[Settings], object]) -> None:
    """``published(settings)``: the latest published snapshot or None (never builds one)."""
    await asyncio.sleep(FIRST_DELAY_S)
    while True:
        try:
            if idle_timeout_s(settings) > 0:
                snap = published(settings)  # never builds one
                if snap is not None and time.time() - snap.ts < 120:
                    res = await executors.run_tmux(sweep, settings, _audit_of(app), snap)
                    if res:
                        log.info("autoexit: %d agent(s) handled", len(res))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("autoexit cycle failed: %s", type(e).__name__)
        await asyncio.sleep(max(30, int(getattr(settings, "autoexit_interval_s", 300))))


def start(app, published: Callable[[Settings], object], settings: Settings | None = None) -> asyncio.Task:
    settings = settings or app.state.settings
    app.state.autoexit_task = asyncio.create_task(loop(app, settings, published), name="pm-autoexit")
    return app.state.autoexit_task


async def stop(app) -> None:
    task = getattr(app.state, "autoexit_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        app.state.autoexit_task = None
