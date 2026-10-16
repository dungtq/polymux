"""Unread ntfy counts for the left sidebar rows (per project, per tmux session).

Same rule as the Inbox badge (inbox.unread_count): a ntfy group counts when it is newer than its read mark,
inside the unread window and not dismissed. Projects match on the message's project path; tmux sessions on
the agent id in the group key (`<agent id>:<event>`) against the agents living in the session's panes.
"""

from __future__ import annotations

import time

from pm.agents import inbox
from pm.notify import feed as ntfy_feed


def _unread_groups(settings, now: float | None = None):
    now = time.time() if now is None else now
    store = inbox.load(settings.state_dir)
    try:
        items = ntfy_feed.recent(settings)
    except Exception:  # noqa: BLE001 - badges are decoration, the sidebar works without them
        return []
    return [g for g in inbox.group_ntfy(items)
            if inbox.ntfy_unread(store, g.key, g.ts, now) and not store.dismissed(g.key, g.ts)]


def by_project(settings, projects) -> dict[str, int]:
    """project name -> unread count (0 omitted)."""
    paths = {str(p.path).rstrip("/"): p.name for p in projects}
    out: dict[str, int] = {}
    for g in _unread_groups(settings):
        if inbox.is_system(g.item):
            continue
        name = paths.get((g.item.project or "").rstrip("/"))
        if name:
            out[name] = out.get(name, 0) + 1
    return out


def system_unread(settings) -> int:
    """Unread system messages (no agent): the bell and Servers badges."""
    return sum(1 for g in _unread_groups(settings) if inbox.is_system(g.item))
