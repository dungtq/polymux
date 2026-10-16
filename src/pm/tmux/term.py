"""Read-only browser terminal: one `tmux attach-session -r` child per connection in a PTY.

The stream is raw terminal bytes and is NEVER logged, indexed, cached or sent
anywhere but the one WebSocket. Nothing the browser sends reaches the PTY: the
master fd is only ever read (and its winsize set).
"""

from __future__ import annotations

import asyncio
import fcntl
import fnmatch
import json
import os
import re
import secrets
import signal
import struct
import subprocess
import termios
import time
from collections import deque
from dataclasses import dataclass, field

from pm.core import executors
from pm.core.config import Settings
from pm.tmux import ctl as tmuxctl

MAX_IN = 4096
READ_CHUNK = 16384
HIGH_WATER = 256 * 1024  # stop reading the PTY above this (tmux is then back-pressured)
LOW_WATER = 64 * 1024
FRAME = 16384
SEND_TIMEOUT_S = 15.0  # a send stalled this long means the client cannot keep up -> 4413
INIT_TIMEOUT_S = 10.0
COLS = (20, 300)
ROWS = (5, 120)
SID_RE = re.compile(r"^s(\d{1,9})$")
WID_RE = re.compile(r"^w(\d{1,9})$")
BACK_RE = re.compile(r"^/(a/[A-Za-z0-9._-]{1,64}|p/[A-Za-z0-9._-]{1,64}|tmux|servers|)$")
BACK_DEFAULT = "/tmux"


def valid_back(back) -> str:
    """Return `back` only if it is an allowed same-origin path, else the default."""
    if isinstance(back, str) and back.isascii() and ".." not in back and BACK_RE.fullmatch(back):
        return back
    return BACK_DEFAULT


def back_label(back: str) -> str:
    return "Back to chat" if back.startswith("/a/") else "Back"


def parse_window(w) -> str | None:
    """URL id `w34` -> `@34`; None for anything else (ignored silently)."""
    m = WID_RE.fullmatch(w) if isinstance(w, str) else None
    return "@" + m.group(1) if m else None


def window_in_session(settings: Settings, tmux_id: str, window_id: str | None) -> str | None:
    """`@N` only if that window belongs to session `tmux_id`."""
    if not window_id:
        return None
    try:
        sessions = tmuxctl.list_sessions(settings)
    except tmuxctl.TmuxError:
        return None
    s = next((x for x in sessions if x.id == tmux_id), None)
    if s is not None and any(w.id == window_id for w in s.windows):
        return window_id
    return None
WINDOW_RATE = 10  # window switches per second, per connection
STALE_VIEW_S = 60.0


class TermError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


def never_attachable(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, g) for g in tmuxctl.ALWAYS_PROTECTED)


def resolve_session(settings: Settings, sid: str) -> tuple[str, str]:
    """URL id `s12` -> (`$12`, name). 404 for unknown, pm*, or pm's own session."""
    m = SID_RE.fullmatch(sid or "")
    if not m:
        raise TermError(404, "unknown session")
    tmux_id = "$" + m.group(1)
    try:
        sessions = tmuxctl.list_sessions(settings)
    except tmuxctl.TmuxError:
        raise TermError(502, "tmux unavailable") from None
    match = next((s for s in sessions if s.id == tmux_id), None)
    if match is None or never_attachable(match.name):
        raise TermError(404, "unknown session")
    pane = os.environ.get("TMUX_PANE")
    if pane and any(p.id == pane for w in match.windows for p in w.panes):
        raise TermError(404, "unknown session")  # pm's own session
    return tmux_id, match.name


def attach_argv(settings: Settings, tmux_id: str) -> list[str]:
    tmuxctl.check_id(tmux_id, "$")
    argv = [settings.tmux_bin]
    if settings.tmux_socket:
        argv += ["-L", settings.tmux_socket]
    return argv + ["attach-session", "-t", tmux_id, "-r"]  # -r = read-only,ignore-size


def clamp_size(cols, rows) -> tuple[int, int] | None:
    if not (isinstance(cols, int) and isinstance(rows, int)) or isinstance(cols, bool) \
            or isinstance(rows, bool):
        return None
    return max(COLS[0], min(COLS[1], cols)), max(ROWS[0], min(ROWS[1], rows))


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


@dataclass
class Limits:
    idle_s: float
    max_s: float
    send_timeout_s: float = SEND_TIMEOUT_S
    init_timeout_s: float = INIT_TIMEOUT_S

    @classmethod
    def from_settings(cls, s: Settings) -> Limits:
        return cls(idle_s=s.terminal_idle_s, max_s=s.terminal_max_s)

    @property
    def ping_s(self) -> float:
        return max(0.2, min(20.0, self.idle_s / 3))

    @property
    def pong_timeout_s(self) -> float:
        return max(3 * self.ping_s, 10.0)


@dataclass
class Result:
    code: int = 1000
    reason: str = ""
    bytes_out: int = 0
    dropped_in: int = 0
    duration_s: float = 0.0


class Outbox:
    """Bounded outbound queue. Binary chunks are coalesced; size is tracked."""

    def __init__(self) -> None:
        self._q: deque[tuple[str, bytes | str]] = deque()
        self.size = 0
        self.event = asyncio.Event()

    def push_bytes(self, data: bytes) -> None:
        self._q.append(("b", data))
        self.size += len(data)
        self.event.set()

    def push_text(self, text: str) -> None:
        self._q.append(("t", text))
        self.event.set()

    def pop(self) -> tuple[str, bytes | str] | None:
        if not self._q:
            self.event.clear()
            return None
        kind, item = self._q.popleft()
        if kind == "t":
            return kind, item
        buf = bytearray(item)  # coalesce following binary chunks up to FRAME
        while self._q and self._q[0][0] == "b" and len(buf) < FRAME:
            buf += self._q.popleft()[1]
        self.size -= len(buf)
        return "b", bytes(buf)

    def __bool__(self) -> bool:
        return bool(self._q)


class Attach:
    """One tmux attach child in a PTY. close() is idempotent and always reaps."""

    def __init__(self, argv: list[str], cols: int, rows: int):
        self.master, slave = os.openpty()
        self.proc: subprocess.Popen | None = None
        try:
            _set_winsize(self.master, cols, rows)
            os.set_blocking(self.master, False)
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                   "HOME": os.environ.get("HOME", "/"),
                   "LANG": os.environ.get("LANG", "C.UTF-8"), "TERM": "xterm-256color"}
            self.proc = subprocess.Popen(  # noqa: S603 - argv list, fixed binary, no shell
                argv, stdin=slave, stdout=slave, stderr=slave, env=env,
                start_new_session=True, close_fds=True, shell=False)
        except BaseException:
            os.close(self.master)
            self.master = -1
            raise
        finally:
            os.close(slave)

    def resize(self, cols: int, rows: int) -> None:
        if self.master >= 0:
            _set_winsize(self.master, cols, rows)
            if self.proc and self.proc.poll() is None:
                self.proc.send_signal(signal.SIGWINCH)

    def kill_now(self) -> None:
        """Synchronous teardown used when the task is being cancelled."""
        if self.master >= 0:
            fd, self.master = self.master, -1
            try:
                os.close(fd)
            except OSError:
                pass
        p = self.proc
        if p is not None and p.poll() is None:
            try:
                p.kill()
            except ProcessLookupError:
                pass
            p.wait()

    async def close(self) -> None:
        if self.master >= 0:
            fd, self.master = self.master, -1
            try:
                os.close(fd)
            except OSError:
                pass
        p = self.proc
        if p is None or p.returncode is not None:
            return
        try:
            try:
                p.send_signal(signal.SIGHUP)
            except ProcessLookupError:
                pass
            for _ in range(50):
                if p.poll() is not None:
                    return
                await asyncio.sleep(0.02)
        finally:  # also on cancellation
            if p.poll() is None:
                try:
                    p.kill()
                except ProcessLookupError:
                    pass
                p.wait()


async def _read_init(ws, limits: Limits, res: Result) -> tuple[int, int] | None:
    """Wait for the init message (it sets the PTY size; nothing is spawned before it)."""
    try:
        msg = await asyncio.wait_for(ws.receive(), limits.init_timeout_s)
    except TimeoutError:
        res.code, res.reason = 4400, "no init"
        return None
    size = None
    if msg.get("type") == "websocket.disconnect":
        res.code, res.reason = 1000, "client closed"
        return None
    if msg.get("text") is not None and len(msg["text"]) <= MAX_IN:
        try:
            d = json.loads(msg["text"])
            if isinstance(d, dict) and d.get("type") == "init":
                size = clamp_size(d.get("cols"), d.get("rows"))
        except ValueError:
            pass
    if size is None:
        res.code, res.reason = 4400, "bad init"
    return size


def _drop_view(settings: Settings, broker: Broker | None, gid: str) -> None:
    """Kill ONLY the grouped session we made; sync, survives cancel."""
    try:
        tmuxctl.kill_view_session(settings, gid)
    except Exception:  # noqa: BLE001 - startup cleanup is the backstop
        pass
    if broker is not None:
        broker.views.discard(gid)
        broker.owners.pop(gid, None)


def _open_view(settings: Settings, tmux_id: str, broker: Broker | None, owner: str | None,
               window_id: str | None, size: tuple[int, int]) -> tuple[str, Attach]:
    """Create this connection's grouped view session and attach to it -> (gid, attach).

    Sync on purpose: a cancel cannot land between creating and recording the ids. The grouped
    session shares the target's windows but keeps its own current window, so prev/next never
    changes what other attached clients see. On failure the view session is dropped here.
    """
    tmuxctl.check_id(tmux_id, "$")
    gid = tmuxctl.new_view_session(settings, tmux_id, "pm-view-" + secrets.token_hex(6))
    try:
        if broker is not None:
            broker.views.add(gid)
            broker.owners[gid] = (owner, tmux_id)
        if window_id:  # open on the requested window (grouped session only)
            tmuxctl.view_open_window(settings, gid, window_id)
        return gid, Attach(attach_argv(settings, gid), *size)
    except BaseException:
        _drop_view(settings, broker, gid)
        raise


@dataclass
class _Conn:
    """State shared by one connection's reader callback and its sender/receiver/watchdog tasks."""

    ws: object
    settings: Settings
    limits: Limits
    res: Result
    still_valid: object
    loop: asyncio.AbstractEventLoop
    t0: float
    gid: str | None = None  # this connection's private grouped session
    attach: Attach | None = None
    reader_on: bool = False
    fd: int = -1
    act: float = 0.0
    pong: float = 0.0
    out: Outbox = field(default_factory=Outbox)
    eof: asyncio.Event = field(default_factory=asyncio.Event)
    switches: deque[float] = field(default_factory=deque)

    def start(self, first_label: dict) -> None:
        self.act = self.pong = time.monotonic()
        self.fd = self.attach.master
        self.loop.add_reader(self.fd, self.on_readable)
        self.reader_on = True
        self.out.push_text(json.dumps(first_label))

    def on_readable(self) -> None:
        try:
            data = os.read(self.fd, READ_CHUNK)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self.loop.remove_reader(self.fd)
            self.reader_on = False
            self.eof.set()
            self.out.event.set()
            return
        self.act = time.monotonic()
        self.out.push_bytes(data)
        if self.out.size >= HIGH_WATER:
            self.loop.remove_reader(self.fd)
            self.reader_on = False

    async def arm(self) -> None:  # crash backstop: tmux drops the group once the client dies
        for _ in range(40):
            if await executors.run_tmux(tmuxctl.arm_view_session, self.settings, self.gid):
                return
            await asyncio.sleep(0.05)

    async def sender(self) -> tuple[int, str]:
        out, attach = self.out, self.attach
        while True:
            item = out.pop()
            if item is None:
                if self.eof.is_set():
                    return 1000, "exited"
                await out.event.wait()
                continue
            kind, payload = item
            try:
                if kind == "b":
                    await asyncio.wait_for(self.ws.send_bytes(payload), self.limits.send_timeout_s)
                    self.res.bytes_out += len(payload)
                else:
                    await asyncio.wait_for(self.ws.send_text(payload), self.limits.send_timeout_s)
            except TimeoutError:
                return 4413, "slow client"
            except Exception:  # noqa: BLE001 - socket gone
                return 1000, "client closed"
            if not self.reader_on and attach.master >= 0 and not self.eof.is_set() \
                    and out.size < LOW_WATER:
                self.loop.add_reader(attach.master, self.on_readable)
                self.reader_on = True

    async def receiver(self) -> tuple[int, str]:
        res = self.res
        while True:
            try:
                m = await self.ws.receive()
            except Exception:  # noqa: BLE001
                return 1000, "client closed"
            if m.get("type") == "websocket.disconnect":
                return 1000, "client closed"
            text, data = m.get("text"), m.get("bytes")
            n = len(text.encode("utf-8", "replace")) if text is not None else len(data or b"")
            if n > MAX_IN:
                res.dropped_in += 1
                return 1009, "frame too large"
            if text is None:  # binary: never forwarded
                res.dropped_in += 1
                continue
            try:
                d = json.loads(text)
            except ValueError:
                d = None
            kind = d.get("type") if isinstance(d, dict) else None
            if kind == "pong":
                self.pong = time.monotonic()
                continue
            if kind == "window" and d.get("dir") in ("next", "prev") and len(d) == 2:
                await self._on_window(d)
                continue
            if kind == "resize" and self._on_resize(d):
                continue
            res.dropped_in += 1
            self.act = time.monotonic()

    async def _on_window(self, d: dict) -> None:
        now = time.monotonic()
        while self.switches and now - self.switches[0] >= 1.0:
            self.switches.popleft()
        if len(self.switches) >= WINDOW_RATE:
            self.res.dropped_in += 1
            return
        self.switches.append(now)
        self.act = now
        try:
            info = await executors.run_tmux(
                tmuxctl.view_select_window, self.settings, self.gid, d["dir"])
            self.out.push_text(json.dumps(info))
        except tmuxctl.TmuxError:
            self.res.dropped_in += 1

    def _on_resize(self, d: dict) -> bool:
        sz = clamp_size(d.get("cols"), d.get("rows"))
        if not sz:
            return False
        self.act = time.monotonic()
        try:
            self.attach.resize(*sz)
        except OSError:
            pass
        return True

    async def watchdog(self) -> tuple[int, str]:
        lim, t0 = self.limits, self.t0
        next_ping = time.monotonic() + lim.ping_s
        while True:
            now = time.monotonic()
            if now - t0 >= lim.max_s:
                return 4409, "max duration"
            if now - self.act >= lim.idle_s:
                return 4408, "idle"
            if now - self.pong >= lim.pong_timeout_s:
                return 4408, "no pong"
            if now >= next_ping:
                if not self.still_valid():
                    return 4401, "session ended"
                self.out.push_text('{"type":"ping"}')
                next_ping = now + lim.ping_s
            deadline = min(t0 + lim.max_s, self.act + lim.idle_s,
                           self.pong + lim.pong_timeout_s, next_ping)
            await asyncio.sleep(max(0.01, deadline - time.monotonic()))


async def _teardown(conn: _Conn, tasks: list[asyncio.Task], broker: Broker | None) -> None:
    # Order matters: under a cancelled scope every await raises again, so the child is
    # signalled and killed synchronously BEFORE anything is awaited.
    conn.res.duration_s = round(time.monotonic() - conn.t0, 3)
    for t in tasks:
        t.cancel()
    try:
        attach = conn.attach
        if attach is not None:
            if conn.reader_on and attach.master >= 0:
                conn.loop.remove_reader(attach.master)
            try:
                await attach.close()  # SIGHUP, short grace, then SIGKILL; always reaps
            finally:
                attach.kill_now()  # no-op when already reaped
    finally:
        if conn.gid is not None:
            _drop_view(conn.settings, broker, conn.gid)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def serve(ws, settings: Settings, tmux_id: str, limits: Limits,
                still_valid=lambda: True, res: Result | None = None,
                broker: Broker | None = None, owner: str | None = None,
                window_id: str | None = None) -> Result:
    """Run one connection (already accepted). Returns when it ends; child is reaped.

    `res` may be supplied by the caller so counters survive cancellation.
    """
    loop = asyncio.get_running_loop()
    t0 = time.monotonic()
    res = res if res is not None else Result()
    res.code, res.reason = 1001, "cancelled"  # overwritten on every normal exit
    conn = _Conn(ws, settings, limits, res, still_valid, loop, t0)
    tasks: list[asyncio.Task] = []
    try:
        size = await _read_init(ws, limits, res)
        if size is None:
            return res
        try:
            conn.gid, conn.attach = _open_view(settings, tmux_id, broker, owner, window_id, size)
            first_label = tmuxctl.view_select_window(settings, conn.gid, None)
        except (OSError, tmuxctl.TmuxError):
            res.code, res.reason = 1011, "spawn failed"
            return res
        conn.start(first_label)
        tasks.append(asyncio.create_task(conn.arm()))
        main = [asyncio.create_task(c()) for c in (conn.sender, conn.receiver, conn.watchdog)]
        tasks.extend(main)
        done, _ = await asyncio.wait(main, return_when=asyncio.FIRST_COMPLETED)
        res.code, res.reason = next(iter(done)).result()
        return res
    finally:
        await _teardown(conn, tasks, broker)


class Broker:
    """Concurrency cap. reserve/release are synchronous so the check cannot race."""

    def __init__(self) -> None:
        self.active = 0
        self.views: set[str] = set()  # live grouped pm-view-* session ids
        self.owners: dict[str, tuple[str | None, str]] = {}  # gid -> (login session id, $target)
        self._last_cleanup = 0.0

    def reserve(self, cap: int) -> bool:
        if self.active >= cap:
            return False
        self.active += 1
        return True

    def view_for(self, owner: str, tmux_id: str) -> str | None:
        """Newest live view of this login session on this tmux session (ticket-validated)."""
        for gid in reversed(list(self.owners)):
            if self.owners[gid] == (owner, tmux_id) and gid in self.views:
                return gid
        return None

    def release(self) -> None:
        self.active = max(0, self.active - 1)

    def cleanup_stale(self, settings: Settings, now: float | None = None) -> list[str]:
        """Kill pm-view-<hex> sessions with no client, older than 60 s, not ours. Rate limited."""
        t = time.monotonic()
        if t - self._last_cleanup < STALE_VIEW_S and self._last_cleanup:
            return []
        self._last_cleanup = t
        wall = time.time() if now is None else now
        killed = []
        try:
            for gid, _name, attached, created in tmuxctl.list_view_sessions(settings):
                if attached == 0 and gid not in self.views and wall - created > STALE_VIEW_S:
                    if tmuxctl.kill_view_session(settings, gid):
                        killed.append(gid)
        except tmuxctl.TmuxError:
            pass
        return killed
