"""Heading of the agent page and the model raw id from a transcript (read-only, best effort)."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from pm.agents import claude_defaults
from pm.agents.catalog import CLAUDE_FULL_IDS
from pm.agents.detect import proc_argv
from pm.agents.launch import EFFORT_FLAG, MODEL_FLAG, effort_switchable
from pm.core import accounts
from pm.core.cache import LRU
from pm.core.config import ACCOUNT_KINDS
from pm.core.models import Agent, AgentKind
from pm.core.procstat import PROC

_TAIL = 262_144


def transcript_meta(path: str) -> tuple[str, str]:
    """(model id, reasoning effort) of the latest assistant message in the tail of a jsonl transcript;
    '' for each one that is not found."""
    if not path:
        return "", ""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - _TAIL))
            data = f.read(_TAIL)
    except OSError:
        return "", ""
    model = effort = ""
    for line in reversed(data.splitlines()):
        if model and effort:
            break
        if b'"assistant"' not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("type") != "assistant":
            continue
        m = e["message"].get("model") if isinstance(e.get("message"), dict) else None
        if not model and isinstance(m, str) and m and not m.startswith("<"):
            model = m[:80]
        eff = e.get("effort")
        if not effort and isinstance(eff, str) and eff:
            effort = eff[:16]
    return model, effort


def model_from_transcript(path: str) -> str:
    """Model id of the latest assistant message in the tail of a jsonl transcript, else ''."""
    return transcript_meta(path)[0]


_CACHE = LRU(max_bytes=262_144, max_entries=512)


_EXTRA_ROOTS: Callable[[], Sequence[Path]] | None = None


def register_extra_roots(provider: Callable[[], Sequence[Path]] | None) -> None:
    """Set once at app start: ``provider()`` returns the agent config folders of the named accounts
    (``accounts.config_dirs``). Their ``projects`` / ``sessions`` count as allowed roots for every reader here,
    also for settings-less callers (Jinja filters). ``None`` clears it."""
    global _EXTRA_ROOTS
    _EXTRA_ROOTS = provider


def _extra(sub: str) -> list[Path]:
    try:
        return [Path(d) / sub for d in _EXTRA_ROOTS()] if _EXTRA_ROOTS else []
    except Exception:  # noqa: BLE001 - a broken provider must not break a list row
        return []


def kind_dirs(settings, kind) -> list[Path] | None:
    """Config folders sessions of this agent kind can live in (``accounts.config_dirs``), None for kinds without
    accounts: pass it as ``config_dirs`` to ``live_model`` / ``live_effort`` / ``live_pill``."""
    k = getattr(kind, "value", kind)
    return accounts.config_dirs(settings, k) if k in ACCOUNT_KINDS else None


def _roots(root: Path | Sequence[Path] | None, default: Path, sub: str = "") -> list[Path]:
    """Allowed roots: ``root`` (one path or several), else ``default``; plus the registered account folders' ``sub``."""
    if root is None:
        base = [default]
    else:
        base = [Path(root)] if isinstance(root, str | os.PathLike) else [Path(r) for r in root]
    return base + (_extra(sub) if sub else [])


def record_meta(path: str, root: Path | Sequence[Path] | None = None) -> tuple[str, str]:
    """``transcript_meta`` for a stored registry path: only a regular file whose realpath is
    under ``~/.claude/projects`` (or one of the ``root`` paths, e.g. an account's ``projects``; no symlink
    escape) is read; cached by ``_file_key``."""
    if not path:
        return "", ""
    try:
        real = os.path.realpath(path)
        if not any(_under(real, b) for b in _roots(root, Path.home() / ".claude" / "projects", "projects")) \
                or not os.path.isfile(real):
            return "", ""
        key = _file_key(real)
    except (OSError, ValueError):
        return "", ""
    hit = _CACHE.get(key)
    if hit is None:
        hit = transcript_meta(real)
        _CACHE.put(key, hit)
    return hit


def model_from_record_transcript(path: str, root: Path | Sequence[Path] | None = None) -> str:
    """Model id of ``record_meta``, '' when unknown."""
    return record_meta(path, root)[0]


def heading(agent: Agent) -> dict:
    """{base, pill, id, text}: ``<agent id> <raw model id>``; the pill falls back to the kind name (``live_model``).
    ``text`` (the page title) starts with ``<project>/`` when the agent belongs to a project."""
    base = agent.id
    pill = live_model(agent)
    where = f"{agent.project}/" if agent.project else ""
    return {"base": base, "pill": pill, "id": agent.id, "text": f"{where}{base} {pill}"}


_DATE = re.compile(r"-\d{8}$")
_SFX = re.compile(r"\[[^\]]*\]$")


def model_short(raw: str) -> str:
    """``claude-sonnet-5-5-20261001`` -> ``sonnet-5-5``: no claude- prefix, no date or [1m] suffix, dots as dashes."""
    m = _SFX.sub("", (raw or "").strip().lower())
    m = _DATE.sub("", m[7:] if m.startswith("claude-") else m)
    return "-".join(m.replace(".", "-").split())


def _under(real: str, base: Path) -> bool:
    try:
        b = os.path.realpath(base)
        return os.path.commonpath([b, real]) == b
    except ValueError:
        return False


def _codex_model(path: str) -> str:
    """``turn_context.payload.model`` of the newest turn in a Codex rollout (tail, else head)."""
    try:
        with open(path, "rb") as f:
            head = f.read(_TAIL)
            f.seek(0, 2)
            f.seek(max(0, f.tell() - _TAIL))
            tail = f.read(_TAIL)
    except OSError:
        return ""
    for data in (tail, head):
        for line in reversed(data.splitlines()):
            if b'"turn_context"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            p = e.get("payload") if isinstance(e, dict) else None
            m = p.get("model") if isinstance(p, dict) else None
            if e.get("type") == "turn_context" and isinstance(m, str) and m:
                return m[:80]
    return ""


def _ro(path: str) -> sqlite3.Connection:
    uri = "file:" + path.replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=0.3)
    con.execute("PRAGMA query_only=1")
    return con


def _devin_model(path: str, home: Path) -> str:
    """``sessions.model`` of the transcript's session in devin's sessions.db, else the ATIF ``agent.model_name``."""
    try:
        con = _ro(str(home / ".local" / "share" / "devin" / "cli" / "sessions.db"))
        try:
            row = con.execute("SELECT model FROM sessions WHERE id=?", (Path(path).stem,)).fetchone()
        finally:
            con.close()
        if row and isinstance(row[0], str) and row[0]:
            return row[0][:80]
    except sqlite3.Error:
        pass
    try:
        with open(path, "rb") as f:
            head = f.read(8192).decode("utf-8", "replace")
    except OSError:
        return ""
    m = re.search(r'"model_name"\s*:\s*"([^"\\]{1,80})"', head)
    return m.group(1) if m else ""


def _cursor_model(path: str) -> str:
    """``lastUsedModel`` from the chat's ``meta`` row (hex-encoded JSON)."""
    try:
        con = _ro(path)
        try:
            row = con.execute("SELECT value FROM meta LIMIT 1").fetchone()
        finally:
            con.close()
        v = row[0] if row else None
        raw = bytes.fromhex(v).decode("utf-8") if isinstance(v, str) else (v or b"").decode("utf-8")
        m = json.loads(raw).get("lastUsedModel")
    except (sqlite3.Error, ValueError, AttributeError):
        return ""
    return m[:80] if isinstance(m, str) else ""


def _agy_model(path: str) -> str:
    """Model name of the newest ``gen_metadata`` row (protobuf field 1.19) of an antigravity conversation db."""
    from pm.chat.sources import antigravity

    try:
        con = _ro(path)
        try:
            rows = con.execute("SELECT CASE WHEN size <= 262144 THEN data END FROM gen_metadata "
                               "ORDER BY idx DESC LIMIT 20").fetchall()
        finally:
            con.close()
        for (data,) in rows:
            u = antigravity.scan_usage(data) if isinstance(data, bytes) else None
            if u and u["model"]:
                return u["model"][:80]
    except (sqlite3.Error, ValueError, TypeError):
        pass
    return ""


def _file_key(real: str) -> tuple[str, int, int, int]:
    """Cache key of a transcript file: (realpath, inode, mtime in ns, size). The size and inode catch a rewrite or
    an append inside one filesystem timestamp tick, where the mtime alone stays the same."""
    st = os.stat(real)
    return real, st.st_ino, st.st_mtime_ns, st.st_size


_KCACHE: dict[tuple[str, int, int, int], str] = {}


def record_model(path: str, home: Path | None = None, claude_dirs: Sequence[Path] | None = None,
                 codex_dirs: Sequence[Path] | None = None) -> str:
    """Raw model id for a stored registry path of any agent kind, '' when unknown. The kind is told from
    where the realpath lives under ``home`` (claude projects, codex sessions, devin transcripts, cursor
    chats, antigravity conversations); any other path is not read. ``claude_dirs`` / ``codex_dirs`` (config
    folders from ``accounts.config_dirs``) replace ``~/.claude`` / ``~/.codex`` as the allowed roots.
    Cached by ``_file_key``."""
    if not path:
        return ""
    home = home or Path.home()
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        return ""
    croots = _roots([d / "projects" for d in claude_dirs] if claude_dirs else None, home / ".claude" / "projects", "projects")
    xroots = _roots([d / "sessions" for d in codex_dirs] if codex_dirs else None, home / ".codex" / "sessions",
                    "sessions")
    if any(_under(real, b) for b in croots):
        return model_from_record_transcript(path, croots)
    if any(_under(real, b) for b in xroots) and real.endswith(".jsonl"):
        fn = lambda: _codex_model(real)  # noqa: E731
    elif _under(real, home / ".local" / "share" / "devin" / "cli" / "transcripts") and real.endswith(".json"):
        fn = lambda: _devin_model(real, home)  # noqa: E731
    elif _under(real, home / ".cursor" / "chats") and os.path.basename(real) == "store.db":
        fn = lambda: _cursor_model(real)  # noqa: E731
    elif _under(real, home / ".gemini" / "antigravity-cli" / "conversations") and real.endswith(".db"):
        fn = lambda: _agy_model(real)  # noqa: E731
    else:
        return ""
    try:
        if not os.path.isfile(real):
            return ""
        key = _file_key(real)
    except OSError:
        return ""
    if key not in _KCACHE:
        if len(_KCACHE) > 512:
            _KCACHE.clear()
        _KCACHE[key] = fn()
    return _KCACHE[key]


_ARGV = LRU(max_bytes=262_144, max_entries=512)


def _arg_flag(agent: Agent, flag: str, proc_root: Path = PROC) -> str:
    """Value of ``flag X`` / ``flag=X`` (before ``--``) in the agent process's argv, else ''.
    Cached per (pid, agent id, flag): a process's argv does not change."""
    pid = getattr(agent, "pid", 0) or 0
    if pid <= 0 or not flag:
        return ""
    key = (proc_root, pid, agent.id, flag)
    hit = _ARGV.get(key)
    if hit is not None:
        return hit
    argv, m = proc_argv(pid, proc_root), ""
    for i, a in enumerate(argv):
        if a == "--":
            break
        if a == flag and i + 1 < len(argv):
            m = argv[i + 1]
            break
        if a.startswith(flag + "="):
            m = a[len(flag) + 1:]
            break
    m = "".join(ch for ch in m if ch.isprintable())[:80]
    if argv:  # an unreadable cmdline is not cached (the process may be starting)
        _ARGV.put(key, m)
    return m


def arg_model(agent: Agent, proc_root: Path = PROC) -> str:
    """The model the agent process was started with (``--model X`` / ``--model=X`` before ``--``), else ''."""
    return _arg_flag(agent, MODEL_FLAG.get(agent.kind, ""), proc_root)


def full_model_id(kind: AgentKind, model: str) -> str:
    """A claude alias as its full id (``opus[1m]`` -> ``claude-opus-5-5[1m]``); anything else unchanged."""
    if kind is not AgentKind.CLAUDE:
        return model
    base, sfx = (model[:model.index("[")], model[model.index("["):]) if "[" in model else (model, "")
    full = CLAUDE_FULL_IDS.get(base.lower())
    return full + sfx if full else model


_NOTED = LRU(max_bytes=65_536, max_entries=512)
SET_EFFORT_TTL = 1800.0


def _note(what: str, agent: Agent, value: str, now: float | None) -> None:
    """Remember a ``/model`` or ``/effort`` value pm just sent: the transcript shows it only from the agent's
    next reply."""
    _NOTED.put((what, agent.id, getattr(agent, "pid", 0) or 0), (value, time.time() if now is None else now))


def _noted(what: str, agent: Agent, seen: str, same, now: float | None) -> str:
    """The value pm set (``_note``) until the transcript shows it (``same(value, seen)``) or ``SET_EFFORT_TTL``
    passes, else ''; a confirmed or expired note is dropped, so the transcript rules again."""
    key = (what, agent.id, getattr(agent, "pid", 0) or 0)
    noted = _NOTED.get(key)
    if noted is None:
        return ""
    value, at = noted
    if not same(value, seen) and (time.time() if now is None else now) - at < SET_EFFORT_TTL:
        return value
    _NOTED.put(key, (value, 0.0))
    return ""


def note_model(agent: Agent, model: str, now: float | None = None) -> None:
    """Remember a ``/model <id>`` pm just sent (shown by ``live_model`` until the transcript has it)."""
    _note("model", agent, model, now)


def live_model(agent: Agent, home: Path | None = None, proc_root: Path = PROC, now: float | None = None,
               config_dirs: Sequence[Path] | None = None) -> str:
    """Raw model id of a running (or ended) agent: a model pm set (``note_model``) until the transcript shows it,
    else the transcript's (``record_model``), else the process's own ``--model`` arg with a claude alias
    resolved to its full id (a New Chat has no reply yet), else the kind name. Never a catalog guess.
    ``config_dirs``: the agent kind's allowed config folders (``accounts.config_dirs``), default ``~``."""
    seen = record_model(agent.transcript or "", home,
                        claude_dirs=config_dirs if agent.kind is AgentKind.CLAUDE else None,
                        codex_dirs=config_dirs if agent.kind is AgentKind.CODEX else None)
    noted = _noted("model", agent, seen,
                   lambda v, s: model_short(full_model_id(agent.kind, v)) == model_short(s), now)
    if noted:
        return full_model_id(agent.kind, noted)
    return seen or full_model_id(agent.kind, arg_model(agent, proc_root)) or agent.kind.value


def note_effort(agent: Agent, level: str, now: float | None = None) -> None:
    """Remember a ``/effort <level>`` pm just sent: the transcript shows it only from the agent's next reply."""
    _note("effort", agent, level, now)


def note_effort_cmd(agent: Agent, text: str) -> None:
    """``note_effort`` for a typed ``/effort <level>`` (a level this agent can switch to; anything else is ignored)."""
    parts = (text or "").split()
    if len(parts) == 2 and parts[0] == "/effort" and effort_switchable(agent.kind, parts[1]):
        note_effort(agent, parts[1])


def live_effort(agent: Agent, home: Path | None = None, proc_root: Path = PROC, now: float | None = None,
                config_dirs: Sequence[Path] | None = None) -> str:
    """Reasoning effort of a running claude agent: a level pm set (``note_effort``) until the transcript shows
    it or ``SET_EFFORT_TTL`` passes, else the newest reply's, else the process's ``--effort`` arg, else ''.
    Other kinds: ''."""
    if agent.kind is not AgentKind.CLAUDE:
        return ""
    root = [d / "projects" for d in config_dirs] if config_dirs else (home or Path.home()) / ".claude" / "projects"
    seen = record_meta(agent.transcript or "", root)[1]
    return (_noted("effort", agent, seen, lambda v, s: v == s, now)
            or seen or _arg_flag(agent, EFFORT_FLAG.get(agent.kind, ""), proc_root))


def settings_defaults(agent: Agent, home: Path | None = None) -> tuple[str, str]:
    """(model, effort) a claude agent falls back to when it has no reply, ``--model`` or ``--effort`` yet: the
    full model id and the level from Claude's settings files (``claude_defaults``); '' for each unset.
    Other kinds: ('', ''). For the composer only: ``live_model`` keeps the kind name as its "unknown"."""
    if agent.kind is not AgentKind.CLAUDE:
        return "", ""
    model, effort = claude_defaults.read(agent.cwd or "", home)
    return full_model_id(agent.kind, model), effort


def _resolved(agent: Agent, home: Path | None = None, config_dirs: Sequence[Path] | None = None) -> tuple[str, str]:
    """(model, effort): the live ones, else the settings defaults (a model equal to the kind name is "unknown")."""
    model = live_model(agent, home, config_dirs=config_dirs)
    effort = live_effort(agent, home, config_dirs=config_dirs)
    if model == agent.kind.value or not effort:
        dmodel, deffort = settings_defaults(agent, home)
        model = (dmodel or model) if model == agent.kind.value else model
        effort = effort or deffort
    return model, effort


def placeholder(agent: Agent, home: Path | None = None, config_dirs: Sequence[Path] | None = None) -> tuple[str, str]:
    """(full, short) composer placeholder: ``Message to <model> \u00b7 <effort> in <window>`` and
    ``Message <model> \u00b7 <effort>``. Model and effort are the live ones, else the settings defaults."""
    model, effort = _resolved(agent, home, config_dirs)
    tail = model_short(model) + (f" \u00b7 {effort}" if effort else "")
    win = tmux_win(agent)
    return f"Message to {tail}" + (f" in {win}" if win else ""), f"Message {tail}"


def new_session_label(agent: Agent, home: Path | None = None, config_dirs: Sequence[Path] | None = None) -> str:
    """Muted "Last message" text for a claude chat with no message yet: ``New <model> session with effort
    <level>``, ``New <model> session`` or ``New session`` (model unknown). Other kinds: ''."""
    if agent.kind is not AgentKind.CLAUDE:
        return ""
    model, effort = _resolved(agent, home, config_dirs)
    if model == agent.kind.value:
        return "New session"
    return f"New {model_short(model)} session" + (f" with effort {effort}" if effort else "")


def live_pill(agent: Agent, home: Path | None = None, config_dirs: Sequence[Path] | None = None) -> str:
    """Short model for the agent heading pill: the same model as the composer placeholder and the Model picker
    (live, else Claude's settings default), the kind name only when neither is known."""
    return model_short(_resolved(agent, home, config_dirs)[0])


def pill_model(transcript: str, claude_dirs: Sequence[Path] | None = None,
               codex_dirs: Sequence[Path] | None = None) -> str:
    """Short model name for a list row, '' when unknown (cached by realpath, inode, mtime and size; one stat per row).
    ``claude_dirs`` / ``codex_dirs``: ``accounts.config_dirs`` (default ``~/.claude`` / ``~/.codex``)."""
    return model_short(record_model(transcript, claude_dirs=claude_dirs, codex_dirs=codex_dirs))


def tmux_slot(label: str, kind: str) -> str:
    """``dev-pm-1-claude`` -> ``pm-1``: the tmux session (without the ``dev-`` prefix) and window index."""
    base = label or ""
    if kind and base.endswith("-" + kind):
        base = base[: -len(kind) - 1]
    return base[4:] if base.startswith("dev-") and len(base) > 4 else base


def tmux_win(agent) -> str:
    """``<tmux session>:<window index>`` from the agent label (``dev-pm-1-claude`` -> ``dev-pm:1``),
    else the tmux window name, else ''."""
    label, kind = getattr(agent, "label", "") or "", getattr(getattr(agent, "kind", None), "value", "")
    m = re.fullmatch(rf"(.+)-(\d+)-{re.escape(kind)}(?:-\d+)?", label) if kind else None
    return f"{m.group(1)}:{m.group(2)}" if m else (getattr(agent, "window_name", "") or "")


def win_prog(window, agents) -> str:
    """Program shown for a tmux window: the detected agent kind of an agent pane in it, else the active
    pane's current command (first pane when none is active). Command names only: control chars dropped,
    capped at 32."""
    ids = {p.id for p in window.panes}
    for a in agents or ():
        if a.pane_id in ids:
            return a.kind.value
    pane = next((p for p in window.panes if p.active), None) or (window.panes[0] if window.panes else None)
    cmd = pane.command if pane else ""
    return "".join(ch for ch in cmd if ch.isprintable())[:32]
