"""Agent state: Claude session-json status mapping and pane-text heuristics."""

from __future__ import annotations

import re

from pm.core.models import AgentKind, AgentState

_CLAUDE_STATUS = {
    "busy": AgentState.RUNNING,
    "running": AgentState.RUNNING,
    "working": AgentState.RUNNING,
    "idle": AgentState.IDLE,
    "waiting": AgentState.WAITING,
    "needs_input": AgentState.WAITING,
    "blocked": AgentState.WAITING,
    "error": AgentState.FAILED,
    "failed": AgentState.FAILED,
}


def map_claude_status(status: object) -> AgentState:
    if not isinstance(status, str):
        return AgentState.UNKNOWN
    return _CLAUDE_STATUS.get(status.strip().lower(), AgentState.UNKNOWN)


_TAIL_LINES = 25

_WAITING = [
    re.compile(r"do you want to\b", re.I),
    re.compile(r"\b(allow|approve|authorize)\b.*\?", re.I),
    re.compile(r"\(\s*y\s*/\s*n\s*\)|\[\s*y\s*/\s*n\s*\]|\by/n\b", re.I),
    re.compile(r"^\s*[❯>›]?\s*1[.)]\s+(yes|allow|approve|run)", re.I | re.M),
    re.compile(r"^\s*[❯>›]?\s*[23][.)]\s+(no\b|don'?t|yes,? and|deny|reject|always)", re.I | re.M),
    re.compile(r"waiting for (your )?(approval|confirmation|input)", re.I),
    re.compile(r"press enter to (confirm|continue)", re.I),
]
_RUNNING = [
    re.compile(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏✻✶✳✢·✽]\s+\S+(ing|…)", re.I),
    re.compile(r"esc to interrupt|ctrl\+c to (interrupt|stop|cancel)", re.I),
    re.compile(r"\b(thinking|working|running|generating|searching|reading|editing)\s*(\.{2,}|…)", re.I),
]
_FAILED = [
    re.compile(r"^\s*(error|fatal|traceback \(most recent)", re.I | re.M),
    re.compile(r"(rate limit|quota exceeded|api error|request failed|connection (refused|lost))", re.I),
]
_IDLE = [
    re.compile(r"^\s*[│|]?\s*[>❯›]\s*(\S.*)?[│|]?\s*$", re.M),
    re.compile(r"\?\s+for shortcuts|bypass permissions|accept edits|auto-accept", re.I),
    re.compile(r"^\s*[│|]\s*>", re.M),
    re.compile(r"^\s*→\s+(Add a follow-up|Plan, search, build anything)", re.M),  # cursor-agent's empty prompt
]


def failed_line(text: str | None) -> str:
    """The newest line of the pane tail that makes ``classify_pane_text`` say FAILED ('' when none)."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()][-8:]
    return next((ln for ln in reversed(lines) if any(p.search(ln) for p in _FAILED)), "")


def classify_pane_text(text: str, kind: AgentKind | str = AgentKind.OTHER) -> tuple[AgentState, float]:
    """Heuristic state from ANSI-stripped pane text. Confidence is always < 1."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return AgentState.UNKNOWN, 0.0
    tail = "\n".join(lines[-_TAIL_LINES:])
    near = "\n".join(lines[-8:])

    if any(p.search(tail) for p in _WAITING):
        return AgentState.WAITING, 0.8
    if any(p.search(near) for p in _RUNNING):
        return AgentState.RUNNING, 0.7
    if any(p.search(near) for p in _FAILED):
        return AgentState.FAILED, 0.5
    if any(p.search(near) for p in _IDLE):
        return AgentState.IDLE, 0.6
    return AgentState.UNKNOWN, 0.2
