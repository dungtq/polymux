"""v7-only state of the dt theme: the open tabs (V7-ISO-9). No framework imports.

One JSON file ``state_dir/v7-tabs.json`` (docs/themes/dt/SHELL.md §3)::

    {"rev": 7, "tabs": [{"kind": "agent", "id": "claude-3fa9c1"}, {"kind": "term", "id": "$4"}],
     "current": {"kind": "agent", "id": "claude-3fa9c1"} | null, "closed": [{"kind": ..., "id": ...}]}

Tabs are addressed by (kind, id), never by position (V7-CODE-4). The home tab is not stored. ``rev`` is bumped on
every change and lets other browsers know their strip is stale. Pure functions change a ``TabState``; ``apply`` runs
one of them under the file lock (``pm.core.filelock``) and writes the file only when something changed.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from pm.agents.lineage import AGENT_ID_RE
from pm.core import filelock
from pm.core.cache import LRU
from pm.core.config import Settings
from pm.core.errors import Invalid
from pm.core.fsutil import JsonStore
from pm.tmux.ctl import ID_RE

MAX_OPEN = 30
MAX_CLOSED = 20
KINDS = ("agent", "term")
FILE = "v7-tabs.json"
LOCK = "v7-tabs.lock"

Ref = tuple[str, str]  # (kind, id)


@dataclass(frozen=True)
class TabState:
    rev: int = 0
    tabs: tuple[Ref, ...] = ()
    current: Ref | None = None
    closed: tuple[Ref, ...] = ()  # oldest first; reopen takes the last


EMPTY = TabState()


def check_ref(kind: str, id_: str) -> Ref:
    """(kind, id) of a tab after checking it: an agent id or a tmux session id (``$n``). Raises ``Invalid``."""
    if kind == "agent" and isinstance(id_, str) and len(id_) <= 64 and AGENT_ID_RE.fullmatch(id_):
        return kind, id_
    if kind == "term" and isinstance(id_, str) and len(id_) <= 16 and ID_RE.fullmatch(id_) and id_[0] == "$":
        return kind, id_
    raise Invalid("bad tab")


def _ref(x) -> Ref | None:
    if not isinstance(x, dict):
        return None
    try:
        return check_ref(x.get("kind"), x.get("id"))
    except Invalid:
        return None


def _refs(xs, cap: int) -> tuple[Ref, ...]:
    out: list[Ref] = []
    for x in xs if isinstance(xs, list) else ():
        r = _ref(x)
        if r is not None and r not in out:
            out.append(r)
    return tuple(out[-cap:])


def from_json(d) -> TabState:
    """A ``TabState`` from the file's object; anything invalid is dropped (a broken file = no tabs)."""
    if not isinstance(d, dict):
        return EMPTY
    rev = d.get("rev")
    tabs = _refs(d.get("tabs"), MAX_OPEN)
    cur = _ref(d.get("current"))
    return TabState(rev if isinstance(rev, int) and rev >= 0 else 0, tabs, cur if cur in tabs else None,
                    _refs(d.get("closed"), MAX_CLOSED))


def to_json(st: TabState) -> dict:
    def j(r: Ref) -> dict:
        return {"kind": r[0], "id": r[1]}
    return {"rev": st.rev, "tabs": [j(r) for r in st.tabs], "current": j(st.current) if st.current else None,
            "closed": [j(r) for r in st.closed]}


# ------------------------------------------------------------------ pure changes (rev untouched)
def _push_closed(closed: tuple[Ref, ...], ref: Ref) -> tuple[Ref, ...]:
    return (*(r for r in closed if r != ref), ref)[-MAX_CLOSED:]


def open_tab(st: TabState, ref: Ref) -> TabState:
    """Open ``ref`` after the current tab (or last) and make it current; an open tab is only focused. Past
    ``MAX_OPEN`` the leftmost other tab is closed."""
    if ref in st.tabs:
        return replace(st, current=ref)
    tabs = list(st.tabs)
    at = tabs.index(st.current) + 1 if st.current in tabs else len(tabs)
    tabs.insert(at, ref)
    closed = st.closed
    while len(tabs) > MAX_OPEN:
        drop = next(r for r in tabs if r != ref)
        tabs.remove(drop)
        closed = _push_closed(closed, drop)
    return TabState(st.rev, tuple(tabs), ref, tuple(r for r in closed if r not in tabs))


def close_tab(st: TabState, ref: Ref) -> TabState:
    """Close ``ref`` (not open: no change). The current tab moves to its left neighbour, else the right one."""
    if ref not in st.tabs:
        return st
    i = st.tabs.index(ref)
    tabs = st.tabs[:i] + st.tabs[i + 1:]
    cur = st.current
    if cur == ref:
        cur = tabs[i - 1] if i > 0 else (tabs[0] if tabs else None)
    return TabState(st.rev, tabs, cur, _push_closed(st.closed, ref))


def reopen_tab(st: TabState) -> tuple[TabState, Ref | None]:
    """Open the most recently closed tab again; (unchanged, None) when nothing was closed."""
    if not st.closed:
        return st, None
    ref = st.closed[-1]
    return open_tab(replace(st, closed=st.closed[:-1]), ref), ref


def close_all(st: TabState) -> tuple[TabState, int]:
    """Close every session tab (Settings › Close all session tabs); they go to the reopen list in their order, the
    home tab becomes current. Returns (state, number closed)."""
    closed = st.closed
    for ref in st.tabs:
        closed = _push_closed(closed, ref)
    return TabState(st.rev, (), None, closed), len(st.tabs)


def set_current(st: TabState, ref: Ref | None) -> TabState:
    """``ref`` becomes current (None = the home tab); a tab that is not open leaves the state unchanged."""
    if ref is not None and ref not in st.tabs:
        return st
    return replace(st, current=ref)


def move_tab(st: TabState, ref: Ref, before: Ref | None) -> TabState:
    """Move ``ref`` in front of ``before`` (None = to the end). Raises ``Invalid`` when either is not open."""
    if ref not in st.tabs or (before is not None and before not in st.tabs):
        raise Invalid("tab not open")
    if ref == before:
        return st
    tabs = [r for r in st.tabs if r != ref]
    tabs.insert(tabs.index(before) if before is not None else len(tabs), ref)
    return replace(st, tabs=tuple(tabs))


# ------------------------------------------------------------------ file IO
def path(settings: Settings) -> Path:
    return Path(settings.state_dir) / FILE


def _store(settings: Settings) -> JsonStore:
    return JsonStore(path(settings), dict, validate=lambda d: isinstance(d, dict))


def load(settings: Settings) -> TabState:
    """The stored tabs (EMPTY when the file is missing or broken)."""
    return from_json(_store(settings).load())


_cache = LRU(max_bytes=1024 * 1024, max_entries=8)  # str(path) -> ((mtime_ns, size), TabState)


def read_cached(settings: Settings) -> TabState:
    """``load`` for the render path: re-read only when the file's (mtime_ns, size) changed."""
    p = path(settings)
    try:
        s = os.stat(p)
    except OSError:
        return EMPTY
    key = (s.st_mtime_ns, s.st_size)
    hit = _cache.get(str(p))
    if hit is not None and hit[0] == key:
        return hit[1]
    st = load(settings)
    _cache.put(str(p), (key, st))
    return st


def apply(settings: Settings, fn: Callable, *args) -> tuple[TabState, object]:
    """Under the lock: load, ``fn(state, *args)`` (a ``TabState`` or ``(TabState, extra)``), bump ``rev`` and write
    when the tabs changed. Returns (stored state, extra). Raises ``Invalid`` from ``fn``, ``OSError`` when busy."""
    f = path(settings)
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        with filelock.locked(f.parent / LOCK):
            old = load(settings)
            got = fn(old, *args)
            new, extra = got if isinstance(got, tuple) else (got, None)
            if (new.tabs, new.current, new.closed) == (old.tabs, old.current, old.closed):
                return old, extra
            new = replace(new, rev=old.rev + 1)
            _store(settings).save(to_json(new))
    except filelock.LockTimeout as e:
        raise OSError("tabs are busy") from e
    return new, extra

