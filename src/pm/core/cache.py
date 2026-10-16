"""Tiny in-process caches for the Files pane: a byte/entry bounded ``LRU`` and a stale-while-revalidate ``SWR``.

No external deps, thread-safe, injectable clock/executor for tests. Errors are never cached: a failing
compute raises to the caller (cold) or keeps the old value (background refresh).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any


def _sizeof(v: Any) -> int:
    if isinstance(v, str):
        return len(v)
    if isinstance(v, (tuple, list)):
        return sum(_sizeof(x) for x in v) + 8
    if isinstance(v, dict):
        return sum(_sizeof(k) + _sizeof(x) for k, x in v.items()) + 8
    return 16


class LRU:
    """Least-recently-used map bounded by total approximate bytes and by entry count."""

    def __init__(self, max_bytes: int = 32 * 1024 * 1024, max_entries: int = 256, sizeof: Callable[[Any], int] = _sizeof):
        self.max_bytes, self.max_entries, self._sizeof = max_bytes, max_entries, sizeof
        self._d: OrderedDict[Hashable, tuple[Any, int]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key: Hashable, default: Any = None) -> Any:
        with self._lock:
            it = self._d.get(key)
            if it is None:
                return default
            self._d.move_to_end(key)
            return it[0]

    def put(self, key: Hashable, value: Any) -> None:
        n = self._sizeof(value)
        with self._lock:
            old = self._d.pop(key, None)
            if old:
                self._bytes -= old[1]
            if n > self.max_bytes:  # never cache what cannot fit
                return
            self._d[key] = (value, n)
            self._bytes += n
            while self._d and (self._bytes > self.max_bytes or len(self._d) > self.max_entries):
                _, (_, sz) = self._d.popitem(last=False)
                self._bytes -= sz

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
            self._bytes = 0

    def __len__(self) -> int:
        return len(self._d)

    def __contains__(self, key: Hashable) -> bool:
        with self._lock:
            return key in self._d

    @property
    def nbytes(self) -> int:
        return self._bytes


def _thread_executor(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True, name="pm-swr").start()


class SWR:
    """Stale-while-revalidate over an LRU: age < fresh serves directly; fresh..stale serves the cached value and
    refreshes in the background (one in flight per key); older or missing computes synchronously."""

    def __init__(self, max_entries: int = 200, clock: Callable[[], float] = time.monotonic,
                 executor: Callable[[Callable[[], None]], None] = _thread_executor, max_bytes: int = 64 * 1024 * 1024):
        self._lru = LRU(max_bytes, max_entries)
        self._clock, self._exec = clock, executor
        self._inflight: set[Hashable] = set()
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)

    def get(self, key: Hashable, compute: Callable[[], Any], fresh: float, stale: float) -> Any:
        hit = self._lru.get(key)
        if hit is not None:
            val, at = hit
            age = self._clock() - at
            if age < fresh:
                return val
            if age < stale:
                self._refresh(key, compute)
                return val
        val = compute()  # errors propagate and are not cached
        self._lru.put(key, (val, self._clock()))
        return val

    def _refresh(self, key: Hashable, compute: Callable[[], Any]) -> None:
        with self._lock:
            if key in self._inflight:
                return
            self._inflight.add(key)

        def run() -> None:
            try:
                self._lru.put(key, (compute(), self._clock()))
            except Exception:  # noqa: BLE001 - keep the old value
                pass
            finally:
                with self._idle:
                    self._inflight.discard(key)
                    self._idle.notify_all()

        try:
            self._exec(run)
        except Exception:  # noqa: BLE001 - could not start a thread: drop the flag
            with self._idle:
                self._inflight.discard(key)

    def wait(self, timeout: float = 5.0) -> bool:
        """Block until no refresh is in flight (tests)."""
        with self._idle:
            return self._idle.wait_for(lambda: not self._inflight, timeout)

    def clear(self) -> None:
        self._lru.clear()
