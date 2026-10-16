"""Chat-style view of one agent: latest message plus any open question.

Blocking; callers use ``asyncio.to_thread``. Never raises: any failure yields
a ChatView with source "none". Transcript text and pane text are never logged
(only exception type names). Every string passes through ``render_pane``
(strip ANSI, then mask) before it enters a ChatView.

Claude transcript access is limited to exactly one file,
``~/.claude/projects/<encoded cwd>/<session uuid>.jsonl``, and only its last
256 KB is read. Other agents use ``pm.chat.sources`` (devin transcript json,
codex rollout, cursor chat store), same bound.

This module shows only the latest message; the full history (user prompts,
assistant turns with thinking and tool-call counts) is ``pm.chat.log``.

``ChatView.source``: ``transcript`` (a session store), ``pane`` (live pane
text, also used when the pane holds a newer assistant block than the store,
which lags while the agent is still generating) or ``none``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from pm.chat import sources as chat_sources
from pm.chat.ask import (  # noqa: F401 - pane parsing lives in pm.chat.ask; re-exported for callers/tests
    _BORDER_ONLY,
    _SPINNER,
    _STATUS,
    _box_top,
    _tail_lines,
    _unbox,
    ask_form,
    find_ask,
    merge,
)
from pm.chat.queued import find_queued
from pm.chat.sources.claude import (  # noqa: F401 - re-exported
    SYSTEMISH,
    encode_cwd,
    live_session_id,
    transcript_path,
)
from pm.chat.sources.claude import entry_text as _entry_text
from pm.chat.sources.common import TAIL_BYTES, mtime, parse_ts, read_tail_lines
from pm.chat.text import clean_text as _clean
from pm.core.cache import LRU
from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, AgentState, Ask, AskQuestion, ChatView
from pm.security.mask import render_pane
from pm.tmux import ctl as tmuxctl

log = logging.getLogger("pm.chat")

MAX_LAST_TEXT = 20000
PANE_LINES = 120

# --------------------------------------------------------------------------
# transcript
# --------------------------------------------------------------------------

def _read_tail(path: Path) -> list[str]:
    return read_tail_lines(path, TAIL_BYTES)


_parse_ts = parse_ts


_SYSTEMISH = SYSTEMISH


@dataclass(frozen=True)
class TranscriptInfo:
    role: str = ""
    text: str = ""
    ts: float = 0.0
    ask_form: tuple[AskQuestion, ...] = ()  # pending AskUserQuestion form of the newest assistant entry
    recent: tuple[str, ...] = ()  # newest-first assistant entry texts (for pane dedup)
    task: str = ""  # newest real user text (the current task), first line, clipped
    task_ts: float = 0.0  # timestamp of that user message


_TAG_BLOCK = re.compile(r"<([A-Za-z]+[-_][\w-]*)(?:\s[^>]*)?>.*?</\1>", re.DOTALL)
_TAG = re.compile(r"</?[A-Za-z][\w-]*(?:\s[^>]*)?>")


def _task_line(text: str) -> str:
    """First non-empty line of real user text, bounded, for the one-line header.

    System-injected tag blocks (<task-notification>, <system-reminder>, ...) are
    dropped and stray tags stripped; "" when nothing meaningful remains.
    """
    text = _TAG_BLOCK.sub(" ", text)
    text = _TAG.sub(" ", text)
    for ln in text.splitlines():
        ln = " ".join(ln.split())
        if ln:
            return ln if len(ln) <= 200 else ln[:199] + "\u2026"
    return ""


def read_transcript_info(path: Path) -> TranscriptInfo:
    """Newest message from the transcript tail.

    A user text entry is returned as-is. For assistant output the whole latest
    run is joined: consecutive assistant entries with no user entry (text or
    tool_result) between them, so a reply split over several jsonl lines
    (text, tool_use, text) is not truncated to its last fragment. Tool-only
    assistant entries and tool_result entries are transparent while looking
    for the newest text, but a tool_result ends the run once it has started.
    """
    role, ts, form = "", 0.0, ()
    run: list[str] = []  # newest first
    run_open = False
    done = False
    recent: list[str] = []
    seen_assistant = False
    task, task_ts = "", 0.0
    for raw in reversed(_read_tail(path)):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        if kind not in ("assistant", "user") or entry.get("isSidechain"):
            continue
        if not seen_assistant and kind == "assistant":
            seen_assistant = True
            form = ask_form(entry)
        r, t = _entry_text(entry)
        if kind == "user" and t and not task:
            task = _task_line(t)  # "" for tag-only messages: keep looking at older ones
            if task:
                task_ts = _parse_ts(entry.get("timestamp"))
        if kind == "assistant" and t and len(recent) < 6:
            recent.append(t)
        if done:
            if len(recent) >= 6 and task:
                break
            continue
        if kind == "assistant":
            if t:
                if not run_open:
                    role, ts = "assistant", _parse_ts(entry.get("timestamp"))
                    run_open = True
                if sum(map(len, run)) <= MAX_LAST_TEXT:  # older fragments past the cap are dropped
                    run.append(t)
            continue
        # user entry
        if t:  # real user text
            if not run_open:
                role, ts = "user", _parse_ts(entry.get("timestamp"))
                run.append(t)
            done = True
        elif run_open and not entry.get("isMeta"):
            done = True  # tool_result (or other non-text user entry) ends the run
        if done and len(recent) >= 6 and task:
            break
    text = "\n\n".join(reversed(run)).strip()
    return TranscriptInfo(role if text else "", text, ts if text else 0.0, form, tuple(recent), task, task_ts)


def read_transcript(path: Path) -> tuple[str, str, float, str]:
    """(role, text, ts, ask_tool_question) from the newest relevant entries."""
    i = read_transcript_info(path)
    return i.role, i.text, i.ts, i.ask_form[0].question if i.ask_form else ""


# --------------------------------------------------------------------------
# pane parsing
# --------------------------------------------------------------------------


_BULLET = "\u25cf\u23fa\u2022"
_MARK_LINE = re.compile(r"^[\u2500-\u257f\u258e\u23bf\u2506\u2514\u251c]")  # box drawing, ▎, ⎿
_PROMPT_LINE = re.compile(r"^(?:[\u2502\u2503]\s*)?[\u276f\u276d\u203a\u2192>$#%](?:\s|$)")
_HIST_PROMPT = re.compile(r"^[\u276f\u276d\u203a\u2192]\s")
_THINKING = re.compile(
    r"^[\u2733\u2736\u273b\u273d\u2722\u2234\u00b7\u2800-\u28ff\u25d0-\u25d3*]?\s*"
    r"(thinking|reasoning|planning|working|generating|cogitating|processing)\b.*(\u2026|\.\.\.|\()", re.IGNORECASE)
_FOOTER = re.compile(r"\d+(?:\.\d+)?%\s*\u00b7|\u00b7\s*\d+(?:\.\d+)?%|^~?/[\w.\-/]+$")
# Claude tool header: "Name(args)", "mcp__srv__tool(args)", "... (ctrl+o to expand)"
_CLAUDE_TOOL = re.compile(r"^[\w.:\-]+\(.*|.*\(ctrl\+o to expand\)\s*$")
# codex / generic tool-summary headers: "Ran cmd", "Explored", "Edited x (+1 -1)"
_TOOL_WORD = re.compile(
    r"^(?:Ran|Run|Running|Explored|Exploring|Edited|Editing|Edit|Added|Searched|Searching|Search|Read|Reading|"
    r"Listed|Listing|List|Called|Calling|Waited|Worked|Fetched|Fetching|Fetch|Wrote|Write|Writing|Executed|"
    r"Executing|Command|Used|Grep|Glob|Bash|Updated Plan|Updated Todos)\b")
_BANNER = re.compile(r"^(?:Cursor Agent|Devin(?: CLI)?|(?:OpenAI )?Codex|Antigravity(?: CLI)?)$|^(?:Cursor Agent|Devin|(?:OpenAI )?Codex|Antigravity(?: CLI)?)\b.*\bv?\d+\.\d+|^v\d{4}\.\d+[\w.\-]*$", re.IGNORECASE)
_DEVIN_TOOL = re.compile(r"^[A-Z][\w-]*(?:\s+[A-Z][\w-]*){0,3}$")


def _is_chrome(s: str) -> bool:
    return bool(_BORDER_ONLY.match(s)) or bool(_STATUS.search(s)) or bool(_SPINNER.match(s))


def _is_tool_head(body: str, kind: AgentKind | str | None) -> bool:
    if kind == AgentKind.CLAUDE:
        return bool(_CLAUDE_TOOL.match(body))
    if len(body) <= 100 and not body.endswith((".", "!", "?", ":")) and _TOOL_WORD.match(body):
        return True
    return kind == AgentKind.DEVIN and bool(_DEVIN_TOOL.match(body))


def _find_input(lines: list[str]) -> int:
    """Index of the input prompt line (<= 5 non-blank lines below it), else len(lines)."""
    nonblank = [i for i, ln in enumerate(lines) if ln.strip()]
    for pos in range(len(nonblank) - 1, max(-1, len(nonblank) - 11), -1):
        i = nonblank[pos]
        if _PROMPT_LINE.match(lines[i].strip()):
            if len(nonblank) - 1 - pos <= 5:
                return i
            break
    return len(lines)


def pane_last_block(lines: list[str], kind: AgentKind | str | None = None, *, after_prompt: bool = False) -> str:
    """Latest assistant prose above the input prompt.

    Hard separators (never part of the answer): drawn boxes and their rows
    (Devin tool boxes, Claude ``⎿`` results, Cursor ``▎`` diffs), prompt/history
    lines, spinner/status/footer lines, ``$ cmd`` lines, tool-summary headers
    ("Ran Command", "Read File", "Edited x", Claude ``Tool(args)``) and any
    deeper-indented output lines right after them. Blank lines inside prose are
    kept, so the whole last message (all paragraphs) is returned. With
    ``after_prompt`` the block must also come after the last history prompt
    (a user message already sent), else "" (the answer is not on screen yet).
    """
    end = _find_input(lines)
    body_lines = lines[:end]
    kinds: list[str] = []
    prompts: list[int] = []
    hold: int | None = None
    for i, raw in enumerate(body_lines):
        s = raw.rstrip()
        st = s.strip()
        if not st:
            kinds.append("blank")
            hold = None
            continue
        ind = len(s) - len(s.lstrip())
        if hold is not None and ind > hold:
            kinds.append("sep")
            continue
        hold = None
        head = st[0] in _BULLET
        body = st[1:].strip() if head else st
        nxt = body_lines[i + 1].strip() if i + 1 < len(body_lines) else ""
        if _HIST_PROMPT.match(st):
            prompts.append(i)
        tool = (
            _MARK_LINE.match(st) is not None
            or _HIST_PROMPT.match(st) is not None
            or _is_chrome(st)
            or _FOOTER.search(st) is not None
            or _THINKING.match(st) is not None
            or _BANNER.match(st) is not None
            or body.startswith("$ ")
            or (head and (_is_tool_head(body, kind) or nxt.startswith("\u23bf")))
        )
        if tool:
            kinds.append("sep")
            hold = ind
        else:
            kinds.append("prose")
    last = len(kinds) - 1
    while last >= 0 and kinds[last] != "prose":
        last -= 1
    if last < 0 or (after_prompt and prompts and prompts[-1] > last):
        return ""
    first = last
    while first - 1 >= 0 and kinds[first - 1] in ("prose", "blank"):
        first -= 1
    block = [_unbox(ln) if kinds[j] == "prose" else "" for j, ln in enumerate(body_lines[first:last + 1], first)]
    while block and not block[0]:
        block.pop(0)
    if block:
        block[0] = re.sub(rf"^[{_BULLET}]\s+", "", block[0])
    out: list[str] = []
    for ln in block:
        if not ln and out and not out[-1]:
            continue
        out.append(ln)
    return "\n".join(out).strip()


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def _none(agent: Agent) -> ChatView:
    return ChatView(agent_id=agent.id, state=agent.state)


def chat_view(settings: Settings, agent: Agent, pane_text: str | None = None) -> ChatView:
    try:
        return _chat_view(settings, agent, pane_text)
    except Exception as exc:  # noqa: BLE001 - never raise; type only, no content
        log.warning("chat_view failed: %s", type(exc).__name__)
        return _none(agent)


def _fingerprint(text: str) -> str:
    """Alphanumerics only, so pane rendering (markdown, wrapping, bullets) still matches the store."""
    return re.sub(r"[\W_]+", "", text.lower())


def pane_is_newer(block: str, recent: tuple[str, ...]) -> bool:
    """True when ``block`` (latest pane prose) is not part of any recent stored message."""
    fb = _fingerprint(block)
    if len(fb) < 8:
        return False
    head = fb[:48]
    return not any(head in _fingerprint(r) for r in recent)


def _claude_info(agent: Agent) -> tuple[TranscriptInfo, float]:
    uuid = agent.session_uuid
    live = live_session_id(agent.pid) if agent.pid > 1 else None
    if live:  # /clear or resume since the last detect: the process knows best
        uuid = live
    path = transcript_path(agent.cwd, uuid or "")
    if path is None or not path.exists():
        return TranscriptInfo(), 0.0
    return read_transcript_info(path), mtime(path)


# (mtime, task, task_ts, deep): ``deep`` = an older-history scan already ran, so a tail without a real
# user message keeps the remembered value instead of re-reading megabytes on every transcript write.
_TASK_CACHE = LRU(max_bytes=1024 * 1024, max_entries=256)
_DEEP_WINDOWS = (1024 * 1024, 4 * 1024 * 1024, 8 * 1024 * 1024)  # tail sizes tried when the 256 KiB tail has none


def _deep_task(path: Path) -> tuple[str, float]:
    """Newest real user text further back than the tail: a long turn buries it under tool_results."""
    for limit in _DEEP_WINDOWS:
        try:
            lines = read_tail_lines(path, limit)
        except OSError:
            return "", 0.0
        for raw in reversed(lines):
            if '"user"' not in raw:
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(entry, dict) or entry.get("type") != "user" or entry.get("isSidechain"):
                continue
            task = _task_line(_entry_text(entry)[1])
            if task:
                return task, _parse_ts(entry.get("timestamp"))
        try:
            if path.stat().st_size <= limit:
                break  # the whole file was read
        except OSError:
            break
    return "", 0.0


def _last_user(agent: Agent) -> tuple[str, float]:
    if agent.kind.value != "claude":
        return "", 0.0
    path = transcript_path(agent.cwd, agent.session_uuid or "")
    if path is None and agent.transcript and Path(agent.transcript).is_file():
        path = Path(agent.transcript)
    if path is None:
        return "", 0.0
    try:
        mt = path.stat().st_mtime
    except OSError:
        return "", 0.0
    key = str(path)
    hit = _TASK_CACHE.get(key)
    if hit and hit[0] == mt:
        return hit[1], hit[2]
    info = read_transcript_info(path)
    task, ts, deep = info.task, info.task_ts, bool(hit and hit[3])
    if not task and hit and hit[1]:
        task, ts = hit[1], hit[2]  # no user text in the tail now: keep the one found before
    elif not task and not deep:
        task, ts = _deep_task(path)
        deep = True
    _TASK_CACHE.put(key, (mt, task, ts, deep))
    return task, ts


def last_user_task(agent: Agent) -> str:
    """One-line last user message of a Claude agent (cached by file mtime), '' when unknown."""
    return _last_user(agent)[0]


def last_user_task_ts(agent: Agent) -> float:
    """Epoch seconds of ``last_user_task`` (0.0 when unknown or the entry has no timestamp)."""
    return _last_user(agent)[1]


def _store_info(agent: Agent) -> tuple[TranscriptInfo, float]:
    if agent.pid <= 1 and agent.transcript:  # ended agent: the store file recorded while it ran
        path = Path(agent.transcript) if Path(agent.transcript).is_file() else None
    else:
        path = chat_sources.session_file(agent.kind, pid=agent.pid, cwd=agent.cwd, age_s=agent.age_s)
    if path is None:
        return TranscriptInfo(), 0.0
    latest = chat_sources.read_latest(agent.kind, path)
    if latest is None:
        return TranscriptInfo(), mtime(path)
    task = _task_line(latest.task) if latest.task else ""
    return (TranscriptInfo("assistant", latest.text, latest.ts, (), latest.recent or (latest.text,),
                           task, latest.task_ts if task else 0.0), mtime(path))


def _chat_view(settings: Settings, agent: Agent, pane_text: str | None) -> ChatView:
    if pane_text is None:
        try:
            pane_text = tmuxctl.capture_pane(settings, agent.pane_id, PANE_LINES)
        except Exception as exc:  # noqa: BLE001
            log.warning("capture failed: %s", type(exc).__name__)
            pane_text = ""
    plain = render_pane(pane_text or "")
    lines = _tail_lines(plain)

    ask, ask_start = find_ask(lines)
    ask_line = ask_start
    if ask:
        ask_start = _box_top(lines, ask_start)

    info, file_mtime = TranscriptInfo(), 0.0
    try:
        if agent.kind == AgentKind.CLAUDE:
            info, file_mtime = _claude_info(agent)
        elif agent.kind in (AgentKind.DEVIN, AgentKind.CODEX, AgentKind.CURSOR, AgentKind.ANTIGRAVITY):
            info, file_mtime = _store_info(agent)
    except Exception as exc:  # noqa: BLE001
        log.warning("session read failed: %s", type(exc).__name__)
        info = TranscriptInfo()
    role, text, ts = info.role, info.text, info.ts
    source = "transcript" if text else "none"

    pane_lines = lines[:ask_start] if ask else lines
    block = pane_last_block(pane_lines, agent.kind)
    if block:
        if not text:
            role, text, ts, source = "assistant", block, 0.0, "pane"
        elif _pane_may_lead(agent) and pane_is_newer(block, info.recent or (text,)):
            block = pane_last_block(pane_lines, agent.kind, after_prompt=True)
        else:
            block = ""
        if block and source != "pane":
            # the store only gains an entry when a message completes: the pane is ahead
            role, text, ts, source = "assistant", block, file_mtime, "pane"

    merged = merge(ask, info.ask_form, lines, ask_line)
    if merged is not ask:
        ask = merged
    elif ask and ask.kind == "choice" and info.ask_form:  # form not matched to the pane: its first question
        ask = Ask(ask.kind, info.ask_form[0].question, ask.options)

    return ChatView(
        agent_id=agent.id,
        state=agent.state,
        last_role=role if text else "",
        last_text=_clean(text, MAX_LAST_TEXT) if text else "",
        last_ts=ts,
        source=source if text else "none",
        asking=ask,
        task=_clean(info.task, 200) if info.task else "",
        task_ts=info.task_ts if info.task else 0.0,
        queued=_clean(find_queued(lines), MAX_LAST_TEXT) if agent.kind == AgentKind.CLAUDE else "",
    )


def _pane_may_lead(agent: Agent) -> bool:
    if agent.kind == AgentKind.CLAUDE:
        return agent.state in (AgentState.RUNNING, AgentState.IDLE, AgentState.UNKNOWN)
    return agent.state in (AgentState.RUNNING, AgentState.UNKNOWN)
