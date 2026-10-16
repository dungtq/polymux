"""Usage statistics from Claude Code transcripts (counters only, no message text is kept or logged).

* ``session_usage(path)``: one session file (the chat panel): tokens, replies, tool calls, wall time, model.
* ``aggregate(home)``: every ``~/.claude/projects/*/*.jsonl``: totals per model / day / session, cached
  30 s per home (same idea as the agentsinv cache); each file is read incrementally (only the bytes
  appended since the last call are parsed), so a refresh costs almost nothing.

Sub-agent (sidechain) entries are counted too here: they are real spend. The chat panel excludes
them to match the token panel (``chat_stats``).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pm.core import timefmt

TTL = 30.0
_CHUNK = 1 << 20
_MAX_FILES = 4000
_LOCK = threading.Lock()


@dataclass
class Counts:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    replies: int = 0
    tools: int = 0

    def add(self, o: Counts) -> None:
        self.input += o.input
        self.output += o.output
        self.cache_write += o.cache_write
        self.cache_read += o.cache_read
        self.replies += o.replies
        self.tools += o.tools

    @property
    def total(self) -> int:
        """New tokens (cache reads are reported apart, as in the token panel)."""
        return self.input + self.output + self.cache_write

    @property
    def cache_hit(self) -> int:
        """Percent of input-side tokens that came from the cache."""
        side = self.input + self.cache_write + self.cache_read
        return round(100 * self.cache_read / side) if side else 0


@dataclass
class _File:
    offset: int = 0
    cwd: str = ""
    first: float = 0.0
    last: float = 0.0
    turns: int = 0
    model: str = ""
    by: dict = field(default_factory=dict)  # (day, model) -> Counts
    last_id: str = ""
    last_u: tuple = (0, 0, 0, 0)
    last_key: tuple = ("", "")
    size: int = 0


_FILES: dict[str, _File] = {}


def _ts(v) -> float:
    if not isinstance(v, str):
        return 0.0
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(UTC).timestamp()
    except ValueError:
        return 0.0


def _day(ts: float) -> str:
    return timefmt.fmt_date(ts) if ts else ""


def _num(u: dict, k: str) -> int:
    v = u.get(k)
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def _consume(st: _File, block: bytes, sidechain: bool) -> None:
    for raw in block.split(b"\n"):
        if b'"assistant"' not in raw:  # user and tool-result lines carry no usage
            continue
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        if not sidechain and e.get("isSidechain"):
            continue
        ts = _ts(e.get("timestamp"))
        if ts:
            st.first = st.first or ts
            st.last = max(st.last, ts)
        cwd = e.get("cwd")
        if isinstance(cwd, str) and not st.cwd:
            st.cwd = cwd[:200]
        if e.get("type") != "assistant":
            continue
        msg = e.get("message")
        if not isinstance(msg, dict):
            continue
        model = msg.get("model")
        model = model[:80] if isinstance(model, str) and model and not model.startswith("<") else ""
        content = msg.get("content")
        tools = sum(1 for b in content if isinstance(b, dict) and b.get("type") == "tool_use") if isinstance(content, list) else 0
        u = msg.get("usage")
        if not isinstance(u, dict):
            continue
        cur = (_num(u, "input_tokens"), _num(u, "output_tokens"), _num(u, "cache_creation_input_tokens"), _num(u, "cache_read_input_tokens"))
        mid = str(msg.get("id") or e.get("requestId") or e.get("uuid") or "")
        key = (_day(ts or st.last), model or st.model)
        same = bool(mid) and mid == st.last_id
        prev = st.last_u if same else (0, 0, 0, 0)
        c = st.by.setdefault(key if not same else st.last_key, Counts())
        c.input += cur[0] - prev[0]
        c.output += cur[1] - prev[1]
        c.cache_write += cur[2] - prev[2]
        c.cache_read += cur[3] - prev[3]
        c.tools += tools
        if not same:
            c.replies += 1
        st.last_id, st.last_u, st.last_key = mid, cur, (key if not same else st.last_key)
        if model:
            st.model = model


def _scan(path: Path, sidechain: bool) -> _File | None:
    """Bring the cached summary of ``path`` up to date (None when unreadable)."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    key = f"{int(sidechain)}:{path}"
    with _LOCK:
        st = _FILES.get(key)
        if st is None or size < st.offset:
            st = _File()
            _FILES[key] = st
            while len(_FILES) > _MAX_FILES:
                _FILES.pop(next(iter(_FILES)))
        if size == st.offset:
            return st
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
                    _consume(st, buf[:cut], sidechain)
                    st.offset += cut + 1
                    buf = buf[cut + 1:]
        except OSError:
            return None
        return st


def _user_turns(path: Path) -> int:
    from pm.chat.stats import session_stats

    s = session_stats(path)
    return s.turns if s else 0


@dataclass
class SessionUsage:
    counts: Counts
    turns: int
    duration: int  # seconds, first to last entry
    model: str


def session_usage(path: Path) -> SessionUsage | None:
    """Usage of one session's main chat (sub-agents excluded)."""
    st = _scan(path, False)
    if st is None:
        return None
    total = Counts()
    with _LOCK:
        for c in st.by.values():
            total.add(c)
        dur = int(st.last - st.first) if st.first and st.last > st.first else 0
        model = st.model
    return SessionUsage(total, _user_turns(path), dur, model)


@dataclass
class Aggregate:
    days: list  # [(day, Counts)] newest first
    models: list  # [(model, Counts)] by total desc
    top: list  # [(label, cwd, Counts, turns_seconds)] by total desc
    total: Counts
    sessions: int
    scanned_at: float = 0.0
    kinds: list = field(default_factory=list)  # [(kind, Counts)]
    top_ids: list = field(default_factory=list)  # session ids (transcript stems), parallel to ``top``


_AGG: dict[tuple[Path, int, int], tuple[float, Aggregate]] = {}


def clear_cache() -> None:
    with _LOCK:
        _AGG.clear()
        _FILES.clear()


def _label(cwd: str, stem: str) -> str:
    base = cwd.rstrip("/").rsplit("/", 1)[-1] if cwd else ""
    return f"{(base or 'session')[:32]} {stem[:6]}"


def aggregate(home: Path | None = None, days: int = 30, top: int = 10) -> Aggregate:
    """Totals over the last ``days`` days (0 = all) for every Claude transcript under ``home``."""
    h = (home or Path.home()).resolve()
    key = (h, days, top)
    with _LOCK:
        hit = _AGG.get(key)
    if hit and time.monotonic() - hit[0] < TTL:
        return hit[1]
    cutoff = _day(time.time() - days * 86400) if days else ""
    by_day: dict[str, Counts] = {}
    by_model: dict[str, Counts] = {}
    rows = []
    grand = Counts()
    base = h / ".claude" / "projects"
    try:
        files = sorted(base.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        files = []
    if cutoff:  # files untouched since before the window cannot contribute
        limit = time.time() - (days + 2) * 86400
        files = [p for p in files if _mtime(p) >= limit]
    for p in files:
        st = _scan(p, True)
        if st is None:
            continue
        tot = Counts()
        with _LOCK:
            items = list(st.by.items())
            cwd, dur = st.cwd, int(st.last - st.first) if st.first else 0
        for (day, model), c in items:
            if cutoff and day < cutoff:
                continue
            by_day.setdefault(day or "unknown", Counts()).add(c)
            by_model.setdefault(model or "unknown", Counts()).add(c)
            tot.add(c)
        if tot.replies:
            grand.add(tot)
            rows.append(((_label(cwd, p.stem), cwd, tot, dur), p.stem))
    rows.sort(key=lambda r: r[0][2].total, reverse=True)
    agg = Aggregate(
        days=sorted(by_day.items(), key=lambda kv: kv[0], reverse=True),
        models=sorted(by_model.items(), key=lambda kv: kv[1].total, reverse=True),
        top=[r for r, _ in rows[:top]], total=grand, sessions=len(rows), scanned_at=time.time(),
        kinds=[("claude", grand)] if grand.replies else [], top_ids=[sid for _, sid in rows[:top]],
    )
    with _LOCK:
        _AGG[key] = (time.monotonic(), agg)
    return agg


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def fmt_duration(sec: int) -> str:
    if sec <= 0:
        return "–"
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m" if m else f"{sec}s"


def panel_data(agent, settings=None) -> dict | None:
    """Numbers for the chat usage panel (blocking). None when the agent has no readable store."""
    kind = agent.kind.value
    if kind == "claude":
        from pm.chat.sources.claude import live_session_id, transcript_path

        uuid = (live_session_id(agent.pid) if agent.pid > 1 else None) or agent.session_uuid or ""
        path = transcript_path(agent.cwd, uuid)
        su = session_usage(path) if path is not None else None
        if su is None:
            return None
        c = su.counts
        return {"model": su.model, "turns": su.turns, "replies": c.replies, "tools": c.tools, "duration": fmt_duration(su.duration),
                "input": c.input, "output": c.output, "cache_write": c.cache_write, "cache_read": c.cache_read,
                "total": c.total, "hit": c.cache_hit, "exact": True}
    from pm.chat.stats import stats_for

    st = stats_for(agent, settings) if kind != "cursor-agent" else None  # cursor scrapes the pane: not here
    if st is None or not st.exact:
        return None
    return {"model": st.model, "turns": st.turns, "replies": None, "tools": None, "duration": "–",
            "input": st.input, "output": st.output, "cache_write": st.cache_write, "cache_read": st.cache_read,
            "total": st.total, "hit": None, "exact": True}
