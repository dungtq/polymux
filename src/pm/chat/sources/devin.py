"""Devin CLI: ``~/.local/share/devin/cli/transcripts/<name>.json`` (ATIF, pretty JSON).

The live process is found through ``session_locks/<name>.lock``: each lock holds
one pid (the ``devin acp`` child of the pane's ``devin``). A session belongs to
an agent when the lock pid equals the agent pid or descends from it. Only the
8-byte lock files and the one matching transcript's last 256 KB are read.

``read_entries`` (pm.chat.log): the file is rewritten as a whole, so the last 4 MB are
re-scanned and only steps with ``step_id`` above the cursor are returned: user steps,
agent steps (message, ``reasoning_content`` as thinking, ``tool_calls`` name + one target line).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .claude import SYSTEMISH
from .common import (
    FIRST_BYTES,
    Chunk,
    Latest,
    RawEntry,
    parse_ts,
    read_tail_bytes,
    text_step,
    tool_name,
    tool_step,
)

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_STEP_RE = re.compile(r'\{\s*"step_id"\s*:')
_MAX_HOPS = 8
_RECENT = 6


def _root(home: Path) -> Path:
    return home / ".local" / "share" / "devin" / "cli"


def _ppid(pid: int, proc_root: Path) -> int:
    """Parent pid; -1 when the process does not exist (stale lock), 0 when unparsable."""
    try:
        fd = os.open(f"{proc_root}/{pid}/stat", os.O_RDONLY)
    except OSError:
        return -1
    try:
        raw = os.read(fd, 4096).decode("utf-8", "replace")
        return int(raw[raw.rindex(")") + 2:].split()[1])
    except (OSError, ValueError, IndexError):
        return 0
    finally:
        os.close(fd)


def lock_map(home: Path, proc_root: Path) -> dict[int, list[str]]:
    """ancestor pid -> session names, for every live lock pid (and up to 8 ancestors)."""
    out: dict[int, list[str]] = {}
    locks = _root(home) / "session_locks"
    try:
        names = os.listdir(locks)
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".lock"):
            continue
        name = fn[:-5]
        if not _NAME_RE.match(name):
            continue
        try:
            with open(locks / fn, "rb") as f:
                pid = int(f.read(32).strip() or 0)
        except (OSError, ValueError):
            continue
        hops = 0
        while pid > 1 and hops <= _MAX_HOPS:
            parent = _ppid(pid, proc_root)
            if parent < 0:
                break  # dead lock pid (stale file)
            out.setdefault(pid, []).append(name)
            pid = parent
            hops += 1
    return out


def locate(pid: int, home: Path, proc_root: Path, locks: dict[int, list[str]] | None = None) -> Path | None:
    locks = lock_map(home, proc_root) if locks is None else locks
    best: tuple[float, Path] | None = None
    for name in locks.get(pid, []):
        p = _root(home) / "transcripts" / f"{name}.json"
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if best is None or m > best[0]:
            best = (m, p)
    return best[1] if best else None


def read_latest(path: Path) -> Latest | None:
    data, _ = read_tail_bytes(path)
    text = data.decode("utf-8", "replace")
    dec = json.JSONDecoder()
    found: list[tuple[float, str]] = []
    for m in reversed(list(_STEP_RE.finditer(text))):
        try:
            step, _ = dec.raw_decode(text, m.start())
        except ValueError:
            continue
        if not isinstance(step, dict) or step.get("source") not in ("agent", "assistant"):
            continue
        msg = step.get("message")
        if isinstance(msg, str) and msg.strip():
            found.append((parse_ts(step.get("timestamp")), msg.strip()))
            if len(found) >= _RECENT:
                break
    if not found:
        return None
    ts, msg = found[0]
    return Latest("assistant", msg, ts, tuple(t for _, t in found))


def read_entries(path: Path, pos: int = -1) -> Chunk:
    data, _ = read_tail_bytes(path, FIRST_BYTES)
    text = data.decode("utf-8", "replace")
    dec = json.JSONDecoder()
    out: list[tuple[int, RawEntry]] = []
    top = -1
    for m in _STEP_RE.finditer(text):
        try:
            step, _ = dec.raw_decode(text, m.start())
        except ValueError:
            continue
        sid = step.get("step_id") if isinstance(step, dict) else None
        if not isinstance(sid, int) or isinstance(sid, bool):
            continue
        top = max(top, sid)
        if sid <= pos:
            continue
        src = step.get("source")
        ts = parse_ts(step.get("timestamp"))
        msg = step.get("message")
        msg = msg.strip() if isinstance(msg, str) else ""
        if src == "user":
            if msg and not msg.startswith(SYSTEMISH):
                out.append((sid, RawEntry("user", f"s{sid}", ts, msg)))
        elif src in ("agent", "assistant"):
            think = step.get("reasoning_content")
            think = think.strip() if isinstance(think, str) else ""
            calls = step.get("tool_calls")
            calls = [c for c in calls if isinstance(c, dict)] if isinstance(calls, list) else []
            tools = tuple(tool_name(c.get("function_name")) for c in calls)
            steps = [st for st in (text_step(think, "thinking"), text_step(msg)) if st]
            steps += [tool_step(c.get("function_name"), c.get("arguments")) for c in calls]
            if msg or think or tools:
                out.append((sid, RawEntry("assistant", f"s{sid}", ts, msg, think, tools, tuple(steps))))
    if top < pos:  # mid-rewrite or a shorter file: keep the cursor, never re-ingest
        return Chunk([], pos)
    out.sort(key=lambda t: t[0])
    return Chunk([e for _, e in out], max(pos, top))
