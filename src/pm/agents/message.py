"""Deliver text and quick keys to an agent pane (tmux send-keys).

The target is re-validated against live tmux on every call. Message text is never
logged here; callers audit length only.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from pm.agents import registry
from pm.chat import ask as ask_mod
from pm.core.config import Settings, account_env
from pm.core.models import Agent, AgentKind, TmuxPane
from pm.core.procstat import PROC, read_proc_stat
from pm.security.mask import render_pane
from pm.tmux import ctl as tmuxctl

MAX_LEN = 4000
QUICK_KEYS = ("y", "n", "1", "2", "3", "4", "5", "6", "7", "8", "9", "Enter", "Escape", "Tab",
              "BTab", "BSpace", "Space", "Up", "Down", "Left", "Right", "Home", "End",
              "PageUp", "PageDown", "C-c", "C-d", "C-l", "C-x", "C-s")
# Timing: TUIs (Claude Code, Codex) treat bytes arriving back-to-back as a paste, and an
# Enter glued to the text is then inserted as a newline instead of submitting.
ENTER_DELAY_S = 0.15
PASTE_DELAY_S = 0.3
RETRY_DELAY_S = 0.3
CONFIRM_DELAY_S = 0.3
CLEAR_DELAY_S = 0.8
PASTE_MIN_LEN = 200
_REASON_MAX = 120
_NONPRINT_RE = re.compile(r"[^\x20-\x7e\u00a1-\uffff]")
_CTRL_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f  ]")  # all but \n


class MessageError(Exception):
    pass


class NotAnAgent(MessageError):
    """Pane is not (or no longer) the given agent."""


class Refused(MessageError):
    """Action refused by policy (protected session, kind without the command)."""


class InvalidMessage(MessageError):
    pass


@dataclass(frozen=True)
class SendResult:
    ok: bool
    agent_id: str
    pane_id: str
    length: int = 0
    lines: int = 0
    detail: str = ""
    submitted: bool = True  # False: delivered but the pane did not visibly react to Enter


def _is_descendant(pid: int, ancestor: int, proc_root: Path) -> bool:
    cur, hops = pid, 0
    while cur > 1 and hops < 64:
        if cur == ancestor:
            return True
        st = read_proc_stat(cur, proc_root)
        if st is None:
            return False
        cur, hops = st.ppid, hops + 1
    return cur == ancestor


def verify_target(settings: Settings, agent: Agent, *, proc_root: Path = PROC) -> None:
    """Raise NotAnAgent unless agent.pane_id is live, in the same session, and still
    runs agent.pid (the pane process or one of its descendants)."""
    if agent.kind is AgentKind.OTHER:
        raise NotAnAgent("not a recognised agent")
    try:
        tmuxctl.check_id(agent.pane_id, "%")
        sessions = tmuxctl.list_sessions(settings)
    except tmuxctl.TmuxError as e:
        raise NotAnAgent(f"pane lookup failed: {e}") from e
    # a window linked into several sessions (grouped / view sessions) lists the pane once per session
    seen = [(s, p) for s in sessions for w in s.windows for p in w.panes if p.id == agent.pane_id]
    if not seen:
        raise NotAnAgent("pane not found")
    mine = [p for s, p in seen if s.id == agent.session_id]
    if not mine:
        raise NotAnAgent("pane moved to another session")
    if not _is_descendant(agent.pid, mine[0].pid, proc_root):
        raise NotAnAgent("pane no longer runs this agent")


def validate_text(text: str) -> str:
    if not isinstance(text, str):
        raise InvalidMessage("text must be a string")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise InvalidMessage("empty message")
    if len(text) > MAX_LEN:
        raise InvalidMessage(f"message too long (max {MAX_LEN})")
    if _CTRL_RE.search(text):
        raise InvalidMessage("control characters not allowed")
    return text.rstrip("\n")


BUILDER_HINT = "[pm builder:"


def builder_hint(cwd: str, host: str = "") -> str:
    """One line telling the agent the message came from Builder mode and how to drive the preview; ``host``
    is the first allowed external origin (``builder_frame_hosts``), left out when there is none."""
    where = f"path in that folder or {host}/..." if host else "path in that folder"
    return (f"{BUILDER_HINT} the preview shows {_CTRL_RE.sub('', cwd).strip()}; to show something, end your reply "
            f"with a line `Preview: <{where}>`]")


def with_builder_hint(text: str, cwd: str, host: str = "") -> str:
    """``text`` + blank line + the builder hint; unchanged when empty, a slash command or over MAX_LEN."""
    out = f"{text.rstrip()}\n\n{builder_hint(cwd, host)}"
    return text if not text.strip() or text.startswith("/") or len(out) > MAX_LEN else out


def strip_builder_hint(text: str | None) -> str:
    """Drop a trailing ``[pm builder: ...]`` line (shown text = what the user typed)."""
    head, _sep, last = (text or "").rstrip().rpartition("\n")
    return head.rstrip() if last.startswith(BUILDER_HINT) else (text or "")


def describe(exc: BaseException) -> str:
    """Short, sanitized reason for logs/flashes: class name or tmux stderr, capped.
    tmux stderr never contains message text (text goes via stdin or after ``--``)."""
    cause = exc.__cause__ or exc
    if isinstance(cause, tmuxctl.TmuxError):
        raw = "tmux: " + str(cause)
    elif isinstance(cause, MessageError):
        raw = str(cause)
    else:
        raw = type(cause).__name__
    return _NONPRINT_RE.sub(" ", raw).strip()[:_REASON_MAX]


def _tail(settings: Settings, pane_id: str) -> str:
    try:
        out = tmuxctl.capture_pane(settings, pane_id, 1)
    except tmuxctl.TmuxError:
        return ""
    return "\n".join([ln.rstrip() for ln in out.splitlines() if ln.strip()][-8:])


def _type_text(settings: Settings, pane: str, text: str) -> None:
    long = "\n" in text or len(text) >= PASTE_MIN_LEN
    if not long and ";" not in text and "\\" not in text:
        tmuxctl.send_keys(settings, pane, text)
        return
    # bracketed paste keeps embedded newlines from submitting early; the buffer is
    # loaded from stdin so tmux argv parsing cannot mangle ';' or '\\'
    buf = f"pm-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    tmuxctl.load_buffer(settings, buf, text)
    tmuxctl.run(settings, ["paste-buffer", "-p", "-d", "-b", buf, "-t", pane])
    time.sleep(PASTE_DELAY_S)


def send_message(settings: Settings, agent: Agent, text: str, *,
                 proc_root: Path = PROC) -> SendResult:
    text = validate_text(text)
    verify_target(settings, agent, proc_root=proc_root)
    pane = agent.pane_id
    typed = False
    last: tmuxctl.TmuxError | None = None
    submitted = True
    for attempt in range(2):
        try:
            if attempt:
                time.sleep(RETRY_DELAY_S)
                verify_target(settings, agent, proc_root=proc_root)
            if not typed:
                _type_text(settings, pane, text)
                typed = True
            time.sleep(ENTER_DELAY_S)
            before = _tail(settings, pane)
            tmuxctl.send_key(settings, pane, "Enter")
            time.sleep(CONFIRM_DELAY_S)
            submitted = bool(before) and _tail(settings, pane) != before
            last = None
            break
        except tmuxctl.TmuxError as e:
            last = e
        except NotAnAgent as e:
            raise MessageError("send failed: pane lost") from e
    if last is not None:
        raise MessageError("send failed") from last
    return SendResult(True, agent.id, pane, len(text), text.count("\n") + 1,
                      submitted=submitted)


def new_chat(settings: Settings, agent: Agent, cmd: str, *, proc_root: Path = PROC,
             sleep=time.sleep) -> SendResult:
    """Send the kind's ``/clear`` to the agent, then drop the pane's tmux scrollback.

    Protected sessions are allowed, exactly as for the message route (the allow-list protects
    sessions from kill/stop, and agents live in them). The history is cleared after the command went in, so the
    old conversation is gone from both the agent context and the tmux buffer."""
    verify_target(settings, agent, proc_root=proc_root)
    res = send_message(settings, agent, cmd, proc_root=proc_root)
    sleep(CLEAR_DELAY_S)  # let the TUI redraw after /clear before its scrollback is dropped
    try:
        tmuxctl.clear_history(settings, agent.pane_id)
    except tmuxctl.TmuxError as e:
        raise MessageError("history not cleared") from e
    return res


UPDATE_NOTICE = "restart to update"


def update_pending(settings: Settings, agent: Agent) -> bool:
    """Claude shows "Update installed · Restart to update" in its status line once a new build is on disk;
    ``/clear`` keeps the old process, so a new chat must quit and relaunch to pick the update up."""
    if agent.kind is not AgentKind.CLAUDE:
        return False
    try:
        screen = tmuxctl.capture_pane(settings, agent.pane_id, 1)
    except tmuxctl.TmuxError:
        return False
    return UPDATE_NOTICE in screen.lower()


EXIT_WAIT_S = 12.0
EXIT_POLL_S = 0.4
_SHELLS = frozenset({"sh", "bash", "zsh", "fish", "dash"})
_RESUME_FLAGS = frozenset({"-r", "--resume", "-c", "--continue", "--conversation",
                           "-i", "--prompt-interactive", "resume", "continue"})
_RESUME_WITH_ARG = frozenset({"-r", "--resume", "--conversation", "-i", "--prompt-interactive", "resume"})


def _cmdline(pid: int, proc_root: Path) -> list[str] | None:
    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
    except OSError:
        return None
    return [a for a in raw.decode("utf-8", "replace").split("\0") if a]


def _relaunch_argv(agent: Agent, proc_root: Path) -> list[str]:
    """The agent's own command line (flags kept) minus resume options and any initial prompt, so the
    relaunch is a fresh session."""
    argv = _cmdline(agent.pid, proc_root)
    if argv is None:
        raise Refused("cannot read the agent's command line")
    if not argv or not re.fullmatch(r"[\w./+-]+", argv[0]):
        raise Refused("unexpected agent command line")
    out, skip = [argv[0]], False
    for a in argv[1:]:
        if a == "--":
            break  # the initial prompt
        if skip:
            skip = False
            continue
        if a == "--use-system-ca" or a.endswith(".js"):
            continue  # wrapper-injected node args (cursor-agent), not the user's flags
        if a in _RESUME_FLAGS:
            skip = a in _RESUME_WITH_ARG
            continue
        out.append(a)
    return out


def _find_pane(settings: Settings, pane_id: str) -> TmuxPane | None:
    for s in tmuxctl.list_sessions(settings):
        for w in s.windows:
            for p in w.panes:
                if p.id == pane_id:
                    return p
    return None


def _pane_ends_with_agent(pane_pid: int, agent_pid: int, proc_root: Path) -> bool:
    """True when the pane's own process ends with the agent, so tmux closes the pane (and a last window
    its session) on /exit: the agent is the pane process (pm's Launch: ``<shell> -c 'exec ...'`` + argv), or
    the pane runs a command through ``<shell> -c`` (older Launch: ``new-window -- "<agent ...>"``). Only an
    interactive shell outlives it.
    pane_current_command cannot tell: a non-interactive ``fish -c`` has no job control and is listed
    as the pane's command while the agent still runs."""
    if pane_pid == agent_pid:
        return True
    argv = _cmdline(pane_pid, proc_root)
    if not argv:
        return False  # unreadable: keep the old shell path, which waits for the agent's pid anyway
    return Path(argv[0].lstrip("-")).name not in _SHELLS or argv[1:2] == ["-c"]


def _wait_exit(settings: Settings, agent: Agent, proc_root: Path, sleep, limit: float, done) -> TmuxPane:
    """Poll until ``done(pane)`` holds for the agent's pane and return that pane; MessageError when the pane
    is gone, Refused after ``limit`` seconds."""
    waited = 0.0
    while True:
        try:
            pane = _find_pane(settings, agent.pane_id)
        except tmuxctl.TmuxError as e:
            raise MessageError("pane lookup failed") from e
        if pane is None:
            raise MessageError("pane is gone")
        if done(pane):
            return pane
        if waited >= limit:
            raise Refused("agent did not exit")
        sleep(EXIT_POLL_S)
        waited += EXIT_POLL_S


def _respawn_chat(settings: Settings, agent: Agent, exit_cmd: str, argv: list[str], *, proc_root: Path,
                  sleep, limit: float, env: dict[str, str]) -> None:
    """Quit the agent while tmux keeps its pane (remain-on-exit), then start ``argv`` in that same pane."""
    try:
        tmuxctl.set_remain_on_exit(settings, agent.pane_id, True)
    except tmuxctl.TmuxError as e:
        raise MessageError("restart failed") from e
    try:
        send_message(settings, agent, exit_cmd, proc_root=proc_root)
        _wait_exit(settings, agent, proc_root, sleep, limit, lambda p: p.dead)
        tmuxctl.clear_history(settings, agent.pane_id)
        tmuxctl.respawn_pane(settings, agent.pane_id, agent.cwd, argv, env=env)
    except tmuxctl.TmuxError as e:
        raise MessageError("restart failed") from e
    finally:
        try:
            tmuxctl.set_remain_on_exit(settings, agent.pane_id, False)
        except tmuxctl.TmuxError:
            pass  # the pane is gone: nothing left to reset


def restart_chat(settings: Settings, agent: Agent, exit_cmd: str, *, proc_root: Path = PROC,
                 sleep=time.sleep, wait_s: float | None = None) -> SendResult:
    """New chat for kinds whose ``/clear`` keeps the same process: quit the agent, then start the same
    command again in the same pane. A new process is a new agent (new permanent id)."""
    verify_target(settings, agent, proc_root=proc_root)
    argv = _relaunch_argv(agent, proc_root)
    limit = EXIT_WAIT_S if wait_s is None else wait_s
    account = registry.account_of(settings, agent.id)  # the stored account; the env is never read from /proc
    env = account_env(settings, agent.kind.value, account) if account else {}
    try:
        pane = _find_pane(settings, agent.pane_id)
    except tmuxctl.TmuxError as e:
        raise MessageError("pane lookup failed") from e
    if pane is not None and _pane_ends_with_agent(pane.pid, agent.pid, proc_root):
        _respawn_chat(settings, agent, exit_cmd, argv, proc_root=proc_root, sleep=sleep,
                      limit=limit, env=env)
        registry.expect_account(settings, agent.kind.value, agent.pane_id, account)
        return SendResult(True, agent.id, agent.pane_id, detail="restart")
    typed = ["env", *(f"{k}={v}" for k, v in env.items()), *argv] if env else argv  # ``env K=V`` reads alike in sh and fish
    try:  # before /exit: a refusal must never leave the agent stopped without its relaunch
        tmuxctl.check_typeable(typed)
    except tmuxctl.ShellQuoteError as e:
        raise Refused(str(e)) from e
    send_message(settings, agent, exit_cmd, proc_root=proc_root)
    pane = _wait_exit(settings, agent, proc_root, sleep, limit,
                      lambda p: p.command in _SHELLS and not (proc_root / str(agent.pid)).exists())
    try:  # quoted for the shell that reads the line: sh quoting typed into fish can run a second command
        line = tmuxctl.quote_for_shell(pane.command, typed)
    except tmuxctl.ShellQuoteError as e:
        raise Refused(str(e)) from e
    try:
        tmuxctl.clear_history(settings, agent.pane_id)
        tmuxctl.send_keys(settings, agent.pane_id, "clear; " + line)
        sleep(ENTER_DELAY_S)
        tmuxctl.send_key(settings, agent.pane_id, "Enter")
    except tmuxctl.TmuxError as e:
        raise MessageError("restart failed") from e
    registry.expect_account(settings, agent.kind.value, agent.pane_id, account)
    return SendResult(True, agent.id, agent.pane_id, detail="restart")


def key_seq(key: str) -> list[str] | None:
    """A quick key, or a short space-separated sequence of them ("Down Enter"); None if invalid."""
    keys = key.split(" ")
    if 1 <= len(keys) <= 10 and all(k in QUICK_KEYS for k in keys):
        return keys
    return None


def quick_key(settings: Settings, agent: Agent, key: str, *,
              proc_root: Path = PROC) -> SendResult:
    keys = key_seq(key)
    if keys is None:
        raise InvalidMessage(f"key not allowed: {key!r}")
    verify_target(settings, agent, proc_root=proc_root)
    try:
        for i, k in enumerate(keys):
            if i:
                time.sleep(0.1)
            tmuxctl.send_key(settings, agent.pane_id, k)
    except tmuxctl.TmuxError as e:
        raise MessageError("send failed") from e
    return SendResult(True, agent.id, agent.pane_id, detail=key)


ASK_PANE_LINES = 120


def answer_other(settings: Settings, agent: Agent, text: str, *, note: int = 0,
                 proc_root: Path = PROC) -> SendResult:
    """Free text into an open AskUserQuestion form: ``note`` 0 = the "Type something" answer (its digit, then
    the text and Enter), ``note`` N = a note on option N (arrows from the ❯ row to N, "n", the text, Enter).

    The keys are worked out from a fresh capture, never taken from the client; refused unless the pane shows
    the form, so the text never lands in the composer as a message. One line only."""
    text = validate_text(text)
    if "\n" in text:
        raise InvalidMessage("one line only")
    if not 0 <= note <= ask_mod.MAX_OPTIONS:
        raise InvalidMessage("invalid option")
    verify_target(settings, agent, proc_root=proc_root)
    try:
        screen = render_pane(tmuxctl.capture_pane(settings, agent.pane_id, ASK_PANE_LINES))
    except tmuxctl.TmuxError as e:
        raise MessageError("pane capture failed") from e
    lines = [ln.rstrip() for ln in screen.splitlines()]
    ask, _ = ask_mod.find_ask(lines)
    if note:
        ok = ask is not None and any(o.key.split()[0] == str(note) for o in ask.options)
        keys = ask_mod.note_keys(lines, note) if ok else None
    else:
        keys = ask_mod.other_key(ask)
    if keys is None:
        raise Refused("no open question for this answer in the pane")
    quick_key(settings, agent, keys, proc_root=proc_root)
    time.sleep(ENTER_DELAY_S)
    return send_message(settings, agent, text, proc_root=proc_root)


def interrupt(settings: Settings, agent: Agent, *, proc_root: Path = PROC) -> SendResult:
    """C-c, then Escape (cancels a pending prompt in Claude/Codex TUIs)."""
    verify_target(settings, agent, proc_root=proc_root)
    try:
        tmuxctl.send_key(settings, agent.pane_id, "C-c")
        tmuxctl.send_key(settings, agent.pane_id, "Escape")
    except tmuxctl.TmuxError as e:
        raise MessageError("send failed") from e
    return SendResult(True, agent.id, agent.pane_id, detail="C-c,Escape")


# "{remote}" in a text = pm.toml push_remote (filled in by load_snippets)
DEFAULT_SNIPPETS: list[tuple[str, str]] = [
    ("Continue", "continue"),
    ("Run tests and fix", "Run the tests and fix any failures."),
    ("Commit and push", "Commit your changes and push the branch to {remote}."),
    ("Summarise status", "Summarise the current status and what remains."),
    ("Stop and handoff", "Stop now and write a handoff note for the next session."),
]


def load_snippets(settings: Settings) -> list[tuple[str, str]]:
    out = [(label, text.replace("{remote}", settings.push_remote)) for label, text in DEFAULT_SNIPPETS]
    extra = settings.snippets or []
    items = extra.items() if isinstance(extra, dict) else extra
    for it in items:
        try:
            label, text = it
        except (TypeError, ValueError):
            continue
        if isinstance(label, str) and isinstance(text, str) and label and text \
                and len(text) <= MAX_LEN:
            out = [s for s in out if s[0] != label] + [(label, text)]
    return out
