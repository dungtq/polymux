"""Find coding agents under tmux panes via /proc and correlate Claude session json.

Only ``<sessions_dir>/<pid>.json`` is ever opened there. Never environ, .key, .env.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pm.agents import subagents
from pm.chat import sources as chat_sources
from pm.chat.sources import devin as devin_src
from pm.chat.sources.claude import load_session, transcript_path
from pm.chat.sources.common import mtime
from pm.chat.sources.lastresp import last_response_ts
from pm.core.models import Agent, AgentKind, AgentState, TmuxPane
from pm.core.procstat import PROC, ProcStat, parse_stat, sample_once

from .state import map_claude_status

log = logging.getLogger(__name__)

_KINDS = {
    "claude": AgentKind.CLAUDE,
    "codex": AgentKind.CODEX,
    "devin": AgentKind.DEVIN,
    "cursor-agent": AgentKind.CURSOR,
    "agent": AgentKind.CURSOR,
    "agy": AgentKind.ANTIGRAVITY,
}
_INTERPRETERS = {"node", "bun", "deno", "python", "python3", "sh", "bash", "env"}
_AUTH_SUBCMDS = {"auth", "login", "logout", "models", "-p", "--print"}  # login/logout/status/print runs of an agent CLI


def _read(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return None


def _read_small(path: str) -> str | None:
    """One open/read/close on a str path: ~4x cheaper than ``Path.read_text`` for /proc files."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return None
    try:
        return os.read(fd, 8192).decode("utf-8", "replace")
    except OSError:
        return None
    finally:
        os.close(fd)


def _scan(proc_root: Path) -> dict[int, ProcStat]:
    out: dict[int, ProcStat] = {}
    root = str(proc_root)
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for n in names:
        if not n.isdigit():
            continue
        raw = _read_small(f"{root}/{n}/stat")
        st = parse_stat(raw) if raw else None
        if st:
            out[st.pid] = st
    return out


class _FullView:
    """Every process, from one scan of /proc (used for fake roots and kernels without
    ``task/*/children``)."""

    def __init__(self, proc_root: Path):
        self.procs = _scan(proc_root)
        self.kids: dict[int, list[int]] = {}
        for p in self.procs.values():
            self.kids.setdefault(p.ppid, []).append(p.pid)

    def stat(self, pid: int) -> ProcStat | None:
        return self.procs.get(pid)

    def children(self, pid: int) -> list[int]:
        return self.kids.get(pid, [])


class _LazyView:
    """Only the processes below the panes: ``/proc/<pid>/task/<tid>/children`` walks the tree
    from the pane pids instead of reading the stat file of every process on the host."""

    def __init__(self, proc_root: Path):
        self.root = str(proc_root)
        self._stats: dict[int, ProcStat | None] = {}

    def stat(self, pid: int) -> ProcStat | None:
        if pid not in self._stats:
            raw = _read_small(f"{self.root}/{pid}/stat")
            self._stats[pid] = parse_stat(raw) if raw else None
        return self._stats[pid]

    def children(self, pid: int) -> list[int]:
        out: list[int] = []
        try:
            tids = os.listdir(f"{self.root}/{pid}/task")
        except OSError:
            return out
        for tid in tids:
            raw = _read_small(f"{self.root}/{pid}/task/{tid}/children")
            if raw:
                out.extend(int(x) for x in raw.split() if x.isdigit())
        return out


def _proc_view(proc_root: Path, panes: Sequence[TmuxPane]):
    if proc_root == PROC and panes:
        probe = panes[0].pid
        if os.path.exists(f"{proc_root}/{probe}/task/{probe}/children"):
            return _LazyView(proc_root)
    return _FullView(proc_root)


# (pid, starttime, comm) -> kind for the REAL /proc only (fake roots in tests are never cached).
# A pid never changes its argv/exe without changing comm or starttime in practice.
_KIND_CACHE: dict[tuple[int, int, str], AgentKind | None] = {}
_KIND_CACHE_MAX = 20000


def proc_argv(pid: int, proc_root: Path = PROC) -> list[str]:
    """The non-empty argv items of ``/proc/<pid>/cmdline``, [] when unreadable."""
    raw = _read(proc_root / str(pid) / "cmdline")
    return [a for a in (raw or "").split("\0") if a]


def _kind_of(pid: int, st: ProcStat, proc_root: Path) -> AgentKind | None:
    if proc_root != PROC:
        return _kind_uncached(pid, st, proc_root)
    key = (pid, st.starttime, st.comm)
    try:
        return _KIND_CACHE[key]
    except KeyError:
        pass
    kind = _kind_uncached(pid, st, proc_root)
    if len(_KIND_CACHE) >= _KIND_CACHE_MAX:
        _KIND_CACHE.clear()
    _KIND_CACHE[key] = kind
    return kind


def _kind_uncached(pid: int, st: ProcStat, proc_root: Path) -> AgentKind | None:
    names: list[str] = []
    try:
        names.append(os.path.basename(os.readlink(proc_root / str(pid) / "exe")))
    except OSError:
        pass
    argv = proc_argv(pid, proc_root)
    if argv:
        names.append(os.path.basename(argv[0]))
        exe_at = 0
        # interpreter wrapper: `node /path/to/codex ...`
        if os.path.basename(argv[0]) in _INTERPRETERS or names[-1].startswith("node"):
            for i, a in enumerate(argv[1:3], 1):
                if not a.startswith("-"):
                    names.append(os.path.basename(a))
                    exe_at = i
                    break
        if argv[exe_at + 1:exe_at + 2] and argv[exe_at + 1] in _AUTH_SUBCMDS:
            return None  # `claude auth login`, `codex login`, ...: not a running agent
    names.append(st.comm)
    for n in names:
        if n in _KINDS:
            return _KINDS[n]
    return None


def _store_ident(f: Path) -> str | None:
    """Session id of a non-Claude store file: its stem, or the directory of a Cursor ``store.db``."""
    name = f.parent.name if f.name.startswith("store.db") else f.stem
    return name if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", name or "") else None


def _ms_to_s(v: object) -> float:
    return float(v) / 1000.0 if isinstance(v, int | float) and not isinstance(v, bool) and v > 0 else 0.0


def _find_agent(view, pane_pid: int, proc_root: Path) -> tuple[int, AgentKind] | None:
    """BFS below the pane process: the shallowest agent process wins."""
    queue = [pane_pid]
    seen = {pane_pid}
    while queue:
        pid = queue.pop(0)
        pst = view.stat(pid)
        kind = _kind_of(pid, pst, proc_root) if pst is not None else None
        if kind is not None:
            return pid, kind
        for c in sorted(view.children(pid)):
            if c not in seen:
                seen.add(c)
                queue.append(c)
    return None


@dataclass
class _Session:
    """What the Claude session json says (defaults = unknown / not Claude)."""

    cwd: str
    state: AgentState = AgentState.UNKNOWN
    since: float = 0.0
    conf: float = 0.0
    uuid: str | None = None
    active: float = 0.0


def _claude_session(sessions_dir: Path, pid: int, st: ProcStat | None, cwd: str) -> _Session:
    out = _Session(cwd)
    sess = load_session(sessions_dir, pid, st)
    if not sess:
        return out
    out.state = map_claude_status(sess.get("status"))
    out.conf = 1.0 if out.state is not AgentState.UNKNOWN else 0.0
    ts = sess.get("statusUpdatedAt") or sess.get("updatedAt") or 0
    out.since = float(ts) / 1000.0 if isinstance(ts, int | float) else 0.0
    out.active = max(_ms_to_s(sess.get("statusUpdatedAt")), _ms_to_s(sess.get("updatedAt")))
    sid = sess.get("sessionId")
    out.uuid = sid if isinstance(sid, str) else None
    if isinstance(sess.get("cwd"), str) and sess["cwd"]:
        out.cwd = sess["cwd"]
    return out


class Locks:
    """Devin lock map, read at most once per pass (``detect_agents``, the Inbox rows) and only when a Devin shows up."""

    def __init__(self, home: Path, proc_root: Path):
        self.home, self.proc_root = home, proc_root
        self.value: dict[int, list[str]] | None = None

    def for_kind(self, kind: AgentKind) -> dict[int, list[str]] | None:
        if kind is AgentKind.DEVIN and self.value is None:
            self.value = devin_src.lock_map(self.home, self.proc_root)
        return self.value


def _session_activity(kind: AgentKind, pid: int, s: _Session, age: float, home: Path, proc_root: Path,
                      locks: Locks) -> tuple[float, float, str, str | None, AgentState]:
    """(active, done, transcript path, ident, state) from the agent's own session file. Best effort:
    on an error the values found so far are kept."""
    active, done, tfile, ident, state = s.active, 0.0, "", None, s.state
    try:
        if kind is AgentKind.CLAUDE:
            f = transcript_path(s.cwd, s.uuid or "", home)
        else:
            f = chat_sources.session_file(kind, pid=pid, cwd=s.cwd, age_s=age, home=home,
                                          proc_root=proc_root, locks=locks.for_kind(kind))
        active = max(active, mtime(f))
        done = last_response_ts(kind, f)
        if f is not None:
            tfile = str(f)
            ident = s.uuid if kind is AgentKind.CLAUDE else _store_ident(f)
        # Claude ends the parent turn right after launching background sub-agents (session json "idle")
        # while they run, also through a long quiet tool call of theirs: not finished yet
        idle_claude = kind is AgentKind.CLAUDE and state is AgentState.IDLE and f is not None
        if idle_claude and subagents.running_for(str(f)) > 0:
            state = AgentState.RUNNING
    except Exception as exc:  # noqa: BLE001 - activity is best effort
        log.warning("session file stat failed: %s", type(exc).__name__)
    return active, done, tfile, ident, state


def _merge_window_activity(active: float, pane: TmuxPane, window_activity: Mapping[str, float] | None) -> float:
    """tmux activity of the pane (``%n``) and its window (``@n``) only raises ``active``."""
    if not window_activity:
        return active
    for key in (pane.id, pane.window_id):
        v = window_activity.get(key)
        if isinstance(v, int | float) and v > 0:
            active = max(active, float(v))
    return active


def _make_agent(pane: TmuxPane, kind: AgentKind, pid: int, s: _Session, res, activity) -> Agent:
    cpu, rss, age = res
    active, done, tfile, ident, state = activity
    digits = "".join(ch for ch in pane.id if ch.isdigit())
    return Agent(
        id=f"{kind.value}-{digits}", kind=kind, pane_id=pane.id, session_id=pane.session_id, pid=pid,
        cwd=s.cwd, state=state, state_since=s.since, confidence=s.conf, session_uuid=s.uuid,
        ident=ident or s.uuid, transcript=tfile, cpu_pct=cpu, rss_mb=rss, age_s=age,
        last_active=active, done_at=done,
    )


def detect_agents(
    panes: Sequence[TmuxPane],
    *,
    sessions_dir: Path = Path("~/.claude/sessions").expanduser(),
    proc_root: Path = PROC,
    window_activity: Mapping[str, float] | None = None,
    home: Path | None = None,
) -> list[Agent]:
    """Agents found under ``panes``.

    ``Agent.last_active`` (epoch seconds, 0.0 = unknown) is the max of:
    (a) Claude session json ``statusUpdatedAt`` / ``updatedAt`` (ms -> s),
    (b) the mtime of the agent's own session file (Claude transcript, Devin
        transcript json, Codex rollout, Cursor store.db or its -wal): stat only,
    (c) ``window_activity`` (optional, from the caller: tmux
        ``#{window_activity}`` keyed by pane id (``%n``) and/or window id
        (``@n``) -> epoch s); both keys are looked up when present.
    It is derived only from files and tmux, never from ``time.time()``, so a pm
    restart cannot reset it. ``home`` overrides ``~`` for the session stores.
    """
    home = home or Path.home()
    # an exited pane whose window is kept (remain-on-exit) is never a live agent, even if its pid was reused
    panes = [p for p in panes if not p.dead]
    locks = Locks(home, proc_root)
    view = _proc_view(proc_root, panes)
    agents: list[Agent] = []
    for pane in panes:
        found = _find_agent(view, pane.pid, proc_root) if view.stat(pane.pid) is not None else None
        if found is None:
            continue
        pid, kind = found
        s = (_claude_session(sessions_dir, pid, view.stat(pid), pane.cwd) if kind is AgentKind.CLAUDE
             else _Session(pane.cwd))
        res = sample_once(pid, proc_root=proc_root) or (0.0, 0.0, 0.0)
        active, done, tfile, ident, state = _session_activity(kind, pid, s, res[2], home, proc_root, locks)
        active = _merge_window_activity(active, pane, window_activity)
        agents.append(_make_agent(pane, kind, pid, s, res, (active, done, tfile, ident, state)))
    return agents
