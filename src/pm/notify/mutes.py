"""Muted ntfy titles: ``<state_dir>/ntfy-mutes.json`` = {title: muted until (epoch s)}.

A muted title leaves every list and count of the ntfy mirror (``feed.recent`` drops it) for ``MUTE_S``; the
message itself stays on the ntfy server. Titles are matched exactly as the feed shows them (clipped, masked).
The file is read again only when its mtime or size changed.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from pm.core import filelock
from pm.core.config import Settings
from pm.core.errors import Conflict, Invalid, NotFound
from pm.core.fsutil import JsonStore

MUTE_S = 24 * 3600
MAX_MUTES = 200
MAX_TITLE = 200  # = feed.MAX_TITLE (feed imports this module, so not imported from there)

_lock = threading.Lock()
_cache: dict = {"sig": None, "data": {}}  # the last file read: (path, mtime_ns, size) and its mapping


def _file(settings: Settings) -> Path:
    return Path(settings.state_dir) / "ntfy-mutes.json"


def _store(settings: Settings) -> JsonStore:
    return JsonStore(_file(settings), dict, validate=lambda d: isinstance(d, dict))


def valid_title(title) -> bool:
    """A non-empty one-line title of at most MAX_TITLE characters."""
    return isinstance(title, str) and 0 < len(title) <= MAX_TITLE and title.strip() != "" \
        and not any(ord(c) < 32 or ord(c) == 127 for c in title)


def _clean(d: dict) -> dict[str, float]:
    return {k: float(v) for k, v in d.items()
            if valid_title(k) and isinstance(v, int | float) and not isinstance(v, bool)}


def _read(settings: Settings) -> dict[str, float]:
    """All stored mutes (expired ones too), cached by the file's mtime and size."""
    f = _file(settings)
    try:
        st = f.stat()
    except OSError:
        return {}
    sig = (str(f), st.st_mtime_ns, st.st_size)
    with _lock:
        if _cache["sig"] == sig:
            return _cache["data"]
    data = _clean(_store(settings).load())
    with _lock:
        _cache.update(sig=sig, data=data)
    return data


def load(settings: Settings, now: float | None = None) -> dict[str, float]:
    """{muted title: until} of the mutes still running."""
    now = time.time() if now is None else now
    return {k: v for k, v in _read(settings).items() if v > now}


def drop_muted(settings: Settings, items: list, now: float | None = None) -> list:
    """``items`` without the ones whose title is muted (the one filter every ntfy list and count goes through)."""
    muted = load(settings, now)
    return [i for i in items if i.title not in muted] if muted else items


def _update(settings: Settings, fn) -> None:
    f = _file(settings)
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        with filelock.locked(f.parent / "ntfy-mutes.lock"):
            _store(settings).update(fn)
    except filelock.LockTimeout as e:
        raise OSError("mutes are busy") from e  # the route maps OSError to "cannot save"


def mute(settings: Settings, title: str, now: float | None = None) -> None:
    """Mute ``title`` for MUTE_S from now (again: the time restarts). Conflict at MAX_MUTES running mutes."""
    if not valid_title(title):
        raise Invalid("bad title")
    now = time.time() if now is None else now

    def add(d: dict) -> dict:
        live = {k: v for k, v in _clean(d).items() if v > now}
        if title not in live and len(live) >= MAX_MUTES:
            raise Conflict(f"at most {MAX_MUTES} muted titles")
        return {**live, title: now + MUTE_S}

    _update(settings, add)


def unmute(settings: Settings, title: str, now: float | None = None) -> None:
    """End the mute of ``title``. NotFound when it is not muted."""
    if not valid_title(title):
        raise Invalid("bad title")
    now = time.time() if now is None else now

    def drop(d: dict) -> dict:
        live = {k: v for k, v in _clean(d).items() if v > now}
        if title not in live:
            raise NotFound("not muted")
        live.pop(title)
        return live

    _update(settings, drop)
