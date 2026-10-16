"""Bounded ring log of browser-side errors (JS errors, failed requests).

Stored as JSONL under state_dir. Only sanitised metadata is kept: paths without
query strings or fragments, a short masked message and stack, a status code, the
browser family (never the full User-Agent). Never bodies, headers or pane text.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from pm.core.fsutil import atomic_write
from pm.security import useragent
from pm.security.mask import mask

MAX_ENTRIES = 500
KEEP_ON_ROTATE = 400
MAX_BODY = 4096
PAGE_SIZE = 25
KINDS = ("js", "promise", "htmx", "fetch", "csp")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
_URL_Q = re.compile(r"[?#][^\s\"')>]*")
_ORIGIN = re.compile(r"https?://[^/\s\"']+")
# a query string in a stack frame, up to the frame's ":line:col" (kept): /static/js/x.js?v=ab12:84:17
_STACK_Q = re.compile(r"[?#][^\s\"')>]{0,300}?(?=:\d+:\d+(?:\D|$))")
STACK_MAX = 800


def clean_path(value: object, limit: int = 200) -> str:
    """URL -> path only: no origin, query string or fragment."""
    s = str(value or "")[:600]
    s = _ORIGIN.sub("", s)
    s = _URL_Q.sub("", s)
    return s[:limit]


def clean_msg(value: object, limit: int = 240, keep_lines: bool = False) -> str:
    s = str(value or "")[:2000 if not keep_lines else 4000]
    s = _URL_Q.sub("", s)
    if keep_lines:
        s = "\n".join(" ".join(ln.split()) for ln in s.splitlines() if ln.strip())
    else:
        s = " ".join(s.split())
    try:
        s = mask(s)
    except Exception:  # noqa: BLE001 - masking must never break logging
        pass
    return s[:limit]


def clean_stack(value: object, limit: int = STACK_MAX) -> str:
    """A JS stack: origins and query strings out (frame ``:line:col`` kept), lines kept, masked."""
    s = _ORIGIN.sub("", str(value or "")[:4000])
    return clean_msg(_STACK_Q.sub("", s), limit, keep_lines=True)


def _int(v: object, lo: int = 0, hi: int = 999_999) -> int:
    try:
        return min(max(int(v), lo), hi)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def sanitize(raw: dict, client: str, now: float | None = None, ua: str = "") -> dict | None:
    """One stored entry from a browser report, or None. ``ua`` is the User-Agent header (only its family is kept)."""
    kind = raw.get("kind")
    if kind not in KINDS:
        return None
    method = str(raw.get("method") or "").upper()
    e = {
        "ts": now if now is not None else time.time(),
        "client": str(client)[:64],
        "kind": kind,
        "page": clean_path(raw.get("page")),
        "url": clean_path(raw.get("url")),
        "method": method if method in METHODS else "",
        "status": _int(raw.get("status"), 0, 999),
        "msg": clean_msg(raw.get("msg")),
        "line": _int(raw.get("line")),
        "col": _int(raw.get("col")),
    }
    stack, fam = clean_stack(raw.get("stack")), useragent.family(ua)
    if stack:
        e["stack"] = stack
    if fam:
        e["ua"] = fam
    return e


class ClientErrorLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._count: int | None = None

    def _lines(self) -> list[bytes]:
        try:
            return self.path.read_bytes().splitlines()
        except FileNotFoundError:
            return []

    def append(self, entry: dict) -> None:
        line = (json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n").encode()
        with self._lock:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self._count is None:
                self._count = len(self._lines())
            if self._count >= MAX_ENTRIES:
                keep = self._lines()[-(KEEP_ON_ROTATE - 1):]
                atomic_write(self.path, b"\n".join(keep) + (b"\n" if keep else b""), mode=0o600, fsync=True, fsync_dir=True)
                self._count = len(keep)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
            self._count += 1

    def clear(self) -> None:
        with self._lock:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self._count = 0

    def _entries(self) -> Iterator[dict]:
        """Stored entries, newest first; unparseable lines skipped."""
        for ln in reversed(self._lines()):
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if isinstance(e, dict):
                yield e

    @staticmethod
    def _hit(e: dict, needle: str) -> bool:
        return not needle or needle in " ".join(str(e.get(k, "")) for k in ("url", "msg", "page")).lower()

    def page(self, page: int = 1, kind: str = "all", text: str = "") -> dict:
        """Newest first. Filter by kind and a substring of url/msg/page."""
        needle = text.strip().lower()[:60]
        rows = [e for e in self._entries() if (kind not in KINDS or e.get("kind") == kind) and self._hit(e, needle)]
        total = len(rows)
        page = max(1, page)
        start = (page - 1) * PAGE_SIZE
        return {"rows": rows[start:start + PAGE_SIZE], "total": total, "page": page,
                "has_older": start + PAGE_SIZE < total, "start": start}

    def grouped(self, page: int = 1, kind: str = "all", text: str = "") -> dict:
        """One row per kind + message + request (method, url, status), newest last-seen first; the same filters as
        ``page``. Row: ``msg, kind, pages`` (distinct, newest first, max 10) ``, count, first, last, status, method,
        url, line, col, stack, ua, sample`` (the newest entry). ``counts`` = entries per kind (+ ``all``) under the
        text filter, any kind; ``total`` = rows after both filters."""
        needle = text.strip().lower()[:60]
        counts = dict.fromkeys(("all", *KINDS), 0)
        groups: dict[tuple, dict] = {}
        for e in self._entries():
            if not self._hit(e, needle):
                continue
            k = e.get("kind")
            counts["all"] += 1
            if k in counts:
                counts[k] += 1
            if kind in KINDS and k != kind:
                continue
            key = (k, e.get("msg", ""), e.get("method", ""), e.get("url", ""), e.get("status", 0))
            g = groups.get(key)
            if g is None:
                g = groups[key] = {"msg": e.get("msg", ""), "kind": k, "pages": [], "count": 0, "first": e.get("ts"),
                                   "last": e.get("ts"), "status": e.get("status", 0), "method": e.get("method", ""),
                                   "url": e.get("url", ""), "line": e.get("line", 0), "col": e.get("col", 0),
                                   "stack": e.get("stack", ""), "ua": e.get("ua", ""), "sample": e}
            g["count"] += 1
            g["first"] = e.get("ts")
            g["stack"] = g["stack"] or e.get("stack", "")
            g["ua"] = g["ua"] or e.get("ua", "")
            if e.get("page") and e["page"] not in g["pages"] and len(g["pages"]) < 10:
                g["pages"].append(e["page"])
        rows = list(groups.values())  # insertion order = newest last-seen first
        page = max(1, page)
        start = (page - 1) * PAGE_SIZE
        return {"rows": rows[start:start + PAGE_SIZE], "counts": counts, "total": len(rows), "page": page,
                "has_older": start + PAGE_SIZE < len(rows), "start": start}


def app_log(state) -> ClientErrorLog:
    """The process's one log, kept on the app state (``state.client_errors``, created from ``state.settings`` on
    first use). The web layer passes FastAPI's ``app.state``; nothing here imports the web stack."""
    log = getattr(state, "client_errors", None)
    if log is None:
        log = state.client_errors = ClientErrorLog(state.settings.client_errors_file)
    return log
