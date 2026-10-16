"""Backlog file domain: atomic locked read-modify-write of ``<project>/BACKLOG.md``, text ops and
summaries. HTTP routes live in web.routes.backlog; this module is shared (todo, plans, project page)."""

from __future__ import annotations

import difflib
import errno
import hashlib
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path

from pm.core import filelock, timefmt
from pm.core.errors import PmError
from pm.core.fsutil import atomic_write, safe_join
from pm.core.markdown_render import render_markdown
from pm.projects import projects as projects_mod

MAX_BYTES = 256 * 1024
MAX_ITEM = 500
ITEM = re.compile(r"^(\s*)- \[([ xX])\] ?(.*)$")
HEAD = re.compile(r"^#{1,6}\s")
INBOX = re.compile(r"^##\s+Inbox\s*$", re.I)
TOKEN = re.compile(r"^[0-9a-f]{16}$")


class Conflict(Exception):
    pass


class Changed(Exception):
    """The file changed between read and replace (external writer)."""


class Bad(Exception):
    def __init__(self, status: int, msg: str, current: bytes | None = None):
        super().__init__(msg)
        self.status, self.msg, self.current = status, msg, current


def token_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


FILE = "BACKLOG.md"


def read(path, limit: int = MAX_BYTES * 4) -> bytes:
    """Read (at most ``limit`` bytes) without following symlinks (a symlinked BACKLOG.md is refused)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return b""
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise Bad(400, "BACKLOG.md is a symlink; refused") from None
        raise
    with os.fdopen(fd, "rb") as f:
        return f.read(limit)


def resolve_path(project_path: str) -> Path:
    """<project>/BACKLOG.md; refuses a symlink or anything resolving outside the project."""
    try:
        return safe_join(project_path, FILE)
    except PmError:
        raise Bad(400, "BACKLOG.md is a symlink or escapes the project; refused") from None


def registry(settings, main_only: bool = False) -> dict[str, str]:
    """name -> path. ``main_only`` drops linked git worktrees (listing/counts only; a worktree
    still resolves by name for its own page and file)."""
    return {p.name: p.path for p in projects_mod.discover(settings)
            if not (main_only and projects_mod.is_linked_worktree(p.path))}


def lookup(settings, name: str) -> Path:
    """Project name -> backlog path. Unknown name raises 404."""
    path = registry(settings).get(name or "")
    if path is None:
        raise Bad(404, "unknown project")
    return resolve_path(path)


def _write_atomic(path, text: str, expect: bytes | None = None, max_bytes: int = MAX_BYTES) -> None:
    """Atomic replace. With ``expect``, re-read just before the rename and raise ``Changed``
    when an external writer touched the file since ``expect`` was read."""
    data = text.encode()
    if len(data) > max_bytes:
        raise Bad(413, f"too large ({len(data)} bytes; max {max_bytes})")
    if not path.parent.is_dir():
        raise Bad(400, "project directory missing")

    def check() -> None:
        if expect is not None and read(path, max_bytes * 4) != expect:
            raise Changed()

    atomic_write(path, data, mode=None, fsync=True, before_replace=check)


def mutate(settings, path, fn, *, token: str | None = None, pre=None, name: str = FILE,
           timeout: float = 5.0, max_bytes: int = MAX_BYTES) -> tuple[int, int]:
    """Read-check-write under the cross-process lock; ``fn(text) -> new text``.

    ``token`` (full-file saves): refuse with 409 (carrying the current bytes) when the file is
    not the one the page showed. Without a token (item ops) ``fn`` is applied to whatever is on
    disk now; if an external writer changes the file between read and replace the apply is
    retried once on the new content, then refused with 409. ``pre`` runs inside the lock
    (lease check). ``max_bytes``: largest file written (the tracker's CHANGELOG.md is bigger).
    Returns (old, new) sizes.
    """
    if token is not None and not TOKEN.match(token):
        raise Bad(400, "missing page token; reload the page")
    try:
        with filelock.locked(filelock.lock_path(settings.state_dir, path), timeout):
            if pre is not None:
                pre()
            for attempt in (0, 1):
                cur = read(path, max_bytes * 4)
                if token is not None and token_of(cur) != token:
                    raise Bad(409, f"{name} changed since this page loaded; nothing was saved.",
                              current=cur)
                new = fn(cur.decode("utf-8", "replace"))
                try:
                    _write_atomic(path, new, expect=cur, max_bytes=max_bytes)
                except Changed:
                    if token is not None or attempt:
                        raise Bad(409, f"{name} was changed by someone else while saving; "
                                       "nothing was saved. Reload and redo the change.",
                                  current=read(path)) from None
                    continue
                return len(cur), len(new.encode())
    except filelock.LockTimeout:
        raise Bad(503, f"{name} is busy (locked by another writer); try again") from None
    raise AssertionError("unreachable")


def locate(items: dict[int, str], idx: int, expect: str) -> int:
    """Line of the item whose text is exactly ``expect``: at ``idx`` if it still matches there,
    else its only occurrence elsewhere; otherwise 409 (gone, changed or ambiguous)."""
    if items.get(idx) == expect:
        return idx
    hits = [i for i, t in items.items() if t == expect]
    if len(hits) == 1:
        return hits[0]
    raise Bad(409, "That item was changed or removed in the file; nothing was saved. Reload.")


def diff_text(mine: str, current: str, cap: int = 200, cap_bytes: int = 20000) -> str:
    """Unified diff current file -> your text (plain text; templates escape it), size-capped."""
    out: list[str] = []
    size = 0
    for ln in difflib.unified_diff(current.splitlines(), mine.splitlines(),
                                   "current file", "your text", lineterm="", n=2):
        ln = ln[:300]
        size += len(ln) + 1
        if len(out) >= cap or size > cap_bytes:
            out.append("... diff truncated")
            break
        out.append(ln)
    return "\n".join(out)


# ------------------------------------------------------------------ pure text ops
def add_item(text: str, item: str, today: str) -> str:
    item = " ".join((item or "").split())
    if not item:
        raise Bad(400, "empty item")
    if len(item) > MAX_ITEM:
        raise Bad(400, f"item too long (max {MAX_ITEM} chars)")
    line = f"- [ ] {item} ({today})"
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    start = next((i for i, ln in enumerate(lines) if INBOX.match(ln)), None)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += ["## Inbox", "", line]
    else:
        end = start + 1
        while end < len(lines) and not HEAD.match(lines[end]):
            end += 1
        at = end
        while at > start + 1 and not lines[at - 1].strip():
            at -= 1
        if at == start + 1:  # empty section: blank line after the heading
            lines.insert(at, "")
            at += 1
        lines.insert(at, line)
        if at + 1 < len(lines) and lines[at + 1].strip():  # blank line before the next heading
            lines.insert(at + 1, "")
    return "\n".join(lines) + "\n"


def backlog_items(text: str) -> dict[int, str]:
    out = {}
    for i, ln in enumerate(text.split("\n")):
        m = ITEM.match(ln)
        if m:
            out[i] = m.group(3)
    return out


def toggle_item(text: str, idx: int, expect: str) -> str:
    lines = text.split("\n")
    idx = locate(backlog_items(text), idx, expect)
    m = ITEM.match(lines[idx])
    mark = " " if m.group(2) in "xX" else "x"
    lines[idx] = f"{m.group(1)}- [{mark}] {m.group(3)}"
    return "\n".join(lines)


def edit_item(text: str, idx: int, expect: str, new: str) -> str:
    """Replace the text of one item (keeps its indent and its [ ]/[x] mark)."""
    new = " ".join((new or "").split())
    if not new:
        raise Bad(400, "empty item")
    if len(new) > MAX_ITEM:
        raise Bad(400, f"item too long (max {MAX_ITEM} chars)")
    lines = text.split("\n")
    idx = locate(backlog_items(text), idx, expect)
    m = ITEM.match(lines[idx])
    lines[idx] = f"{m.group(1)}- [{m.group(2)}] {new}"
    return "\n".join(lines)


def norm_content(raw: str) -> str:
    s = raw.replace("\r\n", "\n").replace("\r", "\n")
    return s.rstrip("\n") + "\n" if s.strip() else ""


# ------------------------------------------------------------------ form posts
def since_label(ts: float) -> str:
    """Lease start for people: ``HH:MM (zone)``."""
    return f"{timefmt._fmt(ts, '%H:%M')} ({timefmt.label()})"


def _form_str(form: Mapping, key: str) -> str:
    v = form.get(key, "")
    return v if isinstance(v, str) else ""


def _form_line(form: Mapping) -> int:
    try:
        return int(str(form.get("line", "")))
    except ValueError:
        raise Bad(400, "bad line") from None


def build_op(kind: str, form: Mapping, *, settings, path, lease_id: str
             ) -> tuple[Callable[[str], str], Callable[[], None] | None, str | None]:
    """(fn, pre, token) for ``mutate`` from one posted form. ``kind`` is add/edit/toggle; anything else is a
    full-file save. Item ops apply to the current file (token None, no pre); a save carries the page token
    (``new_token`` when forced) and a lease check run inside the lock. Bad input raises ``Bad``."""
    if kind == "add":
        item = str(form.get("text", ""))
        return (lambda t: add_item(t, item, timefmt.today().isoformat())), None, None
    if kind in ("edit", "toggle"):
        idx = _form_line(form)
        expect, new_text = form.get("text"), form.get("new_text")
        if not isinstance(expect, str) or len(expect) > 4 * MAX_ITEM or (kind == "edit" and not isinstance(new_text, str)):
            raise Bad(400, "missing item text; reload the page")
        if kind == "edit":
            return (lambda t: edit_item(t, idx, expect, new_text)), None, None
        return (lambda t: toggle_item(t, idx, expect)), None, None
    content = norm_content(str(form.get("content", "")))
    token = _form_str(form, "new_token" if _form_str(form, "force") == "1" else "token")

    def pre() -> None:
        cur = filelock.lease_get(settings.state_dir, path)
        if cur and cur["id"] != lease_id:
            raise Bad(409, f"Being edited by {cur['owner']} since {since_label(cur['since'])}: "
                           "your edit lease was taken over; nothing was saved.")
    return (lambda t: content), pre, token


# ------------------------------------------------------------------ rendering
def inline(text: str) -> str:
    h = render_markdown(text).strip()
    return h[3:-4] if h.startswith("<p>") and h.endswith("</p>") else h


def blocks(text: str) -> list[dict]:
    """Split into markdown chunks and checklist items (with their line index)."""
    out: list[dict] = []
    buf: list[str] = []
    inbox = False

    def flush() -> None:
        if any(x.strip() for x in buf):
            out.append({"kind": "md", "html": render_markdown("\n".join(buf))})
        buf.clear()

    for i, ln in enumerate(text.split("\n")):
        m = ITEM.match(ln)
        if HEAD.match(ln):
            inbox = bool(INBOX.match(ln))
        if m:
            flush()
            out.append({"kind": "item", "line": i, "done": m.group(2) in "xX",
                        "text": m.group(3), "html": inline(m.group(3)), "inbox": inbox})
        else:
            buf.append(ln)
    flush()
    return out


def counts(text: str) -> tuple[int, int]:
    o = d = 0
    for ln in text.split("\n"):
        m = ITEM.match(ln)
        if m:
            if m.group(2) in "xX":
                d += 1
            else:
                o += 1
    return o, d


def entry(name: str, path: str) -> dict | None:
    """One project's backlog for rendering; None when it has no (readable) file."""
    try:
        f = resolve_path(path)
        data = read(f)
    except (Bad, OSError):
        return None
    if not data:
        return None
    text = data.decode("utf-8", "replace")
    o, d = counts(text)
    return {"name": name, "token": token_of(data), "blocks": blocks(text), "open_n": o, "done_n": d}


def project_summary(settings, name: str, limit: int = 30) -> dict:
    """For the project page: open items and the latest done items (line, html), counts, token. ``exists`` False if no file."""
    path = registry(settings).get(name)
    out = {"name": name, "exists": False, "token": "", "items": [], "done_items": [], "open_n": 0, "done_n": 0,
           "more": 0, "done_more": 0, "blocked": False, "max_item": MAX_ITEM}
    if path is None:
        return out
    try:
        f = resolve_path(path)
        data = read(f)
    except Bad:
        out["blocked"] = True
        return out
    except OSError:
        return out
    text = data.decode("utf-8", "replace")
    out["exists"] = bool(data)
    out["token"] = token_of(data)
    items = [b for b in blocks(text) if b["kind"] == "item" and not b["done"]]
    # Inbox first: add_item appends there, so a new item must not fall past the limit when
    # the file has many open items in other sections (stable sort keeps file order).
    items.sort(key=lambda b: not b["inbox"])
    out["open_n"], out["done_n"] = counts(text)
    out["items"] = items[:limit]
    out["more"] = max(0, len(items) - limit)
    done = [b for b in blocks(text) if b["kind"] == "item" and b["done"]]
    out["done_items"] = done[-limit:][::-1]  # newest (last in file) first
    out["done_more"] = max(0, len(done) - limit)
    return out
