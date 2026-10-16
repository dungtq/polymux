"""The dt project view's view model (PRD.md §6.4, LAYOUT.md §5 "Project view"): the project sidebar (every
project with its agents and a need-you badge), the head (name, stats line, Launch agent · Git ▾ · Restart stack · Stop
agents · Settings), the links row (linked projects, plugin ``ext_groups``), the six tiles, the tabs with their counts,
the Agents tab (the Inbox rows of this project in bands, plus Ended with Resume), the tmux tab (the project's sessions
as the tmux page's rows, ``tmux.sess_row``), the Plans rows and the Docs choices (plus Skills).

``dt_project`` is a Jinja global (``template_globals()``, bound to the plugin's settings) called by the dt
``project.html`` and by ``_project_live.html`` (the Agents tab's live fragment, ``GET /p/{name}/live``). It reads only
the route's context (``pages.project_page`` / ``project_live``: project, sidebar, all_agents, agents, idle_agents,
pinned_agents, inbox_meta, sessions, now, ended, ended_total, ended_ntfy, backlog, todo, plans, ps / hid, has_*,
first_key, stop_agents, linked, ext_groups, request) and the poll loop's published snapshot (tmux windows for the
terminal links); no IO. Keys the fragment does not have (sidebar, summaries) give empty parts.

Remembered state: the project sidebar's collapse is the cookie ``pm_v7_pjnav`` ("1" = collapsed; written by
js/project.js), read here so the server renders the final layout (LAYOUT L-1). The first tab comes from ``?tab=``
(else Agents); js/project.js keeps the current tab in the URL hash (``#tab=<key>``).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import quote

from jinja2 import pass_context

from pm.agents import sorting
from pm.core.models import AgentState
from pm.infra import snapshot as services
from pm.web.dt import inbox, menus, tmux
from pm.web.dt.backlog import plural
from pm.web.dt.shell import age

log = logging.getLogger(__name__)

NAV_COOKIE = "pm_v7_pjnav"
AGENTS_SEL = "#pj-agents"  # the Agents tab's live box: ⋯ items refresh it
SIDE_AGENTS = 5  # agent rows per project in the sidebar before "+N more"
PANIC = "PANIC"
TABS = (("agents", "Agents", "bot"), ("tmux", "tmux", "tmux"), ("files", "Files", "folder"), ("git", "Git", "branch"),
        ("stack", "Stack", "box"), ("vms", "VMs", "vm"), ("todo", "TODO", "list"), ("issues", "Issues", "warn"),
        ("plans", "Plans", "file"), ("docs", "Docs", "file"))
HIDE = {"tmux": "live", "files": "browse", "git": "git", "stack": "stack", "todo": "todo", "issues": "backlog",
        "plans": "plans"}
# project settings › Show: the core's components (pm.projects.settings.COMPONENTS) as the dt view names them
COMPONENT_LABELS = {"browse": "Files tab", "git": "Git tab", "skills": "Skills (Docs)", "plans": "Plans tab",
                    "todo": "TODO tab", "backlog": "Issues tab", "readme": "README", "features": "FEATURES",
                    "changelog": "CHANGELOG", "claude": "CLAUDE", "agents": "AGENTS", "stack": "Stack tab",
                    "live": "tmux tab"}
DOCS = (("readme", "README", ""), ("changelog", "CHANGELOG", ""), ("features", "FEATURES", "has_features"),
        ("claude", "CLAUDE", "has_claude"), ("agents", "AGENTS", "has_agents"), ("skills", "Skills", "list"))
STATE_ORDER = {"waiting": 0, "failed": 0, "running": 1, "unknown": 2, "idle": 3}


@dataclass(frozen=True)
class SideAgent:
    id: str
    state: str  # dot class
    word: str  # screen-reader word / title
    href: str
    since: int | None
    age: str
    q: str  # client filter text (agent id, window, project)


@dataclass(frozen=True)
class SideProject:
    name: str
    href: str
    cur: bool
    n: int  # live agents
    running: int
    need: int  # waiting + failed (the need-you badge)
    unread: int  # unread ntfy messages (core sidebar)
    open: bool  # agent rows shown (current project, or one of its agents is not idle)
    agents: list[SideAgent]
    more: int  # agents not listed
    q: str  # client filter text: the project name and every agent id (an agent match keeps its project)


@dataclass(frozen=True)
class Tile:
    key: str  # the tab it opens
    label: str
    value: str
    small: str = ""
    sub: str = ""
    cls: str = ""  # sub line tone: warn | ""


@dataclass(frozen=True)
class PlanRow:
    name: str
    src: str
    label: str
    age: str
    since: int | None
    reader: str  # embed URL for the reader aside
    href: str  # the plan page


@dataclass(frozen=True)
class Docs:
    items: list[dict]  # {"key", "label", "href", "cur", "miss"}: miss = known not to exist (js/project.js skips it)
    html: str  # the first document, rendered by the core ("" = load it, or nothing to show)
    lazy: bool  # the first document is not known: js/project.js loads the choices in order and keeps the first found


@dataclass(frozen=True)
class LinkGroup:
    title: str
    links: list[dict]  # {"label", "url", "desc", "icon", "ext"}: ext = a plugin's link (new browser tab)


@dataclass(frozen=True)
class Sessions:
    rows: list  # pm.web.dt.tmux.Sess, their ⋯ refreshing #pj-agents (the live fragment re-renders this table)
    closable: int  # sessions Close all may close (not protected; the core skips attached / pm's own and says so)
    cols: list[dict]  # the tmux page's header (tmux.header), without its sort links


@dataclass(frozen=True)
class ProjectView:
    name: str
    path: str
    base: str  # /p/<name>
    cur_tab: str
    navc: bool
    stats: list
    actions: list[dict]
    tiles: list[Tile]
    tabs: list[dict]  # pt.tabs items (controls = pj-t-<key>)
    side: list[SideProject]
    side_total: int
    groups: list[inbox.Group]
    ended: list[inbox.Ended]
    ended_total: int
    agents_n: int
    need: int
    plans: list[PlanRow] = field(default_factory=list)
    plans_total: int = 0
    docs: Docs | None = None
    hidden: frozenset = frozenset()
    links: list[LinkGroup] = field(default_factory=list)
    sessions: Sessions | None = None


EMPTY = None


# ------------------------------------------------------------------ sidebar
def _need(a, now: float, window: float) -> bool:
    return inbox.klass(a, now, window) == "needs"


def side_agent(a, now: float) -> SideAgent:
    st = a.state.value
    since = a.state_since or a.last_active
    return SideAgent(a.id, st, inbox.STATE_WORD.get(st, st), "/a/" + a.id,
                     int(since) if since else None, age(since, now),
                     " ".join(x for x in (a.id, a.window_name, a.project or "") if x).lower())


def side(ctx: Mapping, cur: str, now: float, window: float) -> list[SideProject]:
    """Every project of the core sidebar (its order) with its live agents: needs-you first, then running, then by
    name; the current project and projects with a non-idle agent show their rows."""
    sb = ctx.get("sidebar")
    by: dict[str, list] = {}
    for a in ctx.get("all_agents") or ():
        if a.project:
            by.setdefault(a.project, []).append(a)
    out = []
    for r in getattr(sb, "rows", None) or ():
        ags = sorted(by.get(r.key, ()), key=lambda a: (STATE_ORDER.get(a.state.value, 2), a.id))
        need = sum(_need(a, now, window) for a in ags)
        running = sum(a.state is AgentState.RUNNING for a in ags)
        is_cur = r.key == cur
        shown = ags if is_cur else ags[:SIDE_AGENTS]
        out.append(SideProject(
            name=r.key, href=r.href, cur=is_cur, n=len(ags), running=running, need=need,
            unread=int(getattr(r, "unread", 0) or 0),
            open=bool(ags) and (is_cur or any(a.state is not AgentState.IDLE for a in ags)),
            agents=[side_agent(a, now) for a in shown], more=len(ags) - len(shown),
            q=" ".join([r.key, *(a.id for a in ags)]).lower()))
    return out


# ------------------------------------------------------------------ head, tiles, tabs
def git_parts(git) -> tuple[str, str, str]:
    """(branch, summary, tone) of a GitStatus: "3 dirty · ↓2 to pull · ↑1 to push" or "clean · in sync"."""
    if git is None:
        return "–", "not a git repository", ""
    bits, tone = [], ""
    if git.dirty:
        bits.append(f"{git.dirty} dirty")
        tone = "warn"
    if git.behind:
        bits.append(f"↓{git.behind} to pull")
        tone = "warn"
    if git.ahead:
        bits.append(f"↑{git.ahead} to push")
    if not bits:
        bits.append("clean · " + ("in sync" if git.has_gitdev else "no gitdev remote"))
    return git.branch or "detached", " · ".join(bits), tone


def git_menu(name: str, git) -> list[dict]:
    """Git ▾: fetch, pull, push to gitdev. Each item is a page event (js/project.js): it asks the core what it will do
    first (GET /p/<name>/git/plan, fetch: a fixed text), shows that in a confirm, then posts."""
    if git is None:
        return [menus.item("none", "Not a git repository", "branch", disabled=True, why="no .git here")]
    why = "" if git.has_gitdev else "no gitdev remote"
    remotes = menus.item("remotes", "Remotes…", "link", note="add gitdev" if why else "", event="pj:git")

    def it(id_: str, label: str, icon: str, note: str = "") -> dict:
        if why:
            return menus.item(id_, label, icon, disabled=True, why=why)
        return menus.item(id_, label, icon, note=note, event="pj:git")

    return menus.menu(
        it("fetch", "Fetch from gitdev", "refresh"), it("pull", "Pull (fast-forward only)", "down"),
        it("push", "Push to gitdev", "up", "never forced"),
        menus.SEP,
        menus.item("tab", "Open the Git tab", "branch", event="pj:git"), remotes)


def actions(p, base: str, hidden, stop_n: int) -> list[dict]:
    """Head actions: Launch agent (the one green; pm7.launch dialog, else the launch page), Git ▾ (a menu), Restart
    stack (a compose stack only), Stop agents (typed PANIC; disabled without live agents)."""
    out = [{"label": "Launch agent", "icon": "plus", "href": f"{base}/launch", "pri": True,
            "title": f"Launch an agent in {p.name}", "attrs": {"data-pj-launch": p.name}},
           {"label": "Git", "icon": "branch", "title": f"Git actions for {p.name}",
            "attrs": {"data-menu": json.dumps(git_menu(p.name, p.git)), "aria-haspopup": "menu",
                      "aria-expanded": "false", "data-pj-git": p.name}}]
    out.append({"label": "Settings", "icon": "gear", "title": f"Project settings of {p.name}",
                "attrs": {"data-pj-settings": f"{base}/settings/panel"}})
    if p.stack is not None and p.stack.total and "stack" not in hidden:
        out.append({"label": "Restart stack", "icon": "refresh", "title": f"Restart the compose stack of {p.name}",
                    "attrs": {"data-pj-restart": p.name}})
    stop = {"data-pj-stop": p.name, "data-pj-stop-n": str(stop_n)}
    if not stop_n:
        stop["disabled"] = ""
    out.append({"label": "Stop agents", "icon": "stop", "danger": True, "attrs": stop,
                "title": f"Stop every agent of {p.name} (type {PANIC})" if stop_n else f"No agent runs in {p.name}"})
    return out


def stats(p, n: int, running: int, need: int) -> list:
    br, summary, tone = git_parts(p.git)
    out: list = [plural(n, "agent"), f"{running} running"]
    if need:
        out.append({"text": f"{need} need{'s' if need == 1 else ''} you", "cls": "warn"})
    if p.git is not None:
        out.append({"text": f"{br} · {summary}", "cls": tone})
    out.append({"text": p.path, "title": p.path})
    return out


def tiles(ctx: Mapping, p, n: int, running: int, need: int, unread: int) -> list[Tile]:
    _, summary, tone = git_parts(p.git)
    todo, backlog, st = ctx.get("todo") or {}, ctx.get("backlog") or {}, p.stack
    t_open, t_all = int(todo.get("open_n") or 0), int(todo.get("total") or 0)
    agents_sub = (f"{need} need you · " if need else "") + f"{unread} unread"
    return [
        Tile("agents", "Agents", str(n), f"{running} running", agents_sub, "warn" if need else ""),
        Tile("git", "Git", git_parts(p.git)[0], "", summary, tone),
        Tile("todo", "TODO", str(t_open) if t_all else "–", f"open of {t_all}" if t_all else "",
             "refused: symlink" if todo.get("blocked") else ("TODO.md" if t_all else "no TODO items")),
        issues_tile(backlog),
        Tile("stack", "Stack", f"{st.running}/{st.total}" if st and st.total else "–", "up" if st and st.total else "",
             "docker compose" if st and st.total else "no compose file",
             "warn" if st and st.total and st.running < st.total else ""),
        Tile("vms", "VMs", "–", "", "open the tab to list them"),
    ]


def issues_tile(backlog: Mapping) -> Tile:
    """Issues tile (README A18): tracker issues are what the Issues tab lists ("3 open · tracker"); a project that
    keeps BACKLOG.md as a file is counted from the file and says so ("3 in file · BACKLOG.md", like the TODO
    tile's "TODO.md"), so the tile never claims open issues the tab cannot show; the tab explains the import."""
    n = int(backlog.get("open_n") or 0)
    if backlog.get("tracker"):
        return Tile("issues", "Issues", str(n) if n else "–", "open" if n else "", "tracker" if n else "none open")
    if backlog.get("exists"):
        return Tile("issues", "Issues", str(n) if n else "–", "in file" if n else "",
                    "BACKLOG.md" if n else "BACKLOG.md · none open")
    return Tile("issues", "Issues", "–", "", "none open")


def tabs(ctx: Mapping, p, n: int, cur: str, hidden) -> list[dict]:
    todo, backlog = ctx.get("todo") or {}, ctx.get("backlog") or {}
    plans = ctx.get("plans") or {}
    counts = {"agents": n, "tmux": len(ctx.get("sessions") or ()) or None,
              "git": p.git.dirty if p.git and p.git.dirty else None,
              "stack": p.stack.total if p.stack and p.stack.total else None,
              "todo": todo.get("open_n") if todo.get("total") else None,
              # the Issues tab lists tracker issues only (README A18): no count from a BACKLOG.md file
              "issues": (backlog.get("open_n") or None) if backlog.get("tracker") else None,
              "plans": plans.get("total") or None}
    return [{"key": k, "label": lbl, "icon": ic, "controls": f"pj-t-{k}", "cur": k == cur, "count": counts.get(k),
             "title": f"{lbl} of {p.name}"}
            for k, lbl, ic in TABS if HIDE.get(k) not in hidden]


# ------------------------------------------------------------------ Agents tab
def agent_groups(ctx: Mapping, settings, now: float, window: float) -> list[inbox.Group]:
    """Pinned · Needs you · Running · Idle: the core's pinned / active / idle lists (Inbox rule), active split by
    state; rows are the Inbox's (``inbox.row``) with their ⋯ refreshing the Agents tab."""
    meta = ctx.get("inbox_meta") or {}
    snap = services.published(settings) if settings is not None else None
    term_on = bool(getattr(settings, "terminal_enabled", False))
    picked: dict[str, list] = {"pinned": list(ctx.get("pinned_agents") or ()), "needs": [], "running": [],
                               "idle": list(ctx.get("idle_agents") or ())}
    for a in ctx.get("agents") or ():
        picked["needs" if _need(a, now, window) else "running" if a.state is AgentState.RUNNING else "idle"].append(a)
    out = []
    for k, xs in picked.items():
        if xs:
            label, icon, cls = inbox.BANDS[k]
            out.append(inbox.Group(k, label, [inbox.row(a, meta.get(a.id) or {}, snap, term_on, now,
                                                         refresh=AGENTS_SEL) for a in xs], icon, cls))
    return out


def ended_rows(ctx: Mapping, now: float) -> list[inbox.Ended]:
    nts = ctx.get("ended_ntfy") or {}
    return [inbox.ended_row(r, nts.get(r.id), now, refresh=AGENTS_SEL) for r in ctx.get("ended") or ()]


# ------------------------------------------------------------------ Plans, Docs
def plan_rows(ctx: Mapping, base: str, now: float) -> tuple[list[PlanRow], int]:
    plans = ctx.get("plans") or {}
    out = []
    for it in plans.get("shown") or ():  # pm.projects.plans.Plan: name, mtime, src, label
        name, src = str(getattr(it, "name", "") or ""), str(getattr(it, "src", "") or "project")
        if not name:
            continue
        q = "" if src == "project" else f"src={quote(src)}"
        href = f"{base}/plans/{quote(name)}"
        mt = getattr(it, "mtime", 0) or 0
        out.append(PlanRow(name, src, str(getattr(it, "label", "") or src), age(mt, now), int(mt) if mt else None,
                           href + "?embed=1" + (f"&{q}" if q else ""), href + (f"?{q}" if q else "")))
    return out, int(plans.get("total") or len(out))


DOC_FOUND = 'class="pj-doc'  # a rendered document (dt _project_doc.html); a notice ("No README here.") has none


def docs(ctx: Mapping, base: str, hidden) -> Docs:
    """Docs choices README · CHANGELOG · FEATURES · CLAUDE · AGENTS (the optional three only when they exist) and the
    one shown first: README, else the first that exists. The core reads CHANGELOG, then README (first_key /
    first_html); its html is a document or a notice. CHANGELOG found: README is not known, so the tab loads the
    choices in order (lazy). Neither found: both are missing; the first optional one that exists loads, else an
    empty-state box."""
    first, html = ctx.get("first_key") or "", ctx.get("first_html") or ""
    found = DOC_FOUND in html
    missing = set() if found else {"readme", "changelog"}
    items = [{"key": k, "label": lbl, "href": f"{base}/view/{k}", "miss": k in missing, "list": flag == "list"}
             for k, lbl, flag in DOCS if k not in hidden and (flag in ("", "list") or ctx.get(flag))]
    cand = [d["key"] for d in items if not d["miss"] and not d["list"]]  # Skills is a list, never the first doc
    docs_only = [d["key"] for d in items if not d["list"]]
    cur = cand[0] if cand else (docs_only[0] if docs_only else "")
    for d in items:
        d["cur"] = d["key"] == cur
    if not cand:
        return Docs(items, "", False)
    if cur == first and found:
        return Docs(items, html, False)
    return Docs(items, "", True)


# ------------------------------------------------------------------ links, tmux sessions
def link_groups(ctx: Mapping) -> list[LinkGroup]:
    """The links row under the head: the workspace's linked projects (project settings › Workspace; their Files tab and
    page) and every plugin's ``project_links`` group (``ext_groups``, external links (plugins); opened in a new
    browser tab)."""
    out = []
    linked = [str(n) for n in ctx.get("linked") or () if n]
    if linked:
        out.append(LinkGroup("Linked", [
            {"label": n, "url": f"/p/{quote(n, safe='')}?tab=files", "desc": f"Browse {n} (read-only)", "icon": "",
             "ext": False} for n in linked]))
    for title, links in ctx.get("ext_groups") or ():
        out.append(LinkGroup(str(title), [
            {"label": x.label, "url": x.url, "desc": x.desc or x.label, "icon": x.icon, "ext": True} for x in links]))
    return out


def sessions(ctx: Mapping, p, settings, now: float) -> Sessions:
    """The project's tmux sessions (the core's ``sessions``: a session with a live pane of the project) as the tmux
    page's rows (``tmux.sess_row``); pm's own session is not known here, the core's kill guard refuses it."""
    by_pane = {a.pane_id: a for a in ctx.get("all_agents") or ()}
    term_on = bool(getattr(settings, "terminal_enabled", False))
    rows = []
    for s in ctx.get("sessions") or ():
        panes = [x for w in s.windows for x in w.panes]
        ags = [by_pane[x.id] for x in panes if x.id in by_pane]
        info = {"windows": len(s.windows), "panes": len(panes), "agents": ags,
                "last": max((sorting.active_ts(a) for a in ags), default=0.0) or None}
        rows.append(tmux.sess_row(s, info, None, "", "", term_on, settings, now, refresh=AGENTS_SEL, project=p.name))
    return Sessions(rows, sum(1 for r in rows if not r.lock), tmux.header({}, "")[0])


# ------------------------------------------------------------------ settings and remotes panels
@dataclass(frozen=True)
class Panels:
    """What the dt ``_project_settings.html`` / ``_git_remotes.html`` (the core's fragments) need beyond their
    context: the component labels and the pm ssh key's ⋯ items (copy it, or generate it)."""
    labels: dict[str, str]
    key_menu: list[dict]


def panels(ctx: Mapping) -> Panels:
    pub = ctx.get("pub")
    ssh = menus.item("ssh", "Settings › SSH keys", "gear", href="/settings?s=ssh")
    if pub is not None:
        key = menus.item("copy", "Copy public key", "copy", copy=pub.line)
    else:
        key = menus.item("keygen", "Generate pm key…", "plus", post="/settings/ssh/keygen",
                         confirm="Generate pm's ssh key?", ok="Generate", done="Key generated", refresh="#git-remotes",
                         body="An ed25519 key without a passphrase (pm runs unattended). It never overwrites a key.")
    return Panels(dict(COMPONENT_LABELS), menus.menu(key, ssh))


# ------------------------------------------------------------------ the page
def _request_state(ctx: Mapping) -> tuple[str, bool]:
    """(first tab from ?tab=, sidebar collapsed from the cookie)."""
    req = ctx.get("request")
    if req is None:
        return "agents", False
    tab = req.query_params.get("tab") or "agents"
    return tab, req.cookies.get(NAV_COOKIE) == "1"


def build(ctx: Mapping, settings, now: float | None = None) -> ProjectView:
    p = ctx["project"]
    now = float(ctx.get("now") or time.time()) if now is None else now
    window = float(getattr(settings, "idle_after_s", 0) or 0) or 28800.0
    ps = ctx.get("ps")
    hidden = frozenset(getattr(ps, "hidden", None) or ctx.get("hid") or ())  # hid: the live fragment's context
    base = "/p/" + quote(p.name, safe="")
    live = [*(ctx.get("pinned_agents") or ()), *(ctx.get("agents") or ()), *(ctx.get("idle_agents") or ())]
    n, need = len(live), sum(_need(a, now, window) for a in live)
    running = sum(a.state is AgentState.RUNNING for a in live)
    meta = ctx.get("inbox_meta") or {}
    unread = sum(1 for a in live if (meta.get(a.id) or {}).get("unread") or (meta.get(a.id) or {}).get("unread_n"))
    tab, navc = _request_state(ctx)
    tab_items = tabs(ctx, p, n, tab, hidden)
    cur = tab if any(t["key"] == tab for t in tab_items) else "agents"
    for t in tab_items:
        t["cur"] = t["key"] == cur
    sd = side(ctx, p.name, now, window)
    plans, plans_total = plan_rows(ctx, base, now)
    return ProjectView(
        name=p.name, path=p.path, base=base, cur_tab=cur, navc=navc, stats=stats(p, n, running, need),
        actions=actions(p, base, hidden, len(ctx.get("stop_agents") or ())),
        tiles=tiles(ctx, p, n, running, need, unread), tabs=tab_items, side=sd, side_total=len(sd),
        groups=agent_groups(ctx, settings, now, window), ended=ended_rows(ctx, now),
        ended_total=int(ctx.get("ended_total") or 0), agents_n=n, need=need, plans=plans, plans_total=plans_total,
        docs=docs(ctx, base, hidden), hidden=hidden, links=link_groups(ctx),
        sessions=None if "live" in hidden else sessions(ctx, p, settings, now))


# ------------------------------------------------------------------ Jinja global
_logged: set[str] = set()


def bind(settings_of: Callable[[], object]):
    """The ``dt_project`` global: ``dt_project()`` returns the ``ProjectView`` of the page (or the live fragment)
    being rendered, ``dt_project("panels")`` the ``Panels`` of the settings / remotes fragments; None on an error
    (logged once per error type), so the template shows a fallback."""
    @pass_context
    def dt_project(ctx, part: str = "page"):
        try:
            if part == "panels":
                return panels(ctx)
            return build(ctx, settings_of())
        except Exception as e:  # noqa: BLE001 - a broken view model must not break the page
            name = type(e).__name__
            if name not in _logged:
                _logged.add(name)
                log.exception("dt project view model failed")
            return EMPTY
    return dt_project
