"""Fork an agent conversation into a NEW agent (a new background tmux window).

The original session is never touched: a fork must not continue the same thread in two places.
Per-kind behaviour (CLI capabilities checked with ``<cli> --help``):

=============  ========  ==============================================================
kind           mode      argv (after the binary)
=============  ========  ==============================================================
claude         native    ``--resume <uuid> --fork-session`` (new session id, copied history)
codex          native    ``fork <uuid>`` (``codex fork``; uuid = tail of the rollout file stem;
                         without a uuid it degrades to handoff when the rollout file is known)
devin          handoff   ``-- <prompt>``  (devin only has --resume / --continue = same thread)
cursor-agent   handoff   ``-- <prompt>``  (--resume <chatId> would continue the same chat)
antigravity    handoff   ``-i <prompt>``  (agy has --conversation / --continue, no fork flag)
=============  ========  ==============================================================

Handoff = a fresh agent of the same kind in the same cwd whose first prompt is fixed text plus the
path of the original session file; the transcript content itself is never read or sent by pm.
An optional first message ("Fork and send") goes after ``--`` (``launch.prompt_args``) on native forks
and after the fixed text (blank line between) on handoff forks.
Everything is an argv list (positional args of the default-shell, ``tmuxctl.start_words``); ids and paths are
validated; the session id comes from pm's own detection / registry, never from the request.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from pm.agents import launch as launch_mod
from pm.agents import lineage, registry
from pm.chat.sources.claude import live_session_id
from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, Project
from pm.tmux import ctl as tmuxctl

MODES = {
    "claude": "native",
    "codex": "native",
    "devin": "handoff",
    "cursor-agent": "handoff",
    "antigravity": "handoff",
}
_ALIASES = {"agy": "antigravity"}
_TAIL_UUID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
_HANDOFF = ("Continue the work of an earlier {kind} session in this directory. Read its session "
            "file for context: {path} . Then {then}")
_THEN_WAIT, _THEN_ASK = "wait for my next instruction.", "answer my message below."
_MAX_PATH = 1024


class ForkError(launch_mod.LaunchError):
    """The conversation cannot be forked (unknown session, missing file, bad kind)."""


@dataclass(frozen=True)
class ForkResult(launch_mod.LaunchResult):
    mode: str = ""


def norm_kind(kind) -> str:
    k = str(getattr(kind, "value", kind) or "")
    return _ALIASES.get(k, k)


def fork_mode(kind) -> str | None:
    """'native' | 'handoff' | None (unsupported kind: the Fork button is hidden)."""
    return MODES.get(norm_kind(kind))


def _binary(settings: Settings, kind: str) -> str:
    return launch_mod.agent_binary(settings, AgentKind(kind))


def session_id(obj) -> str | None:
    """Best known session id of a live Agent or an ended registry Record (not yet validated)."""
    kind = norm_kind(obj.kind)
    if isinstance(obj, Agent):
        if kind == "claude" and obj.pid > 1:
            live = live_session_id(obj.pid)
            if live:
                return live
        return obj.ident or obj.session_uuid or None
    return obj.ident or None


def _transcript(obj, kind: str, ident: str | None) -> Path | None:
    raw = getattr(obj, "transcript", "") or ""
    if not raw and kind == "claude" and ident:
        from pm.chat.sources.claude import transcript_path
        p = transcript_path(getattr(obj, "cwd", ""), ident)
        raw = str(p) if p else ""
    if not raw or len(raw) > _MAX_PATH or not raw.isprintable() or not os.path.isabs(raw):
        return None
    p = Path(raw)
    return p if p.is_file() else None


def build_argv(settings: Settings, obj, prompt: str = "") -> tuple[str, list[str], str]:
    """(kind, argv, effective mode) for the fork with ``prompt`` as the first message (none when
    empty), or ForkError / LaunchError (invalid prompt)."""
    kind = norm_kind(obj.kind)
    mode = fork_mode(kind)
    if mode is None:
        raise ForkError("this agent kind cannot be forked")
    ident = session_id(obj)
    if not ident:
        raise ForkError("session id unknown")
    binary = _binary(settings, kind)
    if kind == "claude":
        if not registry.UUID_RE.fullmatch(ident):
            raise ForkError("session id unknown")
        return kind, [binary, "--resume", ident, "--fork-session", *launch_mod.prompt_args(prompt)], "native"
    if kind == "codex":
        m = _TAIL_UUID.search(ident)
        if m:
            return kind, [binary, "fork", m.group(1), *launch_mod.prompt_args(prompt)], "native"
    path = _transcript(obj, kind, ident)
    if path is None:
        raise ForkError("session file unknown")
    text = _HANDOFF.format(kind=kind, path=str(path), then=_THEN_ASK if prompt else _THEN_WAIT)
    return kind, [binary, *launch_mod.prompt_args(f"{text}\n\n{prompt}" if prompt else text, kind)], "handoff"


def fork_session(settings: Settings, obj, project: Project, prompt: str = "") -> ForkResult:
    """Start the fork in a new tmux window (``new-window -d``: the current tmux focus is untouched; the
    window stays when the agent quits) and remember where it came from (``pm.agents.lineage``).
    ``prompt``: optional first message."""
    kind, argv, mode = build_argv(settings, obj, prompt)
    if not os.access(argv[0], os.X_OK):
        raise launch_mod.LaunchError("agent binary not executable")
    cwd = Path(obj.cwd) if getattr(obj, "cwd", "") else Path(project.path)
    if not cwd.is_dir():
        cwd = Path(project.path)
    if not cwd.is_dir():
        raise launch_mod.LaunchError("project path missing")
    wname = f"agent-{kind}-fork"[:64].rstrip("-.")
    tmuxctl.check_name(wname)
    sid = launch_mod._find_session(settings, project)
    wid, pid = tmuxctl.new_agent_window(settings, sid, wname, str(cwd), argv)
    lineage.record(settings, obj.id, "fork", pid, kind)
    return ForkResult(sid, wid, wname, str(cwd), pane_id=pid, mode=mode)
