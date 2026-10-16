"""Login state of the installed coding agents: status probe, logout, and login in a tmux window.

Fixed absolute binaries only (from the inventory), argv lists, timeouts, a minimal env. Raw CLI
output is never returned or logged: only parsed, capped fields. Login runs interactively in a
window of the ``agents-auth`` tmux session; its pane text goes out masked (``render_pane``).
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from pm.core.cache import LRU
from pm.core.config import account_env
from pm.core.errors import Conflict, Invalid
from pm.core.models import TmuxWindow
from pm.core.proc import ProcError, ProcNotFound, ProcTimeout, run_argv
from pm.security.mask import mask, render_pane, strip_ansi
from pm.tmux import ctl as tmuxctl

AUTH_SESSION = "agents-auth"  # tmux session for login windows (not matched by the pm* protected glob)
KINDS = ("claude", "codex", "devin", "cursor")
STATUS_TTL = 60.0
STATUS_TIMEOUT = 15.0
LOGOUT_TIMEOUT = 20.0
FIELD_MAX = 120
OUTPUT_MAX = 65536
SEND_MAX = 4096
VIEW_LINES = 40
CAPTURE_LINES = 200
URL_MAX, URLS_MAX = 2048, 5

_STATUS_ARGV = {
    "claude": ("auth", "status", "--json"),
    "codex": ("login", "status"),
    "devin": ("auth", "status"),
    "cursor": ("status",),
}
_LOGIN_ARGV = {
    "claude": ("auth", "login"),
    "codex": ("login", "--device-auth"),
    "devin": ("auth", "login", "--force-manual-token-flow"),
    "cursor": ("login",),
}
_LOGOUT_ARGV = {
    "claude": ("auth", "logout"),
    "codex": ("logout",),
    "devin": ("auth", "logout"),
    "cursor": ("logout",),
}
_EXTRA_ENV = {"cursor": {"NO_OPEN_BROWSER": "1"}}

_OUT_RE = re.compile(r"\bnot\s+(?:logged|signed)\s+in\b|\bnot\s+authenticated\b|\blogged\s+out\b", re.I)
_CODEX_CHATGPT = re.compile(r"\blogged\s+in\s+using\s+chatgpt\b", re.I)
_CODEX_KEY = re.compile(r"\blogged\s+in\s+using\s+an?\s+api\s+key\b", re.I)
_DEVIN_IN = re.compile(r"^\s*logged\s+in\b(?:\s*\(via\s+([^)\n]+)\))?", re.I)
_DEVIN_EMAIL = re.compile(r"^\s*Email:\s*(\S+)", re.M)
_DEVIN_PLAN = re.compile(r"^\s*Plan:\s*(.+)$", re.M)
_CURSOR_IN = re.compile(r"\blogged\s+in\s+as\s+(\S+)", re.I)
_URL_RE = re.compile(r"https://[^\s<>\"'`]+")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


@dataclass(frozen=True)
class AuthStatus:
    name: str
    supported: bool
    state: str = "unknown"  # "in" | "out" | "unknown"
    account: str = ""  # email / user, if the CLI reports it
    method: str = ""  # e.g. "claude.ai", "ChatGPT", "API key"
    plan: str = ""  # e.g. "max", "Pro"
    error: str = ""  # short, safe text; never raw CLI output


@dataclass(frozen=True)
class LoginView:
    active: bool  # a login window for this kind exists
    window_id: str = ""  # @n
    pane_id: str = ""  # %n
    text: str = ""  # last VIEW_LINES lines of the pane, ANSI-stripped + masked
    urls: tuple[str, ...] = ()  # https:// URLs in the masked pane text, deduped, max URLS_MAX
    dead: bool = False  # the login process exited (remain-on-exit pane)


# (name, account) -> (binary, monotonic time, AuthStatus); () = dropped. Bounded: kinds are validated against
# KINDS, accounts against the configured names (callers go through account_env); room for 32 accounts per kind.
_cache = LRU(max_bytes=1024 * 1024, max_entries=4 * 32 * len(KINDS))


def supported(name: str) -> bool:
    return isinstance(name, str) and name in KINDS


def _check_name(name: str) -> str:
    if not supported(name):
        raise Invalid("unsupported agent")
    return name


def _check(name: str, binary: str) -> tuple[str, str]:
    _check_name(name)
    if not isinstance(binary, str) or not binary or not os.path.isabs(binary):
        raise Invalid("agent binary must be an absolute path")
    if not os.path.isfile(binary) or not os.access(binary, os.X_OK):
        raise Invalid("agent binary not executable")
    return name, binary


def _account_env(settings, name: str, account: str) -> dict[str, str]:
    """Env of a named account (``{}`` for the default one); creates its login folder, mode 0700."""
    if not account:
        return {}
    if settings is None:
        raise Invalid("account needs settings")
    env = account_env(settings, name, account)  # Invalid: unknown kind / name
    for d in env.values():
        Path(d).mkdir(mode=0o700, parents=True, exist_ok=True)
    return env


def _env(name: str, settings=None, account: str = "") -> dict[str, str]:
    home = Path.home()
    return {
        "PATH": f"{home}/.local/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "NO_COLOR": "1",
        **_EXTRA_ENV.get(name, {}),
        **_account_env(settings, name, account),
    }


def _field(v: object) -> str:
    """A safe, single-line, capped field from parsed output."""
    if not isinstance(v, str):
        return ""
    return mask(_CTRL_RE.sub("", v).strip())[:FIELD_MAX]


def _parse_claude(text: str) -> tuple[str, str, str, str]:
    try:
        data = json.loads(text[text.find("{"):] if "{" in text else text)
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("loggedIn"), bool):
        if not data["loggedIn"]:
            return "out", "", "", ""
        return "in", _field(data.get("email")), _field(data.get("authMethod")), _field(data.get("subscriptionType"))
    return ("out" if _OUT_RE.search(text) else "unknown"), "", "", ""


def _parse_codex(text: str) -> tuple[str, str, str, str]:
    if _OUT_RE.search(text):
        return "out", "", "", ""
    if _CODEX_CHATGPT.search(text):
        return "in", "", "ChatGPT", ""
    if _CODEX_KEY.search(text):
        return "in", "", "API key", ""
    return "unknown", "", "", ""


def _parse_devin(text: str) -> tuple[str, str, str, str]:
    lines = text.strip().splitlines()
    first = lines[0] if lines else ""
    if _OUT_RE.search(first) or (not _DEVIN_IN.match(first) and _OUT_RE.search(text)):
        return "out", "", "", ""
    m = _DEVIN_IN.match(first)
    if not m:
        return "unknown", "", "", ""
    em, pl = _DEVIN_EMAIL.search(text), _DEVIN_PLAN.search(text)
    return ("in", _field(em.group(1) if em else ""), _field(m.group(1) or ""),
            _field(pl.group(1) if pl else ""))


def _parse_cursor(text: str) -> tuple[str, str, str, str]:
    if _OUT_RE.search(text):
        return "out", "", "", ""
    m = _CURSOR_IN.search(text)
    return ("in", _field(m.group(1)), "", "") if m else ("unknown", "", "", "")


_PARSERS = {"claude": _parse_claude, "codex": _parse_codex, "devin": _parse_devin, "cursor": _parse_cursor}


def parse_status(name: str, text: str) -> AuthStatus:
    """AuthStatus from the (raw) output of the status command; tolerant, unrecognised -> unknown."""
    _check_name(name)
    state, account, method, plan = _PARSERS[name](strip_ansi(text or "")[:OUTPUT_MAX])
    return AuthStatus(name, True, state, account, method, plan)


def _probe(name: str, binary: str, settings=None, account: str = "") -> AuthStatus:
    try:
        r = run_argv([binary, *_STATUS_ARGV[name]], timeout=STATUS_TIMEOUT, env=_env(name, settings, account))
    except ProcTimeout:
        return AuthStatus(name, True, error="timed out")
    except ProcNotFound:
        return AuthStatus(name, True, error="binary not found")
    except ProcError:
        return AuthStatus(name, True, error="failed")
    out = ((r.stdout or b"")[:OUTPUT_MAX] + b"\n" + (r.stderr or b"")[:OUTPUT_MAX]).decode("utf-8", "replace")
    st = parse_status(name, out)
    if st.state == "unknown":
        err = "unrecognised output" if r.returncode == 0 else f"exit {r.returncode}"
        st = AuthStatus(name, True, error=err)
    return st


def status(name: str, binary: str, *, fresh: bool = False, account: str = "", settings=None) -> AuthStatus:
    """Login state of one agent account, cached STATUS_TTL s per (name, account, binary); ``fresh`` re-probes.

    A named ``account`` needs ``settings`` (its login folder comes from ``account_env``)."""
    _check(name, binary)
    hit = _cache.get((name, account))
    if not fresh and hit and hit[0] == binary and time.monotonic() - hit[1] < STATUS_TTL:
        return hit[2]
    st = _probe(name, binary, settings, account)
    _cache.put((name, account), (binary, time.monotonic(), st))
    return st


def cached(name: str, binary: str, *, account: str = "") -> AuthStatus | None:
    """The cached status of one agent account if still fresh, else None. Never probes (no subprocess)."""
    if not supported(name):
        return None
    hit = _cache.get((name, account))
    if hit and hit[0] == binary and time.monotonic() - hit[1] < STATUS_TTL:
        return hit[2]
    return None


def clear_cache(name: str | None = None, *, account: str = "") -> None:
    """Drop everything (``name`` None) or the entry of one (name, account)."""
    if name is None:
        _cache.clear()
    elif name in KINDS:
        _cache.put((name, account), ())


def logout(name: str, binary: str, *, account: str = "", settings=None) -> None:
    """Run the agent's logout command for one account; Conflict on a nonzero exit or timeout."""
    _check(name, binary)
    env = _env(name, settings, account)
    try:
        r = run_argv([binary, *_LOGOUT_ARGV[name]], timeout=LOGOUT_TIMEOUT, env=env)
    except ProcError as e:
        raise Conflict("logout failed") from e
    finally:
        clear_cache(name, account=account)
    if r.returncode != 0:
        raise Conflict("logout failed")


# ---------------------------------------------------------------- login windows


@contextmanager
def _tmux() -> Iterator[None]:
    try:
        yield
    except tmuxctl.TmuxError as e:
        raise Conflict("tmux failed") from e


def _window_name(name: str, account: str = "") -> str:
    return f"auth-{name}-{account}" if account else f"auth-{name}"


def _find(settings, name: str, account: str = "") -> tuple[str | None, TmuxWindow | None]:
    """(session id of AUTH_SESSION, the login window of ``name``), either may be None."""
    for s in tmuxctl.list_sessions(settings):
        if s.name != AUTH_SESSION:
            continue
        for w in s.windows:
            if w.name == _window_name(name, account) and w.panes:
                return s.id, w
        return s.id, None
    return None, None


def _alive(w: TmuxWindow) -> bool:
    return any(not p.dead for p in w.panes)


def _urls(text: str) -> tuple[str, ...]:
    out: list[str] = []
    for m in _URL_RE.finditer(text):
        u = m.group(0).rstrip(").,;")
        if len(u) > len("https://") and len(u) <= URL_MAX and u not in out:
            out.append(u)
            if len(out) >= URLS_MAX:
                break
    return tuple(out)


def _view(settings, wid: str, pid: str, dead: bool) -> LoginView:
    tmuxctl.check_id(wid, "@")
    tmuxctl.check_id(pid, "%")
    shown = render_pane(tmuxctl.capture_pane(settings, pid, CAPTURE_LINES))
    lines = [ln.rstrip() for ln in shown.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return LoginView(True, wid, pid, "\n".join(lines[-VIEW_LINES:]), _urls(shown), dead)


def _window_view(settings, w: TmuxWindow) -> LoginView:
    pane = next((p for p in w.panes if not p.dead), w.panes[0])
    return _view(settings, w.id, pane.id, not _alive(w))


def _spawn(settings, sid: str | None, name: str, argv: list[str], account: str = "") -> tuple[str, str]:
    """Start the login command in a new window (new session when ``sid`` is None) -> (@id, %id)."""
    wname = tmuxctl.check_name(_window_name(name, account))
    pairs = {**_EXTRA_ENV.get(name, {}), **_account_env(settings, name, account)}
    env = tmuxctl.env_args(pairs)
    home = str(Path.home())
    if sid is None:
        head = ["new-session", "-d", "-P", "-F", "#{window_id} #{pane_id}", "-s", AUTH_SESSION]
    else:
        head = ["new-window", "-d", "-P", "-F", "#{window_id} #{pane_id}", "-t", tmuxctl.check_id(sid, "$") + ":"]
    out = tmuxctl.run(settings, [*head, "-n", wname, "-c", tmuxctl.arg(home), *env, "--", *tmuxctl.start_words(settings, argv)]).strip()
    wid, _, pid = out.partition(" ")
    tmuxctl.check_id(wid, "@")
    tmuxctl.check_id(pid.strip(), "%")
    tmuxctl.run(settings, ["set-option", "-w", "-t", wid, "remain-on-exit", "on"])
    return wid, pid.strip()


def login_start(settings, name: str, binary: str, *, account: str = "") -> LoginView:
    """Open (or reuse) the login window of ``name`` / ``account``; a finished one is replaced by a fresh start."""
    _check(name, binary)
    _account_env(settings, name, account)  # validates the account before any window work
    clear_cache(name, account=account)
    with _tmux():
        sid, w = _find(settings, name, account)
        if w is not None and _alive(w):
            return _window_view(settings, w)
        if w is not None:
            tmuxctl.run(settings, ["kill-window", "-t", tmuxctl.check_id(w.id, "@")])
            sid, _ = _find(settings, name, account)  # the session is gone if that was its last window
        wid, pid = _spawn(settings, sid, name, [binary, *_LOGIN_ARGV[name]], account)
        return _view(settings, wid, pid, False)


def login_view(settings, name: str, *, account: str = "") -> LoginView:
    _check_name(name)
    with _tmux():
        _, w = _find(settings, name, account)
        return LoginView(False) if w is None else _window_view(settings, w)


def _clean_input(text: str) -> str:
    if not isinstance(text, str):
        raise Invalid("text required")
    text = text.strip()
    if not text:
        raise Invalid("text required")
    if len(text) > SEND_MAX:
        raise Invalid("text too long")
    if _CTRL_RE.search(text):
        raise Invalid("text has control characters")
    return text


def login_send(settings, name: str, text: str, *, account: str = "") -> None:
    """Type a pasted code/token into the running login window, then Enter."""
    _check_name(name)
    text = _clean_input(text)
    with _tmux():
        _, w = _find(settings, name, account)
        if w is None or not _alive(w):
            raise Invalid("no active login")
        pane = next(p for p in w.panes if not p.dead)
        tmuxctl.send_keys(settings, pane.id, text, literal=True)
        tmuxctl.send_key(settings, pane.id, "Enter")
    clear_cache(name, account=account)


def login_cancel(settings, name: str, *, account: str = "") -> None:
    """Close the login window of ``name`` / ``account`` if there is one."""
    _check_name(name)
    with _tmux():
        _, w = _find(settings, name, account)
        if w is not None:
            tmuxctl.run(settings, ["kill-window", "-t", tmuxctl.check_id(w.id, "@")])
    clear_cache(name, account=account)
