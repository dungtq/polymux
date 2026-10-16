"""Event-loop watchdog.

An asyncio task stamps a heartbeat every ``BEAT_S``; a plain daemon thread (it never
keeps the process alive) looks at the stamp every ``CHECK_S``. When the loop has not run
for ``stall_log_s`` it logs ONE line (at most one per ``LOG_EVERY_S``) with the stalled
seconds, the loop thread's stack and the top frames of busy worker threads. When the stall
reaches ``exit_s`` (0 disables) it logs and ``os._exit(3)`` so systemd restarts the unit.

Stacks are function names and ``file:line`` only: no locals, no argument values, no source.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import threading
import time
import traceback
from collections.abc import Callable

log = logging.getLogger("pm.watchdog")

BEAT_S = 0.25
CHECK_S = 0.5
LOG_EVERY_S = 10.0
MAX_FRAMES = 14
MAX_THREADS = 5
EXIT_CODE = 3
_IDLE_FILES = ("threading.py", "queue.py", "selectors.py", "thread.py")


def _short(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    return "/".join(parts[-2:])


def format_stack(frame, limit: int = MAX_FRAMES) -> str:
    """``outer>...>inner`` as ``file:line:func``; innermost ``limit`` frames, no locals/source."""
    try:
        stack = traceback.StackSummary.extract(traceback.walk_stack(frame), limit=limit,
                                               lookup_lines=False, capture_locals=False)
    except Exception:  # noqa: BLE001
        return "?"
    return ">".join(f"{_short(f.filename)}:{f.lineno}:{f.name}" for f in reversed(stack))


def busy_threads(frames: dict[int, object], skip: set[int], names: dict[int, str],
                 limit: int = MAX_THREADS) -> list[str]:
    """Top frames of worker threads that are doing something (not parked in a pool queue)."""
    out: list[str] = []
    for ident, frame in frames.items():
        if ident in skip:
            continue
        code = getattr(frame, "f_code", None)
        if code is None or code.co_filename.endswith(_IDLE_FILES):
            continue
        out.append(f"{names.get(ident, ident)}={format_stack(frame, 3)}")
        if len(out) >= limit:
            break
    return out


class Watchdog:
    def __init__(self, stall_log_s: float = 2.0, exit_s: float = 30.0, *,
                 clock: Callable[[], float] = time.monotonic,
                 exit_fn: Callable[[int], None] = os._exit,
                 frames_fn: Callable[[], dict] = sys._current_frames,
                 names_fn: Callable[[], dict[int, str]] | None = None,
                 logger: logging.Logger = log) -> None:
        self.stall_log_s = float(stall_log_s)
        self.exit_s = float(exit_s)
        self.clock = clock
        self.exit_fn = exit_fn
        self.frames_fn = frames_fn
        self.names_fn = names_fn or (lambda: {t.ident: t.name for t in threading.enumerate()})
        self.log = logger
        self.loop_ident: int | None = None
        self._last = clock()
        self._lag_ms = 0.0
        self._last_log = float("-inf")
        self._stalled = False
        self._worst = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------ loop side
    def beat(self, lag_s: float = 0.0) -> None:
        self._last = self.clock()
        self._lag_ms = max(0.0, lag_s) * 1000.0

    @property
    def stalled_s(self) -> float:
        return max(0.0, self.clock() - self._last - BEAT_S)

    @property
    def loop_lag_ms(self) -> float:
        """Larger of the last measured wake-up lag and how overdue the next heartbeat is."""
        return round(max(self._lag_ms, self.stalled_s * 1000.0), 1)

    async def _beater(self) -> None:
        while True:
            t0 = self.clock()
            await asyncio.sleep(BEAT_S)
            self.beat(self.clock() - t0 - BEAT_S)

    # ------------------------------------------------------------ thread side
    def check(self) -> str:
        """One decision step: 'ok', 'log' or 'exit' (the effects are performed here)."""
        now = self.clock()
        stalled = max(0.0, now - self._last - BEAT_S)
        if stalled < self.stall_log_s:
            if self._stalled:
                self._stalled = False
                self.log.warning("event loop recovered after %.1fs stall", self._worst)
                self._worst = 0.0
            return "ok"
        self._stalled = True
        self._worst = max(self._worst, stalled)
        exiting = self.exit_s > 0 and stalled >= self.exit_s
        if exiting or now - self._last_log >= LOG_EVERY_S:
            self._last_log = now
            self.log.error("event loop stalled %.1fs%s | main: %s | busy threads: %s",
                           stalled, " (exiting)" if exiting else "", *self._describe())
        if exiting:
            self._flush()
            self.exit_fn(EXIT_CODE)
            return "exit"
        return "log"

    def _describe(self) -> tuple[str, str]:
        try:
            frames = self.frames_fn()
            main = frames.get(self.loop_ident)
            names = self.names_fn()
            skip = {i for i in (self.loop_ident, threading.get_ident()) if i is not None}
            stack = format_stack(main) if main is not None else "?"
            return stack, "; ".join(busy_threads(frames, skip, names)) or "none"
        except Exception:  # noqa: BLE001 - diagnostics must never raise
            return "?", "?"

    def _flush(self) -> None:
        for h in logging.getLogger().handlers + self.log.handlers:
            with contextlib.suppress(Exception):
                h.flush()

    def _run(self) -> None:
        while not self._stop.wait(CHECK_S):
            try:
                self.check()
            except Exception:  # noqa: BLE001 - the watchdog itself must survive
                pass

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        """Call from the event loop (lifespan)."""
        self.loop_ident = threading.get_ident()
        self._last = self.clock()
        self._task = asyncio.get_running_loop().create_task(self._beater(), name="pm-watchdog-beat")
        self._thread = threading.Thread(target=self._run, name="pm-watchdog", daemon=True)
        self._thread.start()

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
