"""Codex CLI: ``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``.

Codex keeps no fd open on the rollout from the TUI pid, so the file is found
by cwd: only rollouts modified since the process started are considered
(newest 12), and only each one's first line (session_meta, bounded to 64 KB)
is read to compare ``cwd``. The newest match wins. Two codex agents in the
same cwd therefore share a file (known limit).

``read_entries`` (pm.chat.log) reads the rollout incrementally by byte offset: user
``message`` items (minus injected environment/AGENTS.md context), assistant ``message``
items, ``reasoning`` summaries (thinking) and ``*_call`` items (tool name + one target line).
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from .claude import SYSTEMISH
from .common import (
    Chunk,
    Latest,
    RawEntry,
    parse_ts,
    read_new_lines,
    read_tail_lines,
    text_step,
    tool_name,
    tool_step,
)

_HEAD = 64 * 1024
_MAX_CANDIDATES = 12
_RECENT = 6
_CACHE: dict[tuple[str, int, str], tuple[float, Path]] = {}
_CACHE_TTL = 30.0


def _candidates(home: Path, started_at: float) -> list[tuple[float, Path]]:
    base = home / ".codex" / "sessions"
    since = started_at - 5 if started_at > 0 else time.time() - 86400
    day = datetime.fromtimestamp(since).date() - timedelta(days=1)
    today = datetime.now().date() + timedelta(days=1)
    out: list[tuple[float, Path]] = []
    n = 0
    while day <= today and n < 10:
        d = base / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
        try:
            names = os.listdir(d)
        except OSError:
            names = []
        for fn in names:
            if fn.startswith("rollout-") and fn.endswith(".jsonl"):
                try:
                    m = (d / fn).stat().st_mtime
                except OSError:
                    continue
                if m >= since:
                    out.append((m, d / fn))
        day += timedelta(days=1)
        n += 1
    out.sort(key=lambda t: t[0], reverse=True)
    return out[:_MAX_CANDIDATES]


def meta_payload(path: Path) -> dict:
    """``payload`` of a rollout's first line (session_meta, at most 64 KB read); {} when unknown."""
    try:
        with open(path, "rb") as f:
            line = f.readline(_HEAD)
        meta = json.loads(line)
    except (OSError, ValueError):
        return {}
    p = meta.get("payload") if isinstance(meta, dict) else None
    return p if isinstance(p, dict) else {}


def meta_cwd(path: Path) -> str:
    """``cwd`` of a rollout's session_meta; "" when unknown."""
    cwd = meta_payload(path).get("cwd")
    return cwd if isinstance(cwd, str) else ""


def locate(pid: int, cwd: str, started_at: float, home: Path) -> Path | None:
    key = (str(home), pid, cwd)
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL and hit[1].exists():
        return hit[1]
    found: Path | None = None
    if cwd:
        for _, p in _candidates(home, started_at):
            if meta_cwd(p) == cwd:
                found = p
                break
    if found is not None:
        if len(_CACHE) > 64:
            _CACHE.clear()
        _CACHE[key] = (time.monotonic(), found)
    else:
        _CACHE.pop(key, None)
    return found


def read_latest(path: Path) -> Latest | None:
    found: list[tuple[float, str]] = []
    for raw in reversed(read_tail_lines(path)):
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        p = e.get("payload") if isinstance(e, dict) else None
        if not (isinstance(p, dict) and e.get("type") == "response_item"
                and p.get("type") == "message" and p.get("role") == "assistant"):
            continue
        content = p.get("content")
        parts = [b.get("text") for b in content if isinstance(b, dict) and b.get("type") in ("output_text", "text")
                 and isinstance(b.get("text"), str)] if isinstance(content, list) else []
        text = "\n".join(t for t in parts if t.strip()).strip()
        if text:
            found.append((parse_ts(e.get("timestamp")), text))
            if len(found) >= _RECENT:
                break
    if not found:
        return None
    return Latest("assistant", found[0][1], found[0][0], tuple(t for _, t in found))


_CODEX_INJECTED = ("<environment_context", "<user_instructions", "# AGENTS.md instructions", "<permissions",
                   "<turn_aborted", "<user_shell_command", "<subagent_notification") + SYSTEMISH


def _texts(content: object, kinds: tuple[str, ...]) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = [b.get("text") for b in content if isinstance(b, dict) and b.get("type") in kinds
             and isinstance(b.get("text"), str)]
    return "\n".join(t for t in parts if t.strip()).strip()


def _part_steps(content: object, kinds: tuple[str, ...], kind: str) -> tuple[tuple[str, str, str], ...]:
    """One step per non-empty part (a string content is one part)."""
    parts = [content] if isinstance(content, str) else [
        b.get("text") for b in content if isinstance(b, dict) and b.get("type") in kinds] \
        if isinstance(content, list) else []
    return tuple(st for st in (text_step(t, kind) for t in parts) if st)


def read_entries(path: Path, pos: int = -1) -> Chunk:
    lines, new_pos, more = read_new_lines(path, pos)
    out: list[RawEntry] = []
    for off, raw in lines:
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("type") != "response_item":
            continue
        p = e.get("payload")
        if not isinstance(p, dict):
            continue
        typ = p.get("type")
        pid = p.get("id")
        uid = (pid if isinstance(pid, str) and pid else f"o{off}")[:80]
        ts = parse_ts(e.get("timestamp"))
        if typ == "message" and p.get("role") == "user":
            text = _texts(p.get("content"), ("input_text", "text"))
            if text and not text.startswith(_CODEX_INJECTED):
                out.append(RawEntry("user", uid, ts, text))
        elif typ == "message" and p.get("role") == "assistant":
            text = _texts(p.get("content"), ("output_text", "text"))
            if text:
                out.append(RawEntry("assistant", uid, ts, text, steps=_part_steps(
                    p.get("content"), ("output_text", "text"), "text")))
        elif typ == "reasoning":
            think = _texts(p.get("summary"), ("summary_text", "text")) or _texts(
                p.get("content"), ("reasoning_text", "text"))
            if think:
                steps = _part_steps(p.get("summary"), ("summary_text", "text"), "thinking") or _part_steps(
                    p.get("content"), ("reasoning_text", "text"), "thinking")
                out.append(RawEntry("assistant", uid, ts, "", think, steps=steps))
        elif isinstance(typ, str) and typ.endswith("_call"):
            name = p.get("name") or typ[:-5]
            args = p.get("arguments")
            if args is None:
                args = p.get("input")
            if args is None:
                args = p.get("action")
            out.append(RawEntry("assistant", uid, ts, tools=(tool_name(name),), steps=(tool_step(name, args),)))
    return Chunk(out, new_pos, more)
