"""Dedicated, bounded thread pools: one per class of blocking work.

Nothing blocking runs on the event loop or on asyncio's shared default executor.
Slow collectors (docker, virsh, hosts, ntfy) can therefore never starve page
handlers, and the snapshot builder is always a single thread (never overlapping
builds). Pools are module-level and live for the process.

* ``snapshot``   1 thread   the only place ``services.build_snapshot`` runs
* ``collectors`` 4 threads  docker / virsh / hosts / systemd / ntfy / alerts / notify
* ``tmux``       4 threads  pane / history / terminal / launch / kill helpers
* ``fanout``     8 threads  leaf work *submitted by* the snapshot build (git status, classify)
* ``panel``      6 threads  the concurrent sections of one servers-panel refresh
* ``hosts``      8 threads  TCP reachability probes (leaf work, 1 s timeout each)
* ``jobs``       2 threads  long user-started steps (New project plugin steps: compose up, VM create, ...)
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

SIZES = {"snapshot": 1, "collectors": 4, "tmux": 4, "fanout": 8, "panel": 6, "hosts": 8, "jobs": 2}

snapshot = ThreadPoolExecutor(SIZES["snapshot"], thread_name_prefix="pm-snapshot")
collectors = ThreadPoolExecutor(SIZES["collectors"], thread_name_prefix="pm-collectors")
tmux = ThreadPoolExecutor(SIZES["tmux"], thread_name_prefix="pm-tmux")
fanout = ThreadPoolExecutor(SIZES["fanout"], thread_name_prefix="pm-fanout")

panel = ThreadPoolExecutor(SIZES["panel"], thread_name_prefix="pm-panel")

hosts = ThreadPoolExecutor(SIZES["hosts"], thread_name_prefix="pm-hosts")

jobs = ThreadPoolExecutor(SIZES["jobs"], thread_name_prefix="pm-jobs")

POOLS = {"snapshot": snapshot, "collectors": collectors, "tmux": tmux, "fanout": fanout, "panel": panel, "hosts": hosts,
         "jobs": jobs}


def run(pool: ThreadPoolExecutor, fn: Callable[..., Any], *args: Any, **kw: Any) -> asyncio.Future:
    """Awaitable: run ``fn`` on ``pool`` (not the loop's default executor) in a copy of the caller's context
    (``run_in_executor`` does not copy contextvars, e.g. the request's UI theme)."""
    ctx = contextvars.copy_context()
    return asyncio.get_running_loop().run_in_executor(pool, functools.partial(ctx.run, fn, *args, **kw))


def run_snapshot(fn: Callable[..., Any], *args: Any, **kw: Any) -> asyncio.Future:
    return run(snapshot, fn, *args, **kw)


def run_collectors(fn: Callable[..., Any], *args: Any, **kw: Any) -> asyncio.Future:
    return run(collectors, fn, *args, **kw)


def run_tmux(fn: Callable[..., Any], *args: Any, **kw: Any) -> asyncio.Future:
    return run(tmux, fn, *args, **kw)


def run_jobs(fn: Callable[..., Any], *args: Any, **kw: Any) -> asyncio.Future:
    return run(jobs, fn, *args, **kw)


def sizes() -> dict[str, int]:
    return {name: pool._max_workers for name, pool in POOLS.items()}  # noqa: SLF001
