"""Tier-2 kills with strict guards and a typed confirm (the target's current name)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from pm.core.config import Settings
from pm.tmux import ctl as tmuxctl


class KillRefused(Exception):
    pass


class ConfirmMismatch(KillRefused):
    pass


@dataclass(frozen=True)
class KillResult:
    target: str
    name: str
    session: str


def _self_session_id(settings: Settings, sessions) -> str | None:
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return None  # not running inside tmux (e.g. systemd unit)
    if not tmuxctl.ID_RE.fullmatch(pane):
        raise KillRefused("cannot resolve own session")
    for s in sessions:
        for w in s.windows:
            if any(p.id == pane for p in w.panes):
                return s.id
    raise KillRefused("cannot resolve own session")


def _guard(settings: Settings, target: str, kind: str, confirm: str,
           force_attached: bool) -> KillResult:
    tmuxctl.check_id(target, kind)
    try:
        sessions = tmuxctl.list_sessions(settings)
    except tmuxctl.TmuxError as e:
        raise KillRefused("cannot list tmux state") from e
    me = _self_session_id(settings, sessions)
    sess = name = None
    for s in sessions:
        if kind == "$" and s.id == target:
            sess, name = s, s.name
        for w in s.windows:
            if kind == "@" and w.id == target:
                sess, name = s, w.name
            for p in w.panes:
                if kind == "%" and p.id == target:
                    sess, name = s, p.id  # panes are unnamed: confirm with the pane id
    if sess is None or name is None:
        raise KillRefused("target not found or session unresolved")
    if tmuxctl.is_protected(sess.name, settings):
        raise KillRefused("session is protected")
    if me is not None and sess.id == me:
        raise KillRefused("refusing to touch pm's own session")
    if sess.attached > 0 and not force_attached:
        raise KillRefused("session has attached clients")
    if not isinstance(confirm, str) or confirm != name:
        raise ConfirmMismatch("confirm text does not match target name")
    return KillResult(target, name, sess.name)


def kill_window(settings: Settings, window_id: str, confirm: str,
                force_attached: bool = False) -> KillResult:
    r = _guard(settings, window_id, "@", confirm, force_attached)
    tmuxctl.run(settings, ["kill-window", "-t", window_id])
    return r


def kill_session(settings: Settings, session_id: str, confirm: str,
                 force_attached: bool = False) -> KillResult:
    r = _guard(settings, session_id, "$", confirm, force_attached)
    tmuxctl.run(settings, ["kill-session", "-t", session_id])
    return r


def kill_pane(settings: Settings, pane_id: str, confirm: str,
              force_attached: bool = False) -> KillResult:
    r = _guard(settings, pane_id, "%", confirm, force_attached)
    tmuxctl.run(settings, ["kill-pane", "-t", pane_id])
    return r


@dataclass(frozen=True)
class ClearResult:
    session: str
    killed: tuple[str, ...]  # names of the windows removed
    kept: int  # windows left in the session


def clear_ended_windows(settings: Settings, session_id: str) -> ClearResult:
    """Kill the finished windows of one session: every pane has exited (remain-on-exit, ``dead``).

    Live windows (agents, shells, anything running) are never touched. Refuses a protected
    session and pm's own session. If every window is finished, the active one is kept so the
    session itself survives. Attached clients are fine: only dead windows go."""
    tmuxctl.check_id(session_id, "$")
    try:
        sessions = tmuxctl.list_sessions(settings)
    except tmuxctl.TmuxError as e:
        raise KillRefused("cannot list tmux state") from e
    me = _self_session_id(settings, sessions)
    sess = next((s for s in sessions if s.id == session_id), None)
    if sess is None:
        raise KillRefused("target not found or session unresolved")
    if tmuxctl.is_protected(sess.name, settings):
        raise KillRefused("session is protected")
    if me is not None and sess.id == me:
        raise KillRefused("refusing to touch pm's own session")
    dead = [w for w in sess.windows if w.panes and all(p.dead for p in w.panes)]
    if len(dead) == len(sess.windows) and dead:
        keep = next((w for w in dead if w.active), dead[0])
        dead = [w for w in dead if w.id != keep.id]
    for w in dead:
        tmuxctl.check_id(w.id, "@")
        tmuxctl.run(settings, ["kill-window", "-t", w.id])
    return ClearResult(sess.name, tuple(w.name for w in dead), len(sess.windows) - len(dead))


@dataclass(frozen=True)
class CloseAllResult:
    closed: tuple[str, ...]  # session names killed
    skipped: tuple[tuple[str, str], ...]  # (session name, reason)


def close_sessions(settings: Settings, session_ids: list[str],
                   force_attached: bool = False) -> CloseAllResult:
    """Kill each given session through ``kill_session`` (so protected sessions, pm's own session
    and attached clients are refused one by one). The typed confirm is the session's current
    name; a session that vanished meanwhile is skipped. Attached sessions are skipped unless forced."""
    try:
        names = {s.id: s.name for s in tmuxctl.list_sessions(settings)}
    except tmuxctl.TmuxError as e:
        raise KillRefused("cannot list tmux state") from e
    closed: list[str] = []
    skipped: list[tuple[str, str]] = []
    for sid in session_ids:
        name = names.get(sid)
        if name is None:
            continue
        try:
            kill_session(settings, sid, name, force_attached=force_attached)
            closed.append(name)
        except KillRefused as e:
            skipped.append((name, str(e)))
        except tmuxctl.TmuxError:
            skipped.append((name, "tmux error"))
    return CloseAllResult(tuple(closed), tuple(skipped))
