"""The dt shell's view model (SHELL.md §2): nav rows with counts, sessions grouped by project, the tab strip.

``dt_shell`` is a Jinja global (``template_globals()``) and runs during template render, so it only reads data that
is already in memory: the poll loop's published snapshot (with the hook tracker's states merged in), the cached
tabs file and the core's nav list (``render.NAV`` plus plugin ``nav_items``). It never raises: on an error it logs
once per error type and returns an empty ``Shell``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import NamedTuple
from urllib.parse import urlsplit

from jinja2 import pass_context

from pm.agents import sorting
from pm.agents.inbox import stale_failed
from pm.agents.lineage import AGENT_ID_RE
from pm.core import timefmt
from pm.core.models import Agent, AgentState
from pm.infra import snapshot as services
from pm.web import render
from pm.web.deps import project_sidebar
from pm.web.dt import stores
from pm.web.render import urlid

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NavRow:
    key: str
    href: str
    label: str
    icon: str
    count: int | None = None
    hot: bool = False  # the count is "needs you"
    chord: str = ""
    cur: bool = False


@dataclass(frozen=True)
class SessRow:
    id: str
    kind: str  # agent kind (claude, codex, ...)
    state: str  # running | waiting | failed | idle | unknown
    href: str
    label: str  # the agent id (DESIGN.md §6.1: a session row shows the id)
    since: int | None = None  # epoch of state_since
    open: bool = False  # in a tab
    cur: bool = False  # this page is that agent
    title: str = ""  # window / agent name when it differs from the id (row tooltip and search only)
    age: str = ""  # time in this state at render time ("5s", "3m", "2h", "4d"); ticker.js keeps it current


@dataclass(frozen=True)
class SessGroup:
    project: str
    href: str
    dot: str
    rows: list[SessRow] = field(default_factory=list)


@dataclass(frozen=True)
class Tab:
    kind: str  # home | agent | term
    id: str
    title: str
    href: str
    state: str = ""  # agent state, "ended" when the agent / session is gone; home: the page's agent, else ""
    agent_kind: str = ""
    cur: bool = False
    icon: str = ""  # home tab only: the sprite icon of the page it shows


@dataclass(frozen=True)
class Shell:
    nav: list[NavRow] = field(default_factory=list)
    foot: list[NavRow] = field(default_factory=list)
    groups: list[SessGroup] = field(default_factory=list)
    tabs: list[Tab] = field(default_factory=list)
    home: Tab = field(default_factory=lambda: Tab("home", "", "pm", "/", icon="inbox"))
    rev: int = 0
    need: int = 0  # needs you: waiting + failed, a stale failure not (``needs_you``, = the Inbox's Needs you view)
    running: int = 0
    total: int = 0
    chat: bool = False  # a session's own page (/a/<id>[/...], /term/s<n>): no chat aside (README A28)


EMPTY = Shell()

class DtNav(NamedTuple):
    """How dt shows one core nav item (``render.NavItem``): its dt key, sprite icon, G chord, label override."""
    key: str
    icon: str
    chord: str = ""
    label: str = ""  # "" = the core item's label
    foot: bool = False  # sidebar foot instead of the page nav


# core NAV key -> dt slot, in the v7 order (PRD §5): Inbox, Projects, tmux, Notifications, Backlog, Agents, Servers;
# foot Audit, Settings. Hrefs and the item list come from the core (SHELL.md §2).
DT_NAV = {"inbox": DtNav("inbox", "inbox", "G I"), "projects": DtNav("projects", "folder", "G P"),
          "tmux": DtNav("tmux", "tmux", "G T"), "ntfy": DtNav("notifications", "bell", "G N", "Notifications"),
          "backlog": DtNav("backlog", "list", "G B"), "agents": DtNav("agents", "bot", "G A"),
          "servers": DtNav("servers", "server", "G S"),
          "audit": DtNav("audit", "shield", foot=True), "settings": DtNav("settings", "sliders", "mod+,", foot=True)}
PLUGIN_ICON = "box"  # a core / plugin nav item dt has no slot for: shown after the page nav with this icon
AGENT_ICON = "chat"  # home tab icon on an agent page that is not open as a tab
_NAV_ALIASES = {"ntfy": "notifications", "term": "tmux"}  # core page ``nav`` values -> dt nav keys
_PREFIXES = (("/a/", "inbox"), ("/p/", "projects"), ("/term/", "tmux"), ("/tmux/", "tmux"), ("/ntfy", "notifications"),
             ("/notifications", "notifications"))
NO_PROJECT = "other"
Item = tuple[str, str, str, str, str]  # key, href, label, icon, chord


def nav_items(core: Sequence | None = None) -> tuple[list[Item], list[Item]]:
    """(page nav, foot) as (key, href, label, icon, chord): the core nav list (``render.NAV`` + plugin
    ``nav_items``, from ``env.globals["NAV"]``) in the dt order; items without a dt slot follow the page nav in
    the core's order (icon ``PLUGIN_ICON``, no chord). A dt slot whose core item is missing is not shown."""
    core = render.env.globals.get("NAV", render.NAV) if core is None else core
    by = {i.key: i for i in core}
    main: list[Item] = []
    foot: list[Item] = []
    for ck, s in DT_NAV.items():
        if ck in by:
            (foot if s.foot else main).append((s.key, by[ck].href, s.label or by[ck].label, s.icon, s.chord))
    taken = {r[0] for r in main + foot}
    for i in core:
        if i.key not in DT_NAV and i.key not in taken:
            taken.add(i.key)
            main.append((i.key, i.href, i.label, PLUGIN_ICON, ""))
    return main, foot


def age(since: float | None, now: float) -> str:
    """Short age of an epoch: the first unit of ``timefmt.duration`` (5s, 3m, 2h, 4d; = ticker.js); "" unknown."""
    return timefmt.duration(now - since).split()[0] if since else ""


def page_path(request) -> str:
    """Path of the page being shown. A /dt/ fragment request is about the page that loaded it (HX-Current-URL, else
    Referer, same host only); only the path is used."""
    path = request.url.path
    if not path.startswith("/dt/"):
        return path
    u = urlsplit(request.headers.get("hx-current-url") or request.headers.get("referer") or "")
    if u.netloc and u.netloc != request.headers.get("host"):
        return "/"  # another site's page: not ours to show
    return u.path if u.path.startswith("/") and not u.path.startswith("//") else "/"


def nav_key(nav: str | None, path: str, hrefs: Mapping[str, str]) -> str:
    """dt nav key of a page (``hrefs``: dt key -> href of the shown rows): its ``nav`` context value when it names
    one, else from the path; "" = none."""
    key = _NAV_ALIASES.get(nav or "", nav or "")
    if key in hrefs:
        return key
    if path in ("/", ""):
        return "inbox"
    for pre, k in _PREFIXES:
        if path.startswith(pre):
            return k
    seg = "/" + path.strip("/").split("/", 1)[0]
    return next((k for k, href in hrefs.items() if href == seg and href != "/"), "")


def page_ref(path: str, agent_id: str = "") -> stores.Ref | None:
    """The tab a page shows: ("agent", id) for /a/<id>[/...] (``agent_id``: the page's agent, wins there),
    ("term", "$n") for /term/s<n>; None otherwise."""
    if agent_id and path.startswith("/a/") and AGENT_ID_RE.fullmatch(agent_id):
        return "agent", agent_id
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "a" and AGENT_ID_RE.fullmatch(parts[1]):
        return "agent", parts[1]
    if len(parts) == 2 and parts[0] == "term" and parts[1][:1] == "s" and parts[1][1:].isdigit():
        return "term", "$" + parts[1][1:]
    return None


def _label(a: Agent) -> str:
    return a.window_name or a.label or a.id


def _kind_of(agent_id: str) -> str:
    return agent_id.rsplit("-", 1)[0]


def tab_href(ref: stores.Ref) -> str:
    """Page of a tab: /a/<agent id> or /term/s<n>."""
    return "/term/" + urlid(ref[1]) if ref[0] == "term" else "/a/" + ref[1]


def _tab(ref: stores.Ref, snap, cur: bool) -> Tab:
    kind, id_ = ref
    if kind == "term":
        s = snap.session(id_) if snap is not None else None
        title = s.name if s is not None else id_
        state = "" if snap is None or s is not None else "ended"
        return Tab("term", id_, title, tab_href(ref), state, "", cur)
    a = snap.agent(id_) if snap is not None else None
    if a is None:
        return Tab("agent", id_, id_, tab_href(ref), "unknown" if snap is None else "ended", _kind_of(id_), cur)
    return Tab("agent", id_, a.id, tab_href(ref), a.state.value, a.kind.value, cur)


def _groups(snap, open_refs: set, page_agent: str, now: float) -> list[SessGroup]:
    """Live agents grouped by project (project list order), rows by name; agents without a project last."""
    by: dict[str, list[Agent]] = {}
    for a in sorting.sort_agents(snap.agents, "name"):
        by.setdefault(a.project or NO_PROJECT, []).append(a)
    side = {r.key: r for r in project_sidebar(snap, "").rows}
    order = [p.name for p in snap.projects if p.name in by] + ([NO_PROJECT] if NO_PROJECT in by else [])
    out = []
    for name in order:
        row = side.get(name)
        rows = [SessRow(a.id, a.kind.value, a.state.value, "/a/" + a.id, a.id,
                        int(a.state_since) if a.state_since else None, ("agent", a.id) in open_refs,
                        a.id == page_agent, "" if _label(a) == a.id else _label(a), age(a.state_since, now))
                for a in by[name]]
        out.append(SessGroup(name, row.href if row else "", row.dot if row else "", rows))
    return out


DEFAULT_WINDOW_S = 28800.0  # idle_after_s unset: 8 h


def idle_window(settings) -> float:
    """The finished -> idle window in seconds (``settings.idle_after_s``, else ``DEFAULT_WINDOW_S``)."""
    return float(getattr(settings, "idle_after_s", 0) or 0) or DEFAULT_WINDOW_S


def needs_you(a: Agent, now: float, window: float) -> bool:
    """Waiting or failed, except a failure older than ``window`` (``inbox.stale_failed``: it is idle by then)."""
    if a.state is AgentState.FAILED:
        return not stale_failed(a, now, window)
    return a.state is AgentState.WAITING


def _nav_rows(rows, key: str, home_cur: bool, counts: Mapping[str, int], need: int) -> list[NavRow]:
    return [NavRow(k, href, label, icon, counts.get(k) or None, k == "inbox" and need > 0, chord,
                   home_cur and k == key) for k, href, label, icon, chord in rows]


def build(snap, tabs: stores.TabState, path: str, nav: str | None = None, agent_id: str = "",
          now: float | None = None, window: float = DEFAULT_WINDOW_S) -> Shell:
    """The shell for the page at ``path`` (``nav``/``agent_id``: the page's context values, if any; ``now``: the
    clock for the session ages and the needs-you count, default the current time; ``window``: ``idle_window``). Notifications has no count: the unread system count
    (``sidebadges.system_unread``) reads files and the ntfy feed, too slow for a render (SHELL.md §2)."""
    agents = snap.agents if snap is not None else ()
    page = page_ref(path, agent_id)
    cur = page if page in tabs.tabs else None
    page_agent = page[1] if page and page[0] == "agent" else ""
    main, foot = nav_items()
    rows = {k: (href, label, icon) for k, href, label, icon, _ in main + foot}
    # an agent page that is not a tab: the home tab shows that agent and its session row (not a nav row) is current
    key = "" if cur is None and page_agent else nav_key(nav, path, {k: v[0] for k, v in rows.items()})
    now = time.time() if now is None else now
    need = sum(needs_you(a, now, window) for a in agents)
    running = sum(a.state is AgentState.RUNNING for a in agents)
    groups = _groups(snap, set(tabs.tabs), page_agent, now) if snap else []
    counts = {"inbox": need, "projects": sum(g.project != NO_PROJECT for g in groups),
              "tmux": len(snap.sessions) if snap is not None else 0}
    href, label, icon = rows.get(key, ("/", "pm", "inbox"))
    state = ""
    if cur is None and page_agent:
        label, icon, state = page_agent, AGENT_ICON, _tab(page, snap, False).state
    home = Tab("home", "", label, path if cur is None and not path.startswith("/dt/") else href, state, "",
               cur is None, icon)
    return Shell(_nav_rows(main, key, cur is None, counts, need), _nav_rows(foot, key, cur is None, {}, 0), groups,
                 [_tab(r, snap, r == cur) for r in tabs.tabs], home, tabs.rev, need, running, len(agents),
                 page is not None)


def snapshot_of(request):
    """The published snapshot with the tracker's states merged in; None before the first build. No await."""
    st = request.app.state
    snap = services.published(st.settings)
    return services.merge_tracker(snap, getattr(st, "tracker", None)) if snap is not None else None


def for_request(request, nav: str | None = None, agent_id: str = "") -> Shell:
    """``build`` for ``request``: its snapshot, the cached tabs file and the page path."""
    settings = request.app.state.settings
    return build(snapshot_of(request), stores.read_cached(settings), page_path(request), nav, agent_id,
                 window=idle_window(settings))


_logged: set[str] = set()  # error types already logged (bounded by the exception classes)


@pass_context
def dt_shell(ctx) -> Shell:
    """Jinja global: the ``Shell`` of the page being rendered (context ``request``, ``nav``, ``agent``, ``embed``)."""
    try:
        request = ctx.get("request")
        if request is None or ctx.get("embed"):
            return EMPTY
        agent = ctx.get("agent")
        return for_request(request, ctx.get("nav"), getattr(agent, "id", "") or "")
    except Exception as e:  # noqa: BLE001 - a broken shell must not break the page
        name = type(e).__name__
        if name not in _logged:
            _logged.add(name)
            log.exception("dt shell failed; rendering an empty shell")
        return EMPTY
