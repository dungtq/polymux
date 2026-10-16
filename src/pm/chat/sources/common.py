"""Shared helpers for the chat source readers: bounded tail reads, timestamps, history entries."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

TAIL_BYTES = 256 * 1024
# a session id / chat dir name in UUID form (Claude sessionId, Cursor chat dir); any case
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
WARN_PCT = 60
RED_PCT = 80


def read_tail_bytes(path: Path, limit: int = TAIL_BYTES) -> tuple[bytes, bool]:
    """Last ``limit`` bytes of ``path`` and whether the head was cut off."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        start = max(0, size - limit)
        f.seek(start)
        return f.read(limit), start > 0


def read_tail_lines(path: Path, limit: int = TAIL_BYTES) -> list[str]:
    data, cut = read_tail_bytes(path, limit)
    lines = data.split(b"\n")
    if cut:
        lines = lines[1:]  # partial first line
    return [ln.decode("utf-8", "replace") for ln in lines if ln.strip()]


def parse_ts(value: object) -> float:
    """ISO-8601 string (any fraction length, Z or offset) -> epoch s; 0.0 on failure."""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        v = float(value)
        return v / 1000.0 if v > 1e11 else v
    if not isinstance(value, str):
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def mtime(path: Path | None) -> float:
    """st_mtime of ``path`` (and of a SQLite ``-wal`` sibling); 0.0 when unknown. Stat only."""
    if path is None:
        return 0.0
    best = 0.0
    for p in (path, path.with_name(path.name + "-wal")):
        try:
            best = max(best, p.stat().st_mtime)
        except OSError:
            continue
    return best


# --------------------------------------------------------------------------
# full message history (pm.chat.log): incremental entry readers
# --------------------------------------------------------------------------

FIRST_BYTES = 4 * 1024 * 1024  # first ingest of an append-only file: its last 4 MB only
CHUNK_BYTES = 8 * 1024 * 1024  # most bytes read per sync; the rest waits for the next one
FIRST_ROWS = 2000  # first ingest of a row/step store: its last 2000 rows only
CHUNK_ROWS = 2000
MAX_TOOL_NAME = 64


@dataclass(frozen=True)
class Block:
    """One text block or tool call of an assistant piece, in order (``pm.chat.itemsync``). Unmasked.

    ``kind`` "text" / "think" (thinking, for kinds that keep it in clear): ``text`` is the whole block.
    ``kind`` "tool": ``id`` is the tool-use id ("" when the store has none), ``name`` the tool name,
    ``line`` its target (``tool_target``, pre-cut to ``STEP_RAW``), ``background`` when the input asks to run
    in the background. Never other inputs.
    """

    kind: str  # "text" | "think" | "tool"
    id: str = ""
    name: str = ""
    line: str = ""
    text: str = ""
    background: bool = False


@dataclass(frozen=True)
class RawEntry:
    """One store entry relevant to the chat history. Unmasked: the caller masks.

    ``role`` "user" is one real user prompt (a turn boundary). ``role`` "assistant" is one
    piece of an assistant turn: text, thinking (\"\" when absent or redacted) and the names of
    the tools it called (one name per call; never inputs or results).

    ``steps`` (assistant only): the piece's blocks in order as ``(kind, name, line)`` with kind
    "text" | "thinking" | "tool"; ``line`` is one collapsed line (first line of the text, or the
    tool's target such as the command or file path), pre-cut to ``STEP_RAW`` chars, unmasked.

    Claude only: ``blocks`` (text blocks and tool calls in order), ``model`` (full model id) and
    ``end_turn`` (stop reason "end_turn") on assistant pieces; ``role`` "result" is one tool result
    (``uid`` = its tool-use id, ``text`` "ok" | "err", ``thinking`` the note "exit N" or ""; never
    any other result content). ``pm.chat.log`` ignores "result" entries.

    Claude background tasks (``pm.chat.itemsync`` only; ``pm.chat.log`` ignores them): ``role`` "bg"
    is the immediate result of a background launch (Bash ``run_in_background``, async Agent) instead
    of "result": ``uid`` = its tool-use id, ``note`` = the task id. ``role`` "bgend" is a task
    notification: ``uid`` = the tool-use id ("" when absent), ``text`` "ok" (completed) | "err"
    (failed, killed, other), ``note`` = the task id or "". ``role`` "bgstop" is a TaskStop / KillShell
    call: ``uid`` "", ``note`` = the task id. Task ids match ``[A-Za-z0-9_-]{1,64}``; never a
    notification's summary, result or output file.

    ``mid_turn`` (Claude, role "user"): a prompt Claude took in while it was working (typed during
    a turn, or pm's "Send now"; a ``queued_command`` attachment). ``pm.chat.itemsync`` adds it to the
    open turn without closing it; ``pm.chat.log`` skips it.
    """

    role: str
    uid: str  # stable within the conversation
    ts: float = 0.0
    text: str = ""
    thinking: str = ""
    tools: tuple[str, ...] = ()
    steps: tuple[tuple[str, str, str], ...] = ()
    blocks: tuple[Block, ...] = ()
    model: str = ""
    end_turn: bool = False
    mid_turn: bool = False
    note: str = ""  # background task id ("bg", "bgend", "bgstop")


@dataclass(frozen=True)
class Chunk:
    entries: list[RawEntry] = field(default_factory=list)
    pos: int = -1  # cursor to pass back next time (byte offset, rowid, step idx or step id)
    more: bool = False  # the read was bounded: call again soon
    reset: bool = False  # the store went backwards (rewritten): drop the conv and re-read from -1


def tool_name(name: object) -> str:
    return name.strip()[:MAX_TOOL_NAME] if isinstance(name, str) and name.strip() else "tool"


# --------------------------------------------------------------------------
# steps: one line per text / thinking block / tool call (masked and cut to 160 by pm.chat.log)
# --------------------------------------------------------------------------

STEP_RAW = 400  # parsers pre-cut lines to this (masking needs some context); chatlog caps at 160
_WS = re.compile(r"\s+")
_PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.MULTILINE)

# argument keys (lowercase, "_"/"-" removed) tried in order for a tool call's target
_TARGET_KEYS = (
    "command", "cmd", "commandline", "script",
    "pattern", "globpattern", "regex",
    "filepath", "absolutepath", "targetfile", "notebookpath", "path", "file", "filename", "uri",
    "url", "query", "searchquery", "q",
    "description", "skill", "subject", "recipient", "to", "targetdirectory", "directorypath", "dir",
)


def first_line(text: object, limit: int = STEP_RAW) -> str:
    """First non-empty line of ``text``, whitespace collapsed, cut to ``limit`` chars ("" when none)."""
    if not isinstance(text, str):
        return ""
    for ln in text[:limit * 8].splitlines():
        ln = _WS.sub(" ", ln).strip()
        if ln:
            return ln[:limit]
    return ""


def _value_line(v: object) -> str:
    if isinstance(v, str):
        return first_line(v)
    if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
        # ["bash", "-lc", "script"] -> the script; else the argv joined
        if len(v) >= 3 and v[-2] in ("-c", "-lc", "-ic") and v[-1].strip():
            return first_line(v[-1])
        return first_line(" ".join(v))
    return ""


def tool_target(args: object) -> str:
    """The obvious target of a tool call from its arguments (dict, or JSON text): the command,
    file path, pattern, URL, query or description; "" when none. Unmasked, one line."""
    if isinstance(args, str):
        raw = args.strip()
        if raw.startswith("{"):
            try:
                args = json.loads(raw)
            except ValueError:
                return first_line(raw)
        else:
            m = _PATCH_FILE.search(raw[:STEP_RAW * 8])
            return first_line(m.group(1)) if m else first_line(raw)
    if not isinstance(args, dict):
        return ""
    norm: dict[str, object] = {}
    for k, v in args.items():
        if isinstance(k, str):
            norm.setdefault(k.lower().replace("_", "").replace("-", ""), v)
    for key in _TARGET_KEYS:
        line = _value_line(norm.get(key))
        if line:
            return line
    for key in ("action", "input", "arguments", "args", "params"):  # one nested level (codex action, ...)
        v = norm.get(key)
        if isinstance(v, dict):
            inner = {k.lower().replace("_", "").replace("-", ""): x for k, x in v.items() if isinstance(k, str)}
            for k2 in _TARGET_KEYS:
                line = _value_line(inner.get(k2))
                if line:
                    return line
    return ""


def tool_block(tid: object, name: object, args: object = None) -> Block:
    bg = isinstance(args, dict) and args.get("run_in_background") is True
    return Block("tool", tid[:80] if isinstance(tid, str) else "", tool_name(name), tool_target(args),
                 background=bg)


def tool_step(name: object, args: object = None) -> tuple[str, str, str]:
    return ("tool", tool_name(name), tool_target(args))


def text_step(text: object, kind: str = "text") -> tuple[str, str, str] | None:
    line = first_line(text)
    return (kind, "", line) if line else None


def read_new_lines(path: Path, pos: int, first: int | None = None,
                   chunk: int | None = None) -> tuple[list[tuple[int, str]], int, bool]:
    """Complete lines after byte ``pos`` as ``(offset, text)``, the new pos and whether more remain.

    ``pos < 0`` starts at the last ``first`` bytes (the partial first line is skipped). A trailing
    line without a newline is left for the next read (the writer is mid-line).
    """
    first = FIRST_BYTES if first is None else first
    chunk = CHUNK_BYTES if chunk is None else chunk
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        skip_partial = False
        if pos < 0:
            pos = max(0, size - first)
            if pos > 0:
                f.seek(pos - 1)
                skip_partial = f.read(1) != b"\n"  # mid-line: drop the partial first line
        if pos >= size:
            return [], pos, False
        f.seek(pos)
        data = f.read(chunk)
    more = pos + len(data) < size
    end = data.rfind(b"\n")
    if end < 0:
        if more:  # one line longer than a chunk: skip it whole
            return [], pos + len(data), True
        return [], pos, False
    data = data[:end + 1]
    out: list[tuple[int, str]] = []
    off = pos
    first_line = True
    for raw in data.split(b"\n")[:-1]:
        here = off
        off += len(raw) + 1
        if first_line and skip_partial:
            first_line = False
            continue
        first_line = False
        if raw.strip():
            out.append((here, raw.decode("utf-8", "replace")))
    return out, pos + end + 1, more


# --------------------------------------------------------------------------
# shared result types
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Latest:
    role: str  # always "assistant"
    text: str  # unmasked: callers must mask
    ts: float  # epoch s of the message; 0.0 when the store has none
    recent: tuple[str, ...] = ()  # newest-first assistant texts (incl. text), for dedup against the pane
    task: str = ""  # newest user prompt when the store has it (unmasked: callers must mask)
    task_ts: float = 0.0


@dataclass
class Stats:
    turns: int = 0  # real user messages (tool results and injected notices do not count)
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    ctx: int = 0  # context size of the latest reply: input + cache write + cache read
    model: str = ""
    window_hint: int = 0  # exact context window when the agent reports it (0 = guess)
    pct_hint: int = -1  # context percent when only a percentage is known (-1 = compute from ctx)
    source: str = "claude"  # which agent kind produced this (shown in the panel footer)
    exact: bool = True  # False when numbers are scraped/estimated rather than read from a store

    @property
    def window(self) -> int:
        """Context window guess: 1M for [1m] models or when the context already exceeds 200k."""
        if self.window_hint:
            return self.window_hint
        return 1_000_000 if "1m" in self.model.lower() or self.ctx > 200_000 else 200_000

    @property
    def pct(self) -> int:
        if self.pct_hint >= 0:
            return min(100, self.pct_hint)
        return min(100, round(100 * self.ctx / self.window)) if self.ctx else 0

    @property
    def level(self) -> str:
        """ok / warn / red by how full the context is (red: compact or clear soon)."""
        return "red" if self.pct >= RED_PCT else "warn" if self.pct >= WARN_PCT else "ok"

    @property
    def total(self) -> int:
        """Tokens the model processed or produced for new content (cache reads are shown apart)."""
        return self.input + self.output + self.cache_write
