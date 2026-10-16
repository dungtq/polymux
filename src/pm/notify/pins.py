"""Pinned ntfy messages: a snapshot per message id in ``<state_dir>/ntfy-pins.json``.

A pin keeps its own copy of the message (clipped and masked like the feed), so it survives the message
leaving the feed window. Newest pin first, at most ``MAX_PINS``.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from pm.core import filelock
from pm.core.config import Settings
from pm.core.errors import Conflict, Invalid, NotFound
from pm.core.fsutil import JsonStore
from pm.core.models import NtfyItem
from pm.notify.feed import MAX_MESSAGE, MAX_TITLE, clip

MAX_PINS = 200
ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def _file(settings: Settings) -> Path:
    return Path(settings.state_dir) / "ntfy-pins.json"


def _store(settings: Settings) -> JsonStore:
    return JsonStore(_file(settings), list, validate=lambda d: isinstance(d, list))


def _item(d) -> NtfyItem | None:
    """A stored dict as an NtfyItem; None when it is malformed."""
    try:
        if not isinstance(d, dict) or not ID_RE.fullmatch(d["id"]):
            return None
        tags = tuple(t for t in d.get("tags", ()) if isinstance(t, str))[:20]
        click, project = d.get("click", ""), d.get("project")
        return NtfyItem(id=d["id"], time=float(d["time"]), title=clip(d.get("title"), MAX_TITLE),
                        message=clip(d.get("message"), MAX_MESSAGE), tags=tags,
                        project=project if isinstance(project, str) else None,
                        click=click if isinstance(click, str) else "", priority=int(d.get("priority", 3)))
    except (KeyError, TypeError, ValueError):
        return None


def load(settings: Settings) -> list[NtfyItem]:
    """Pinned messages, newest pin first; malformed entries are skipped."""
    out = (_item(d) for d in _store(settings).load())
    return [i for i in out if i is not None]


def _snapshot(item: NtfyItem) -> dict:
    return {"id": item.id, "time": item.time, "title": clip(item.title, MAX_TITLE),
            "message": clip(item.message, MAX_MESSAGE), "tags": list(item.tags)[:20],
            "project": item.project, "click": item.click[:500], "priority": item.priority,
            "pinned": time.time()}


def _update(settings: Settings, fn) -> None:
    f = _file(settings)
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        with filelock.locked(f.parent / "ntfy-pins.lock"):
            _store(settings).update(fn)
    except filelock.LockTimeout as e:
        raise OSError("pins are busy") from e  # the route maps OSError to "cannot save"


def pin(settings: Settings, item: NtfyItem) -> None:
    """Pin ``item`` (idempotent). Conflict when MAX_PINS are already stored."""
    if not ID_RE.fullmatch(item.id):
        raise Invalid("bad id")

    def add(pins: list) -> list:
        pins = [d for d in pins if isinstance(d, dict)]
        if any(d.get("id") == item.id for d in pins):
            return pins
        if len(pins) >= MAX_PINS:
            raise Conflict(f"at most {MAX_PINS} pinned messages")
        return [_snapshot(item), *pins]

    _update(settings, add)


def unpin(settings: Settings, item_id: str) -> None:
    """Remove a pin by id. NotFound when it is not pinned."""
    if not ID_RE.fullmatch(item_id):
        raise Invalid("bad id")

    def drop(pins: list) -> list:
        kept = [d for d in pins if isinstance(d, dict) and d.get("id") != item_id]
        if len(kept) == len([d for d in pins if isinstance(d, dict)]):
            raise NotFound("not pinned")
        return kept

    _update(settings, drop)
