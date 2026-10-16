"""Canvas store: versioned markdown documents attached to one agent (chat side panel).

Layout (never inside a repo): ``<state_dir>/canvas/<agent id>/<canvas id>.json``. Agent ids are keyed
like the chat routes (permanent ids, so canvases survive exit/resume). Every change runs under an
exclusive per-agent flock (``filelock.locked``) and is written atomically (tmp + fsync + rename).
Each canvas keeps at most ``MAX_VERSIONS`` versions (oldest dropped, numbers keep counting up).
"""

from __future__ import annotations

import contextlib
import json
import re
import secrets
import time
from pathlib import Path

from pm.core import filelock
from pm.core.fsutil import atomic_write

MAX_TEXT = 200 * 1024
MAX_TITLE = 120
MAX_VERSIONS = 50
MAX_CANVASES = 20
MAX_NOTE = 120
AID_RE = re.compile(r"^[a-z]+(?:-[a-z]+)*-[0-9a-z]+$")
CID_RE = re.compile(r"^c[0-9a-f]{12}$")


class CanvasError(Exception):
    def __init__(self, status: int, msg: str, **extra):
        super().__init__(msg)
        self.status, self.msg, self.extra = status, msg, extra


def _dir(state_dir, aid: str) -> Path:
    if not AID_RE.fullmatch(aid or ""):
        raise CanvasError(404, "unknown agent")
    return Path(state_dir) / "canvas" / aid


def _path(state_dir, aid: str, cid: str) -> Path:
    if not CID_RE.fullmatch(cid or ""):
        raise CanvasError(404, "no such canvas")
    return _dir(state_dir, aid) / f"{cid}.json"


@contextlib.contextmanager
def _lock(state_dir, aid: str):
    d = _dir(state_dir, aid)
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with filelock.locked(d / ".lock", 5.0):
            yield
    except filelock.LockTimeout:
        raise CanvasError(409, "busy, try again", error="busy") from None


def _read(p: Path) -> dict:
    try:
        d = json.loads(p.read_text())
    except FileNotFoundError:
        raise CanvasError(404, "no such canvas") from None
    except (OSError, ValueError):
        raise CanvasError(500, "canvas unreadable") from None
    if not isinstance(d, dict) or not isinstance(d.get("versions"), list):
        raise CanvasError(500, "canvas unreadable")
    return d


def _write(p: Path, d: dict) -> None:
    atomic_write(p, json.dumps(d), mode=0o600, fsync=True)


def _check_text(text: str) -> None:
    if len(text.encode()) > MAX_TEXT:
        raise CanvasError(413, f"text too large (max {MAX_TEXT // 1024} KB)", error="too_large")


def _title(title: str) -> str:
    t = " ".join(str(title or "").split())
    if not t:
        raise CanvasError(400, "title required", error="title")
    if len(t) > MAX_TITLE:
        raise CanvasError(400, f"title too long (max {MAX_TITLE})", error="title")
    return t


def _view(d: dict) -> dict:
    return {"id": d["id"], "title": d["title"], "cur": d["cur"], "versions": d["versions"]}


def _ver(n: int, text: str, note: str, now: float) -> dict:
    return {"n": n, "ts": int(now), "text": text, "note": str(note or "")[:MAX_NOTE]}


def list_canvases(state_dir, aid: str) -> list[dict]:
    d = _dir(state_dir, aid)
    out = []
    for p in d.glob("c*.json") if d.is_dir() else ():
        if not CID_RE.fullmatch(p.stem):
            continue
        try:
            c = _read(p)
        except CanvasError:
            continue
        out.append({"id": c["id"], "title": c["title"], "cur": c["cur"], "updated": c.get("updated", 0)})
    out.sort(key=lambda c: (-c["updated"], c["id"]))
    return out


def get(state_dir, aid: str, cid: str) -> dict:
    return _view(_read(_path(state_dir, aid, cid)))


def create(state_dir, aid: str, title: str, text: str, now: float | None = None) -> dict:
    title = _title(title)
    _check_text(text)
    now = time.time() if now is None else now
    with _lock(state_dir, aid):
        if len(list_canvases(state_dir, aid)) >= MAX_CANVASES:
            raise CanvasError(409, f"too many canvases (max {MAX_CANVASES})", error="limit")
        cid = "c" + secrets.token_hex(6)
        d = {"id": cid, "title": title, "cur": 1, "updated": int(now),
             "versions": [_ver(1, text, "", now)]}
        _write(_path(state_dir, aid, cid), d)
        return _view(d)


def save(state_dir, aid: str, cid: str, text: str, base: int, note: str = "",
         title: str | None = None, now: float | None = None) -> tuple[dict, bool]:
    """(canvas, changed). Stale ``base`` -> 409 with ``cur``; identical text -> unchanged."""
    _check_text(text)
    new_title = _title(title) if title else None
    now = time.time() if now is None else now
    p = _path(state_dir, aid, cid)
    with _lock(state_dir, aid):
        d = _read(p)
        if base != d["cur"]:
            raise CanvasError(409, "stale", error="stale", cur=d["cur"])
        last = d["versions"][-1] if d["versions"] else {"text": None}
        renamed = new_title is not None and new_title != d["title"]
        if last["text"] == text and not renamed:
            return _view(d), False
        if renamed:
            d["title"] = new_title
        if last["text"] != text:
            d["cur"] += 1
            d["versions"].append(_ver(d["cur"], text, note, now))
            del d["versions"][:-MAX_VERSIONS]
        d["updated"] = int(now)
        _write(p, d)
        return _view(d), True


def restore(state_dir, aid: str, cid: str, n: int, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    p = _path(state_dir, aid, cid)
    with _lock(state_dir, aid):
        d = _read(p)
        src = next((v for v in d["versions"] if v.get("n") == n), None)
        if src is None:
            raise CanvasError(404, "no such version", error="version")
        d["cur"] += 1
        d["versions"].append(_ver(d["cur"], src["text"], f"restored v{n}", now))
        del d["versions"][:-MAX_VERSIONS]
        d["updated"] = int(now)
        _write(p, d)
        return _view(d)


def delete(state_dir, aid: str, cid: str) -> int:
    """Remove the canvas; returns the number of versions it held."""
    p = _path(state_dir, aid, cid)
    with _lock(state_dir, aid):
        n = len(_read(p)["versions"])
        p.unlink()
        return n
