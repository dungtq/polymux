"""Read-only view of a Claude session's sub-agents (``<session>/subagents/agent-*.jsonl``).

Workflow sub-agents (``subagents/workflows/wf_<id>/agent-*.jsonl``) are listed too, with the
workflow id as ``group``. Lists them (type + description from ``*.meta.json``, last activity, running when written in the
last ``BUSY_S`` seconds) and shows one sub-agent's work: its prompt, then its text and tool steps.
Text is masked like the chat; nothing is logged or indexed. Symlinks are never followed.

Running = not finished and written recently, or waiting on a tool call that has no result yet (a long
Bash/test run writes nothing for minutes) for up to ``WAIT_S``: a workflow sub-agent is finished when the workflow's
``journal.jsonl`` has a ``result`` line for its agent id (running while it is started without a result
and written in the last ``WF_BUSY_S``); a flat one is running when written in the last ``BUSY_S``.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from pm.chat.sources.common import parse_ts, read_new_lines, tool_step
from pm.core import timefmt
from pm.core.cache import LRU
from pm.security.mask import mask

BUSY_S = 60.0
MAX_LIST = 60
TAIL_BYTES = 400_000
MAX_STEPS = 200
SID_RE = re.compile(r"^agent-[A-Za-z0-9_-]{1,64}$")
WF_RE = re.compile(r"^wf_[A-Za-z0-9_-]{1,64}$")
MAX_WF = 40
WF_BUSY_S = 300.0
WAIT_S = 1800.0  # open tool call (no result yet): still running this long after the last write
JOURNAL_BYTES = 262_144
COLD_BYTES = 2_000_000
_TOOL_RE = re.compile(rb'"type"\s*:\s*"(?:server_)?tool_use"')
_RES_RE = re.compile(rb'"type"\s*:\s*"tool_result"')
_USAGE_RE = re.compile(rb'"usage"\s*:')
_USAGE_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")


@dataclass(frozen=True)
class Sub:
    sid: str
    kind: str = ""
    desc: str = ""
    mtime: float = 0.0
    size: int = 0
    running: bool = False
    group: str = ""  # workflow id (wf_...) for workflow sub-agents, else ""
    group_label: str = ""  # workflow phase from the meta, if any
    done: bool = False  # the workflow journal has a result for it
    calls: int = 0  # tool calls (``calls_more``: at least, the file was only read from its tail)
    calls_more: bool = False
    last: str = ""  # last tool name
    last_target: str = ""  # its target, masked, short
    t0: float = 0.0  # first entry's timestamp, 0 when unknown
    waiting: bool = False  # last tool call has no result yet (e.g. a long Bash run)
    tokens: int = 0  # latest assistant entry's usage: input + cache read + cache write + output (0 = unknown)

    def elapsed(self, now: float) -> str:
        """'2m' since the first entry (running) or since the last write (finished)."""
        return timefmt.duration((now - self.t0) if self.running and self.t0 else (now - self.mtime),
                                days_hours=False)


@dataclass
class Group:
    gid: str  # "" = direct (flat) sub-agents
    label: str
    subs: list[Sub] = field(default_factory=list)
    total: int = 0  # started agents (journal) or listed ones
    done: int = 0

    @property
    def running(self) -> int:
        return sum(1 for s in self.subs if s.running)

    @property
    def mtime(self) -> float:
        return max((s.mtime for s in self.subs), default=0.0)


@dataclass
class Summary:
    running: int
    finished: int
    total: int
    groups: list[Group]


@dataclass(frozen=True)
class Step:
    role: str  # prompt | text | tool
    text: str
    label: str = ""
    id: str = ""  # tool-use id ("" for prompt/text, or a tool call without one)
    status: str = ""  # tool with an id: "run" until its result, then "ok" | "err"; else ""
    ts: float = 0.0  # entry timestamp, 0 when unknown
    ended: float = 0.0  # tool: its result's timestamp


@dataclass(frozen=True)
class Block:
    """One bubble of the opened sub-agent's mini chat: a ``prompt``, a ``reply`` (``last`` = the final
    text step) or ``tools`` (consecutive tool steps, per-name ``counts`` in first-seen order, ``running``
    = calls still waiting for a result)."""
    kind: str  # prompt | reply | tools
    step: Step | None = None  # prompt / reply
    last: bool = False
    steps: tuple[Step, ...] = ()
    counts: tuple[tuple[str, int], ...] = ()
    running: int = 0


def _tools(steps: list[Step]) -> Block:
    counts: dict[str, int] = {}
    for st in steps:
        counts[st.label or "tool"] = counts.get(st.label or "tool", 0) + 1
    return Block("tools", steps=tuple(steps), counts=tuple(counts.items()),
                 running=sum(1 for st in steps if st.status == "run"))


def blocks(steps: list[Step]) -> list[Block]:
    """Group ``steps`` into mini-chat blocks: consecutive tool steps fold into one ``tools`` block."""
    out: list[Block] = []
    cur: list[Step] = []
    for st in steps:
        if st.role == "tool":
            cur.append(st)
            continue
        if cur:
            out.append(_tools(cur))
            cur = []
        out.append(Block("prompt" if st.role == "prompt" else "reply", st))
    if cur:
        out.append(_tools(cur))
    last = next((i for i in range(len(out) - 1, -1, -1) if out[i].kind == "reply"), -1)
    if last >= 0:
        out[last] = replace(out[last], last=True)
    return out


@dataclass
class Work:
    sub: Sub
    steps: list[Step] = field(default_factory=list)
    truncated: bool = False

    def blocks(self) -> list[Block]:
        return blocks(self.steps)


def sub_dir(transcript: str | Path | None) -> Path | None:
    if not transcript:
        return None
    t = Path(transcript)
    if t.suffix != ".jsonl":
        return None
    d = t.with_suffix("") / "subagents"
    return d if d.is_dir() and not d.is_symlink() else None


def _meta(p: Path) -> dict:
    try:
        if p.is_symlink() or p.stat().st_size > 8192:
            return {}
        o = json.loads(p.read_text(errors="replace"))
        return o if isinstance(o, dict) else {}
    except (OSError, ValueError):
        return {}


def _s(v: object, n: int) -> str:
    return mask(v)[:n] if isinstance(v, str) else ""


def _wf_dirs(d: Path) -> list[Path]:
    w = d / "workflows"
    if w.is_symlink() or not w.is_dir():
        return []
    try:
        dirs = [p for p in w.iterdir() if WF_RE.match(p.name) and p.is_dir() and not p.is_symlink()]
    except OSError:
        return []
    dirs.sort(key=lambda p: p.name)
    return dirs[:MAX_WF]


def _journal(d: Path) -> tuple[set[str], set[str]]:
    """(started agent ids, ids with a result) of a workflow dir's journal.jsonl; bounded, no symlink."""
    p = d / "journal.jsonl"
    started: set[str] = set()
    done: set[str] = set()
    try:
        if p.is_symlink() or not p.is_file():
            return started, done
        with open(p, "rb") as f:
            raw = f.read(JOURNAL_BYTES)
    except OSError:
        return started, done
    for line in raw.splitlines():
        if b'"agentId"' not in line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        aid = o.get("agentId") if isinstance(o, dict) else None
        if isinstance(aid, str) and len(aid) <= 64:
            (done if o.get("type") == "result" else started).add(aid)
    return started, done


_ACT: dict[str, tuple[int, int, bool, str, str, float, frozenset, int]] = {}


def _ts(line: bytes) -> float:
    try:
        v = json.loads(line).get("timestamp")
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp() if isinstance(v, str) else 0.0
    except (ValueError, AttributeError, TypeError):
        return 0.0


def _open_calls(data: bytes, openc: frozenset) -> frozenset:
    """Tool-use ids still waiting for a result after ``data`` (bounded)."""
    cur = set(openc)
    for line in data.splitlines():
        use, res = _TOOL_RE.search(line), _RES_RE.search(line)
        if not (use or res):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = e.get("message") if isinstance(e, dict) else None
        for b in (msg.get("content") if isinstance(msg, dict) else None) or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") in ("tool_use", "server_tool_use") and isinstance(b.get("id"), str):
                cur.add(b["id"])
            elif b.get("type") == "tool_result":
                cur.discard(b.get("tool_use_id"))
    return frozenset(list(cur)[-32:])


def _usage_total(data: bytes) -> int | None:
    """Tokens of the newest assistant entry with a ``message.usage`` in ``data`` (input + cache read
    + cache write + output), None when there is none."""
    for line in reversed(data.splitlines()):
        if not _USAGE_RE.search(line):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = e.get("message") if isinstance(e, dict) and e.get("type") == "assistant" else None
        u = msg.get("usage") if isinstance(msg, dict) else None
        if isinstance(u, dict):
            return sum(v for k in _USAGE_KEYS if isinstance(v := u.get(k), int) and not isinstance(v, bool))
    return None


def _activity(p: Path, size: int) -> tuple[int, bool, str, str, float, bool, int]:
    """(tool calls, calls_more, last tool name, last target (masked), first timestamp, waiting, tokens)
    of a sub-agent transcript; ``waiting`` = a tool call is still open (no ``tool_result`` for its id
    yet), ``tokens`` = ``_usage_total`` of the newest assistant entry. Incremental: a file is read once
    (its last ``COLD_BYTES``), then only what was appended."""
    key = str(p)
    c = _ACT.get(key)
    if c is not None and size < c[0]:
        c = None
    pos, calls, more, last, target, t0, openc, tokens = c if c else (0, 0, False, "", "", 0.0, frozenset(), 0)
    try:
        with open(p, "rb") as f:
            if c is None:
                t0 = _ts(f.readline(65536))
                if size > COLD_BYTES:
                    pos, more = size - COLD_BYTES, True
                    f.seek(pos)
                    f.readline()  # skip the partial first line
                    pos = f.tell()
            f.seek(pos)
            data = f.read(max(0, size - pos))
    except OSError:
        return (0, False, "", "", 0.0, False, 0)
    cut = data.rfind(b"\n") + 1  # leave a line the writer is still on for the next read
    data = data[:cut]
    if data:
        calls += len(_TOOL_RE.findall(data))
        openc = _open_calls(data, openc)
        used = _usage_total(data)
        tokens = tokens if used is None else used
        for line in reversed(data.splitlines()):
            if not _TOOL_RE.search(line):
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            msg = e.get("message") if isinstance(e, dict) else None
            blocks = [b for b in (msg.get("content") if isinstance(msg, dict) else None) or []
                      if isinstance(b, dict) and b.get("type") in ("tool_use", "server_tool_use")]
            if blocks:
                _k, name, tgt = tool_step(blocks[-1].get("name"), blocks[-1].get("input"))
                last, target = mask(name or "")[:40], mask(tgt or "").replace("\n", " ")[:120]
                break
    if len(_ACT) > 512:
        _ACT.clear()
    _ACT[key] = (pos + cut, calls, more, last, target, t0, openc, tokens)
    return calls, more, last, target, t0, bool(openc), tokens


def list_subs(transcript, now: float | None = None) -> list[Sub]:
    d = sub_dir(transcript)
    if d is None:
        return []
    now = time.time() if now is None else now
    out: list[Sub] = []
    for base, group in [(d, "")] + [(w, w.name) for w in _wf_dirs(d)]:
        started, results = _journal(base) if group else (set(), set())
        try:
            files = [p for p in base.glob("agent-*.jsonl") if SID_RE.match(p.stem) and not p.is_symlink()]
        except OSError:
            continue
        for p in files[:MAX_LIST]:
            try:
                st = p.stat()
            except OSError:
                continue
            m = _meta(p.with_suffix(".meta.json"))
            aid = p.stem[6:]
            done = aid in results
            fresh = now - st.st_mtime < (WF_BUSY_S if aid in started else BUSY_S)
            calls, more, last, target, t0, waiting, tokens = _activity(p, st.st_size)
            fresh = fresh or (waiting and now - st.st_mtime < WAIT_S)
            out.append(Sub(p.stem, _s(m.get("agentType"), 40), _s(m.get("description"), 200),
                           st.st_mtime, st.st_size, fresh and not done, group,
                           _s(m.get("workflowPhase"), 60) if group else "", done, calls, more, last, target, t0,
                           waiting and not done, tokens))
    out.sort(key=lambda s: -s.mtime)
    return out[:MAX_LIST]


def summarize(subs: list[Sub], transcript=None) -> Summary:
    """Counts and the workflow/direct groups (Direct first, then workflows newest first) for the tab and its strip."""
    by: dict[str, Group] = {}
    for s in subs:
        g = by.setdefault(s.group, Group(s.group, ("workflow " + (s.group_label + " " if s.group_label else "")
                                                   + s.group[3:11]).strip() if s.group else "Direct"))
        g.subs.append(s)
        g.done += 0 if s.running else 1  # direct group; a workflow's is set from its journal below
    d = sub_dir(transcript)
    for g in by.values():
        g.subs.sort(key=lambda s: (not s.running, -s.mtime))
        g.total = len(g.subs)
        if g.gid and d is not None:
            started, results = _journal(d / "workflows" / g.gid)
            g.total = max(len(started | results), len(g.subs))
            g.done = min(len(results), g.total)
    groups = sorted(by.values(), key=lambda g: (g.gid != "", -g.mtime))  # Direct first, then newest workflow
    run = sum(1 for s in subs if s.running)
    return Summary(run, len(subs) - run, len(subs), groups)


RUNNING_TTL_S = 3.0
_running_cache = LRU(max_bytes=1 << 20, max_entries=64)  # transcript -> (monotonic time, running sub-agents)


def running_count(agent) -> int:
    """Running sub-agents of a Claude agent (``summarize(list_subs(...)).running``); 0 for other kinds. Blocking
    (a glob and a stat per sub-agent file); cached per transcript for RUNNING_TTL_S."""
    tr = agent.transcript if agent.kind.value == "claude" else ""
    if not tr:
        return 0
    now = time.monotonic()
    hit = _running_cache.get(tr)
    if hit is not None and now - hit[0] < RUNNING_TTL_S:
        return hit[1]
    try:
        n = summarize(list_subs(tr)).running
    except Exception:  # noqa: BLE001 - callers work without the count
        n = 0
    _running_cache.put(tr, (now, n))
    return n


def _blocks(entry: dict) -> list[Step]:
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    role = entry.get("type")
    ts = parse_ts(entry.get("timestamp"))
    out: list[Step] = []
    if isinstance(content, str):
        if content.strip():
            out.append(Step("prompt" if role == "user" else "text", mask(content.strip())[:4000], ts=ts))
        return out
    for b in content if isinstance(content, list) else []:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text" and role == "assistant" and isinstance(b.get("text"), str) and b["text"].strip():
            out.append(Step("text", mask(b["text"].strip())[:4000], ts=ts))
        elif t == "text" and role == "user" and isinstance(b.get("text"), str) and b["text"].strip():
            out.append(Step("prompt", mask(b["text"].strip())[:4000], ts=ts))
        elif t in ("tool_use", "server_tool_use"):
            _k, name, target = tool_step(b.get("name"), b.get("input"))
            tid = b["id"][:80] if isinstance(b.get("id"), str) else ""
            out.append(Step("tool", mask(target or "")[:300], name, tid, "run" if tid else "", ts))
    return out


def _results(entry: dict) -> list[tuple[str, bool]]:
    """(tool-use id, is_error) per ``tool_result`` block of a user entry; never the result content."""
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return [(b["tool_use_id"], b.get("is_error") is True) for b in (content if isinstance(content, list) else [])
            if isinstance(b, dict) and b.get("type") == "tool_result" and isinstance(b.get("tool_use_id"), str)]


def _add(steps: list[Step], open_: dict[str, int], e: dict) -> None:
    """Append an entry's steps; a tool result sets its call's status (ok/err) and ended."""
    if e.get("type") == "user":
        for tid, err in _results(e):
            i = open_.pop(tid[:80], None)
            if i is not None:
                steps[i] = replace(steps[i], status="err" if err else "ok", ended=parse_ts(e.get("timestamp")))
    for st in _blocks(e):
        if st.id:
            open_[st.id] = len(steps)
        steps.append(st)


def read_work(transcript, sid: str, wf: str = "") -> Work | None:
    d = sub_dir(transcript)
    if d is None or not SID_RE.match(sid or ""):
        return None
    if wf:
        if not WF_RE.match(wf):
            return None
        d = d / "workflows" / wf
        if d.is_symlink() or (d.parent.is_symlink()):
            return None
    p = d / f"{sid}.jsonl"
    if p.is_symlink() or not p.is_file():
        return None
    sub = next((s for s in list_subs(transcript) if s.sid == sid and s.group == wf), Sub(sid, group=wf))
    try:
        lines, _pos, _more = read_new_lines(p, -1, TAIL_BYTES, TAIL_BYTES)
    except OSError:
        return None
    steps: list[Step] = []
    open_: dict[str, int] = {}  # tool-use id -> index in steps, until its result
    if sub.size > TAIL_BYTES:  # the tail starts mid-file: keep the opening prompt
        try:
            with open(p, "rb") as f:
                first = json.loads(f.readline(65536).decode("utf-8", "replace"))
            if isinstance(first, dict) and first.get("type") == "user":
                steps += _blocks(first)[:1]
        except (OSError, ValueError):
            pass
    for _off, raw in lines:
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("type") in ("user", "assistant"):
            _add(steps, open_, e)
    trunc = len(steps) > MAX_STEPS or sub.size > TAIL_BYTES
    keep = steps[-MAX_STEPS:]
    if steps and steps[0].role == "prompt" and keep[0] is not steps[0]:
        keep = [steps[0]] + keep[1:]
    return Work(sub, keep, trunc)
