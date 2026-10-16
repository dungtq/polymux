"""Pure one-line statistics for page headings. Zero segments are hidden (except the main one)."""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping

from pm.agents.inbox import FINISHED_WINDOW_S, stale_failed

SEP = " · "


def _seg(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _join(title: str, main: str, rest: Iterable[tuple[int, str]]) -> str:
    return SEP.join([title, main, *[f"{n} {w}" for n, w in rest if n]])


def ntfy_total(counts: Mapping[str, int] | None) -> int:
    return sum(int(v) for v in (counts or {}).values())


def stats_inbox(snapshot, ntfy_counts: Mapping[str, int] | None = None, total: int | None = None,
                idle_after_s: float = FINISHED_WINDOW_S) -> str:
    """``total`` (agents before a text filter) switches the main segment to "N of M agents"."""
    states = [a.state.value for a in snapshot.agents]
    now = time.time()
    # same rule as inbox.bucket: idle = last response >= idle_after_s ago (or unknown); failed counts as waiting there
    fresh = sum(1 for a in snapshot.agents
                if a.state.value == "idle" and a.done_ts() and now - a.done_ts() < idle_after_s)
    old_failed = sum(1 for a in snapshot.agents if a.state.value == "failed" and stale_failed(a, now, idle_after_s))
    n = len(states)
    main = _seg(n, "agent") if total is None else f"{n} of {total} agents"
    return _join("Inbox", main, [
        (states.count("running"), "running"),
        (fresh, "finished"),
        (sum(1 for s in states if s in ("idle", "unknown")) - fresh + old_failed, "idle"),
        (states.count("waiting") + states.count("failed") - old_failed, "waiting"),
        (ntfy_total(ntfy_counts), "ntfy"),
    ])


def stats_projects(snapshot, ntfy_counts: Mapping[str, int] | None = None) -> str:
    ps = list(snapshot.projects)
    return _join("Projects", str(len(ps)), [
        (sum(1 for p in ps if p.git and p.git.dirty), "dirty"),
        (sum(1 for p in ps if p.git and p.git.behind), "behind"),
        (sum(1 for p in ps if p.agent_ids), "with agents"),
        (ntfy_total(ntfy_counts), "ntfy"),
    ])


def stats_tmux(snapshot) -> str:
    ss = list(snapshot.sessions)
    wins = [w for s in ss for w in s.windows]
    return _join("tmux", _seg(len(ss), "session"), [
        (len(wins), "windows" if len(wins) != 1 else "window"),
        (sum(len(w.panes) for w in wins), "panes"),
        (sum(s.attached for s in ss), "clients"),
        (len(snapshot.agents), "agents"),
    ])


def stats_ntfy(total: int, unread: int = 0, today: int = 0) -> str:
    return _join("ntfy", str(total), [(unread, "unread"), (today, "today")])
