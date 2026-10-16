"""Pure, stable sort helpers for the inbox and project lists (ties by id / name)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from urllib.parse import urlencode

from pm.core.models import Agent, AgentState, Project

DEFAULT = "newest"
# Sortable columns per page: column id -> (label, first key, second key, first arrow, second arrow).
# A header click picks the first key; clicking the active column flips to the other one.
# Keys stay plain ``?sort=`` values; "-x" is the reverse of "x".
COLUMNS: dict[str, dict[str, tuple[str, str, str, str, str]]] = {
    "inbox": {
        "age": ("Age", "newest", "oldest", "\u25bc", "\u25b2"),
        "proj": ("Project", "project", "-project", "\u25b2", "\u25bc"),
        "main": ("Agent", "name", "-name", "\u25b2", "\u25bc"),
        "start": ("Started", "started", "-started", "\u25bc", "\u25b2"),
        "res": ("cpu / mem", "cpu", "-cpu", "\u25bc", "\u25b2"),
        "ram": ("RAM", "ram", "-ram", "\u25bc", "\u25b2"),  # no classic cell; themes may show it
        "state": ("State", "state", "-state", "\u25b2", "\u25bc"),
    },
    "projects": {
        "main": ("Project dir", "name", "-name", "\u25b2", "\u25bc"),
        "age": ("Agents", "agents", "-agents", "\u25bc", "\u25b2"),
        "res": ("TODO", "todo", "-todo", "\u25b2", "\u25bc"),
        "ntfy": ("ntfy", "ntfy", "-ntfy", "\u25bc", "\u25b2"),
        "act": ("Active", "newest", "oldest", "\u25bc", "\u25b2"),
        "git": ("Git status", "dirty", "-dirty", "\u25bc", "\u25b2"),
        "issues": ("Issues", "issues", "-issues", "\u25bc", "\u25b2"),  # no classic cell; themes may show it
    },
    "tmux": {
        "main": ("Session", "name", "-name", "\u25b2", "\u25bc"),
        "age": ("Windows / panes", "counts", "-counts", "\u25bc", "\u25b2"),
        "agents": ("Agents", "agents", "-agents", "\u25bc", "\u25b2"),
        "act": ("Last active", "newest", "oldest", "\u25bc", "\u25b2"),
    },
}
DEFAULTS = {"tmux": "name"}
COOKIES = {"inbox": "pm_sort_inbox", "projects": "pm_sort_projects", "tmux": "pm_sort_tmux"}
_STATE_ORDER = {"waiting": 0, "running": 1}


def valid_key(page: str, key: str | None) -> bool:
    cols = COLUMNS.get(page, {})
    return key is not None and any(key in (c[1], c[2]) for c in cols.values())


def choose(page: str, param: str | None, cookie: str | None) -> tuple[str, bool]:
    """Return (key, persist). Explicit param wins (bad -> newest, not persisted)."""
    default = DEFAULTS.get(page, DEFAULT)
    if param is not None:
        return (param, True) if valid_key(page, param) else (default, False)
    if valid_key(page, cookie):
        return cookie, False  # type: ignore[return-value]
    return default, False


def active_ts(a: Agent) -> float:
    """Real activity time: last_active, else a real state_since, else 0 (unknown, sinks).

    Never "first seen by pm": the tracker does not invent timestamps.
    """
    if a.state is AgentState.IDLE:  # an idle agent's age is its last response ("Done at")
        return a.done_ts()
    return a.last_active or a.state_since or 0.0


def _state_rank(a: Agent) -> int:
    return _STATE_ORDER.get(a.state.value, 2)


def sort_agents(agents: Iterable[Agent], key: str) -> list[Agent]:
    """Flat sort by a column key; "-x" is the reverse of "x". Unknown keys = newest."""
    items = sorted(agents, key=lambda a: a.id)  # deterministic base order
    if key == "oldest":  # unknown activity still sinks
        return sorted(items, key=lambda a: (active_ts(a) == 0, active_ts(a)))
    rev = key.startswith("-")
    base = key.lstrip("-")
    ranks: dict[str, tuple[Callable[[Agent], tuple], bool]] = {
        "project": (lambda a: ((a.project or a.cwd or "").lower(), -active_ts(a)), False),
        "name": (lambda a: ((a.label or a.id).lower(), a.id), False),
        "cpu": (lambda a: (a.cpu_pct,), True),
        "ram": (lambda a: (a.rss_mb,), True),
        "started": (lambda a: (not a.age_s, a.age_s, a.id), False),  # newest start = smallest uptime; unknown last
        "state": (lambda a: (_state_rank(a), a.state.value, -active_ts(a)), False),
    }
    if base in ranks:
        fn, desc = ranks[base]
        return sorted(items, key=fn, reverse=desc != rev)
    return sorted(items, key=lambda a: -active_ts(a))


# Inbox sections. Failed/unknown are folded into Idle (documented choice: both mean
# "not working, not asking"); the row badge still shows the exact state.
SECTIONS: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("waiting", "Waiting for you", frozenset({"waiting"})),
    ("running", "Running", frozenset({"running"})),
    ("idle", "Idle", frozenset({"idle", "failed", "unknown"})),
)


def group_agents(agents: Iterable[Agent], key: str) -> list[dict]:
    """[{key, title, agents}] for non-empty sections; the sort applies inside each."""
    ordered = sort_agents(agents, key)
    out = []
    for skey, title, states in SECTIONS:
        rows = [a for a in ordered if a.state.value in states]
        if rows:
            out.append({"key": skey, "title": title, "agents": rows})
    return out


def q_text(*parts: str | None) -> str:
    """Lowercase filter haystack from plain name-type strings (never message or body text)."""
    return " ".join(p for p in parts if p).lower()


def agent_q(a, model: str = "") -> str:
    """Filter text of a live Agent or an ended Record: label, tmux window name, project (else cwd), id, kind
    and short model name. One function for the server filter and every row's data-q, so both match alike."""
    kind = getattr(a.kind, "value", a.kind)
    return q_text(a.label, getattr(a, "window_name", ""), a.project or a.cwd, a.id, kind, model)


def fold_q(q: str | None) -> str:
    """The typed query as filter.js q() folds it: trimmed, lowercase, runs of whitespace as one space."""
    return " ".join((q or "").lower().split())


def project_q(p: Project) -> str:
    """Filter text of a project row: name, path, git branch, "dirty" when the tree is dirty, "stack" when it has a
    compose stack. One function for the server filter and every row's data-q."""
    g = p.git
    return q_text(p.name, p.path, g.branch if g else "", "dirty" if g and g.dirty else "", "stack" if p.stack else "")


def session_q(s, agents: Iterable[Agent] = ()) -> str:
    """Filter text of a tmux session row: session name, every window name, and each agent's label and id."""
    return q_text(s.name, *(w.name for w in s.windows), *(x for a in agents for x in (a.label, a.id)))


def filter_agents(agents: Iterable[Agent], q: str, model_of: Callable[[Agent], str] | None = None) -> list[Agent]:
    """Case-insensitive substring over agent_q (model_of(agent) supplies the model name)."""
    needle = (q or "").strip().lower()
    items = list(agents)
    if not needle:
        return items
    return [a for a in items if needle in agent_q(a, model_of(a) if model_of else "")]


def activity(p: Project, agents: Mapping[str, Agent]) -> float:
    """Latest activity: git HEAD commit time or newest agent state change; 0 when none."""
    ts = [p.git.last_commit_ts] if p.git and p.git.last_commit_ts else []
    ts += [active_ts(agents[i]) for i in p.agent_ids if i in agents and active_ts(agents[i])]
    return max(ts, default=0.0)


def _pct(p: Project) -> float:
    t = p.todo
    return (t.done / t.total) if t and t.total else 1.0  # no TODO = nothing remaining


def sort_projects(
    projects: Iterable[Project], key: str, agents: Mapping[str, Agent] | None = None,
    ntfy: Mapping[str, int] | None = None, issues: Mapping[str, int] | None = None,
) -> list[Project]:
    """``issues`` = {project name: open issues} for the ``issues`` key."""
    agents = agents or {}
    items = sorted(projects, key=lambda p: p.name.lower())
    rev = key.startswith("-")
    base = key.lstrip("-")
    # (rank, descending-by-default)
    ranks: dict[str, tuple[Callable[[Project], tuple], bool]] = {
        "name": (lambda p: (p.name.lower(),), False),
        "dirty": (lambda p: (p.git.dirty if p.git else 0,), True),
        "agents": (lambda p: (len(p.agent_ids),), True),
        "todo": (lambda p: (_pct(p),), False),
        "ntfy": (lambda p: ((ntfy or {}).get(p.path, 0),), True),
        "issues": (lambda p: ((issues or {}).get(p.name, 0),), True),
    }
    if base in ranks:
        fn, desc = ranks[base]
        return sorted(items, key=fn, reverse=desc != rev)
    sign = 1 if key == "oldest" else -1
    # projects without git (or without any activity) sink; alphabetical among themselves
    return sorted(
        items,
        key=lambda p: (0, sign * activity(p, agents)) if p.git is not None else (1, 0.0),
    )


def sort_cols(page: str, active: str, path: str, query: Mapping[str, str]) -> dict[str, dict]:
    """Header cells for the sort row, by column id: {label, href, active, arrow}.

    Plain links (``?sort=``); other query params are preserved. The active column links to
    its reverse and shows an arrow.
    """
    base = {k: v for k, v in query.items() if k != "sort"}
    out = {}
    for cid, (label, k1, k2, a1, a2) in COLUMNS[page].items():
        on = active in (k1, k2)
        nxt = k2 if active == k1 else k1
        out[cid] = {"label": label, "active": on, "href": path + "?" + urlencode({**base, "sort": nxt}),
                    "arrow": (a1 if active == k1 else a2) if on else ""}
    return out


def sort_sessions(sessions: Iterable, key: str, info: Mapping[str, dict]) -> list:
    """tmux sessions by name / windows+panes / agent count / last agent activity."""
    items = sorted(sessions, key=lambda s: s.name.lower())
    if key == "oldest":
        return sorted(items, key=lambda s: (not info[s.id]["last"], info[s.id]["last"] or 0.0))
    rev = key.startswith("-")
    base = key.lstrip("-")
    ranks: dict[str, tuple[Callable, bool]] = {
        "name": (lambda s: s.name.lower(), False),
        "counts": (lambda s: (info[s.id]["windows"], info[s.id]["panes"]), True),
        "agents": (lambda s: len(info[s.id]["agents"]), True),
    }
    if base in ranks:
        fn, desc = ranks[base]
        return sorted(items, key=fn, reverse=desc != rev)
    return sorted(items, key=lambda s: -(info[s.id]["last"] or 0.0))


# ---- list limit ("Show: 10 / 20 / 50 / All") ----
LIMIT_CHOICES: tuple[str, ...] = ("10", "20", "50", "all")
LIMIT_COOKIES = {
    "inbox": "pm_limit_inbox", "projects": "pm_limit_projects", "tmux": "pm_limit_tmux",
}


def valid_limit(value: str | None) -> bool:
    return value in LIMIT_CHOICES


def choose_limit(
    page: str, param: str | None, cookie: str | None, default: int = 10
) -> tuple[str, bool]:
    """Return (key, persist); key is "10"/"20"/"50"/"all". Explicit valid param wins and
    persists; invalid param -> default (not persisted); else a valid cookie; else default."""
    dkey = str(default) if str(default) in LIMIT_CHOICES else "10"
    if param is not None:
        return (param, True) if valid_limit(param) else (dkey, False)
    if valid_limit(cookie):
        return cookie, False  # type: ignore[return-value]
    return dkey, False


def limit_value(key: str) -> int | None:
    return None if key == "all" else int(key)


def apply_limit(items, key: str) -> list:
    """Truncate AFTER sorting; "all" keeps everything."""
    items = list(items)
    n = limit_value(key)
    return items if n is None else items[:n]


def limit_links(key: str, path: str, query: Mapping[str, str]) -> list[dict]:
    base = {k: v for k, v in query.items() if k != "n"}
    return [
        {"key": k, "label": "All" if k == "all" else k, "active": k == key,
         "href": path + "?" + urlencode({**base, "n": k})}
        for k in LIMIT_CHOICES
    ]


FILTERS = (("dirty", "Dirty"), ("agents", "Has agents"), ("todo", "Has TODO"), ("pinned", "Pinned"))


def _project_tests(pins: set[str]) -> dict[str, Callable[[Project], bool]]:
    return {
        "dirty": lambda p: bool(p.git and p.git.dirty),
        "agents": lambda p: bool(p.agent_ids),
        "todo": lambda p: bool(p.todo and p.todo.total > p.todo.done),  # open items
        "pinned": lambda p: p.name in pins,
    }


def filter_projects(
    projects: Iterable[Project], q: str = "", flags: Iterable[str] = (), pins: Iterable[str] = (),
) -> list[Project]:
    """Case-insensitive search over project_q AND every selected filter."""
    q = fold_q(q)
    flags = set(flags)
    tests = _project_tests(set(pins))
    return [
        p for p in projects
        if (not q or q in project_q(p)) and all(tests[f](p) for f in flags if f in tests)
    ]


def filter_counts(projects: Iterable[Project], q: str = "", pins: Iterable[str] = ()) -> dict[str, int]:
    """Rows each project filter segment would show for the query ``q``: {"": All, "dirty": n, "agents": n,
    "todo": n, "pinned": n} (one segment at a time, as the filter select picks them)."""
    matched = filter_projects(projects, q)
    tests = _project_tests(set(pins))
    return {"": len(matched), **{f: sum(1 for p in matched if tests[f](p)) for f, _ in FILTERS}}


# tmux page segments (``?f=``): one at a time
TMUX_FILTERS = (("agents", "Has agents"), ("attached", "Attached"), ("protected", "Protected"))


def _session_tests(info: Mapping[str, Iterable[Agent]]) -> dict[str, Callable]:
    return {
        "agents": lambda s: bool(list(info.get(s.id, ()))),
        "attached": lambda s: s.attached > 0,
        "protected": lambda s: bool(s.protected),
    }


def filter_tmux(sessions: Iterable, flag: str, info: Mapping[str, Iterable[Agent]]) -> list:
    """Sessions of one tmux segment (``TMUX_FILTERS``); an unknown or empty ``flag`` keeps all."""
    items = list(sessions)
    test = _session_tests(info).get(flag)
    return items if test is None else [s for s in items if test(s)]


def tmux_counts(sessions: Iterable, info: Mapping[str, Iterable[Agent]]) -> dict[str, int]:
    """{"": All, "agents": n, "attached": n, "protected": n} over ``sessions``."""
    items = list(sessions)
    tests = _session_tests(info)
    return {"": len(items), **{f: sum(1 for s in items if tests[f](s)) for f, _ in TMUX_FILTERS}}


def filter_sessions(sessions: Iterable, q: str, info: Mapping[str, Iterable[Agent]]) -> list:
    """tmux sessions whose session_q contains q; info maps session id -> its agents. Empty q keeps all."""
    needle = fold_q(q)
    items = list(sessions)
    if not needle:
        return items
    return [s for s in items if needle in session_q(s, info.get(s.id, ()))]


def pinned_first(projects: list[Project], pins: Iterable[str]) -> list[Project]:
    pins = set(pins)
    return [p for p in projects if p.name in pins] + [p for p in projects if p.name not in pins]
