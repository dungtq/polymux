"""Which agent (and tmux pane) sent an ntfy message.

An agent sends its finish ntfy from a Bash tool call, so the `--title '...'` of that
call sits in its own session file with a timestamp. Match message title + time to
those calls (the agent's own and its sub-agents'); no change to how agents publish. Only the title is read from the
file; it is already shown in the feed. Scans are cached by (path, mtime, size).
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pm.agents import registry
from pm.core.config import Settings
from pm.core.models import NtfyItem
from pm.notify import feed as ntfy_feed

MAX_BEFORE_S = 1800.0  # the tool call is logged before the (chained, slow) publish lands
MAX_AFTER_S = 30.0  # clock slack
MAX_FILE = 64 * 1024 * 1024
_TITLE_RE = re.compile(r"""--title[ =]+(?:'([^']*)'|"([^"]*)"|(\S+))""")

_lock = threading.Lock()
_cache: dict[str, tuple[float, int, list[tuple[float, str]]]] = {}


@dataclass(frozen=True)
class Owner:
    agent_id: str
    label: str
    pane: str  # last known tmux pane (%n), "" = unknown
    live: bool
    transcript: str = ""  # session file: the model pill of the agent name

    @property
    def kind(self) -> str:
        """Agent kind from the label suffix (`dev-pm-3-claude` -> `claude`); `other` when unknown."""
        for k in ("cursor-agent", "antigravity", "claude", "codex", "devin"):
            stem = self.label.rstrip("0123456789").rstrip("-")
            if stem.endswith("-" + k) or stem == k:
                return k
        return "other"

    @property
    def short(self) -> str:
        """`dev-pm-3` for `dev-pm-3-claude` (the kind is shown elsewhere)."""
        head, _, tail = self.label.rpartition("-")
        return head if head and not tail.isdigit() else self.label


def _ts(raw) -> float | None:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def scan(path: str) -> list[tuple[float, str]]:
    """(epoch, title) for every `ntfy publish --title ...` call in one session file."""
    try:
        st = Path(path).stat()
    except OSError:
        return []
    if st.st_size > MAX_FILE:
        return []
    with _lock:
        hit = _cache.get(path)
        if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
            return hit[2]
    out: list[tuple[float, str]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "ntfy" not in line or "--title" not in line or '"tool_use"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                ts = _ts(d.get("timestamp"))
                content = (d.get("message") or {}).get("content")
                if ts is None or not isinstance(content, list):
                    continue
                for b in content:
                    cmd = (b.get("input") or {}).get("command") if isinstance(b, dict) else None
                    if isinstance(cmd, str) and "ntfy" in cmd:
                        m = _TITLE_RE.search(cmd)
                        if m:
                            out.append((ts, next(g for g in m.groups() if g is not None)))
    except OSError:
        return []
    with _lock:
        _cache[path] = (st.st_mtime, st.st_size, out)
    return out


def build_index(settings: Settings, since: float) -> list[tuple[float, str, Owner]]:
    """Every ntfy call by a known agent since `since`, as (epoch, title, owner)."""
    idx = []
    for rec in registry.with_transcripts(settings, since):
        owner = Owner(rec.id, rec.label or rec.id, rec.last_pane, not rec.ended, rec.transcript)
        t = Path(rec.transcript)
        files = [t]
        if t.suffix == ".jsonl":  # sub-agents publish too; their calls count for the parent
            files += sorted(t.with_suffix("").glob("subagents/*.jsonl"))
            files += sorted(t.with_suffix("").glob("subagents/workflows/wf_*/agent-*.jsonl"))
        for f in files:
            for ts, title in scan(str(f)):
                if ts >= since - MAX_BEFORE_S:
                    idx.append((ts, title, owner))
    return idx


def find(index: list[tuple[float, str, Owner]], title: str, when: float) -> Owner | None:
    """Owner of the same-titled call that precedes the message most closely."""
    best, gap = None, None
    for ts, t, owner in index:
        d = when - ts
        if t == title and -MAX_AFTER_S <= d <= MAX_BEFORE_S and (gap is None or abs(d) < gap):
            best, gap = owner, abs(d)
    return best


_ID_TITLE_RE = re.compile(r"^([a-z][a-z0-9-]*-[0-9a-f]{6}) - ")


def title_owner(settings: Settings, title: str) -> Owner | None:
    """Owner by the agent id the title starts with (`claude-73e574 - ...`) when the registry knows it; no index."""
    m = _ID_TITLE_RE.match(title)
    if m:
        rec = registry.get(settings, m.group(1))
        if rec is not None:
            return Owner(rec.id, rec.label or rec.id, rec.last_pane, not rec.ended, rec.transcript)
    return None


def owner_for(settings: Settings, index: list[tuple[float, str, Owner]], title: str, when: float) -> Owner | None:
    """Owner of a message: the agent id the title starts with (`claude-73e574 - ...`), else the transcript match."""
    return title_owner(settings, title) or find(index, title, when)


@dataclass(frozen=True)
class AgentFeed:
    item: NtfyItem  # the newest message the agent published
    unread: int = 0  # its messages the ``unread`` predicate of ``agent_feed`` counts


def agent_feed(settings: Settings, ids, project_path: str | None = None, unread=None) -> dict[str, AgentFeed]:
    """{agent id: AgentFeed} for the wanted ids in one owner-mapping pass over the feed; ``unread(item) -> bool``
    (optional, e.g. ``inbox.item_unread`` with a loaded store) counts the agent's unread messages. {} when the
    feed or transcripts are unavailable."""
    items = ntfy_feed.recent(settings, project_path=project_path)
    ids = set(ids)
    if not items or not ids:
        return {}
    index = build_index(settings, min(i.time for i in items))
    newest: dict[str, NtfyItem] = {}
    counts: dict[str, int] = {}
    for it in items:  # newest first
        owner = owner_for(settings, index, it.title, it.time)
        if owner is None or owner.agent_id not in ids:
            continue
        newest.setdefault(owner.agent_id, it)
        if unread is not None and unread(it):
            counts[owner.agent_id] = counts.get(owner.agent_id, 0) + 1
    return {k: AgentFeed(v, counts.get(k, 0)) for k, v in newest.items()}


def agent_ntfy(settings: Settings, ids, project_path: str | None = None) -> dict:
    """{agent id: newest NtfyItem it published}; {} when the feed or transcripts are unavailable."""
    return {k: f.item for k, f in agent_feed(settings, ids, project_path).items()}
