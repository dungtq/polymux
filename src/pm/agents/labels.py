"""Display names for agents: ``<tmux session>-<window index>-<kind>``.

The URL id (``claude-140``) stays the stable key; this label is display only.
Pure functions, no I/O. Output is always ``[A-Za-z0-9._-]`` and <= 64 chars.

Collision rule: when two or more agents in the SAME window share the same base
label (same kind), ALL of them get ``-<pane position in window, 1-based>``
appended. The pane position comes from the tmux pane order, so labels are
deterministic and do not change between polls.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable

from pm.core.models import Agent, TmuxSession

MAX_LABEL = 64
_BAD = re.compile(r"[^A-Za-z0-9._-]+")
_DASHES = re.compile(r"-{2,}")


def sanitize(text: str) -> str:
    s = _DASHES.sub("-", _BAD.sub("-", str(text or "")))
    return s.strip("-")


def fallback(agent_id: str) -> str:
    return sanitize(agent_id)[:MAX_LABEL]


def agent_label(session_name: str, window_index: int, kind: str, *,
                pane_index: int | None = None, taken: Collection[str] = ()) -> str:
    """Build one label. ``taken`` holds base labels that collide in this window;
    when the base is in it and ``pane_index`` is given, ``-<pane_index>`` is added."""
    sess = sanitize(session_name) or "tmux"
    kd = sanitize(kind) or "agent"
    idx = str(int(window_index))
    base = f"{sess}-{idx}-{kd}"
    hit = base in taken or (
        pane_index is not None and len(base) > MAX_LABEL
        and agent_label(session_name, window_index, kind) in taken)
    suffix = f"-{int(pane_index)}" if pane_index is not None and hit else ""
    tail = f"-{idx}-{kd}{suffix}"
    room = MAX_LABEL - len(tail)
    if room < 1:  # absurd kind: keep index, clip kind
        kd = kd[: max(1, MAX_LABEL - len(f"x-{idx}-") - len(suffix))]
        tail = f"-{idx}-{kd}{suffix}"
        room = max(1, MAX_LABEL - len(tail))
    head = sess[:room].rstrip("-.") or sess[:room]
    return f"{head}{tail}"[:MAX_LABEL]


def label_agents(agents: Iterable[Agent], sessions: Iterable[TmuxSession]) -> dict[str, str]:
    """Map agent id -> label using the tmux snapshot; unknown pane -> old id."""
    where: dict[str, tuple[str, int, str, int]] = {}  # pane -> (sess, win idx, win id, pos)
    for s in sessions:
        for w in s.windows:
            for pos, p in enumerate(w.panes, 1):
                where[p.id] = (s.name, w.index, w.id, pos)
    agents = list(agents)
    bases: dict[tuple[str, str], list[str]] = {}
    for a in agents:
        loc = where.get(a.pane_id)
        if loc:
            b = agent_label(loc[0], loc[1], a.kind.value)
            bases.setdefault((loc[2], b), []).append(a.id)
    clash = {k for k, ids in bases.items() if len(ids) > 1}
    out: dict[str, str] = {}
    for a in agents:
        loc = where.get(a.pane_id)
        if not loc:
            out[a.id] = fallback(a.id)
            continue
        b = agent_label(loc[0], loc[1], a.kind.value)
        taken = {b} if (loc[2], b) in clash else ()
        out[a.id] = agent_label(loc[0], loc[1], a.kind.value, pane_index=loc[3], taken=taken)
    return out


MAX_WNAME = 48
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2066-\u2069]+")


def clean_window_name(text: str) -> str:
    """Display-only tmux window name: control/bidi chars dropped, <= 48 chars. Never an id."""
    return _CTRL.sub(" ", str(text or "")).strip()[:MAX_WNAME].strip()


def window_names(agents: Iterable[Agent], sessions: Iterable[TmuxSession]) -> dict[str, str]:
    """Map agent id -> name of the tmux window holding its pane ('' when unknown)."""
    by_pane = {p.id: w.name for s in sessions for w in s.windows for p in w.panes}
    return {a.id: clean_window_name(by_pane.get(a.pane_id, "")) for a in agents}
