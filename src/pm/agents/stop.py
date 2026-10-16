"""Stop / panic: Escape then C-c to agent panes. Never kills a pane; reports instead."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from pm.core.config import Settings
from pm.core.models import Agent
from pm.tmux import ctl as tmuxctl
from pm.tmux import kill as tmuxkill

DELAY_S = 1.5
_SHELLS = frozenset({"sh", "bash", "zsh", "fish", "dash"})
_SCOPE_PROJECT = re.compile(r"^project:([A-Za-z0-9._-]{1,64})$")


@dataclass(frozen=True)
class StopResult:
    agent_id: str
    status: str  # stopped | still_running | gone | error
    detail: str = ""


def _send(settings: Settings, agent: Agent) -> str | None:
    try:
        tmuxctl.send_key(settings, agent.pane_id, "Escape")
        tmuxctl.send_key(settings, agent.pane_id, "C-c")
    except tmuxctl.TmuxError as e:
        return str(e)[:200]
    return None


def _check(settings: Settings, agent: Agent) -> StopResult:
    try:
        panes = {p.id: p for s in tmuxctl.list_sessions(settings)
                 for w in s.windows for p in w.panes}
    except tmuxctl.TmuxError as e:
        return StopResult(agent.id, "error", str(e)[:200])
    pane = panes.get(agent.pane_id)
    if pane is None:
        return StopResult(agent.id, "gone", "pane no longer exists")
    if pane.command in _SHELLS:
        return StopResult(agent.id, "stopped")
    return StopResult(agent.id, "still_running",
                      f"{pane.command} still running; pane not killed")


def stop_agent(settings: Settings, agent: Agent, delay: float = DELAY_S,
               sleep: Callable[[float], None] = time.sleep) -> StopResult:
    err = _send(settings, agent)
    if err:
        return StopResult(agent.id, "error", err)
    sleep(delay)
    return _check(settings, agent)


def exit_agent(settings: Settings, agent: Agent, delay: float = DELAY_S,
               sleep: Callable[[float], None] = time.sleep) -> StopResult:
    """Quit an idle agent: Ctrl-C twice (Claude exits on the second press). Refuses protected
    sessions and pm's own session; never kills the pane. Status adds ``refused``."""
    try:
        sessions = tmuxctl.list_sessions(settings)
        me = tmuxkill._self_session_id(settings, sessions)
    except (tmuxctl.TmuxError, tmuxkill.KillRefused) as e:
        return StopResult(agent.id, "error", str(e)[:200])
    sess = next((s for s in sessions for w in s.windows for p in w.panes
                 if p.id == agent.pane_id), None)
    if sess is None:
        return StopResult(agent.id, "gone", "pane no longer exists")
    if tmuxctl.is_protected(sess.name, settings):
        return StopResult(agent.id, "refused", "session is protected")
    if me is not None and sess.id == me:
        return StopResult(agent.id, "refused", "pm's own session")
    try:
        tmuxctl.send_key(settings, agent.pane_id, "C-c")
        sleep(0.4)
        tmuxctl.send_key(settings, agent.pane_id, "C-c")
    except tmuxctl.TmuxError as e:
        return StopResult(agent.id, "error", str(e)[:200])
    sleep(delay)
    return _check(settings, agent)


def select(agents: list[Agent], scope: str) -> list[Agent]:
    if scope == "all":
        return list(agents)
    m = _SCOPE_PROJECT.fullmatch(scope) if isinstance(scope, str) else None
    if not m:
        raise ValueError(f"invalid scope: {scope!r}")
    return [a for a in agents if a.project == m.group(1)]


def panic(settings: Settings, agents: list[Agent], scope: str = "all",
          delay: float = DELAY_S,
          sleep: Callable[[float], None] = time.sleep) -> dict[str, StopResult]:
    targets = select(list(agents), scope)
    results: dict[str, StopResult] = {}
    sent: list[Agent] = []
    for a in targets:
        err = _send(settings, a)
        if err:
            results[a.id] = StopResult(a.id, "error", err)
        else:
            sent.append(a)
    if sent:
        sleep(delay)
        for a in sent:
            results[a.id] = _check(settings, a)
    return results


def kill_agent_pane(settings: Settings, agent: Agent) -> StopResult:
    """Kill the agent's tmux pane by id (``kill-pane -t %n``; tmux closes the window with its
    last pane). Statuses: killed | gone | refused | error. Refuses protected sessions, pm's own
    session, and the only pane of a session's only window (that would end the whole session)."""
    try:
        tmuxctl.check_id(agent.pane_id, "%")
        sessions = tmuxctl.list_sessions(settings)
        me = tmuxkill._self_session_id(settings, sessions)
    except (tmuxctl.TmuxError, tmuxkill.KillRefused) as e:
        return StopResult(agent.id, "error", str(e)[:200])
    hit = next(((s, w) for s in sessions for w in s.windows for p in w.panes
                if p.id == agent.pane_id), None)
    if hit is None:
        return StopResult(agent.id, "gone", "pane no longer exists")
    sess, win = hit
    if tmuxctl.is_protected(sess.name, settings):
        return StopResult(agent.id, "refused", "session is protected")
    if me is not None and sess.id == me:
        return StopResult(agent.id, "refused", "pm's own session")
    if len(sess.windows) == 1 and len(win.panes) == 1:
        return StopResult(agent.id, "refused",
                          "it is the only pane of the only window in its tmux session; "
                          "killing it would end the whole session")
    try:
        tmuxctl.run(settings, ["kill-pane", "-t", agent.pane_id])
    except tmuxctl.TmuxError as e:
        return StopResult(agent.id, "error", str(e)[:200])
    return StopResult(agent.id, "killed")
