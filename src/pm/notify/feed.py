"""Read-only mirror of the ntfy topic (Inbox, Projects count, project page).

One shared in-memory cache, mirrored to ``<state_dir>/ntfy-feed.json`` so a pm
restart or a page reload does not poll ntfy again while the copy is fresh.
Pages never wait on a slow ntfy: stale data is served while a single background
thread refreshes, and a refresh only asks for what is new (``since`` = the last
poll, a few seconds back) instead of the whole history. Message text is masked
and never logged; failures are logged by exception type only. Muted titles
(``notify.mutes``) are dropped from every list and count here, in ``_matched``.

A pushed burst ends in one ``inbox`` hub event (``_flush``). With ``start(app, on_new=...)`` (injected by
``web.app``) the new items go to ``on_new(items) -> [payload]`` first: each payload (an agent's
``{"agent", "ntfy": 1, "unread", "last"}``) is published as its own ``inbox`` event, and the plain
``{"ntfy": 1}`` only when there is none.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import re
import threading
import time
from pathlib import Path

import httpx

from pm.core import executors
from pm.core.cache import LRU
from pm.core.config import Settings
from pm.core.fsutil import JsonStore
from pm.core.models import NtfyItem
from pm.notify import mutes
from pm.notify import ntfy as notify
from pm.projects import projects
from pm.security.mask import mask

log = logging.getLogger("pm.ntfy_feed")

TIMEOUT_S = 3.0
MAX_ITEMS = 5000
MAX_TITLE = 200
MAX_MESSAGE = 1000
FIRST_WAIT_S = 1.0  # first ever load waits this long at most
OVERLAP_S = 30  # incremental polls start this far before the last poll (merged by id)

STREAM_READ_S = 120.0  # ntfy sends a keepalive every ~45 s; silence this long means a dead connection
BACKOFF_MAX_S = 60.0
FLUSH_S = 0.5  # coalesce a burst of pushed messages into one save + hub event
_sleep = asyncio.sleep  # tests: no real backoff

_lock = threading.Lock()  # guards the state below
# polled: {topic url: wall-clock time of its last successful poll}; at: monotonic time of the last refresh
_state: dict = {"key": None, "items": [], "at": 0.0, "ok": False, "thread": None, "polled": {}, "live": set()}
_transport = None  # tests only: httpx transport


def reset() -> None:
    with _lock:
        _state.update(key=None, items=[], at=0.0, ok=False, thread=None, polled={}, live=set())
    _matched_memo.clear()


def _store(settings: Settings) -> JsonStore:
    return JsonStore(settings.state_dir / "ntfy-feed.json", dict,
                     validate=lambda o: isinstance(o, dict), warn_corrupt=True, logger=log)


def _load_disk(settings: Settings, key) -> None:
    """Seed ``_state`` (caller holds ``_lock``, key already set) from the saved copy of the same topics."""
    try:
        d = _store(settings).load()
        saved_key = d.get("key") or [[], ""]
        if saved_key[0] != list(key[0]):
            return  # other topics: nothing here belongs to us
        same_window = saved_key[1] == key[1]  # another history window: show the old items, refetch behind them
        items = [NtfyItem(**{**r, "tags": tuple(r.get("tags", ())), "title": clip(r.get("title"), MAX_TITLE),
                             "message": clip(r.get("message"), MAX_MESSAGE)}) for r in d["items"]][:MAX_ITEMS]
        age = max(0.0, time.time() - float(d["saved"]))
        polled = {str(k): float(v) for k, v in d["polled"].items()} if same_window else {}
    except Exception:  # noqa: BLE001 - a bad copy is just a cold start
        return
    _state.update(items=items, polled=polled, ok=True, at=time.monotonic() - age if same_window else 0.0)


def _save_disk(settings: Settings, key, items: list[NtfyItem], polled: dict) -> None:
    try:
        rows = [dataclasses.asdict(i) | {"project": ""} for i in items]
        _store(settings).save({"key": [list(key[0]), key[1]], "saved": time.time(), "polled": polled, "items": rows})
    except Exception as exc:  # noqa: BLE001 - the cache is an optimisation only
        log.warning("ntfy feed save failed: %s", exc.__class__.__name__)


def clip(text, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    return mask(text)[:limit]


def _parse_line(line: str) -> NtfyItem | None:
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict) or d.get("event") != "message":
        return None
    try:
        t = float(d.get("time"))
    except (TypeError, ValueError):
        return None
    tags = d.get("tags")
    tags = tuple(str(x)[:300] for x in tags[:20]) if isinstance(tags, list) else ()
    prio = d.get("priority")
    prio = prio if isinstance(prio, int) and not isinstance(prio, bool) and 1 <= prio <= 5 else 3
    click = d.get("click")
    return NtfyItem(
        id=str(d.get("id", ""))[:64], time=t,
        title=clip(d.get("title"), MAX_TITLE), message=clip(d.get("message"), MAX_MESSAGE),
        tags=tags, click=click[:500] if isinstance(click, str) else "", priority=prio,
    )


def parse(text: str) -> list[NtfyItem]:
    items = [i for i in (_parse_line(ln) for ln in text.splitlines() if ln.strip()) if i]
    items.sort(key=lambda i: i.time, reverse=True)
    return items[:MAX_ITEMS]


def _since(value: str) -> str:
    """ntfy parses `since` with Go durations, which have no `d` unit."""
    m = re.fullmatch(r"(\d+)d", value.strip())
    return f"{int(m.group(1)) * 24}h" if m else value


def _topics(settings: Settings) -> tuple[str, ...]:
    urls = (settings.ntfy_topic_url, settings.ntfy_pm_topic_url)
    return tuple(dict.fromkeys(u for u in urls if u))


def _fetch_one(settings: Settings, topic: str, since: str) -> list[NtfyItem]:
    url = topic.rstrip("/") + "/json"
    kw: dict = {"timeout": TIMEOUT_S, "follow_redirects": False}
    if _transport is not None:
        kw["transport"] = _transport
    else:
        kw["verify"] = notify._ssl_context()
    with httpx.Client(**kw) as c:
        r = c.get(url, params={"poll": "1", "since": since})
        r.raise_for_status()
        return parse(r.text)


def _fetch(settings: Settings) -> list[NtfyItem]:
    """Both topics merged by time; one topic failing keeps the other's items.

    A topic polled before only asks for what came after that poll; the first poll (or a restart with no
    saved copy) reads the whole ``ntfy_history``. New items are merged into the cached ones by id.
    """
    with _lock:
        horizon = time.time() - _history_s(settings)  # only cached items age out; ntfy applies it to new ones
        known = {i.id or f"{i.time}": i for i in _state["items"] if i.time >= horizon}
        polled = dict(_state["polled"])
    failed = 0
    topics = _topics(settings)
    for t in topics:
        since = str(int(polled[t] - OVERLAP_S)) if t in polled else _since(settings.ntfy_history)
        started = time.time()
        try:
            for i in _fetch_one(settings, t, since):
                known.setdefault(i.id or f"{i.time}", i)
            polled[t] = started
        except Exception as exc:  # noqa: BLE001 - type only, never message text
            failed += 1
            log.warning("ntfy fetch failed: %s", exc.__class__.__name__)
    if topics and failed == len(topics):
        raise RuntimeError("all ntfy topics failed")
    out = sorted(known.values(), key=lambda i: i.time, reverse=True)[:MAX_ITEMS]
    with _lock:
        _state["polled"] = {t: v for t, v in polled.items() if t in topics}
    return out


def _history_s(settings: Settings) -> float:
    m = re.fullmatch(r"(\d+)([smhd])", settings.ntfy_history.strip())
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)] if m else 7 * 86400.0


def _refresh(settings: Settings, key) -> None:
    try:
        items, ok = _fetch(settings), True
    except Exception as exc:  # noqa: BLE001 - type only, never message text
        log.warning("ntfy fetch failed: %s", exc.__class__.__name__)
        items, ok = None, False
    save = None
    with _lock:
        if _state["key"] == key:
            if ok:
                _state.update(items=items, ok=True)
                save = (items, dict(_state["polled"]))
            else:
                _state["ok"] = False
            _state["at"] = time.monotonic()
        _state["thread"] = None
    if save:
        _save_disk(settings, key, *save)


def _ensure_locked(settings: Settings):
    """Reset and seed ``_state`` when the topics or history changed (caller holds ``_lock``)."""
    key = (_topics(settings), settings.ntfy_history)
    if _state["key"] != key:
        _state.update(key=key, items=[], at=0.0, ok=False, thread=None, polled={})
        _load_disk(settings, key)
    return key


def _snapshot(settings: Settings) -> tuple[list[NtfyItem], bool]:
    wait = None
    with _lock:
        key = _ensure_locked(settings)
        live = bool(key[0]) and set(key[0]) <= _state["live"]  # every topic pushes: nothing to poll
        fresh = live or (_state["at"] and time.monotonic() - _state["at"] < settings.ntfy_cache_s)
        if not fresh and _state["thread"] is None:
            th = threading.Thread(target=_refresh, args=(settings, key), daemon=True)
            _state["thread"] = th
            th.start()
            if not _state["at"]:
                wait = th
    if wait is not None:
        wait.join(FIRST_WAIT_S)
    with _lock:
        return list(_state["items"]), bool(_state["ok"])


# ------------------------------------------------------------------ push (open stream)
def _ingest(settings: Settings, topic: str, items: list[NtfyItem]) -> list[NtfyItem]:
    """Merge pushed items; returns the new ones. Also marks the topic as seen alive now."""
    with _lock:
        _ensure_locked(settings)
        _state["polled"][topic] = time.time()
        known = {i.id or f"{i.time}": i for i in _state["items"]}
        new = [i for i in items if (i.id or f"{i.time}") not in known]
        if new:
            known.update({i.id or f"{i.time}": i for i in new})
            _state["items"] = sorted(known.values(), key=lambda i: i.time, reverse=True)[:MAX_ITEMS]
        _state.update(ok=True, at=time.monotonic())
        return new


def _set_live(topic: str, on: bool) -> None:
    with _lock:
        (_state["live"].add if on else _state["live"].discard)(topic)


async def _flush(app, settings: Settings, fresh: list | None = None) -> None:
    """Save the burst, then publish it: the ``on_new`` payloads of ``fresh`` (drained here; the stream keeps
    appending to it), else one plain ``{"ntfy": 1}``. Items that arrive while a round runs (no new flush starts
    meanwhile) get another round."""
    await _sleep(FLUSH_S)
    while True:
        await _flush_round(app, settings, fresh)
        if not fresh:
            return


async def _flush_round(app, settings: Settings, fresh: list | None) -> None:
    with _lock:
        key = _state["key"]
        snap = (list(_state["items"]), dict(_state["polled"]))
    await executors.run_collectors(_save_disk, settings, key, *snap)
    new = list(fresh or ())
    if fresh:
        fresh.clear()
    payloads: list = []
    on_new = getattr(app.state, "ntfy_on_new", None)
    if on_new is not None and new:
        try:
            payloads = list(await on_new(new))
        except Exception as exc:  # noqa: BLE001 - type only; the plain event below still goes out
            log.warning("ntfy live payloads failed: %s", exc.__class__.__name__)
            payloads = []
    hub = getattr(app.state, "hub", None)
    if hub is not None:
        with contextlib.suppress(Exception):  # best effort: the badges also refresh on their own
            for d in payloads or [{"ntfy": 1}]:
                hub.publish("inbox", d)


async def _read_stream(app, settings: Settings, topic: str) -> None:
    with _lock:
        _ensure_locked(settings)
        last = _state["polled"].get(topic)
    since = str(int(last - OVERLAP_S)) if last else _since(settings.ntfy_history)
    kw: dict = {"timeout": httpx.Timeout(TIMEOUT_S, read=STREAM_READ_S), "follow_redirects": False}
    if _transport is not None:
        kw["transport"] = _transport
    else:
        kw["verify"] = notify._ssl_context()
    pending: asyncio.Task | None = None
    fresh: list[NtfyItem] = []  # new since the last flush (drained by _flush)
    try:
        async with httpx.AsyncClient(**kw) as c, c.stream("GET", topic.rstrip("/") + "/json",
                                                           params={"since": since}) as r:
            r.raise_for_status()
            _set_live(topic, True)
            async for ln in r.aiter_lines():
                item = _parse_line(ln) if ln.strip() else None
                new = _ingest(settings, topic, [item] if item else [])
                fresh.extend(new)
                if new and (pending is None or pending.done()):
                    pending = asyncio.create_task(_flush(app, settings, fresh), name="pm-ntfy-flush")
    except asyncio.CancelledError:
        if pending is not None:
            pending.cancel()  # shutdown: drop the flush
        raise
    finally:
        if pending is not None and not pending.cancelled():
            await pending  # stream ended on its own: still save what it delivered


async def _stream_loop(app, settings: Settings, topic: str) -> None:
    delay = 1.0
    while True:
        started = time.monotonic()
        try:
            await _read_stream(app, settings, topic)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - type only, never message text
            log.warning("ntfy stream failed: %s", exc.__class__.__name__)
        _set_live(topic, False)
        delay = 1.0 if time.monotonic() - started > 30 else min(delay * 2, BACKOFF_MAX_S)
        await _sleep(delay)


def start(app, settings: Settings | None = None, on_new=None) -> list[asyncio.Task]:
    """Open the push streams. ``on_new``: async ``(new items) -> [inbox payload]`` (see the module doc)."""
    settings = settings or app.state.settings
    app.state.ntfy_on_new = on_new
    topics = _topics(settings) if settings.ntfy_stream else ()
    app.state.ntfy_tasks = [asyncio.create_task(_stream_loop(app, settings, t), name="pm-ntfy-stream")
                            for t in topics]
    return app.state.ntfy_tasks


async def stop(app) -> None:
    tasks = getattr(app.state, "ntfy_tasks", None) or []
    for t in tasks:
        t.cancel()
    for t in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await t
    app.state.ntfy_tasks = []
    with _lock:
        _state["live"].clear()


def _match(item: NtfyItem, paths: set[str]) -> NtfyItem:
    """``item`` with ``project`` = the longest project path (``paths``: without trailing "/") its path tag is in,
    else the tag itself. Walks the tag's parents (one set lookup each), not the project list."""
    path = next((t for t in item.tags if t.startswith("/")), None)
    if path is None:
        return item
    norm = path.rstrip("/") or "/"
    cand = norm
    while True:
        if cand in paths:
            return dataclasses.replace(item, project=cand)
        if not cand:
            return dataclasses.replace(item, project=norm)
        cand = cand.rpartition("/")[0]


_matched_memo = LRU(max_entries=4)  # (message ids, project paths) -> matched items: one match pass per feed change


def _matched(settings: Settings) -> list[NtfyItem]:
    """The feed without muted messages, each with its project; memoised on the message ids and project paths, so
    several lists and counts in one request (and the requests until the next message) share one pass."""
    items, _ = _snapshot(settings)
    items = mutes.drop_muted(settings, items)
    paths = frozenset(p.path.rstrip("/") for p in projects.discover(settings))
    key = (tuple((i.id, i.time) for i in items), paths)
    hit = _matched_memo.get(key)
    if hit is None:
        hit = [_match(i, paths) for i in items]
        _matched_memo.put(key, hit)
    return list(hit)


def signature(settings: Settings) -> tuple[int, str]:
    """(message count, newest message id) of the feed: changes when a message arrives (cache keys)."""
    items, _ = _snapshot(settings)
    return len(items), (items[0].id if items else "")


def recent(settings: Settings, *, limit: int | None = None,
           project_path: str | None = None) -> list[NtfyItem]:
    items = _matched(settings)
    if project_path is not None:
        want = str(Path(project_path)).rstrip("/")
        items = [i for i in items if i.project == want]
    return items if limit is None else items[: max(0, limit)]


def counts_by_project(settings: Settings) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in _matched(settings):
        if i.project:
            out[i.project] = out.get(i.project, 0) + 1
    return out


def fetch_ok(settings: Settings) -> bool:
    return _snapshot(settings)[1]


def collect_ntfy(settings: Settings, budget: float = 1.0) -> tuple[bool | None, int | None]:
    """(mirror reachable, messages in the last 24 h); (None, None) when unavailable.

    Guarded, run in a daemon thread so the servers panel never waits more than ``budget``.
    """
    box: list[tuple[bool | None, int | None]] = []

    def work() -> None:
        try:
            ok = bool(fetch_ok(settings))
            cutoff = time.time() - 86400
            n = sum(1 for i in recent(settings) if i.time >= cutoff) if ok else None
            box.append((ok, n))
        except Exception:  # noqa: BLE001
            pass

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(budget)
    return box[0] if box else (None, None)
