"""The dt Projects list and the New project dialog's view models (PRD.md §6.4 first and last bullets, LAYOUT.md
§5 "Projects list", L-6).

``dt_projects`` is a Jinja global (``template_globals()``, bound to the plugin's settings) called by the dt
``projects.html`` / ``_projects_list.html`` / ``projects/_new.html``. ``dt_projects()`` builds the list from the route's
view model (``pm.web.routes.pages.projects_page``: projects, cols, self_url, activity, ntfy_counts, stats, pins, q,
flags, seg_counts, issue_counts, limit_*); agent states come from the poll loop's published snapshot. No file, tmux or
network IO. ``dt_projects("new")`` is the dialog's form model (projects root, templates, agent kinds, permission
modes, the push remote and its URL preview) from the settings only; the live plan and the run are the core's
``GET /p/new/plan`` and ``POST /p/new/run`` (js/newproject.js).

Query parameters are the core's: ``q``, ``f`` (one segment: dirty | agents | todo | pinned), ``sort``, ``n``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

from jinja2 import pass_context

from pm.agents import launch as launch_mod
from pm.agents import sorting
from pm.core import timefmt
from pm.core.models import AgentKind, Project
from pm.infra import snapshot as services
from pm.projects import newproject
from pm.web.dt import menus
from pm.web.dt.inbox import klass, params_of
from pm.web.dt.shell import age

log = logging.getLogger(__name__)

PATH = "/p"
SEGS = (("", "All"), *sorting.FILTERS)  # "" = All; the core's one-at-a-time project filters (?f=)
# column -> (core sort key of the first click, that key's order as the column's aria-sort: True = ascending).
# Agents / Issues / ntfy / Git start with the most first (descending); Project and TODO (least done first) ascending;
# Active "newest" = the smallest age first (ascending, as the Inbox's Age).
SORTS = {"name": ("name", True), "agents": ("agents", False), "todo": ("todo", True), "issues": ("issues", False),
         "ntfy": ("ntfy", False), "active": ("newest", True), "git": ("dirty", False)}
REVERSE = {"newest": "oldest", "oldest": "newest"}
COLS = {"name": "main", "agents": "age", "todo": "res", "issues": "issues", "ntfy": "ntfy", "active": "act",
        "git": "git"}  # tb.gh key -> the core's sort_cols column id (its href flips the order on a second click)
STATE_WORD = {"waiting": "needs you", "running": "running"}


@dataclass(frozen=True)
class Row:
    """One project row. ``menu`` = the ⋯ items (docs/themes/dt/PARTS.md §9)."""
    name: str
    href: str
    path: str
    pinned: bool
    agents: int
    state: str  # waiting | running | idle: the agents' summary dot ("" = no agents)
    word: str  # the dot's title and screen-reader word ("2 running", "1 needs you")
    todo_done: int
    todo_total: int
    issues: int
    ntfy: int
    active: str  # age of the last activity ("" = none)
    active_title: str
    branch: str  # "" = no git
    dirty: int
    ahead: int
    behind: int
    git_title: str
    stack: str  # "2/3" ("" = no stack)
    stack_ok: bool
    meta: str  # phone line 2
    menu: list = field(default_factory=list)


@dataclass(frozen=True)
class Page:
    stats: list
    segs: list[dict]
    cols: list[dict]
    sort: str | None  # tb.gh order ("name", "-agents", ...)
    rows: list[Row]
    shown: int
    total: int
    more_url: str | None
    empty: str
    empty_url: str | None
    empty_link: str
    self_url: str
    remote: str  # pm.toml push_remote: what ahead / behind count against


@dataclass(frozen=True)
class NewForm:
    root: str  # dev_root (the folder preview)
    templates: list[dict]  # [{name, description}]
    kinds: list[str]
    modes: dict  # launch.form_spec: {kind: {mode_label, modes: [{key, help, bypass}], extra, efforts}}
    remote: str  # git_remote_template with <name>, "" = not configured
    remote_name: str  # pm.toml push_remote: the remote the step adds


# ------------------------------------------------------------------ helpers
def url_with(params: Mapping[str, str], **kw) -> str:
    """``/p`` with ``params`` changed by ``kw`` (None / "" drops a parameter)."""
    p = {k: v for k, v in {**params, **kw}.items() if v not in (None, "")}
    return PATH + ("?" + urlencode(p) if p else "")


def stats_of(text: str, total: int) -> list:
    """The core's stats line ("Projects · 12 · 3 dirty · 1 behind · ...") as head stats: the count gets its noun,
    "behind" is yellow (it says so in words)."""
    parts = [s for s in (text or "").split(" · ") if s]
    if parts and parts[0] == "Projects":
        parts = parts[1:]
    if parts and parts[0].isdigit():
        parts = parts[1:]
    out: list = [f"{total} project{'' if total == 1 else 's'}"]
    for s in parts:
        out.append({"text": s, "cls": "warn"} if s.endswith(" behind") else s)
    return out


def header(cols: Mapping[str, Mapping], sort_key: str) -> tuple[list[dict], str | None]:
    """tb.gh columns and the current order. Links are the core's (``sorting.sort_cols``: every other parameter kept,
    the current column links to its reverse); Stack has no core sort key and is not sortable."""
    def href(k: str) -> str | None:
        c = cols.get(COLS[k]) if cols else None
        return c.get("href") if isinstance(c, Mapping) else None

    order = None
    for k, (first, up) in SORTS.items():
        second = REVERSE.get(first, "-" + first)
        if sort_key in (first, second):
            asc = up if sort_key == first else not up
            order = k if asc else "-" + k
    out = [{"sr": "Pinned"},
           {"label": "Project", "key": "name", "href": href("name")},
           {"label": "Agents", "key": "agents", "href": href("agents"), "num": True},
           {"label": "TODO", "key": "todo", "href": href("todo"), "num": True, "cls": "hide-n",
            "title": "TODO items done / total"},
           {"label": "Issues", "key": "issues", "href": href("issues"), "num": True, "cls": "hide-n",
            "title": "Open issues"},
           {"label": "ntfy", "key": "ntfy", "href": href("ntfy"), "num": True, "cls": "hide-n"},
           {"label": "Active", "key": "active", "href": href("active"), "num": True, "cls": "hide-n",
            "title": "Last commit or agent activity"},
           {"label": "Git", "key": "git", "href": href("git"), "cls": "hide-s"},
           {"label": "Stack", "cls": "hide-n", "title": "Compose services up / total"},
           {"sr": "Actions"}]
    return out, order


def agent_state(p: Project, agents: Mapping, now: float, window: float) -> tuple[str, str]:
    """(dot state, word) of a project's live agents: waiting (yellow) when any needs you, else running, else idle."""
    c = {"needs": 0, "running": 0, "idle": 0}
    for aid in p.agent_ids:
        a = agents.get(aid)
        if a is not None:
            c[klass(a, now, window)] += 1
    if c["needs"]:
        return "waiting", f"{c['needs']} {STATE_WORD['waiting']}"
    if c["running"]:
        return "running", f"{c['running']} running"
    return ("idle", "idle") if p.agent_ids else ("", "")


def _menu(p: Project, pinned: bool) -> list[dict]:
    """⋯ items (PRD §6.4: pin also from the phone, via ⋯)."""
    enc = quote(p.name, safe="")
    return menus.menu(
        menus.item("open", "Open project", "folder", key="Enter", href=f"/p/{enc}"),
        menus.item("launch", "Launch agent…", "play", event="pj:launch"),  # projects.js: the Launch dialog
        menus.SEP,
        menus.item("pin", "Unpin" if pinned else "Pin to the top", "pin", key="P", post=f"/p/{enc}/pin",
                   vals={"pinned": "0" if pinned else "1"}, done=("Unpinned " if pinned else "Pinned ") + p.name,
                   refresh="#pj"),
        menus.item("copy", "Copy folder path", "copy", copy=p.path))


def row(p: Project, ctx: Mapping, agents: Mapping, now: float, window: float, remote: str) -> Row:
    pinned = p.name in (ctx.get("pins") or ())
    state, word = agent_state(p, agents, now, window)
    g, t, s = p.git, p.todo, p.stack
    ts = (ctx.get("activity") or {}).get(p.name) or 0.0
    branch = (g.branch or "no branch") if g else ""
    if g is None:
        git_title = "Not a git repository"
    else:
        git_title = " · ".join(x for x in (
            f"branch {branch}", f"{g.dirty} changed" if g.dirty else "clean", f"{g.ahead} ahead" if g.ahead else "",
            f"{g.behind} behind" if g.behind else "", "" if g.has_remote else f"no {remote} remote") if x)
    n = len(p.agent_ids)
    meta = [(branch + (f" · {g.dirty} dirty" if g.dirty else "")) if g else "no git",
            f"{n} agent{'' if n == 1 else 's'}" if n else ""]
    if t and t.total:
        meta.append(f"TODO {t.done}/{t.total}")
    return Row(
        name=p.name, href=f"/p/{quote(p.name, safe='')}", path=p.path, pinned=pinned, agents=n, state=state,
        word=word, todo_done=t.done if t else 0, todo_total=t.total if t else 0,
        issues=int((ctx.get("issue_counts") or {}).get(p.name, 0) or 0),
        ntfy=int((ctx.get("ntfy_counts") or {}).get(p.path, 0) or 0),
        active=age(ts, now) if ts else "", active_title=timefmt.fmt_full(ts) if ts else "",
        branch=branch, dirty=g.dirty if g else 0, ahead=g.ahead if g else 0, behind=g.behind if g else 0,
        git_title=git_title, stack=f"{s.running}/{s.total}" if s else "",
        stack_ok=bool(s and s.total and s.running == s.total), meta=" · ".join(x for x in meta if x),
        menu=_menu(p, pinned))


def _empty(params: Mapping[str, str], q: str, flags: list[str], shown: int) -> tuple[str, str | None, str]:
    """(text, link href, link text) of the empty line (DESIGN §6.21)."""
    if shown:
        return "", None, ""
    if q:
        return f"No project matches “{q}”.", url_with(params, q=None), "Clear the filter"
    if flags:
        names = dict(sorting.FILTERS)
        return f"No project in “{' + '.join(names.get(f, f) for f in flags)}”.", url_with(params, f=None), "Show all"
    return "No projects yet. New project creates one under the projects root.", None, ""


# ------------------------------------------------------------------ the page
def build(ctx: Mapping, settings, now: float | None = None) -> Page:
    """The Projects list for a route context (``projects_page``'s view model)."""
    now = time.time() if now is None else now
    self_url = ctx.get("self_url") or PATH
    params = params_of(self_url)
    sort_key = params.get("sort") or sorting.DEFAULT
    flags = [f for f in (ctx.get("flags") or ()) if f in dict(sorting.FILTERS)]
    snap = services.published(settings) if settings is not None else None
    agents = {a.id: a for a in snap.agents} if snap is not None else {}
    window = float(getattr(settings, "idle_after_s", 0) or 0) or 28800.0
    remote = settings.push_remote if settings is not None else ""
    rows = [row(p, ctx, agents, now, window, remote) for p in ctx.get("projects") or ()]
    counts = ctx.get("seg_counts") or {}
    cur = flags[0] if len(flags) == 1 else ("" if not flags else None)
    segs = [{"key": k or "all", "label": lbl, "href": url_with(params, f=k or None, n=None),
             "cur": k == cur, "count": counts.get(k)} for k, lbl in SEGS]
    cols, order = header(ctx.get("cols") or {}, sort_key)
    shown, total = len(rows), int(ctx.get("limit_total") or len(rows))
    q = ctx.get("q") or ""
    empty, eurl, elink = _empty(params, q, flags, shown)
    return Page(stats=stats_of(ctx.get("stats") or "", int(ctx.get("total_all") or 0)), segs=segs, cols=cols,
                sort=order, rows=rows, shown=shown, total=total,
                more_url=url_with(params, n="all") if shown < total else None,
                empty=empty, empty_url=eurl, empty_link=elink, self_url=self_url, remote=remote)


def new_form(settings) -> NewForm:
    """The New project dialog's form model (settings only; the plan and the run are the core's routes)."""
    kinds = [k.value for k in AgentKind if k is not AgentKind.OTHER]
    remote = newproject.remote_url(settings, "<name>") if settings is not None else ""
    return NewForm(root=str(getattr(settings, "dev_root", "") or ""),
                   templates=[{"name": t["name"], "description": t["description"]}
                              for t in newproject.templates(settings)] if settings is not None else [],
                   kinds=kinds, modes=launch_mod.form_spec(settings) if settings is not None else {}, remote=remote,
                   remote_name=settings.push_remote if settings is not None else "")


# ------------------------------------------------------------------ Jinja global
_logged: set[str] = set()


def bind(settings_of: Callable[[], object]):
    """The ``dt_projects`` global: ``dt_projects()`` = the ``Page`` of the list being rendered, ``dt_projects("new")``
    = the dialog's ``NewForm``. Never raises (None on an error, logged once per error type)."""
    @pass_context
    def dt_projects(ctx, part: str = "list"):
        try:
            return new_form(settings_of()) if part == "new" else build(ctx, settings_of())
        except Exception as e:  # noqa: BLE001 - a broken view model must not break the page
            name = type(e).__name__
            if name not in _logged:
                _logged.add(name)
                log.exception("dt projects view model failed")
            return None
    return dt_projects

