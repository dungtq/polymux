"""Past CLI sessions on disk for a set of working directories ("Import past sessions", #156).

Pure discovery (stat, dir-list and a bounded head read; no subprocess, no pane text). Per kind:

* Claude: top-level ``~/.claude/projects/<encode_cwd(cwd)>/<uuid>.jsonl`` only (sub-agent dirs are
  skipped). The encoding is lossy, so a session whose head names another ``cwd`` is dropped;
  headless runs (``claude -p`` / SDK, ``entrypoint`` "sdk-*", e.g. pm's quick chat) are skipped.
* Codex: ``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`` of the days in the window; the first
  line's ``cwd`` (``codex.meta_payload``) must equal the directory; ``codex exec`` and sub-agent
  rollouts are skipped. Ident = file stem.
* Cursor: ``~/.cursor/chats/<md5(cwd)>/<chat uuid>/store.db``; ident = the chat dir name.

Devin and Antigravity stores carry no cwd and are not discovered. Each kind keeps its newest
``cap`` files in ``[since, until]`` (mtime); callers pass ``until`` a little before now so a
session still being written is left to the live detection.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import codex, cursor
from .claude import encode_cwd
from .common import UUID_RE, mtime

MAX_FILES = 2000  # per kind and scan
MAX_DAYS = 400  # codex day dirs walked at most
_HEAD = 64 * 1024


@dataclass(frozen=True)
class PastSession:
    kind: str  # AgentKind value: claude, codex, cursor-agent
    ident: str
    path: Path
    cwd: str
    mtime: float


def claude_head(path: Path) -> tuple[str, bool]:
    """(first ``cwd``, interactive) from the first 64 KB of a Claude transcript. Headless runs
    (``claude -p``, SDK: ``entrypoint`` "sdk-*") are not interactive; no entrypoint counts as one."""
    try:
        with open(path, "rb") as f:
            head = f.read(_HEAD)
    except OSError:
        return "", True
    cwd, entry = "", ""
    for line in head.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        if not cwd and isinstance(e.get("cwd"), str):
            cwd = e["cwd"]
        if not entry and isinstance(e.get("entrypoint"), str):
            entry = e["entrypoint"]
        if cwd and entry:
            break
    return cwd, not entry.startswith("sdk")


def codex_head(path: Path) -> tuple[str, bool]:
    """(cwd, interactive) from a rollout's session_meta: ``codex exec`` runs (source "exec",
    originator "codex_exec") and sub-agent rollouts (a non-string source) are not interactive."""
    p = codex.meta_payload(path)
    cwd = p.get("cwd") if isinstance(p.get("cwd"), str) else ""
    src = p.get("source")
    batch = src == "exec" or p.get("originator") == "codex_exec" or (src is not None and not isinstance(src, str))
    return cwd, not batch


def head_of(kind: str, path: Path) -> tuple[str, bool]:
    """(cwd recorded in a store file's head, interactive session) for claude and codex."""
    if kind == "claude":
        return claude_head(path)
    if kind == "codex":
        return codex_head(path)
    return "", True


def _newest(items: list[PastSession], cap: int) -> list[PastSession]:
    items.sort(key=lambda s: s.mtime, reverse=True)
    return items[:cap]


def _claude(cwd: str, home: Path, since: float, until: float, cap: int,
            head: Callable[[str, Path], tuple[str, bool]]) -> list[PastSession]:
    d = home / ".claude" / "projects" / encode_cwd(cwd)
    out: list[PastSession] = []
    try:
        with os.scandir(d) as it:
            for e in it:
                stem = e.name[:-6] if e.name.endswith(".jsonl") else ""
                if not UUID_RE.match(stem) or not e.is_file(follow_symlinks=False):
                    continue
                st = e.stat(follow_symlinks=False)
                if st.st_size > 0 and since <= st.st_mtime <= until:
                    out.append(PastSession("claude", stem, Path(e.path), cwd, st.st_mtime))
    except OSError:
        return []
    keep = []
    for s in _newest(out, cap):
        c, interactive = head("claude", s.path)
        if interactive and c in ("", cwd):
            keep.append(s)
    return keep


def _codex_files(home: Path, since: float, until: float, cap: int) -> list[tuple[float, Path]]:
    base = home / ".codex" / "sessions"
    day = datetime.fromtimestamp(since).date() - timedelta(days=1)
    last = datetime.fromtimestamp(until).date() + timedelta(days=1)
    day = max(day, last - timedelta(days=MAX_DAYS))
    out: list[tuple[float, Path]] = []
    while day <= last:
        d = base / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
        day += timedelta(days=1)
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for fn in names:
            if fn.startswith("rollout-") and fn.endswith(".jsonl"):
                m = mtime(d / fn)
                if m and since <= m <= until:
                    out.append((m, d / fn))
    out.sort(key=lambda t: t[0], reverse=True)
    return out[:cap]


def _cursor(cwd: str, home: Path, since: float, until: float, cap: int) -> list[PastSession]:
    base = cursor.chat_dir(cwd, home)
    try:
        names = os.listdir(base)
    except OSError:
        return []
    out: list[PastSession] = []
    for n in names:
        if not UUID_RE.match(n):
            continue
        db = base / n / "store.db"
        m = mtime(db)
        if m and since <= m <= until:
            out.append(PastSession("cursor-agent", n, db, cwd, m))
    return _newest(out, cap)


def discover_many(cwds: Iterable[str], home: Path, *, since: float, until: float, cap: int = MAX_FILES,
                  head: Callable[[str, Path], tuple[str, bool]] = head_of) -> dict[str, list[PastSession]]:
    """{cwd: past sessions of every supported kind, newest first} for the given directories.

    ``head(kind, path)`` returns (cwd recorded in a store file, interactive); inject a cached one."""
    want = [c for c in dict.fromkeys(cwds) if c]
    found: dict[str, list[PastSession]] = {c: [] for c in want}
    if not want:
        return found
    for c in want:
        found[c] += _claude(c, home, since, until, cap, head)
        found[c] += _cursor(c, home, since, until, cap)
    for m, p in _codex_files(home, since, until, cap):
        c, interactive = head("codex", p)
        if interactive and c in found:
            found[c].append(PastSession("codex", p.stem, p, c, m))
    for c in want:
        found[c].sort(key=lambda s: s.mtime, reverse=True)
    return found


def discover(cwd: str, home: Path, *, since: float, until: float, cap: int = MAX_FILES,
             head: Callable[[str, Path], tuple[str, bool]] = head_of) -> list[PastSession]:
    """Past sessions of one working directory, newest first."""
    return discover_many([cwd], home, since=since, until=until, cap=cap, head=head).get(cwd, [])
