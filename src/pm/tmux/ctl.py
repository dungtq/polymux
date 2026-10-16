"""argv-only tmux runner: no shell, fixed binary, timeout, bounded output, ids only."""

from __future__ import annotations

import dataclasses
import fnmatch
import logging
import os
import re
import time
import uuid

from pm.core.cache import LRU
from pm.core.config import Settings
from pm.core.models import TmuxPane, TmuxSession, TmuxWindow
from pm.core.proc import ProcError, run_argv

TIMEOUT_S = 5.0
MAX_OUTPUT = 1_000_000
SEP = "<|pm|>"
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
ID_RE = re.compile(r"^[$@%]\d+$")
ALLOWED_KEYS = frozenset({
    "y", "n", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    "Enter", "Escape", "Tab", "BTab", "BSpace", "Space", "Up", "Down", "Left", "Right",
    "Home", "End", "PageUp", "PageDown", "C-c", "C-d", "C-l", "C-x", "C-s",
})
ALWAYS_PROTECTED = ("pm*",)


log = logging.getLogger(__name__)


class TmuxError(Exception):
    pass


def arg(word: str | os.PathLike) -> str:
    """``word`` as a tmux argument that comes back unchanged: tmux (even after ``--``) reads an argument ending
    in ``;`` as a command separator and turns a trailing ``\\;`` into ``;``, so ``x;`` is sent as ``x\\;``
    (also right for a word ending in ``\\;``). Use it for every user value: argv words, ``-c <cwd>`` (a path is
    taken as its string)."""
    word = os.fspath(word)
    return word[:-1] + "\\;" if word.endswith(";") else word


def check_id(target: str, kind: str | None = None) -> str:
    if not isinstance(target, str) or not ID_RE.fullmatch(target):
        raise TmuxError(f"invalid tmux id: {target!r}")
    if kind and target[0] != kind:
        raise TmuxError(f"expected {kind}-id, got {target!r}")
    return target


def check_name(name: str) -> str:
    # leading '-' rejected too, even though the regex allows it: belt and braces.
    if not isinstance(name, str) or not NAME_RE.fullmatch(name) or name.startswith("-"):
        raise TmuxError(f"invalid name: {name!r}")
    return name


def run(settings: Settings, args: list[str], timeout: float = TIMEOUT_S,
        input_: bytes | None = None) -> str:
    argv = [settings.tmux_bin]
    if settings.tmux_socket:
        argv += ["-L", settings.tmux_socket]
    argv += args
    try:
        p = run_argv(argv, timeout=timeout, input=input_)
    except ProcError as e:
        raise TmuxError(f"tmux failed: {type(e.__cause__ or e).__name__}") from e
    if p.returncode != 0:
        msg = p.stderr[:300].decode("utf-8", "replace").strip()
        raise TmuxError(msg or f"tmux exit {p.returncode}")
    return p.stdout[:MAX_OUTPUT].decode("utf-8", "replace")


def is_protected(name: str, settings: Settings) -> bool:
    if any(fnmatch.fnmatchcase(name, g) for g in ALWAYS_PROTECTED):
        return True
    return not any(fnmatch.fnmatchcase(name, g) for g in settings.unprotected_sessions)


def autoexit_settings(settings: Settings) -> Settings:
    """Settings whose "unprotected" sessions are the auto-exit allow-list (``autoexit_sessions``);
    ``pm*`` stays protected. Pass it to is_protected / exit_agent / kill_window for auto-exit only."""
    return dataclasses.replace(settings, unprotected_sessions=settings.autoexit_sessions)


_FIELDS = [
    "#{session_id}", "#{session_name}", "#{session_attached}",
    "#{window_id}", "#{window_index}", "#{window_name}", "#{window_active}",
    "#{pane_id}", "#{pane_pid}", "#{pane_current_command}", "#{pane_current_path}",
    "#{pane_active}", "#{pane_title}", "#{pane_dead}",
]
_FORMAT = SEP.join(_FIELDS)


def _int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return 0


def parse_panes(out: str, settings: Settings) -> tuple[TmuxSession, ...]:
    sessions: dict[str, dict] = {}
    for line in out.splitlines():
        f = line.split(SEP)
        if len(f) == len(_FIELDS) - 1:
            f.append("0")  # older/fake output without pane_dead
        if len(f) != len(_FIELDS):
            continue
        sid, sname, att, wid, widx, wname, wact, pid, ppid, cmd, cwd, pact, title, dead = f
        if not (ID_RE.fullmatch(sid) and ID_RE.fullmatch(wid) and ID_RE.fullmatch(pid)):
            continue
        if VIEW_RE.fullmatch(sname):
            continue  # pm's per-connection grouped view: the same panes again, never their home session
        s = sessions.setdefault(sid, {"name": sname, "att": _int(att), "w": {}})
        w = s["w"].setdefault(
            wid, {"index": _int(widx), "name": wname, "active": wact == "1", "p": []}
        )
        w["p"].append(
            TmuxPane(
                id=pid, window_id=wid, session_id=sid, pid=_int(ppid), command=cmd,
                cwd=cwd, active=pact == "1", title=title, dead=dead == "1",
            )
        )
    result = []
    for sid, s in sessions.items():
        wins = tuple(
            TmuxWindow(id=wid, session_id=sid, index=w["index"], name=w["name"],
                       active=w["active"], panes=tuple(w["p"]))
            for wid, w in sorted(s["w"].items(), key=lambda kv: kv[1]["index"])
        )
        result.append(
            TmuxSession(id=sid, name=s["name"], attached=s["att"],
                        protected=is_protected(s["name"], settings), windows=wins)
        )
    return tuple(result)


def list_sessions(settings: Settings) -> tuple[TmuxSession, ...]:
    try:
        out = run(settings, ["list-panes", "-a", "-F", _FORMAT])
    except TmuxError as e:
        # no server running is an empty state, not an error
        if "no server running" in str(e) or "error connecting" in str(e):
            return ()
        raise
    return parse_panes(out, settings)


def capture_pane(settings: Settings, pane_id: str, lines: int = 200) -> str:
    check_id(pane_id, "%")
    lines = max(1, min(int(lines), 5000))
    return run(settings, ["capture-pane", "-p", "-J", "-S", f"-{lines}", "-t", pane_id])


def clear_history(settings: Settings, pane_id: str) -> None:
    """Drop the scrollback history of one pane (``tmux clear-history -t %n``); the visible screen stays."""
    check_id(pane_id, "%")
    run(settings, ["clear-history", "-t", pane_id])


def new_session(settings: Settings, name: str, cwd: str | None = None) -> str:
    check_name(name)
    args = ["new-session", "-d", "-P", "-F", "#{session_id}"]
    if cwd:
        args += ["-c", arg(cwd)]
    args += ["-s", name]
    return run(settings, args).strip()


def new_window(settings: Settings, session_id: str, name: str | None = None,
               cwd: str | None = None) -> str:
    check_id(session_id, "$")
    args = ["new-window", "-d", "-P", "-F", "#{window_id}", "-t", session_id + ":"]
    if name is not None:
        args += ["-n", check_name(name)]
    if cwd:
        args += ["-c", arg(cwd)]
    return run(settings, args).strip()


# ---- commands: started by tmux (argv as shell positional args) or typed into an interactive shell ----
class ShellQuoteError(ValueError):
    """The pane's shell is unknown (or the argv cannot be typed safely): refuse instead of guessing."""


_EXEC_SH = ("/bin/sh", "-c", 'exec "$@"', "sh")
SHELL_TTL_S = 60.0
_shells = LRU(max_entries=16)  # (tmux_bin, socket) -> (default-shell, read at)
_warned = LRU(max_entries=16)  # default-shell values already logged as unknown
# unquoted words read the same by sh, bash, zsh, dash and fish (no '=', '~', '%', globs, braces)
_PLAIN_WORD = re.compile(r"[A-Za-z0-9_./:,+@-]+")
_POSIX_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "mksh"})
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def default_shell(settings: Settings) -> str:
    """tmux's global ``default-shell`` (an absolute path), cached for SHELL_TTL_S per server; "" when tmux
    cannot tell (nothing cached then)."""
    key = (settings.tmux_bin, settings.tmux_socket)
    hit = _shells.get(key)
    now = time.monotonic()
    if hit and now - hit[1] < SHELL_TTL_S:
        return hit[0]
    try:
        shell = run(settings, ["show-options", "-gv", "default-shell"]).strip()
    except TmuxError:
        return ""
    if not os.path.isabs(shell):
        return ""
    _shells.put(key, (shell, now))
    return shell


def command_words(argv: list[str], shell: str) -> list[str]:
    """The words after ``--`` of new-window / split-window / respawn-pane / new-session that run ``argv``
    through the user's ``shell`` (tmux's default-shell), so its startup files still apply (fish -c loads
    config.fish: SSH_AUTH_SOCK, mise, PATH), with argv as positional arguments that no shell re-parses:
    fish ``-c 'exec $argv'``, sh/bash/zsh/dash/ksh ``-c 'exec "$@"'``. A one string command would be parsed by
    that shell, and fish reads sh quoting differently (#204). Unknown or unreadable shell: /bin/sh (logged).
    Every word goes through ``arg`` (a trailing ``;`` would end the tmux command)."""
    if not argv or not all(isinstance(a, str) and "\0" not in a for a in argv) or argv[0].startswith("-"):
        raise TmuxError("invalid command")
    name = os.path.basename(shell)
    if os.path.isabs(shell) and name == "fish":
        return [arg(w) for w in (shell, "-c", "exec $argv", *argv)]
    if os.path.isabs(shell) and name in _POSIX_SHELLS:
        return [arg(w) for w in (shell, "-c", 'exec "$@"', name, *argv)]
    if _warned.get(shell) is None:
        _warned.put(shell, True)
        log.warning("tmux default-shell %r unknown or unreadable: commands start via /bin/sh", shell[:64])
    return [arg(w) for w in (*_EXEC_SH, *argv)]


def start_words(settings: Settings, argv: list[str]) -> list[str]:
    """``command_words`` for tmux's current default-shell: what every start site puts after ``--``."""
    return command_words(argv, default_shell(settings))


def _fish_word(a: str) -> str:
    # fish single quotes: only \\ and \' are escapes (sh has none, hence the mismatch)
    return "'" + a.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _sh_word(a: str) -> str:
    return "'" + a.replace("'", "'\"'\"'") + "'"


def check_typeable(argv: list[str]) -> None:
    """ShellQuoteError unless ``argv`` can be typed into any shell: not empty, no control character (a typed
    newline would run the line early). Shell independent, so it can run before anything is changed."""
    if not argv or not all(isinstance(a, str) and not _CTRL.search(a) for a in argv):
        raise ShellQuoteError("command cannot be typed into a shell")


def quote_for_shell(shell: str, argv: list[str]) -> str:
    """One command line that the interactive ``shell`` (a name or path; ``-fish`` = login fish) reads back as
    exactly ``argv``, for typing into a pane. sh, bash, zsh, dash, ksh: POSIX single quotes; fish: its own
    rules. Any other shell, an empty argv or a control character (a typed newline would run the line early):
    ShellQuoteError."""
    name = shell.rsplit("/", 1)[-1].lstrip("-")
    if name == "fish":
        quote = _fish_word
    elif name in _POSIX_SHELLS:
        quote = _sh_word
    else:
        raise ShellQuoteError(f"unknown shell in the pane: {name[:32]!r}")
    check_typeable(argv)
    return " ".join(a if _PLAIN_WORD.fullmatch(a) else quote(a) for a in argv)


def split_pane(settings: Settings, pane_id: str, cwd: str, argv: list[str]) -> str:
    """Split ``pane_id`` (``%n``) and run ``argv`` in the new pane (``-d``: focus stays). Returns
    ``"@window %pane"`` of the new pane. ``argv`` is a fully validated argv list (``start_words``)."""
    check_id(pane_id, "%")
    return run(settings, ["split-window", "-d", "-P", "-F", "#{window_id} #{pane_id}",
                          "-t", pane_id, "-c", arg(cwd), "--", *start_words(settings, argv)]).strip()


def set_remain_on_exit(settings: Settings, pane_id: str, on: bool) -> None:
    """Keep the pane (dead) when its process exits (``on``), or drop the pane's own value again so the
    window/global ``remain-on-exit`` applies (``off``)."""
    check_id(pane_id, "%")
    run(settings, ["set-option", "-p", "-t", pane_id, "remain-on-exit", "on"] if on
        else ["set-option", "-p", "-u", "-t", pane_id, "remain-on-exit"])


def respawn_pane(settings: Settings, pane_id: str, cwd: str, argv: list[str]) -> None:
    """Run ``argv`` in the dead pane ``pane_id`` (same pane id, window and session), through the default-shell
    with argv as positional arguments (``start_words``), so no shell re-parses them (fish reads sh quoting
    differently)."""
    check_id(pane_id, "%")
    if not argv or not all(isinstance(a, str) for a in argv):
        raise TmuxError("respawn-pane needs a command")
    args = ["respawn-pane", "-t", pane_id]
    if cwd:
        args += ["-c", arg(cwd)]
    run(settings, args + ["--", *start_words(settings, argv)])


def rename_session(settings: Settings, session_id: str, name: str) -> None:
    check_id(session_id, "$")
    check_name(name)
    run(settings, ["rename-session", "-t", session_id, "--", name])


def rename_window(settings: Settings, window_id: str, name: str) -> None:
    check_id(window_id, "@")
    check_name(name)
    run(settings, ["rename-window", "-t", window_id, "--", name])


# Raw bytes per allowed key, for the paste-buffer fallback below.
_KEY_BYTES = {
    "Enter": b"\r", "Escape": b"\x1b", "Tab": b"\t", "BTab": b"\x1b[Z", "BSpace": b"\x7f", "Space": b" ",
    "Up": b"\x1b[A", "Down": b"\x1b[B", "Right": b"\x1b[C", "Left": b"\x1b[D",
    "Home": b"\x1b[H", "End": b"\x1b[F", "PageUp": b"\x1b[5~", "PageDown": b"\x1b[6~",
    "C-c": b"\x03", "C-d": b"\x04", "C-l": b"\x0c", "C-x": b"\x18", "C-s": b"\x13",
}


def _writable_client(settings: Settings) -> str | None:
    """Name of an attached client that is not read-only (the Terminal tab attaches with -r)."""
    try:
        out = run(settings, ["list-clients", "-F", "#{client_name} #{client_readonly}"])
    except TmuxError:
        return None
    for ln in out.splitlines():
        name, _, ro = ln.rpartition(" ")
        if name and ro == "0":
            return name
    return None


def _send(settings: Settings, pane_id: str, argv: list[str], raw: bytes) -> None:
    """send-keys; when tmux picks a read-only client ("client is read-only", e.g. the Terminal
    tab's attach -r) retry through a writable client with ``send-keys -c``.

    The paste-buffer fallback is only safe for plain printable text: paste-buffer turns control
    bytes (ESC, DEL, ^C ...) into literal caret text typed into the agent, so any other key fails
    loudly instead of polluting the composer.
    """
    try:
        run(settings, argv)
        return
    except TmuxError as e:
        if "read-only" not in str(e):
            raise
    client = _writable_client(settings) if argv and argv[0] == "send-keys" else None
    if client:
        run(settings, [argv[0], "-c", client, *argv[1:]])
        return
    if raw in (b"\r", b"\n") or any(c < 0x20 or c == 0x7f for c in raw.replace(b"\r", b"").replace(b"\n", b"")):
        raise TmuxError("no writable tmux client for control keys")
    buf = f"pm-send-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    run(settings, ["load-buffer", "-b", buf, "-"], input_=raw)
    run(settings, ["paste-buffer", "-r", "-d", "-b", buf, "-t", pane_id])


def send_keys(settings: Settings, pane_id: str, text: str, literal: bool = True) -> None:
    check_id(pane_id, "%")
    if not literal:
        raise TmuxError("non-literal send_keys not allowed; use send_key")
    _send(settings, pane_id, ["send-keys", "-l", "-t", pane_id, "--", text], text.encode("utf-8"))


def load_buffer(settings: Settings, name: str, text: str) -> None:
    """Fill a tmux paste buffer from stdin (no argv parsing: tmux treats a trailing ';'
    in an argument as a command separator and eats a backslash before it)."""
    check_name(name)
    run(settings, ["load-buffer", "-b", name, "-"], input_=text.encode("utf-8"))


def send_key(settings: Settings, pane_id: str, key: str) -> None:
    check_id(pane_id, "%")
    if key not in ALLOWED_KEYS:
        raise TmuxError(f"key not allowed: {key!r}")
    _send(settings, pane_id, ["send-keys", "-t", pane_id, "--", key], _KEY_BYTES.get(key, key.encode()))


# ---- per-connection grouped "view" sessions (read-only terminal window navigation) ----
VIEW_PREFIX = "pm-view-"
VIEW_RE = re.compile(r"^pm-view-[0-9a-f]{12}$")


def new_view_session(settings: Settings, target_id: str, name: str) -> str:
    """Create a detached session grouped with `target_id` (shares windows, own current window)."""
    check_id(target_id, "$")
    if not VIEW_RE.fullmatch(name):
        raise TmuxError(f"invalid view session name: {name!r}")
    gid = run(settings, ["new-session", "-d", "-P", "-F", "#{session_id}",
                         "-t", target_id, "-s", name]).strip()
    check_id(gid, "$")
    return gid


def arm_view_session(settings: Settings, gid: str) -> bool:
    """Set destroy-unattached once a client is attached (tmux destroys an unattached
    session the moment the option is set, so this must come AFTER the attach)."""
    check_id(gid, "$")
    try:
        n = run(settings, ["display-message", "-p", "-t", gid,
                           "#{session_name} #{session_attached}"]).split()
        if len(n) != 2 or not VIEW_RE.fullmatch(n[0]) or n[1] == "0":
            return False
        run(settings, ["set-option", "-t", gid, "destroy-unattached", "on"])
    except TmuxError:
        return False
    return True


def kill_view_session(settings: Settings, gid: str) -> bool:
    """Kill ONLY a session whose current name is pm-view-<12 hex>. True if killed."""
    check_id(gid, "$")
    try:
        name = run(settings, ["display-message", "-p", "-t", gid, "#{session_name}"]).strip()
    except TmuxError:
        return False  # already gone
    if not VIEW_RE.fullmatch(name):
        return False
    try:
        run(settings, ["kill-session", "-t", gid])
    except TmuxError:
        return False
    return True


def list_view_sessions(settings: Settings) -> list[tuple[str, str, int, float]]:
    """(id, name, attached clients, created epoch) for pm-view-<hex> sessions only."""
    fmt = SEP.join(["#{session_id}", "#{session_name}", "#{session_attached}", "#{session_created}"])
    try:
        out = run(settings, ["list-sessions", "-F", fmt])
    except TmuxError as e:
        if "no server running" in str(e) or "error connecting" in str(e):
            return []
        raise
    res = []
    for line in out.splitlines():
        f = line.split(SEP)
        if len(f) != 4 or not ID_RE.fullmatch(f[0]) or not VIEW_RE.fullmatch(f[1]):
            continue
        try:
            res.append((f[0], f[1], int(f[2]), float(f[3])))
        except ValueError:
            continue
    return res


def view_select_window(settings: Settings, gid: str, direction: str | None) -> dict:
    """Move the grouped session's current window (next/prev, wraps) and describe it."""
    check_id(gid, "$")
    if direction in ("next", "prev"):
        run(settings, ["select-window", "-t", gid + ":", "-n" if direction == "next" else "-p"])
    elif direction is not None:
        raise TmuxError("bad direction")
    out = run(settings, ["list-windows", "-t", gid, "-F",
                         SEP.join(["#{window_index}", "#{window_active}", "#{window_name}"])])
    rows = [ln.split(SEP, 2) for ln in out.splitlines()]
    rows = [r for r in rows if len(r) == 3 and r[0].isdigit()]
    for pos, (idx, act, name) in enumerate(rows, 1):
        if act == "1":
            clean = "".join(ch for ch in name if ch.isprintable())[:64]
            return {"type": "window", "index": int(idx), "pos": pos,
                    "count": len(rows), "name": clean}
    raise TmuxError("no active window")


# ---- scrollback history of a view session's active pane (read-only, never mutates) ----
HISTORY_MAX_LINES = 10_000
HISTORY_MAX_BYTES = 1_500_000


def view_active_pane(settings: Settings, gid: str) -> str:
    """Active pane id of the grouped session's current window (resolved server-side)."""
    check_id(gid, "$")
    return check_id(run(settings, ["display-message", "-p", "-t", gid, "#{pane_id}"]).strip(), "%")


def capture_history(settings: Settings, pane_id: str, lines: int) -> str:
    """`capture-pane -p -e -J -S -<lines>` (ANSI colours kept). Tail-capped to 1.5 MB."""
    check_id(pane_id, "%")
    n = max(1, min(HISTORY_MAX_LINES, int(lines)))
    argv = [settings.tmux_bin]
    if settings.tmux_socket:
        argv += ["-L", settings.tmux_socket]
    argv += ["capture-pane", "-p", "-e", "-J", "-S", f"-{n}", "-t", pane_id]
    try:
        p = run_argv(argv, timeout=TIMEOUT_S)
    except ProcError as e:
        raise TmuxError(f"tmux failed: {type(e.__cause__ or e).__name__}") from e
    if p.returncode != 0:
        raise TmuxError(p.stderr[:300].decode("utf-8", "replace").strip() or "capture failed")
    data = p.stdout
    if len(data) > HISTORY_MAX_BYTES:
        data = data[-HISTORY_MAX_BYTES:]
        nl = data.find(b"\n")
        data = data[nl + 1:] if nl >= 0 else data
    return data.decode("utf-8", "replace")


def view_open_window(settings: Settings, gid: str, window_id: str) -> bool:
    """Select `window_id` (@N) in the grouped view session only if that window belongs to it
    (i.e. to the target session). Never touches the target's own current window. False when
    the window is not part of the session or tmux refuses."""
    check_id(gid, "$")
    check_id(window_id, "@")
    try:
        out = run(settings, ["list-windows", "-t", gid, "-F", "#{window_id}"])
        if window_id not in out.split():
            return False
        run(settings, ["select-window", "-t", gid + ":" + window_id])
    except TmuxError:
        return False
    return True
