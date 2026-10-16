"""Per-chat recap: first prompt, last reply, tool calls, duration and files touched.

Derived on demand from the agent's own session store (Claude jsonl in full, bounded; other kinds
from their latest-message reader). Blocking; callers use ``executors``. Never raises. Text and file
names pass through ``render_pane`` (strip ANSI, then mask) and are never logged or sent to ntfy.
Results are cached per (path, mtime, size), so a poll of an unchanged chat costs one stat.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from pm.chat import sources as chat_sources
from pm.chat.live import _task_line, transcript_path
from pm.chat.sources.claude import entry_text as _entry_text
from pm.chat.sources.common import mtime, parse_ts
from pm.core import timefmt
from pm.core.models import Agent
from pm.security.mask import render_pane

log = logging.getLogger("pm.recap")

MAX_BYTES = 16 * 1024 * 1024  # a longer session is recapped from its first 16 MB
MAX_FILES = 12
PROMPT_CHARS = 280
REPLY_CHARS = 420
RAW_CHARS = 1500
_AWAY_TAIL = re.compile(r"\s*\(disable recaps in /config\)\s*$")
_FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_CACHE: dict[str, tuple[tuple[float, int], Recap]] = {}
_MAX_CACHE = 64
_LOCK = threading.Lock()


@dataclass(frozen=True)
class Recap:
    prompt: str = ""
    reply: str = ""
    tools: int = 0
    started: float = 0.0
    last: float = 0.0
    files: tuple[str, ...] = ()
    more_files: int = 0
    exact: bool = True  # False: only the first/last message are known (non-Claude stores)
    reply_raw: str = ""  # newline-preserving (masked) reply, for Markdown rendering in the row panel
    away: str = ""  # Claude's own "while you were away" recap (latest away_summary entry); other kinds have none

    @property
    def duration(self) -> float:
        return max(0.0, self.last - self.started) if self.started and self.last else 0.0

    @property
    def empty(self) -> bool:
        return not (self.prompt or self.reply or self.away)


def _clean(text: str, limit: int) -> str:
    t = " ".join(render_pane(text).split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def _raw(text: str, limit: int) -> str:
    """Masked, ANSI-stripped text with its line breaks kept (rendered as Markdown by the caller)."""
    t = render_pane(text).strip()
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def _short(path: str, cwd: str) -> str:
    if cwd and path.startswith(cwd.rstrip("/") + "/"):
        return path[len(cwd.rstrip("/")) + 1:]
    return path


def parse_claude(path: Path, cwd: str = "") -> Recap:
    prompt = reply = away = ""
    tools = 0
    first = last = 0.0
    files: dict[str, None] = {}
    read = 0
    with open(path, "rb") as f:
        for raw in f:
            read += len(raw)
            if read > MAX_BYTES:
                break
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(e, dict) or e.get("isSidechain"):
                continue
            ts = parse_ts(e.get("timestamp"))
            if ts:
                first = first or ts
                last = max(last, ts)
            if e.get("type") == "system" and e.get("subtype") == "away_summary" and isinstance(e.get("content"), str):
                away = e["content"]
                continue
            role, text = _entry_text(e)
            if role == "user" and not prompt:
                prompt = _task_line(text) or text
            elif role == "assistant":
                reply = text
            msg = e.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if e.get("type") == "assistant" and isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        tools += 1
                        inp = b.get("input")
                        fp = inp.get("file_path") or inp.get("notebook_path") if isinstance(inp, dict) else None
                        if b.get("name") in _FILE_TOOLS and isinstance(fp, str) and fp:
                            files[fp] = None
    names = [_clean(_short(p, cwd), 160) for p in files]
    return Recap(_clean(prompt, PROMPT_CHARS), _clean(reply, REPLY_CHARS), tools, first, last,
                 tuple(names[:MAX_FILES]), max(0, len(names) - MAX_FILES),
                 reply_raw=_raw(reply, RAW_CHARS), away=_raw(_AWAY_TAIL.sub("", away), RAW_CHARS))


def _other(agent: Agent, config_dir: Path | None = None) -> tuple[Recap, float]:
    if agent.pid <= 1 and agent.transcript:
        p = Path(agent.transcript)
        path = p if p.is_file() else None
    else:
        path = chat_sources.session_file(agent.kind, pid=agent.pid, cwd=agent.cwd, age_s=agent.age_s,
                                        **({"config_dir": config_dir} if config_dir else {}))
    if path is None:
        return Recap(), 0.0
    latest = chat_sources.read_latest(agent.kind, path)
    if latest is None:
        return Recap(), mtime(path)
    return Recap(_clean(_task_line(latest.task) if latest.task else "", PROMPT_CHARS),
                 _clean(latest.text, REPLY_CHARS), 0, latest.task_ts, latest.ts, exact=False,
                 reply_raw=_raw(latest.text, RAW_CHARS)), mtime(path)


def recap_for(agent: Agent, config_dir: Path | None = None) -> Recap:
    """Recap of the agent's current chat; an empty Recap when nothing can be read. ``config_dir``: the agent's
    named-account folder (``registry.config_dir_of``)."""
    try:
        if agent.kind.value == "claude":
            from pm.chat.sources.claude import live_session_id

            uuid = (live_session_id(agent.pid, **({"config_dir": config_dir} if config_dir else {})) if agent.pid > 1 else None) or agent.session_uuid or ""
            path = transcript_path(agent.cwd, uuid, **({"config_dir": config_dir} if config_dir else {}))
            if path is None or not path.is_file():
                return Recap()
            st = path.stat()
            key, sig = str(path), (st.st_mtime, st.st_size)
            with _LOCK:
                hit = _CACHE.get(key)
            if hit and hit[0] == sig:
                return hit[1]
            rec = parse_claude(path, agent.cwd)
            with _LOCK:
                if len(_CACHE) >= _MAX_CACHE:
                    _CACHE.pop(next(iter(_CACHE)))
                _CACHE[key] = (sig, rec)
            return rec
        return _other(agent, config_dir)[0]
    except Exception as exc:  # noqa: BLE001 - never raise, never log content
        log.warning("recap failed: %s", type(exc).__name__)
        return Recap()


@dataclass(frozen=True)
class HistoryRow:
    id: str
    name: str
    kind: str
    ended: float
    started: float
    transcript: str = ""  # for the model pill of the agent name


def history_for(settings, agent: Agent, limit: int = 10) -> tuple[list[HistoryRow], int]:
    """Ended agents of the same project / cwd (registry rows), newest first; excludes this one."""
    from pm.agents import registry

    recs, total = registry.ended_history(settings, project=agent.project, cwd=agent.cwd,
                                         exclude_id=agent.id, limit=limit)
    rows = [HistoryRow(r.id, _clean(r.label or r.id, 80), r.kind, r.ended_at or r.last_seen, r.first_seen,
                        r.transcript)
            for r in recs]
    return rows, total


def human_duration(s: float) -> str:
    return timefmt.duration(s)
