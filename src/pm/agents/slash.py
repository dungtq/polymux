"""Per-kind slash command catalog and the server-side policy for messages starting with ``/``.

Curated from docs/SLASH-COMMANDS.md (probed 2026-10-08). Names differ between CLIs, so there
is one static list per agent kind. ``risk``: ``info`` (read-only, any time), ``action``
(changes the session, only offered while the agent is not running), ``danger`` (ends or
reconfigures the session; needs ``confirm=1`` in the POST, enforced here, not only in JS).
``output``: ``panel`` (TUI-only output worth capturing into the output card) or ``none``.

Policy for ``/`` messages (``check_message``):
* The first token (up to whitespace) is the command. A token containing another ``/`` is a
  file path (``/home/x look at this``), not a command, and is never restricted.
* A command in the kind's catalog follows its risk: ``danger`` requires ``confirm=1``.
* A command in the kind's danger list that the palette does not offer (``/yolo``, ``/quit``...)
  is also refused without ``confirm=1``.
* Any other command is an unknown passthrough: allowed (the CLI answers "unknown command" or
  treats it as chat), because skills and MCP prompts add commands pm cannot list.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from pm.core.models import AgentKind


class SlashError(Exception):
    """Refused slash message; ``str(e)`` is safe to show."""


@dataclass(frozen=True)
class Cmd:
    cmd: str
    args: str
    desc: str
    risk: str = "info"
    output: str = "none"


def _c(cmd, desc, risk="info", output="none", args=""):
    return Cmd(cmd, args, desc, risk, output)


_CLAUDE = (
    _c("/context", "Show context usage", output="panel"),
    _c("/cost", "Show cost and usage", output="panel"),
    _c("/compact", "Summarise the conversation to free context", "action", args="[instructions]"),
    _c("/clear", "Start a new chat (drops context)", "danger"),
    _c("/model", "Switch model", "action"),
    _c("/plan", "Enter plan mode", "action"),
    _c("/advisor", "Set or turn off the advisor model", "action", args="[model|off]"),
    _c("/rewind", "Rewind the conversation", "action"),
    _c("/exit", "Exit the agent", "danger"),
    _c("/logout", "Log out of the account", "danger"),
)
_DEVIN = (
    _c("/context", "Show context usage (estimate)", output="panel"),
    _c("/usage", "Show usage", output="panel"),
    _c("/session-stats", "Session token statistics", output="panel"),
    _c("/recap", "Recap the session", output="panel"),
    _c("/steps", "List steps", output="panel"),
    _c("/compact", "Summarise the conversation to free context", "action"),
    _c("/model", "Switch model", "action"),
    _c("/fast", "Toggle fast mode", "action"),
    _c("/plan", "Enter plan mode", "action"),
    _c("/fork", "Fork the session", "action", args="[step]"),
    _c("/revert", "Revert to a step", "action", args="<step>"),
    _c("/clear", "Start a new chat (drops context)", "danger"),
    _c("/exit", "Exit the agent", "danger"),
    _c("/logout", "Log out of the account", "danger"),
    _c("/dangerous", "Skip permission prompts", "danger"),
    _c("/rm-session", "Delete this session", "danger"),
)
_CURSOR = (
    _c("/about", "Version and account", output="panel"),
    _c("/summarize", "Summarise the conversation to free context", "action"),
    _c("/model", "Switch model", "action"),
    _c("/fast", "Toggle fast mode", "action"),
    _c("/plan", "Enter plan mode", "action"),
    _c("/ask", "Ask mode (read-only)", "action"),
    _c("/fork", "Fork the session", "action"),
    _c("/rewind", "Rewind the conversation", "action"),
    _c("/clear", "Start a new chat (drops context)", "danger"),
    _c("/update", "Update the CLI", "danger"),
)
_AGY = (
    _c("/context", "Show context usage by category", output="panel"),
    _c("/credits", "Show credits", output="panel"),
    _c("/model", "Switch model", "action"),
    _c("/effort", "Set reasoning effort", "action"),
    _c("/plan", "Enter plan mode", "action"),
    _c("/fork", "Fork the session", "action"),
    _c("/rewind", "Rewind the conversation", "action"),
    _c("/clear", "Start a new chat (drops context)", "danger"),
    _c("/exit", "Exit the agent", "danger"),
    _c("/logout", "Log out of the account", "danger"),
    _c("/permissions", "Change permissions", "danger"),
)

CATALOG: dict[str, tuple[Cmd, ...]] = {
    AgentKind.CLAUDE.value: _CLAUDE,
    AgentKind.DEVIN.value: _DEVIN,
    AgentKind.CURSOR.value: _CURSOR,
    AgentKind.ANTIGRAVITY.value: _AGY,
    AgentKind.CODEX.value: (),
}
# Dangerous in the CLI but not worth offering in the palette; still need confirm=1.
EXTRA_DANGER: dict[str, frozenset[str]] = {
    "claude": frozenset(),
    "devin": frozenset({"/quit", "/yolo", "/update", "/upgrade", "/new"}),
    "cursor-agent": frozenset({"/logout", "/quit", "/exit"}),
    "antigravity": frozenset({"/new", "/quit"}),
    "codex": frozenset({"/quit", "/exit", "/logout"}),
}
# semantic action -> per-kind command name (None: not available)
COMPACT: dict[str, str | None] = {
    "claude": "/compact", "devin": "/compact", "cursor-agent": "/summarize",
    "antigravity": None, "codex": None,
}


# semantic action -> per-kind quit command (None: not available). Sent as a normal message.
EXIT: dict[str, str | None] = {
    "claude": "/exit", "devin": "/exit", "cursor-agent": "/exit",
    "antigravity": "/exit", "codex": "/quit",
}


def exit_cmd(kind: str) -> str | None:
    return EXIT.get(kind)


def catalog_for(kind: str) -> list[dict]:
    return [asdict(c) for c in CATALOG.get(kind, ())]


# Commands the launch form's prompt box offers (a launch has no running turn, so no gating).
LAUNCH_CMDS = ("/plan", "/advisor")


def launch_catalog() -> dict[str, list[dict]]:
    """Per kind: the catalog entries the launch form's prompt box offers (``/plan``, ``/advisor``)."""
    return {k: [asdict(c) for c in cmds if c.cmd in LAUNCH_CMDS] for k, cmds in CATALOG.items()}


def clear_cmd(kind: str) -> str | None:
    """The "start a new chat" command of this kind (``/clear``), or None when the kind has none.
    Codex has no catalog but also understands ``/clear``."""
    if kind == AgentKind.CODEX.value or lookup(kind, "/clear"):
        return "/clear"
    return None


def compact_cmd(kind: str) -> str | None:
    return COMPACT.get(kind)


def command_of(text: str) -> str | None:
    """The leading ``/cmd`` token of a message, or None when it is not a command
    (no leading slash, bare ``/``, or a path such as ``/home/x``)."""
    if not text.startswith("/"):
        return None
    tok = text.split(None, 1)[0].lower()
    if len(tok) < 2 or "/" in tok[1:]:
        return None
    return tok


def lookup(kind: str, cmd: str) -> Cmd | None:
    for c in CATALOG.get(kind, ()):
        if c.cmd == cmd:
            return c
    return None


def risk_of(kind: str, text: str) -> str | None:
    """info | action | danger | unknown for a slash message; None when not a command."""
    cmd = command_of(text)
    if cmd is None:
        return None
    c = lookup(kind, cmd)
    if c is not None:
        return c.risk
    return "danger" if cmd in EXTRA_DANGER.get(kind, frozenset()) else "unknown"


def check_message(kind: str, text: str, confirm: bool, *, running: bool = False) -> str | None:
    """Raise SlashError if the slash message is refused; return its risk (None: plain text)."""
    risk = risk_of(kind, text)
    if risk == "danger" and not confirm:
        raise SlashError("this command needs confirmation")
    if risk == "action" and running:
        raise SlashError("wait until the agent is idle")
    return risk
