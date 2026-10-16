"""Token and turn totals of a Claude session's main chat (sub-agent / sidechain entries excluded).

Reads the one session file incrementally: per file only the bytes appended since the last call are
parsed, so a poll costs almost nothing. Only counters are kept; no message text is stored or logged.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

from pm.chat.sources.claude import entry_text as _entry_text
from pm.chat.sources.claude import live_session_id, transcript_path
from pm.chat.sources.common import Stats

_MAX_FILES = 64
_CHUNK = 1 << 20
_LOCK = threading.Lock()


@dataclass
class _State:
    offset: int = 0
    stats: Stats | None = None
    msgs: dict | None = None  # message id -> usage tuple (a reply is split over several jsonl lines)


_CACHE: dict[str, _State] = {}


def _usage(entry: dict) -> tuple[str, tuple[int, int, int, int]] | None:
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return None
    u = msg.get("usage")
    if not isinstance(u, dict):
        return None

    def n(k: str) -> int:
        v = u.get(k)
        return v if isinstance(v, int) and v > 0 else 0

    key = str(msg.get("id") or entry.get("requestId") or entry.get("uuid") or "")
    return key, (n("input_tokens"), n("output_tokens"), n("cache_creation_input_tokens"), n("cache_read_input_tokens"))


def session_stats(path: Path) -> Stats | None:
    """Totals for ``path`` (None when unreadable)."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    key = str(path)
    with _LOCK:
        st = _CACHE.get(key)
        if st is None or size < st.offset:  # new or rewritten file
            st = _State(0, Stats(), {})
            _CACHE[key] = st
            while len(_CACHE) > _MAX_FILES:
                _CACHE.pop(next(iter(_CACHE)))
        try:
            with open(path, "rb") as f:
                f.seek(st.offset)
                buf = b""
                while True:
                    chunk = f.read(_CHUNK)
                    if not chunk:
                        break
                    buf += chunk
                    cut = buf.rfind(b"\n")
                    if cut < 0:
                        continue
                    _consume(st, buf[:cut])
                    st.offset += cut + 1
                    buf = buf[cut + 1:]
        except OSError:
            return None
        s = st.stats
        assert s is not None
        return Stats(s.turns, s.input, s.output, s.cache_write, s.cache_read, s.ctx, s.model)  # claude defaults for the rest


def _consume(st: _State, block: bytes) -> None:
    s, msgs = st.stats, st.msgs
    assert s is not None and msgs is not None
    for raw in block.split(b"\n"):
        if not raw.strip():
            continue
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("isSidechain"):
            continue
        kind = e.get("type")
        if kind == "user":
            if _entry_text(e)[1]:
                s.turns += 1
        elif kind == "assistant":
            got = _usage(e)
            if got is None:
                continue
            mid, u = got
            old = msgs.get(mid) if mid else None
            if old is not None:  # later lines of the same reply carry the final output count
                s.input += u[0] - old[0]
                s.output += u[1] - old[1]
                s.cache_write += u[2] - old[2]
                s.cache_read += u[3] - old[3]
            else:
                s.input += u[0]; s.output += u[1]; s.cache_write += u[2]; s.cache_read += u[3]
            if mid:
                msgs[mid] = u
            if u[0] + u[2] + u[3]:
                s.ctx = u[0] + u[2] + u[3]
            m = (e.get("message") or {}).get("model")
            if isinstance(m, str) and not m.startswith("<"):
                s.model = m[:80]


def human(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def stats_for(agent, settings=None, locks: dict[int, list[str]] | None = None, config_dir: Path | None = None):
    """Token statistics for the agent's main chat (claude, devin, cursor-agent, antigravity), or None. ``locks``:
    the Devin lock map when the caller already read it (``devin_stats.session_stats``). ``config_dir``: the agent's
    named-account folder (Claude)."""
    kind = agent.kind.value
    if kind == "claude":
        uuid = (live_session_id(agent.pid, **({"config_dir": config_dir} if config_dir else {})) if agent.pid > 1 else None) or agent.session_uuid or ""
        path = transcript_path(agent.cwd, uuid, **({"config_dir": config_dir} if config_dir else {}))
        return session_stats(path) if path is not None else None
    if kind == "devin":
        from pm.chat.sources.devin_stats import session_stats as devin

        return devin(agent, locks=locks)
    if kind == "cursor-agent":
        from pm.chat.sources.cursor_stats import session_stats as cursor
        from pm.tmux import ctl as tmuxctl

        if settings is None or not agent.pane_id:
            return None
        try:
            pane = tmuxctl.capture_pane(settings, agent.pane_id, 60)
        except Exception:  # noqa: BLE001
            return None
        return cursor(agent, pane)
    if kind == "antigravity":
        try:
            from pm.chat.sources.antigravity import session_stats as agy
        except ImportError:
            return None
        return agy(agent)
    return None


def ctx_pct(agent, locks: dict[int, list[str]] | None = None, config_dir: Path | None = None) -> int | None:
    """Context fill of the agent's main chat in percent (``Stats.pct``); None when unknown. Cursor-agent is skipped:
    its numbers come from a pane read (``stats_for``), too costly for a list row. ``locks``: see ``stats_for`` (a
    list of rows reads the Devin lock map once, not per row)."""
    if agent.kind.value == "cursor-agent":
        return None
    try:
        st = stats_for(agent, locks=locks, **({"config_dir": config_dir} if config_dir else {}))
    except Exception:  # noqa: BLE001 - a row works without it
        return None
    if st is None or (not st.ctx and st.pct_hint < 0):
        return None
    return st.pct
