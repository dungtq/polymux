"""Read side of the audit log for the /audit page: newest-first paging from the file end.

Never loads the whole file: it reads backwards in chunks and stops after ``SCAN_CAP`` lines.
Display-only collapse hides an ``attempt`` row when the next entry (chronologically) of the
same client+action is its outcome; nothing is changed on disk.

Result classes: ``ok``, ``attempt``, ``refused`` (result ``denied``), ``failed`` (``error`` and any other value);
``denied`` is the classic union of refused and failed. ``select`` is the one filter pass, shared by ``read_page``
and the CSV export.
"""

from __future__ import annotations

import csv
import io
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

PAGE_SIZE = 50
SCAN_CAP = 50_000  # max lines examined per request
PAIR_WINDOW_S = 120.0
RESULTS = ("all", "ok", "denied", "refused", "failed", "attempt")
CATEGORIES = ("agents", "terminal", "control", "login", "settings", "files", "other")
_CHUNK = 64 * 1024


def iter_reverse(path: Path) -> Iterator[dict]:
    """Yield parsed entries newest first; unparseable lines are skipped."""
    try:
        fh = open(path, "rb")  # noqa: SIM115
    except FileNotFoundError:
        return
    with fh:
        pos = fh.seek(0, 2)
        buf = b""
        while pos > 0:
            step = min(_CHUNK, pos)
            pos -= step
            fh.seek(pos)
            buf = fh.read(step) + buf
            parts = buf.split(b"\n")
            buf = parts[0]  # possibly partial line, completed by the next chunk
            for ln in reversed(parts[1:]):
                e = _parse(ln)
                if e is not None:
                    yield e
        e = _parse(buf)
        if e is not None:
            yield e


def _parse(ln: bytes) -> dict | None:
    ln = ln.strip()
    if not ln:
        return None
    try:
        e = json.loads(ln)
    except ValueError:
        return None
    return e if isinstance(e, dict) else None


# Category of an action: first rule that matches wins (exact names, prefixes, suffixes). Control before agents and
# terminal, so agent_kill / tmux_session_kill are control.
_CAT_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = (
    ("login", ("logout",), ("login_", "unlock", "passkey_auth_"), ()),
    ("control", ("pm_reload", "panic"), ("container_", "vm_", "pvm_", "proc_", "stack_"), ("_kill", "_stop")),
    ("terminal", (), ("terminal.", "term_", "tmux_"), ()),
    ("agents", (), ("agent_", "agents_"), ()),
    ("settings", (), ("prefs_", "composer_", "qchat_", "autoexit_", "session_", "passkey_", "ssh_", "settings_"), ()),
    ("files", (), ("issue_", "todo_", "backlog_", "plan_", "canvas_", "tracker_", "git_"), ()),
)


def category(action: str) -> str:
    """The Category select's class of an audit action (``CATEGORIES``); ``other`` when no rule matches. A plugin's
    namespaced action (``<plugin>.prefs.save``) is classed by its name without the namespace (``prefs_save``)."""
    a = str(action or "").lower()
    names = (a, a.split(".", 1)[1].replace(".", "_")) if "." in a else (a,)
    for cat, exact, prefixes, suffixes in _CAT_RULES:
        if any(n in exact or n.startswith(prefixes) or n.endswith(suffixes) for n in names):
            return cat
    return "other"


def _ts(e: dict) -> float:
    try:
        return float(e.get("ts") or 0)
    except (TypeError, ValueError):
        return 0.0


def result_class(result: object) -> str:
    """ok | attempt | refused (``denied``) | failed (``error`` and anything else)."""
    r = str(result or "")
    return r if r in ("ok", "attempt") else "refused" if r == "denied" else "failed"


def words_of(q: str) -> tuple[str, ...]:
    """The filter box text as case-folded words (max 60 chars); every word must match."""
    return tuple(" ".join(str(q or "").split())[:60].casefold().split())


def _matches(e: dict, result: str, words: tuple[str, ...] = (), cat: str = "") -> bool:
    k = result_class(e.get("result"))
    if result == "denied":
        if k not in ("refused", "failed"):
            return False
    elif result != "all" and k != result:
        return False
    if cat and category(str(e.get("action", ""))) != cat:
        return False
    if not words:
        return True
    d = e.get("detail")
    vals = [str(v) for v in d.values()] if isinstance(d, dict) else []
    hay = " ".join([str(e.get(f, "")) for f in ("action", "target", "client")] + vals).casefold()
    return all(w in hay for w in words)


@dataclass
class Scan:
    """What ``select`` saw besides the rows it yields (filled while it runs)."""
    truncated: bool = False
    hidden: int = 0
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(RESULTS, 0))


def select(path: Path, result: str = "all", q: str = "", show_attempts: bool = False, cat: str = "",
           scan: Scan | None = None) -> Iterator[dict]:
    """Entries matching result + filter words (action, target, client, detail values) + category, newest first.

    An attempt whose outcome follows (same client+action, within ``PAIR_WINDOW_S``) is collapsed: skipped, or
    yielded with ``_hidden`` when ``show_attempts``. ``scan.counts[r]`` is what ``result=r`` would list under the
    same words, category and attempts switch; ``scan.hidden`` counts the collapsed rows of this result."""
    result = result if result in RESULTS else "all"
    cat = cat if cat in CATEGORIES else ""
    words, scan = words_of(q), scan if scan is not None else Scan()
    newer: dict[tuple, dict] = {}  # nearest newer entry per client+action
    for n, e in enumerate(iter_reverse(path), 1):
        if n > SCAN_CAP:
            scan.truncated = True
            return
        key = (e.get("client", ""), e.get("action", ""))
        nxt = newer.get(key)
        newer[key] = e
        if not _matches(e, "all", words, cat):
            continue
        k = result_class(e.get("result"))
        paired = (k == "attempt" and nxt is not None and nxt.get("result") != "attempt"
                  and 0 <= _ts(nxt) - _ts(e) <= PAIR_WINDOW_S)
        scan.counts[k] += 1
        scan.counts["denied"] += k in ("refused", "failed")
        scan.counts["all"] += not paired or show_attempts
        if not _matches(e, result):
            continue
        if paired and result != "attempt":
            scan.hidden += 1
            if not show_attempts:
                continue
            e = {**e, "_hidden": True}
        yield e


@dataclass
class AuditPage:
    rows: list[dict] = field(default_factory=list)
    page: int = 1
    total: int = 0  # matching rows within the scanned window
    hidden: int = 0  # collapsed attempts within the scanned window
    truncated: bool = False  # scan cap reached; older entries exist unseen
    has_older: bool = False
    counts: dict[str, int] = field(default_factory=dict)  # per RESULTS value, same words/category/attempts


def read_page(path: Path, page: int = 1, result: str = "all", action: str = "",
              show_attempts: bool = False, size: int = PAGE_SIZE, cat: str = "") -> AuditPage:
    """One page of ``select``. ``action`` is the filter box text (the historic name; it matches more than the action)."""
    page = max(1, page)
    lo, hi = (page - 1) * size, page * size
    out, scan = AuditPage(page=page), Scan()
    for i, e in enumerate(select(path, result, action, show_attempts, cat, scan)):
        out.total = i + 1
        if lo <= i < hi:
            out.rows.append(e)
    out.hidden, out.truncated, out.counts = scan.hidden, scan.truncated, scan.counts
    out.has_older = out.total > hi or (out.truncated and out.total >= hi)
    return out


CSV_COLUMNS = ("time", "ts", "client", "action", "category", "target", "result", "detail")
_CSV_RISKY = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(v: object) -> str:
    """A CSV cell a spreadsheet will not run: text starting with = + - @ tab or CR gets a leading quote."""
    if isinstance(v, dict):
        v = json.dumps(v, separators=(",", ":"), sort_keys=True, default=str)
    s = str(v)
    return "'" + s if s.startswith(_CSV_RISKY) else s


def csv_lines(path: Path, result: str = "all", q: str = "", show_attempts: bool = False,
              cat: str = "") -> Iterator[str]:
    """The ``select`` rows as CSV text, header first, one line per chunk (streamed; nothing held in memory)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")

    def line(cells) -> str:
        buf.seek(0)
        buf.truncate()
        w.writerow([csv_cell(c) for c in cells])
        return buf.getvalue()

    yield line(CSV_COLUMNS)
    for e in select(path, result, q, show_attempts, cat):
        ts = _ts(e)
        when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
        yield line((when, f"{ts:.3f}", e.get("client", ""), e.get("action", ""), category(e.get("action", "")),
                    e.get("target", ""), e.get("result", ""), e.get("detail") or {}))
