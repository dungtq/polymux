"""Resume an ended agent session in a new tmux window (claude, codex, devin, cursor-agent).

argv only (validated values as default-shell positional args, as launch does); the session id is validated per kind
(``registry.resume_ident``) and taken from pm's own registry, never from the request.
Per-kind argv: ``_ARGV`` below. An optional first message ("Resume and send") is the CLI's own
initial prompt, appended as ``-- <prompt>`` (``launch.prompt_args``), never typed into the pane.
"""

from __future__ import annotations

import os
from pathlib import Path

from pm.agents import launch as launch_mod
from pm.agents import message as msg
from pm.agents import registry
from pm.core.config import Settings
from pm.core.errors import Invalid
from pm.core.models import AgentKind, Project
from pm.tmux import ctl as tmuxctl

# argv after the binary; {} = the validated id. None of these CLIs takes the id after ``--``
# (devin / cursor-agent: ``--`` starts the prompt), so the id is checked to never start with "-".
_ARGV = {
    "claude": ["--resume", "{}"],
    "codex": ["resume", "{}"],
    "devin": ["--resume", "{}"],
    "cursor-agent": ["--resume", "{}"],
}


def resumable_kind(kind: str) -> bool:
    return kind in _ARGV


def first_message(text: str) -> str:
    """The optional first message of "Resume and send" / "Fork and send": ``""`` when blank, else the
    validated text (``message.validate_text``). ``Invalid`` for a slash command (it needs a running agent)
    or invalid text. Check it before any tmux window is started."""
    if not text.strip():
        return ""
    if text.lstrip().startswith("/"):
        raise Invalid("slash commands need a running agent")
    try:
        return msg.validate_text(text)
    except msg.InvalidMessage:
        raise Invalid("invalid input") from None


def build_argv(settings: Settings, rec: registry.Record, prompt: str = "") -> list[str]:
    """Full argv to resume ``rec`` (+ ``prompt`` as its first message), or LaunchError (unknown kind,
    missing / invalid id, invalid prompt)."""
    ident = registry.resume_ident(rec.kind, rec.ident)
    if rec.kind not in _ARGV or ident is None:
        raise launch_mod.LaunchError("session cannot be resumed")
    return [launch_mod.agent_binary(settings, AgentKind(rec.kind)),
            *(ident if x == "{}" else x for x in _ARGV[rec.kind]), *launch_mod.prompt_args(prompt)]


def resume_session(settings: Settings, rec: registry.Record, project: Project,
                   prompt: str = "") -> launch_mod.LaunchResult:
    argv = build_argv(settings, rec, prompt)
    if not os.access(argv[0], os.X_OK):
        raise launch_mod.LaunchError("agent binary not executable")
    cwd = Path(rec.cwd) if rec.cwd else Path(project.path)
    if not cwd.is_dir():
        cwd = Path(project.path)
    if not cwd.is_dir():
        raise launch_mod.LaunchError("project path missing")
    wname = f"agent-{rec.kind}-resume"
    sid = launch_mod._find_session(settings, project)
    out = tmuxctl.run(settings, ["new-window", "-d", "-P", "-F", "#{window_id} #{pane_id}",
                                 "-t", sid + ":", "-n", wname, "-c", tmuxctl.arg(str(cwd)),
                                 "--", *tmuxctl.start_words(settings, argv)]).strip()
    wid, _, pid = out.partition(" ")
    tmuxctl.check_id(wid, "@")
    pid = pid.strip()
    if pid:
        tmuxctl.check_id(pid, "%")
    return launch_mod.LaunchResult(sid, wid, wname, str(cwd), pane_id=pid)
