"""Claude Code transcript: ~/.claude/projects/<encoded cwd>/<session uuid>.jsonl.

Location helpers plus ``read_entries`` (incremental by byte offset) for ``pm.chat.log`` and
``pm.chat.itemsync``.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import stat
from dataclasses import replace
from pathlib import Path

from pm.core.errors import PmError
from pm.core.fsutil import encode_cwd, safe_join
from pm.core.procstat import PROC, ProcStat, read_proc_stat

from .common import (
    UUID_RE,
    Block,
    Chunk,
    RawEntry,
    parse_ts,
    read_new_lines,
    read_tail_bytes,
    text_step,
    tool_block,
    tool_name,
    tool_step,
)

# user entries whose text starts like this are injected by the harness, not typed by the user
SYSTEMISH = ("<command-", "<local-command", "<system-reminder", "<task-notification", "[Request interrupted")

# background tasks: a task id from the launch result, a notification or TaskStop (else ignored)
_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_BG_BASH = re.compile(r"^Command running in background with ID: ([A-Za-z0-9_-]{1,64})\b")
_BG_AGENT = re.compile(r"^agentId: ([A-Za-z0-9_-]{1,64})\b", re.MULTILINE)
_NOTE_HEAD = 2048  # the id/status tags come before summary and result
_NOTE_TASK = re.compile(r"^<task-id>([A-Za-z0-9_-]{1,64})</task-id>$", re.MULTILINE)
_NOTE_TOOL = re.compile(r"^<tool-use-id>([A-Za-z0-9_-]{1,80})</tool-use-id>$", re.MULTILINE)
_NOTE_STATUS = re.compile(r"^<status>([a-z_]{1,20})</status>$", re.MULTILINE)
_STOP_TOOLS = ("TaskStop", "KillShell")

# a background Bash task's output file ends with this line once the command is done
_TASK_EXIT = re.compile(rb"\[exited with code (\d{1,3})\]\s*$")
_TASK_TAIL = 64


def transcript_path(cwd: str, session_uuid: str, home: Path | None = None) -> Path | None:
    if not cwd or not UUID_RE.match(session_uuid or ""):
        return None
    base = (home or Path.home()) / ".claude" / "projects"
    return base / encode_cwd(cwd) / f"{session_uuid}.jsonl"


_TASK_DIRS = 1000


def task_exit(transcript: Path, task_id: str, *, tmp_root: Path | None = None) -> tuple[int, float] | None:
    """(exit code, mtime) of a finished background task, else None; never raises.

    Reads only the last 64 bytes of ``<tmp_root>/<project slug>/<session uuid>/tasks/<task id>.output``
    (``tmp_root`` = ``/tmp/claude-<uid>``, owned by us and not a symlink) for the final
    ``[exited with code N]`` line; the bytes are never kept, logged or shown. The session folder is the
    one the claude process started with, which a resumed or cleared chat's transcript no longer names:
    the transcript's own session is tried first, then the project's other session folders (at most
    ``_TASK_DIRS``) for that exact task id.
    """
    try:
        if not _TASK_ID.match(task_id or ""):
            return None
        slug, sess = transcript.parent.name, transcript.stem
        if not slug or not UUID_RE.match(sess):
            return None
        root = tmp_root if tmp_root is not None else Path(f"/tmp/claude-{os.getuid()}")
        st = root.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            return None
        path = _task_file(root, slug, sess, task_id)
        if path is None:
            return None
        st = path.lstat()
        m = _TASK_EXIT.search(read_tail_bytes(path, _TASK_TAIL)[0])
        return (int(m.group(1)), st.st_mtime) if m else None
    except (OSError, ValueError, PmError):  # missing file or dir; safe_join's Invalid / Denied
        return None


def _task_file(root: Path, slug: str, sess: str, task_id: str) -> Path | None:
    """The task's output file (a regular file, no symlink anywhere below ``root``), else None."""
    names = [sess]
    try:
        with os.scandir(safe_join(root, slug)) as it:
            for i, e in enumerate(it):
                if i >= _TASK_DIRS:
                    break
                if e.name != sess and UUID_RE.match(e.name) and e.is_dir(follow_symlinks=False):
                    names.append(e.name)
    except (OSError, PmError):
        pass
    for name in names:
        try:
            path = safe_join(root, f"{slug}/{name}/tasks/{task_id}.output")
            if stat.S_ISREG(path.lstat().st_mode):
                return path
        except (OSError, PmError):
            continue
    return None


_MAX_SESSION_JSON = 64 * 1024


def load_session(sessions_dir: Path, pid: int, st: ProcStat) -> dict | None:
    """``<sessions_dir>/<pid>.json`` when it belongs to this very process (pid + start time), else None."""
    path = sessions_dir / f"{int(pid)}.json"  # int() guards the filename
    try:
        if path.stat().st_size > _MAX_SESSION_JSON:
            return None
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("pid") != pid:
        return None
    if str(data.get("procStart")) != str(st.starttime):
        return None  # stale file from a recycled pid
    return data


def live_session_id(
    pid: int,
    *,
    sessions_dir: Path | None = None,
    proc_root: Path = PROC,
) -> str | None:
    """Current Claude sessionId of a live pid (``/clear`` and resume change it), or None.

    Opens only ``<sessions_dir>/<pid>.json`` and checks it against the process
    start time, exactly like ``pm.agents.detect.detect_agents``.
    """
    sessions_dir = sessions_dir or Path("~/.claude/sessions").expanduser()
    st = read_proc_stat(int(pid), proc_root)
    if st is None:
        return None
    sess = load_session(sessions_dir, int(pid), st)
    sid = sess.get("sessionId") if sess else None
    return sid if isinstance(sid, str) and sid else None


def entry_text(entry: dict) -> tuple[str, str]:
    """(role, text) for an entry carrying human-readable text, else ("", "")."""
    kind = entry.get("type")
    msg = entry.get("message")
    if kind not in ("assistant", "user") or not isinstance(msg, dict):
        return "", ""
    if entry.get("isSidechain") or entry.get("isMeta"):
        return "", ""
    content = msg.get("content")
    parts: list[str] = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if kind == "user" and block.get("type") == "tool_result":
                return "", ""
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
    text = "\n".join(p for p in parts if p.strip()).strip()
    if not text:
        return "", ""
    if kind == "user" and text.startswith(SYSTEMISH):
        return "", ""
    return str(kind), text


def _user_text(entry: dict) -> str:
    """Text of a real user prompt, else "" (tool results, meta, sidechain, harness injections)."""
    if entry.get("isSidechain") or entry.get("isMeta") or entry.get("isCompactSummary"):
        return ""
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    parts: list[str] = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                return ""
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
    text = "\n".join(p for p in parts if p.strip()).strip()
    return "" if text.startswith(SYSTEMISH) else text


def _queued_text(entry: dict) -> str:
    """Text of a prompt Claude took in mid-turn (``attachment`` ``queued_command`` typed by a human),
    else "" (task notifications, peer messages, other attachments)."""
    att = entry.get("attachment")
    if not isinstance(att, dict) or att.get("type") != "queued_command" or att.get("commandMode") != "prompt":
        return ""
    origin = att.get("origin")
    if not isinstance(origin, dict) or origin.get("kind") != "human":
        return ""
    prompt = att.get("prompt")
    if isinstance(prompt, list):
        prompt = "\n".join(b["text"] for b in prompt
                           if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
    text = prompt.strip() if isinstance(prompt, str) else ""
    return "" if text.startswith(SYSTEMISH) else text


def _narration(block: dict) -> bool:
    """A thinking block Claude writes as user-facing narration (its signature names "narration")."""
    sig = block.get("signature")
    if not isinstance(sig, str):
        return False
    try:
        return b"narration" in base64.b64decode(sig[:64] + "==", validate=False)
    except (ValueError, binascii.Error):
        return False


def _assistant(entry: dict) -> RawEntry:
    """One assistant piece (``uid``/``ts`` filled by the caller): text, thinking, tools, steps, blocks."""
    msg = entry.get("message")
    msg = msg if isinstance(msg, dict) else {}
    content = msg.get("content")
    model = msg.get("model")[:80] if isinstance(msg.get("model"), str) else ""
    end_turn = msg.get("stop_reason") == "end_turn"
    if isinstance(content, str):
        st = text_step(content)
        blocks = (Block("text", text=content.strip()),) if content.strip() else ()
        return RawEntry("assistant", "", text=content.strip(), steps=(st,) if st else (), blocks=blocks,
                        model=model, end_turn=end_turn)
    texts: list[str] = []
    thinking: list[str] = []
    tools: list[str] = []
    steps: list[tuple[str, str, str]] = []
    blocks: list[Block] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        typ = block.get("type")
        st = None
        text = block.get("text")
        if typ == "thinking" and _narration(block):
            typ, text = "text", block.get("thinking")  # narration: a note to the user, shown as text
        if typ == "text" and isinstance(text, str) and text.strip():
            texts.append(text.strip())
            blocks.append(Block("text", text=text.strip()))
            st = text_step(text)
        elif typ == "thinking" and isinstance(block.get("thinking"), str) and block["thinking"].strip():
            thinking.append(block["thinking"].strip())  # often "" (redacted, signature only)
            st = text_step(block["thinking"], "thinking")
        elif typ in ("tool_use", "server_tool_use"):
            tools.append(tool_name(block.get("name")))
            blocks.append(tool_block(block.get("id"), block.get("name"), block.get("input")))
            st = tool_step(block.get("name"), block.get("input"))
        if st:
            steps.append(st)
    return RawEntry("assistant", "", text="\n\n".join(texts), thinking="\n\n".join(thinking), tools=tuple(tools),
                    steps=tuple(steps), blocks=tuple(blocks), model=model, end_turn=end_turn)


_EXIT = re.compile(r"^\s*Exit code (-?\d{1,5})\b")


def _result_text(content: object, n: int = 64) -> str:
    """Head of a tool result's text (only to read an "Exit code N" prefix or a background task id)."""
    if isinstance(content, str):
        return content[:n]
    for b in content if isinstance(content, list) else []:
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
            return b["text"][:n]
    return ""


def _notification(text: object, ts: float) -> RawEntry | None:
    """A background task's end from a ``<task-notification>``: tool id, task id and ok/err only
    (never its summary, result or output file)."""
    if not isinstance(text, str) or not text.startswith("<task-notification"):
        return None
    head = text[:_NOTE_HEAD]
    task, tool, status = _NOTE_TASK.search(head), _NOTE_TOOL.search(head), _NOTE_STATUS.search(head)
    if task is None and tool is None:
        return None
    return RawEntry("bgend", tool.group(1) if tool else "", ts, "ok" if status and status.group(1) == "completed"
                    else "err", note=task.group(1) if task else "")


def _queued_note(entry: dict, ts: float) -> RawEntry | None:
    """A task notification delivered as a ``queued_command`` attachment."""
    att = entry.get("attachment")
    if not isinstance(att, dict) or att.get("type") != "queued_command":
        return None
    origin = att.get("origin")
    if not isinstance(origin, dict) or origin.get("kind") != "task-notification":
        return None
    body = att.get("prompt") if isinstance(att.get("prompt"), str) else att.get("content")
    return _notification(body, ts)


def _stops(entry: dict, ts: float) -> list[RawEntry]:
    """A "bgstop" entry per TaskStop / KillShell call (its task id only)."""
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    out: list[RawEntry] = []
    for b in content if isinstance(content, list) else []:
        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in _STOP_TOOLS:
            args = b.get("input") if isinstance(b.get("input"), dict) else {}
            task = args.get("task_id") or args.get("shell_id")
            if isinstance(task, str) and _TASK_ID.match(task):
                out.append(RawEntry("bgstop", "", ts, note=task))
    return out


def _bg_task(entry: dict, block: dict, only: bool) -> str:
    """Task id when this tool result is a background launch, else "". The entry's structured
    ``toolUseResult`` (only when it holds this one result), else the head of the result text."""
    tur = entry.get("toolUseResult") if only else None
    tid: object = None
    if isinstance(tur, dict):
        if tur.get("backgroundTaskId"):
            tid = tur.get("backgroundTaskId")
        elif tur.get("isAsync") is True:
            tid = tur.get("agentId")
    if tid is None:
        head = _result_text(block.get("content"), 256)
        m = _BG_BASH.match(head) or (_BG_AGENT.search(head) if head.startswith("Async agent launched") else None)
        tid = m.group(1) if m else None
    return tid if isinstance(tid, str) and _TASK_ID.match(tid) else ""


def _results(entry: dict, ts: float) -> list[RawEntry]:
    """One "result" entry per tool_result block: ok/err and the note "exit N" (nothing else); a
    background launch is a "bg" entry with its task id instead."""
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"] \
        if isinstance(content, list) else []
    out: list[RawEntry] = []
    for b in blocks:
        tid = b.get("tool_use_id")
        if not isinstance(tid, str) or not tid:
            continue
        task = _bg_task(entry, b, len(blocks) == 1)
        if task:
            out.append(RawEntry("bg", tid[:80], ts, note=task))
            continue
        m = _EXIT.match(_result_text(b.get("content")))
        out.append(RawEntry("result", tid[:80], ts, "err" if b.get("is_error") is True else "ok",
                            f"exit {m.group(1)}" if m else ""))
    return out


def read_entries(path: Path, pos: int = -1) -> Chunk:
    """User prompts (``mid_turn`` for ones taken in while working), assistant pieces and tool results
    after byte ``pos`` (-1: the last 4 MB)."""
    lines, new_pos, more = read_new_lines(path, pos)
    out: list[RawEntry] = []
    for off, raw in lines:
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        kind = entry.get("type")
        uid = entry.get("uuid") if isinstance(entry.get("uuid"), str) and entry.get("uuid") else f"o{off}"
        uid = uid[:80]
        ts = parse_ts(entry.get("timestamp"))
        if kind == "user":
            msg = entry.get("message")
            note = _notification(msg.get("content"), ts) if isinstance(msg, dict) else None
            if note is not None:
                out.append(note)
                continue
            text = _user_text(entry)
            if text:
                out.append(RawEntry("user", uid, ts, text))
            else:
                out.extend(_results(entry, ts))
        elif kind == "assistant":
            e = _assistant(entry)
            if e.text or e.thinking or e.tools or e.end_turn:
                out.append(replace(e, uid=uid, ts=ts))
            out.extend(_stops(entry, ts))
        elif kind == "attachment":
            note = _queued_note(entry, ts)
            if note is not None:
                out.append(note)
                continue
            text = _queued_text(entry)
            if text:
                out.append(RawEntry("user", uid, ts, text, mid_turn=True))
    return Chunk(out, new_pos, more)
