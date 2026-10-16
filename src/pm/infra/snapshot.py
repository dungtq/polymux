"""Read-only snapshot of tmux, agents, projects, git and stacks for the pages.

``build_snapshot`` blocks (subprocess + /proc); callers use ``asyncio.to_thread``.
Pane text only enters through ``security.mask.render_pane`` and is never logged
or stored on the snapshot.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import math
import threading
import time
from dataclasses import dataclass, field, replace

from pm import agents as agents_mod
from pm.agents import labels
from pm.core import executors
from pm.core.config import Settings
from pm.core.models import Agent, AgentState, Project, TmuxSession
from pm.git import ctl as gitctl
from pm.infra import stacks
from pm.projects import projects
from pm.security.mask import render_pane, strip_ansi
from pm.tmux import ctl as tmuxctl

log = logging.getLogger(__name__)

SNAPSHOT_TTL_S = 2.0
SLOW_TTL_S = 30.0  # git / compose / TODO cost ~10 subprocesses per project: refresh rarely ...
SLOW_TTL_IDLE_S = 300.0  # ... and almost never while nobody is looking
SLOW_MIN_PER_BUILD = 4  # at most this many (plus a share of the backlog) refreshed per build
PROJ_TTL_S = 30.0  # cwd -> project memo
CLASSIFY_LINES = 40
CLASSIFY_TTL_S = 10.0  # UNKNOWN agents: reuse the pane classification while its window is quiet
IDLE_FRESH_S = 2.5  # page staleness tolerated after a quiet minute (poll loop sleeps longer)

_ORDER = {
    AgentState.WAITING: 0,
    AgentState.RUNNING: 1,
    AgentState.FAILED: 2,
    AgentState.IDLE: 2,
    AgentState.UNKNOWN: 3,
}


@dataclass(frozen=True)
class Snapshot:
    ts: float
    sessions: tuple[TmuxSession, ...] = ()
    agents: tuple[Agent, ...] = ()
    projects: tuple[Project, ...] = ()
    errors: tuple[str, ...] = field(default=())
    activity: dict = field(default_factory=dict)  # tmux window id -> last activity epoch

    def agent(self, agent_id: str) -> Agent | None:
        return next((a for a in self.agents if a.id == agent_id), None)

    def project(self, name: str) -> Project | None:
        return next((p for p in self.projects if p.name == name), None)

    def session(self, session_id: str) -> TmuxSession | None:
        return next((s for s in self.sessions if s.id == session_id), None)


@dataclass
class _State:
    settings: Settings
    snap: Snapshot | None = None
    build_lock: threading.Lock = field(default_factory=threading.Lock)
    slow: dict[str, tuple[float, tuple]] = field(default_factory=dict)
    cls: dict[str, tuple[float, float, AgentState, float]] = field(default_factory=dict)
    proj: dict[str, tuple[float, str | None]] = field(default_factory=dict)
    proj_key: tuple = ()
    last_build: float = 0.0


_states: dict[int, _State] = {}
_states_lock = threading.Lock()

# Demand tracking: the poll loop slows down when nobody asked for data for IDLE_AFTER_S.
IDLE_AFTER_S = 60.0
_demand = [time.monotonic()]
_build_count = [0]  # monotonic counter of real builds (tests, /healthz diagnostics)


def touch() -> None:
    """Somebody (page, SSE, API) wants fresh data: the poll loop returns to its fast cadence."""
    _demand[0] = time.monotonic()


def demand_age_s() -> float:
    return time.monotonic() - _demand[0]


def is_idle() -> bool:
    return demand_age_s() >= IDLE_AFTER_S


def build_count() -> int:
    return _build_count[0]


def _state(settings: Settings) -> _State:
    with _states_lock:
        st = _states.get(id(settings))
        if st is None or st.settings is not settings:
            st = _State(settings)
            _states[id(settings)] = st
        return st


def published(settings: Settings) -> Snapshot | None:
    """The most recent snapshot the producer published (None before the first build)."""
    return _state(settings).snap


def snapshot_age_s(settings: Settings) -> float | None:
    snap = published(settings)
    return None if snap is None else max(0.0, time.time() - snap.ts)


def _refresh_slow(settings: Settings, st: _State, base: list[Project], now: float) -> dict[str, tuple]:
    """git / compose / TODO info per project, refreshed incrementally.

    A cold cache is filled at once (fan-out pool); afterwards only the oldest few expired
    entries are refreshed per build, enough to cycle through every project once per SLOW_TTL_S.
    """
    cache = st.slow
    ttl = SLOW_TTL_IDLE_S if is_idle() else SLOW_TTL_S
    paths = {p.path for p in base}
    for gone in [k for k in cache if k not in paths]:
        del cache[gone]
    missing = [p.path for p in base if p.path not in cache]
    expired = sorted(
        (p.path for p in base if p.path in cache and now - cache[p.path][0] >= ttl),
        key=lambda k: cache[k][0],
    )
    dt = max(1.0, now - st.last_build) if st.last_build else 1.0
    budget = max(SLOW_MIN_PER_BUILD, math.ceil(len(base) * dt / ttl) + 1)
    todo = missing + expired[:budget]
    if todo:
        infos = list(executors.fanout.map(functools.partial(_slow_one, remote=settings.push_remote), todo))
        for path, info in zip(todo, infos):
            cache[path] = (now, info)
    return {k: v[1] for k, v in cache.items()}


def _slow_one(path: str, *, remote: str) -> tuple:
    try:
        git = gitctl.status(path, remote=remote)
    except Exception:  # noqa: BLE001 - one bad repo must not blank the page
        git = None
    try:
        stack = stacks.stack_status(path)
    except Exception:  # noqa: BLE001
        stack = None
    return (git, stack, projects.parse_todo(path))


def _project_of(st: _State, cwd: str, base: list[Project], now: float) -> Project | None:
    """``projects.project_for_cwd`` memoised per cwd (it resolves symlinks and walks parents)."""
    key = tuple(p.path for p in base)
    if st.proj_key != key:
        st.proj.clear()
        st.proj_key = key
    hit = st.proj.get(cwd)
    if hit is not None and now - hit[0] < PROJ_TTL_S:
        path = hit[1]
        return next((p for p in base if p.path == path), None) if path else None
    p = projects.project_for_cwd(cwd, base)
    st.proj[cwd] = (now, p.path if p else None)
    return p


def _classify_one(settings: Settings, a: Agent) -> tuple[AgentState, float] | None:
    try:
        text = strip_ansi(tmuxctl.capture_pane(settings, a.pane_id, CLASSIFY_LINES))
    except tmuxctl.TmuxError:
        return None
    return agents_mod.classify_pane_text(text, a.kind)  # text is not kept or masked: never leaves


def _classify_all(settings: Settings, st: _State, found: list[Agent], panes, win: dict[str, float],
                  now: float) -> list[Agent]:
    """Pane-text classification of UNKNOWN agents: cached per pane while its window is quiet,
    the rest captured concurrently (one tmux call each)."""
    win_of = {p.id: p.window_id for p in panes}
    todo: list[Agent] = []
    out: dict[str, Agent] = {}
    for a in found:
        if a.state is not AgentState.UNKNOWN:
            continue
        act = win.get(win_of.get(a.pane_id, ""), 0.0)
        hit = st.cls.get(a.pane_id)
        if hit and hit[1] == act and now - hit[0] < CLASSIFY_TTL_S:
            out[a.id] = replace(a, state=hit[2], confidence=hit[3])
        else:
            todo.append(a)
    if todo:
        res = list(executors.fanout.map(lambda a: _classify_one(settings, a), todo))
        for a, r in zip(todo, res):
            if r is None:
                continue
            state, conf = r
            st.cls[a.pane_id] = (now, win.get(win_of.get(a.pane_id, ""), 0.0), state, conf)
            out[a.id] = replace(a, state=state, confidence=conf)
    live = {a.pane_id for a in found}
    for k in [k for k in st.cls if k not in live]:
        del st.cls[k]
    return [out.get(a.id, a) for a in found]


def window_activity(settings: Settings) -> dict[str, float]:
    """Read-only: tmux window id (@n) -> last activity epoch. One tmux call; {} on error."""
    try:
        out = tmuxctl.run(
            settings, ["list-windows", "-a", "-F", "#{session_id} #{window_id} #{window_activity}"]
        )
    except tmuxctl.TmuxError:
        return {}
    res: dict[str, float] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 3 or not parts[1].startswith("@"):
            continue
        try:
            res[parts[1]] = float(parts[2])
        except ValueError:
            continue
    return res


def _fill_activity(found: list[Agent], panes, win: dict[str, float]) -> list[Agent]:
    """tmux fallback: only where the detector left last_active unknown (0)."""
    win_of = {p.id: p.window_id for p in panes}
    return [
        replace(a, last_active=win[win_of[a.pane_id]])
        if not a.last_active and win.get(win_of.get(a.pane_id, ""), 0) > 0
        else a
        for a in found
    ]


def _build(settings: Settings, now: float) -> Snapshot:
    st = _state(settings)
    errors: list[str] = []
    try:
        # hide pm's own read-only terminal helper sessions (grouped views)
        sessions = tuple(
            s for s in tmuxctl.list_sessions(settings) if not tmuxctl.VIEW_RE.fullmatch(s.name)
        )
    except tmuxctl.TmuxError as e:
        sessions = ()
        errors.append(f"tmux: {e}")
    panes = [p for s in sessions for w in s.windows for p in w.panes]
    try:
        found = agents_mod.detect_agents(panes)
    except Exception as e:  # noqa: BLE001
        found = []
        errors.append(f"agents: {type(e).__name__}")
    win: dict[str, float] = window_activity(settings) if sessions else {}
    if found:
        # applied here (after detection) so it works whatever detect_agents accepts
        found = _fill_activity(found, panes, win)
        found = _classify_all(settings, st, found, panes, win, now)
    base = projects.discover(settings)

    bound: list[Agent] = []
    for a in found:
        p = _project_of(st, a.cwd, base, now)
        bound.append(replace(a, project=p.name if p else None))
    names = labels.label_agents(bound, sessions)
    wnames = labels.window_names(bound, sessions)
    bound = [replace(a, label=names.get(a.id, ""), window_name=wnames.get(a.id, "")) for a in bound]
    # permanent ids (session identity, persisted); a failed tmux / detect round must not end records
    bound = agents_mod.registry.assign(settings, bound, complete=not errors)

    # map each session to the projects its panes sit in: one lookup per distinct cwd
    sess_by_path: dict[str, list[str]] = {}
    pane_by_path: dict[str, list[str]] = {}
    for s in sessions:
        for w in s.windows:
            for pane in w.panes:
                if pane.dead:
                    continue  # exited (remain-on-exit): not live, never listed
                hit = _project_of(st, pane.cwd, base, now)
                if hit is not None:
                    pane_by_path.setdefault(hit.path, []).append(pane.id)
                    ids = sess_by_path.setdefault(hit.path, [])
                    if s.id not in ids:
                        ids.append(s.id)

    infos = _refresh_slow(settings, st, base, now)
    st.last_build = now
    by_proj: dict[str, list[str]] = {}
    for a in bound:
        if a.project:
            by_proj.setdefault(a.project, []).append(a.id)

    out: list[Project] = []
    for p in base:
        git, stack, todo = infos.get(p.path, (None, None, None))
        out.append(
            replace(
                p,
                git=git,
                stack=stack,
                todo=todo,
                sessions=tuple(sess_by_path.get(p.path, ())),
                pane_ids=tuple(pane_by_path.get(p.path, ())),
                agent_ids=tuple(by_proj.get(p.name, ())),
            )
        )
    return Snapshot(now, sessions, tuple(bound), tuple(out), tuple(errors), win)


def _do_build(st: _State, settings: Settings) -> Snapshot:
    """One real build + publish. Caller holds ``st.build_lock``."""
    snap = _build(settings, time.time())
    st.snap = snap
    _build_count[0] += 1
    return snap


def publish(settings: Settings, snap: Snapshot) -> None:
    """Install ``snap`` as the latest snapshot (tests, external producers)."""
    _state(settings).snap = snap


def build_and_publish(settings: Settings) -> Snapshot:
    """The producer step (poll loop): one real build, published. Serialised; never on the loop."""
    st = _state(settings)
    with st.build_lock:
        return _do_build(st, settings)


def build_snapshot(settings: Settings, *, force: bool = False) -> Snapshot:
    """Blocking (subprocess + /proc). Reuses the published snapshot younger than SNAPSHOT_TTL_S
    unless ``force``; concurrent callers queue on the build lock and then share the winner's
    result instead of building again. Request handlers use ``latest_snapshot`` instead."""
    st = _state(settings)
    snap = st.snap
    if snap is not None and not force and time.time() - snap.ts < SNAPSHOT_TTL_S:
        return snap
    t0 = time.time()
    with st.build_lock:
        snap = st.snap
        if snap is not None and (snap.ts >= t0 or (not force and time.time() - snap.ts < SNAPSHOT_TTL_S)):
            return snap
        return _do_build(st, settings)


FALLBACK_MAX_AGE_S = 15.0


def effective_max_age(max_age_s: float) -> float:
    """After a quiet minute the poller sleeps longer, so pages accept less staleness."""
    return min(max_age_s, IDLE_FRESH_S) if is_idle() else max_age_s


def latest_snapshot(settings: Settings, max_age_s: float = FALLBACK_MAX_AGE_S) -> Snapshot:
    """Blocking. The last published snapshot when it is at most ``max_age_s`` old (the poll loop
    keeps it fresh); otherwise ONE shared build (single flight). Never builds when fresh."""
    touch()
    st = _state(settings)
    max_age_s = effective_max_age(max_age_s)
    snap = st.snap
    if snap is not None and time.time() - snap.ts <= max_age_s:
        return snap
    t0 = time.time()
    with st.build_lock:
        snap = st.snap
        if snap is not None and (time.time() - snap.ts <= max_age_s or snap.ts >= t0):
            return snap
        return _do_build(st, settings)


# async single flight: all waiters of one event loop share one in-flight future
_inflight: dict[int, tuple[object, asyncio.Future]] = {}


async def snapshot_async(settings: Settings, max_age_s: float = FALLBACK_MAX_AGE_S) -> Snapshot:
    """Awaitable accessor for handlers: returns the published snapshot without any thread hop
    when it is fresh enough; otherwise awaits ONE shared build on the single snapshot thread."""
    touch()
    snap = published(settings)
    max_age_s = effective_max_age(max_age_s)
    if snap is not None and time.time() - snap.ts <= max_age_s:
        return snap
    loop = asyncio.get_running_loop()
    key = id(settings)
    hit = _inflight.get(key)
    if hit is None or hit[0] is not loop or hit[1].done():
        fut = loop.run_in_executor(executors.snapshot, latest_snapshot, settings, max_age_s)
        _inflight[key] = (loop, fut)
        fut.add_done_callback(lambda f, k=key: _inflight.pop(k, None) if _inflight.get(k, (0, 0))[1] is f else None)
    else:
        fut = hit[1]
    return await asyncio.shield(fut)


def inbox(snapshot: Snapshot) -> list[Agent]:
    """Waiting first, then running, then idle/failed (most recent real activity first)."""
    return sorted(
        snapshot.agents,
        key=lambda a: (_ORDER.get(a.state, 3), -(a.last_active or a.state_since), a.id),
    )


def pane_text(settings: Settings, pane_id: str, lines: int = 200) -> str | None:
    """Masked, ANSI-stripped pane text; None when the pane is gone."""
    try:
        return render_pane(tmuxctl.capture_pane(settings, pane_id, lines))
    except tmuxctl.TmuxError:
        return None


def merge_tracker(snapshot: Snapshot, tracker) -> Snapshot:
    """Overlay hook/poll-tracked states (events.AgentTracker) onto a snapshot."""
    if tracker is None:
        return snapshot
    try:
        tracked = {a.id: a for a in tracker.agents}
    except Exception:  # noqa: BLE001 - tracker is best effort
        return snapshot
    merged = []
    for a in snapshot.agents:
        t = tracked.get(a.id)
        if t is not None and t.state is not AgentState.UNKNOWN:
            a = replace(
                a, state=t.state, state_since=t.state_since,
                last_active=max(a.last_active, t.last_active),
            )
        merged.append(a)
    return replace(snapshot, agents=tuple(merged))
