"""Needs-you inbox: state buckets, merged notifications, read/dismiss store, unread count.

The store is one small JSON file in ``state_dir`` (``inbox_state.json``): a global
"read everything up to" time plus per-key read / dismiss times, and per-key "marked unread" times
(``mark_unread``: such a key stays unread whatever its read times say, until it is marked read). A key is
``<agent id>:<event>`` for pm notifications and agent rows, ``n:<ntfy id>`` for
other ntfy messages. Pane text only ever appears in a rendered page (masked and
ANSI-stripped by ``services.pane_text``); it is never stored, logged or sent.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from pm.agents.state import failed_line
from pm.chat import live
from pm.chat.ask import find_ask, hint_line
from pm.core.config import Settings
from pm.core.fsutil import JsonStore, atomic_write
from pm.core.models import Agent, AgentState, Ask, NtfyItem
from pm.security.mask import mask

KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
FINISHED_WINDOW_S = Settings.idle_after_s  # default: an idle agent is "finished" this long after its last response, then "idle"
MIN_WINDOW_S = 60
NTFY_UNREAD_WINDOW_S = 24 * 3600
KEEP_S = 14 * 86400
MAX_KEYS = 500
EVENTS = ("waiting", "failed", "idle")
_CLICK_ID = re.compile(r"/a/([A-Za-z0-9._-]{1,64})/?$")

_lock = threading.Lock()


# ------------------------------------------------------------------ store
def _path(state_dir: Path) -> Path:
    return Path(state_dir) / "inbox_state.json"


def _load(state_dir: Path) -> dict:
    d = JsonStore(_path(state_dir), dict, validate=lambda x: isinstance(x, dict)).load()
    out = {"all": 0.0, "read": {}, "dismiss": {}, "unread": {}}
    try:
        out["all"] = float(d.get("all", 0))
    except (TypeError, ValueError):
        pass
    for name in ("read", "dismiss", "unread"):
        m = d.get(name)
        if isinstance(m, dict):
            for k, v in m.items():
                if isinstance(k, str) and KEY_RE.fullmatch(k) and isinstance(v, (int, float)):
                    out[name][k] = float(v)
    return out


def _save(state_dir: Path, st: dict) -> None:
    cut = time.time() - KEEP_S
    for name in ("read", "dismiss", "unread"):
        m = {k: v for k, v in st[name].items() if v >= cut}
        if len(m) > MAX_KEYS:
            m = dict(sorted(m.items(), key=lambda kv: kv[1])[-MAX_KEYS:])
        st[name] = m
    p = _path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps(st), mode=0o600, fsync=True)


class Store:
    """Snapshot of the read / dismiss state; use the module functions to change it."""

    def __init__(self, data: dict):
        self.d = data

    def read_at(self, key: str) -> float:
        return max(self.d["all"], self.d["read"].get(key, 0.0))

    def marked_unread(self, key: str) -> bool:
        """The user marked ``key`` unread (``mark_unread``) and has not read it since."""
        return key in self.d["unread"]

    def unread(self, key: str, ts: float) -> bool:
        return self.marked_unread(key) or ts > self.read_at(key)

    def dismissed(self, key: str, ts: float) -> bool:
        return ts <= self.d["dismiss"].get(key, 0.0)


def load(state_dir: Path) -> Store:
    with _lock:
        return Store(_load(state_dir))


def ensure_baseline(state_dir: Path) -> None:
    """First use: everything that exists now counts as read, so the badge starts at 0."""
    with _lock:
        if not _path(state_dir).exists():
            try:
                st = _load(state_dir)
                st["all"] = time.time()
                _save(state_dir, st)
            except OSError:
                pass


def mark(state_dir: Path, key: str, *, dismiss: bool, now: float | None = None) -> None:
    if not KEY_RE.fullmatch(key):
        raise ValueError("bad key")
    now = time.time() if now is None else now
    with _lock:
        st = _load(state_dir)
        st["read"][key] = now
        st["unread"].pop(key, None)
        if dismiss:
            st["dismiss"][key] = now
        _save(state_dir, st)


def mark_many(state_dir: Path, keys, *, now: float | None = None) -> None:
    """Mark several keys read at once (the bell's "Mark all read")."""
    keys = [k for k in keys if KEY_RE.fullmatch(k)]
    if not keys:
        return
    now = time.time() if now is None else now
    with _lock:
        st = _load(state_dir)
        for k in keys:
            st["read"][k] = now
            st["unread"].pop(k, None)
        _save(state_dir, st)


def mark_all(state_dir: Path, *, now: float | None = None) -> None:
    with _lock:
        st = _load(state_dir)
        st["all"] = time.time() if now is None else now
        st["unread"] = {}
        _save(state_dir, st)


def mark_unread(state_dir: Path, key: str, *, now: float | None = None) -> None:
    """Mark ``key`` unread: it stays unread (in every bucket) until ``mark`` / ``mark_many`` / ``mark_all``."""
    if not KEY_RE.fullmatch(key):
        raise ValueError("bad key")
    now = time.time() if now is None else now
    with _lock:
        st = _load(state_dir)
        st["unread"][key] = now
        _save(state_dir, st)


# ------------------------------------------------------------------ agents
def agent_key(a: Agent) -> str:
    ev = "idle" if a.state is AgentState.IDLE else a.state.value
    return f"{a.id}:{ev}"


def agent_ts(a: Agent) -> float:
    if a.state is AgentState.IDLE:  # last response ("Done at"), not pm's own restart time or tmux redraws
        return a.done_ts()
    return a.state_since or a.last_active or 0.0


def idle_window(settings: Settings) -> int:
    """The finished -> idle window in seconds from ``settings.idle_after_s``, never below ``MIN_WINDOW_S``."""
    return max(MIN_WINDOW_S, int(settings.idle_after_s))


def stale_failed(a: Agent, now: float, window_s: float = FINISHED_WINDOW_S) -> bool:
    """A "failed" agent (pane-text heuristic for Devin/Codex/Cursor, which never clears itself) whose
    last response is at least ``window_s`` old is no longer news: it goes to Idle."""
    ts = max(a.done_ts(), a.last_active, a.state_since)  # a fresh failure after an old reply still counts
    return bool(ts) and now - ts >= window_s


def bucket(a: Agent, store: Store, now: float, window_s: float = FINISHED_WINDOW_S) -> str:
    """waiting | finished | running | idle. Idle = last response (``Agent.done_ts``) at least
    ``window_s`` ago, or unknown; a younger one is "finished" unless dismissed."""
    if a.state is AgentState.FAILED and stale_failed(a, now, window_s):
        return "idle"
    if a.state in (AgentState.WAITING, AgentState.FAILED):
        return "waiting"
    if a.state is AgentState.RUNNING:
        return "running"
    if a.state is AgentState.IDLE:
        ts = agent_ts(a)
        key = agent_key(a)
        if ts and now - ts < window_s and not store.dismissed(key, ts):
            return "finished"
    return "idle"


SECTIONS = (
    ("running", "Running"),
    ("waiting", "Waiting for you"),
    ("finished", "Finished"),
    ("idle", "Idle"),
)


PINNED = ("pinned", "Pinned")


def group_agents(agents, store: Store, now: float, sort, window_s: float = FINISHED_WINDOW_S,
                 pins=()) -> list[dict]:
    """[{key,title,agents,total}] in fixed order; ``sort`` orders inside a bucket. Pinned agents (``pins``: agent
    ids, ``pm.agents.pinned``) leave their bucket for the "pinned" section, which comes first."""
    pins = set(pins)
    buckets: dict[str, list[Agent]] = {k: [] for k, _ in (PINNED, *SECTIONS)}
    for a in agents:
        buckets["pinned" if a.id in pins else bucket(a, store, now, window_s)].append(a)
    return [{"key": k, "title": t, "agents": sort(buckets[k]), "total": len(buckets[k])}
            for k, t in (PINNED, *SECTIONS)]


_BOX = "│┃║╭╮╰╯─━═┌┐└┘├┤┬┴┼▌▐█▏▕"
_ASK = re.compile(r"\?|\(y/n\)|\[y/n\]|\ballow\b|\bapprove\b|\bproceed\b|\bconfirm\b|"
                  r"\bdo you want\b|\bchoose\b|\bselect\b", re.I)


def _ask_line(ask: Ask) -> str:
    """One line of an ask's text: the merged form question whole, else its last "?" line (joined with the
    line above when it wraps), else its last line."""
    if ask.questions:
        return " ".join(ask.text.split())
    lines = [ln.strip() for ln in ask.text.splitlines() if ln.strip() and not hint_line(ln)]
    if not lines:
        return ""
    i = next((k for k in range(len(lines) - 1, -1, -1) if "?" in lines[k]), len(lines) - 1)
    pick = lines[i]
    if i and pick[:1].islower():  # wrapped question: "Which layout for the" / "demo page?"
        pick = f"{lines[i - 1]} {pick}"
    return pick


def question_of(text: str | None, limit: int = 220, asking: Ask | None = None) -> str:
    """The pending question from already masked, ANSI-stripped pane text. ``asking`` (``ChatView.asking``,
    with the transcript form merged when it matches the pane) wins, then the pane's open ask
    (``find_ask``), then a best guess. Footer / key-hint lines are never the answer."""
    if asking is None and text:
        asking, _ = find_ask(text.splitlines())
    pick = _ask_line(asking) if asking else ""
    if not pick and text:
        lines = []
        for raw in text.splitlines()[-40:]:
            ln = raw.strip().strip(_BOX + " >❯").strip()
            if ln and not set(ln) <= set(_BOX + "-=_ ") and not hint_line(ln):
                lines.append(ln)
        if lines:
            pick = next((ln for ln in reversed(lines[-14:]) if _ASK.search(ln)), lines[-1])
    return pick if len(pick) <= limit else pick[: limit - 1] + "…"


MAX_ASK_OPTIONS = 4
ASK_OPTION_MAX = 80


def options_of(asking: Ask | None, limit: int = MAX_ASK_OPTIONS, width: int = ASK_OPTION_MAX) -> list[dict]:
    """The numbered option labels of an open ask, ``[{"n": 1, "label": "Yes"}, ...]``: the current form question's
    options when the transcript form is merged (``Ask.questions``), else the pane's; navigation rows (Back, Next,
    keys without a digit) are left out. Masked, at most ``limit`` options of at most ``width`` characters."""
    if asking is None:
        return []
    qs = asking.questions
    form = bool(qs) and asking.current < len(qs)
    opts = qs[asking.current].options if form else asking.options
    out = []
    for i, o in enumerate(opts, 1):
        head = (o.key or "").split(" ", 1)[0]
        if form and not head.isdigit():
            head = str(i)  # a form question's options are numbered in order
        if not head.isdigit():
            continue
        label = mask(" ".join((o.label or "").split()))
        out.append({"n": int(head), "label": label if len(label) <= width else label[: width - 1] + "…"})
    return out[:limit]


def ask_of(text: str | None, asking: Ask | None = None) -> tuple[str, list[dict]]:
    """(``question_of``, ``options_of``) of one pane read: the pending question and its numbered options."""
    if asking is None and text:
        asking, _ = find_ask(text.splitlines())
    return question_of(text, asking=asking), options_of(asking)


_qcache: dict[str, tuple[float, object]] = {}
Q_TTL_S = 8.0


def cached_question(pane_id: str, since: float, fetch, now: float | None = None, event: str = "waiting"):
    """``fetch()`` (the question, e.g. ``question_of`` / ``ask_of``, or the error line, ``error_of``) cached per
    event, pane and state start for ``Q_TTL_S``."""
    now = time.monotonic() if now is None else now
    ck = f"{event}:{pane_id}:{since}"
    hit = _qcache.get(ck)
    if hit and now - hit[0] < Q_TTL_S:
        return hit[1]
    q = fetch()
    if len(_qcache) > 64:
        _qcache.clear()
    _qcache[ck] = (now, q)
    return q


def error_of(text: str | None, limit: int = 220) -> str:
    """The error line of a failed agent from already masked, ANSI-stripped pane text (masked again), '' when
    none."""
    pick = mask(failed_line(text))
    return pick if len(pick) <= limit else pick[: limit - 1] + "…"


# ------------------------------------------------------------------ last message
LAST_TEXT_MAX = 120


def last_src(nt, user, uts) -> str:
    """Which last message a row shows: 'user' when the user's last message (``user``, epoch ``uts``) is newer
    than the newest ntfy item ``nt`` (or there is no item), 'ntfy' when there is an item, else ''. An unknown
    ``uts`` (0) loses to the item. The classic ``msg_pick`` macro calls this."""
    if user and (not nt or (uts or 0) > (getattr(nt, "time", 0) or 0)):
        return "user"
    return "ntfy" if nt else ""


def last_message(agent: Agent, nt, limit: int = LAST_TEXT_MAX) -> dict | None:
    """``{"text", "src", "ts"}`` of the row's last message (``last_src``): the user's last message
    (``chat.live.last_user_task``) or the ntfy item's message / title; one line, masked, at most ``limit``
    characters. None when there is neither."""
    user, uts = live.last_user_task(agent), live.last_user_task_ts(agent)
    src = last_src(nt, user, uts)
    if not src:
        return None
    text, ts = (user, uts) if src == "user" else (nt.message or nt.title, nt.time)
    text = mask(" ".join((text or "").split()))
    return {"text": text if len(text) <= limit else text[: limit - 1] + "…", "src": src, "ts": ts or 0.0}


# ------------------------------------------------------------------ ntfy
@dataclass(frozen=True)
class NtfyGroup:
    key: str
    item: NtfyItem  # newest
    count: int
    ts: float


def ntfy_key(item: NtfyItem) -> str:
    m = _CLICK_ID.search(item.click or "")
    ev = next((t for t in item.tags if t in EVENTS), None)
    if m and ev:
        return f"{m.group(1)}:{ev}"
    return "n:" + re.sub(r"[^A-Za-z0-9._-]", "", item.id)[:60]


SYSTEM_TITLE_RE = re.compile(r"^pm [a-z]+ -- ")  # pm's own health alerts: "pm alert -- host: ...", "pm recovered -- ..."


def is_system(item) -> bool:
    """A system message belongs to no agent: pm's own alert / recovery notice (title ``pm <word> -- ``)
    without an ``/a/<agent id>`` click link. It shows in the topbar bell, never in the ntfy tables."""
    return not _CLICK_ID.search(getattr(item, "click", "") or "") \
        and bool(SYSTEM_TITLE_RE.match(getattr(item, "title", "") or ""))


def group_ntfy(items) -> list[NtfyGroup]:
    """Newest first; the same agent+event collapses into one group with a count."""
    groups: dict[str, NtfyGroup] = {}
    for i in sorted(items, key=lambda x: x.time, reverse=True):
        k = ntfy_key(i)
        g = groups.get(k)
        groups[k] = NtfyGroup(k, g.item, g.count + 1, g.ts) if g else NtfyGroup(k, i, 1, i.time)
    return list(groups.values())


def ntfy_unread(store: Store, key: str, ts: float, now: float) -> bool:
    """An ntfy message / group counts as unread: marked unread, or newer than its read mark inside
    ``NTFY_UNREAD_WINDOW_S`` (callers add the dismiss check where they need it)."""
    return store.marked_unread(key) or (now - ts < NTFY_UNREAD_WINDOW_S and store.unread(key, ts))


def item_unread(store: Store, item: NtfyItem, now: float) -> bool:
    """One ntfy message is unread (``ntfy_unread`` on its group key) and not dismissed; system messages never."""
    key = ntfy_key(item)
    return not is_system(item) and ntfy_unread(store, key, item.time, now) and not store.dismissed(key, item.time)


def unread_count(agents, groups, store: Store, now: float | None = None,
                 window_s: float = FINISHED_WINDOW_S) -> int:
    now = time.time() if now is None else now
    keys: set[str] = set()
    for a in agents:
        ts = agent_ts(a)
        k = agent_key(a)
        if store.marked_unread(k) or (bucket(a, store, now, window_s) in ("waiting", "finished") and store.unread(k, ts)):
            keys.add(k)
    for g in groups:
        if is_system(g.item):  # counted by the bell, not by the Inbox
            continue
        if ntfy_unread(store, g.key, g.ts, now) and not store.dismissed(g.key, g.ts):
            keys.add(g.key)
    return len(keys)
