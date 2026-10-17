"""The dt Inbox's view model (PRD.md §6.2, LAYOUT.md §5): the core's state sections re-bucketed into Pinned ·
Needs you (waiting + failed) · Running · Idle (finished + idle), views with counts (All / Needs you / Running / Idle /
Ended), the Unread switch, Group: state | project, header sort links, row menus.

``dt_inbox`` is a Jinja global (``template_globals()``, bound to the plugin's settings) called by the dt
``inbox.html`` / ``_inbox.html``. It reads only the route's view model (``pm.web.routes.pages.inbox_page``: sections,
agents, inbox_meta, ended, ...) and the poll loop's published snapshot (tmux window of a pane, session protection);
no file, tmux or network IO. The fragment render has no ``request`` in its context, so the page's query parameters
are read from ``sort_url`` (the current URL with the chosen sort; every other parameter kept).

dt query parameters: ``view`` (needs | running | idle | ended; none = All), ``un=1`` (unread only), ``grp=project``,
``eall=1`` (every fetched ended row), plus the core's ``q``, ``sort`` (also ``ram`` / ``-ram``) and ``n``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit

from jinja2 import pass_context

from pm.agents import sorting
from pm.agents.head import tmux_win
from pm.core import timefmt
from pm.core.models import Agent, AgentState
from pm.infra import snapshot as services
from pm.web.dt import menus
from pm.web.dt.shell import age, idle_window, needs_you
from pm.web.render import term_ok, urlid

log = logging.getLogger(__name__)

VIEWS = (("all", "All"), ("needs", "Needs you"), ("running", "Running"), ("idle", "Idle"), ("ended", "Ended"))
BANDS = {"pinned": ("Pinned", "pin", ""), "needs": ("Needs you", "warn", "warn"), "running": ("Running", "", ""),
         "idle": ("Idle", "", "")}
ENDED_FIRST = 8  # the Ended view shows this many rows until "Show all"
CTX_WARN = 80  # context fill (%) that gets the row's ctx chip (PRD §6.2)
CTX_BAR_WARN = 60  # the details' context bar turns yellow (DESIGN §4)
STATE_WORD = {"running": "Running", "waiting": "Needs you: waiting for an answer", "failed": "Needs you: failed",
              "idle": "Idle", "unknown": "State unknown"}
NO_PROJECT = "other"


@dataclass(frozen=True)
class Row:
    """One live agent row and its details. ``menu`` = the ⋯ items (docs/themes/dt/PARTS.md §9)."""
    id: str
    kind: str
    state: str  # running | waiting | failed | idle | unknown (the dot class)
    word: str  # the state for the dot's title and the screen-reader word
    project: str
    key: str  # inbox read key (<id>:<event>)
    unread: bool
    unread_n: int
    pinned: bool
    ctx_pct: int | None
    q: str  # pending question (waiting)
    err: str  # error line (failed)
    last: str  # last message text
    src: str  # "you" | "ntfy" | ""
    since: int | None  # epoch the age counts from (ticker.js)
    age: str
    tmux: str  # "<session>:<window index>"
    term: str  # /term/s<n>?w=w<m>, "" = no terminal
    sid: str
    cpu: str
    ram: str
    model: str  # short model name, "" = unknown (core inbox_meta)
    options: list  # numbered options of the pending question: [(n, label)] (core inbox_meta, waiting only)
    started: str
    folder: str
    q_text: str  # name-type text (filter, row title)
    menu: list = field(default_factory=list)


@dataclass(frozen=True)
class Group:
    key: str
    label: str
    rows: list[Row]
    icon: str = ""
    cls: str = ""  # band tone: warn | bad | ""


@dataclass(frozen=True)
class Ended:
    id: str
    kind: str
    project: str
    last: str
    ran: str
    ended: str  # age since it ended
    ended_at: str  # absolute time (title)
    resume: bool
    menu: list = field(default_factory=list)


@dataclass(frozen=True)
class Inbox:
    view: str
    views: list[dict]
    groups: list[Group]
    ended: list[Ended]
    cols: list[dict]  # header (tb.gh)
    sort: str | None  # current order for aria-sort ("name", "-age", ...)
    stats: list
    unread_only: bool
    unread: int  # agents with something unread (shown rows)
    grp: str  # state | project
    ctl_url: str  # base URL of the Unread / Group form (without un, grp)
    poll_url: str  # this list's URL (nodiff refresh, address bar)
    shown: int
    total: int
    more_url: str | None
    empty: str  # "" or the empty line's text
    empty_url: str | None
    empty_link: str
    need: int
    empty_nav: bool = False  # the empty line's link stays on the Inbox (loads into #inbox: data-i-nav)


EMPTY = None


# ------------------------------------------------------------------ helpers
def params_of(sort_url: str) -> dict[str, str]:
    """The page's query parameters from ``sort_url`` (``/?a=1&sort=x``)."""
    return dict(parse_qsl(urlsplit(sort_url or "/").query, keep_blank_values=False))


def url_with(params: Mapping[str, str], **kw) -> str:
    """``/`` with ``params`` changed by ``kw`` (None / "" drops a parameter)."""
    p = {k: v for k, v in {**params, **kw}.items() if v not in (None, "")}
    return "/?" + urlencode(p) if p else "/"


def klass(a: Agent, now: float, window: float) -> str:
    """needs | running | idle: the view an agent belongs to (a stale failure is idle, as in ``inbox.bucket``)."""
    if needs_you(a, now, window):
        return "needs"
    return "running" if a.state is AgentState.RUNNING else "idle"


def _since(a: Agent) -> float:
    return a.done_ts() if a.state is AgentState.IDLE else (a.state_since or a.last_active)


def _window(snap, a: Agent):
    """(session, window) of the agent's pane in the published snapshot, (None, None) when unknown."""
    s = snap.session(a.session_id) if snap is not None else None
    if s is None:
        return None, None
    return s, next((w for w in s.windows if any(p.id == a.pane_id for p in w.panes)), None)


def _num(v: float, unit: str) -> str:
    return f"{v:.0f}{unit}" if v and v >= 0.5 else "–"


def _options(m: Mapping) -> list[tuple[int, str]]:
    """The pending question's numbered options from the core's meta (``[{"n", "label"}]``, masked, short)."""
    out = []
    for o in m.get("options") or ():
        n, label = o.get("n") if isinstance(o, Mapping) else None, o.get("label") if isinstance(o, Mapping) else None
        if isinstance(n, int) and 0 < n < 10 and isinstance(label, str):
            out.append((n, label))
    return out[:4]


# ------------------------------------------------------------------ rows
def _menu(a: Agent, m: Mapping, term: str, win, sess, label: str, opts: list[tuple[int, str]],
          refresh: str = "#inbox") -> list[dict]:
    """The ⋯ items of a live row (PRD §6.2: Pin, Mark unread, Copy id, Kill window typed; plus open / answer / stop);
    ``refresh`` = the list box that reloads after a post (the Inbox, the project view's Agents tab)."""
    pinned, unread = bool(m.get("pinned")), bool(m.get("unread"))
    answers = []
    if a.state is AgentState.WAITING:
        pairs = opts or [(n, "") for n in (1, 2, 3)]  # no options read: the first three keys, unlabelled
        answers = [menus.item(f"ans{n}", f"{n} · {t}" if t else f"Answer {n}", note="sends the key to the pane",
                              post=f"/a/{a.id}/key", vals={"key": str(n)}, done=f"Sent {n} to {a.id}",
                              refresh=refresh) for n, t in pairs]
    if a.state in (AgentState.RUNNING, AgentState.WAITING):
        stop = menus.item("stop", "Interrupt", "stop", note="Esc in the pane", post=f"/a/{a.id}/interrupt",
                          done=f"Interrupt sent to {a.id}", refresh=refresh)
    else:
        stop = menus.item("stop", "Exit…", "power", post=f"/a/{a.id}/exit", confirm=f"Exit {a.id}?", ok="Exit",
                          done=f"Exit sent to {a.id}", refresh=refresh,
                          body="pm sends the agent's quit command. The session moves to Ended and can be resumed.")
    if win is None:
        kill = menus.item("kill", "Kill window…", "trash", danger=True, disabled=True, why="tmux window unknown")
    elif sess is not None and sess.protected:
        kill = menus.item("kill", "Kill window…", "trash", danger=True, disabled=True, why="protected tmux session")
    else:  # V7-SEC-6: the question names the window and its agent; the user types the agent id it names
        kill = menus.item("kill", f"Kill window {label}…", "trash", danger=True,
                          post=f"/tmux/window/{urlid(win.id)}/kill", typed=a.id,
                          confirm=f"Kill window {label} of {a.id}?", ok="Kill", done=f"Killed window {label}",
                          refresh=refresh)
    return menus.menu(
        menus.item("open", "Open chat", "side-r", key="Enter", event="i:open"),
        menus.item("tab", "Open in a tab", "tabs", key="O", event="i:tab"),
        menus.item("term", "Terminal (read-only)", "tmux", event="i:term") if term else None,
        menus.SEP,
        menus.item("pin", "Unpin" if pinned else "Pin to the top", "pin", key="P", post=f"/a/{a.id}/pin",
                   vals={"pinned": "0" if pinned else "1"}, done=("Unpinned " if pinned else "Pinned ") + a.id,
                   refresh=refresh),
        menus.item("read", "Mark read" if unread else "Mark unread", "check", key="E",
                   post="/inbox/read" if unread else "/inbox/mark-unread", vals={"key": m.get("key", "")},
                   done="Marked read" if unread else "Marked unread", refresh=refresh),
        menus.details(),
        menus.copy_id(a.id),
        menus.SEP, answers, menus.SEP, stop, kill)


def row(a: Agent, m: Mapping, snap, term_on: bool, now: float, refresh: str = "#inbox") -> Row:
    """One live row from an Agent and its ``inbox_meta`` entry (``refresh``: the list box its ⋯ posts reload)."""
    sess, win = _window(snap, a)
    label = f"{sess.name}:{win.index}" if sess is not None and win is not None else tmux_win(a)
    term = ""
    if term_on and sess is not None and term_ok(sess.name):
        term = f"/term/{urlid(sess.id)}" + (f"?w={urlid(win.id)}" if win is not None else "")
    since = _since(a)
    st = a.state.value
    src = {"user": "you", "ntfy": "ntfy"}.get(m.get("last_src") or "", "")
    pct = m.get("ctx_pct")
    project = a.project or NO_PROJECT
    opts = _options(m) if st == "waiting" else []
    return Row(
        id=a.id, kind=a.kind.value, state=st, word=STATE_WORD.get(st, st), project=project, key=m.get("key", ""),
        unread=bool(m.get("unread")), unread_n=int(m.get("unread_n") or 0), pinned=bool(m.get("pinned")),
        ctx_pct=int(pct) if isinstance(pct, (int, float)) else None,
        q=m.get("q", "") if st == "waiting" else "", err=m.get("err", "") if st == "failed" else "",
        last=m.get("last_text", "") or "", src=src, since=int(since) if since else None, age=age(since, now),
        tmux=label, term=term, sid=a.session_id, cpu=_num(a.cpu_pct, "%"), ram=_num(a.rss_mb, "M"),
        model=str(m.get("model") or ""), options=opts, started=timefmt.fmt_dt(now - a.age_s) if a.age_s else "",
        folder=a.cwd,
        q_text=" ".join(x for x in (a.id, project, label, a.window_name) if x),
        menu=_menu(a, m, term, win, sess, label, opts, refresh))


def ended_row(r, nt, now: float, refresh: str = "#inbox") -> Ended:
    """One Ended row from a ``registry.Record`` and its newest ntfy item (or None); ``refresh`` as in ``row``."""
    end = r.ended_at or r.last_seen
    resume = bool(r.resume_id)
    menu = menus.menu(
        menus.item("open", "Open chat", "side-r", key="Enter", event="i:open"),
        menus.item("tab", "Open in a tab", "tabs", key="O", event="i:tab"),
        menus.item("page", "Open transcript", "chat", href=f"/a/{r.id}"),
        menus.item("resume", "Resume", "play", post=f"/a/{r.id}/resume", confirm=f"Resume {r.id} in a new tmux window?",
                   ok="Resume", done=f"Resuming {r.id}", refresh=refresh) if resume else None,
        menus.copy_id(r.id))
    last = (getattr(nt, "message", "") or getattr(nt, "title", "")) if nt is not None else ""
    return Ended(id=r.id, kind=r.kind, project=r.project or r.cwd or NO_PROJECT, last=" ".join(last.split()),
                 ran=timefmt.duration(end - r.first_seen) if end and r.first_seen else "–",
                 ended=age(end, now), ended_at=timefmt.fmt_full(end) if end else "", resume=resume, menu=menu)


# ------------------------------------------------------------------ bands, views, groups
def bands(sections, sort_key: str) -> dict[str, list[Agent]]:
    """{band: agents shown}: Pinned · Needs you (core "waiting" = waiting + failed) · Running · Idle (core "finished"
    + "idle", merged in the page's order). The core already applied the Show limit per section."""
    by = {s["key"]: s.get("agents", ()) for s in sections or ()}
    idle = [*by.get("finished", ()), *by.get("idle", ())]
    return {"pinned": list(by.get("pinned", ())), "needs": list(by.get("waiting", ())),
            "running": list(by.get("running", ())), "idle": sorting.sort_agents(idle, sort_key)}


def groups_of(picked: dict[str, list[Agent]], rows: Mapping[str, Row], grp: str) -> list[Group]:
    """The bands as groups (Group: state), or the same rows grouped by project (first appearance first)."""
    if grp != "project":
        return [Group(k, BANDS[k][0], [rows[a.id] for a in xs], BANDS[k][1], BANDS[k][2])
                for k, xs in picked.items() if xs]
    out: dict[str, list[Row]] = {}
    for xs in picked.values():
        for a in xs:
            out.setdefault(rows[a.id].project, []).append(rows[a.id])
    return [Group("p:" + p, p, rs, "folder") for p, rs in out.items()]


def header(params: Mapping[str, str], sort_key: str) -> tuple[list[dict], str | None]:
    """tb.gh columns with their sort links and the current order for aria-sort. Every sortable column uses the
    core's sort keys (``sorting.COLUMNS["inbox"]``: first key, its reverse on a second click; the core sorts each
    section before its Show limit)."""
    def link(first: str, second: str) -> str:
        cur = sort_key in (first, second)
        return url_with(params, sort=(second if sort_key == first else first) if cur else first)

    pairs = {"name": ("name", "-name"), "project": ("project", "-project"), "age": ("newest", "oldest"),
             "cpu": ("cpu", "-cpu"), "ram": ("ram", "-ram")}
    order = None
    for k, (a, b) in pairs.items():
        if sort_key in (a, b):
            # newest first = smallest age first (ascending); cpu / ram first = highest first (descending)
            up = sort_key == a if k in ("name", "project", "age") else sort_key == b
            order = k if up else "-" + k
    out = [{"sr": "State"}, {"label": "Agent", "key": "name", "href": link(*pairs["name"])},
           {"label": "Project", "key": "project", "href": link(*pairs["project"]), "cls": "hide-n"},
           {"label": "tmux", "cls": "hide-n i-w"}, {"label": "Last message"},
           {"label": "Age", "key": "age", "href": link(*pairs["age"]), "num": True},
           {"label": "CPU", "key": "cpu", "href": link(*pairs["cpu"]), "num": True, "cls": "hide-n i-w"},
           {"label": "RAM", "key": "ram", "href": link(*pairs["ram"]), "num": True, "cls": "hide-n i-w"},
           {"sr": "Actions"}]
    return out, order


def counts(agents, now: float, window: float) -> dict[str, int]:
    c = {"all": 0, "needs": 0, "running": 0, "idle": 0}
    for a in agents:
        c["all"] += 1
        c[klass(a, now, window)] += 1
    return c


# ------------------------------------------------------------------ the page
def build(ctx: Mapping, settings, now: float | None = None) -> Inbox:
    """The Inbox for a route context (``inbox_page``'s view model)."""
    now = time.time() if now is None else now
    params = params_of(ctx.get("sort_url") or "/")
    if params.get("un") != "1":  # the Unread / Group form sends un=0 and grp=state: keep the URLs short
        params.pop("un", None)
    if params.get("grp") != "project":
        params.pop("grp", None)
    params.pop("rs", None)  # the old dt-side RAM sort (now the core's sort=ram): dropped from older URLs
    view = params.get("view", "all")
    view = view if view in dict(VIEWS) else "all"
    un, grp = params.get("un") == "1", "project" if params.get("grp") == "project" else "state"
    sort_key = params.get("sort", sorting.DEFAULT)
    window = idle_window(settings)
    agents = list(ctx.get("agents") or ())
    c = counts(agents, now, window)
    meta = ctx.get("inbox_meta") or {}
    snap = services.published(settings) if settings is not None else None
    term_on = bool(getattr(settings, "terminal_enabled", False))
    picked: dict[str, list[Agent]] = {}
    for k, xs in bands(ctx.get("sections"), sort_key).items():
        picked[k] = [a for a in xs if view == "all" or klass(a, now, window) == view]
    rows = {a.id: row(a, meta.get(a.id) or {}, snap, term_on, now) for xs in picked.values() for a in xs}
    unread_n = sum(1 for r in rows.values() if r.unread or r.unread_n)
    if un:
        picked = {k: [a for a in xs if rows[a.id].unread or rows[a.id].unread_n] for k, xs in picked.items()}
    shown = sum(len(xs) for xs in picked.values())
    total = c.get(view, 0)
    ended = []
    if view == "ended":
        nts = ctx.get("ended_ntfy") or {}
        recs = list(ctx.get("ended") or ())
        recs = recs if params.get("eall") == "1" else recs[:ENDED_FIRST]
        ended = [ended_row(r, nts.get(r.id), now) for r in recs]
        shown, total = len(ended), int(ctx.get("ended_total") or 0)
    cols, order = header(params, sort_key)
    views = [{"key": k, "label": lbl, "href": url_with(params, view=None if k == "all" else k, eall=None),
              "cur": k == view, "count": int(ctx.get("ended_total") or 0) if k == "ended" else c[k],
              "warn": k == "needs" and c["needs"] > 0} for k, lbl in VIEWS]
    more = None
    if shown < total and not un:
        more = url_with(params, eall="1") if view == "ended" else url_with(params, n="all")
        if view == "ended" and params.get("eall") == "1":
            more = None
    empty, eurl, elink = _empty(params, view, un, shown, ctx.get("q") or "", ctx.get("launch_href") or "/p")
    stats = [f"{c['all']} agent{'' if c['all'] == 1 else 's'}", f"{c['running']} running"]
    if c["needs"]:
        stats.append({"text": f"{c['needs']} need{'s' if c['needs'] == 1 else ''} you", "cls": "warn"})
    if unread_n:
        stats.append(f"{unread_n} unread")
    return Inbox(view=view, views=views, groups=groups_of(picked, rows, grp) if view != "ended" else [],
                 ended=ended, cols=cols, sort=order, stats=stats, unread_only=un, unread=unread_n, grp=grp,
                 ctl_url=url_with(params, un=None, grp=None), poll_url=url_with(params), shown=shown, total=total,
                 more_url=more, empty=empty, empty_url=eurl, empty_link=elink, need=c["needs"],
                 empty_nav=bool(eurl) and (eurl == "/" or eurl.startswith("/?")))


def _empty(params, view: str, un: bool, shown: int, q: str, launch: str) -> tuple[str, str | None, str]:
    """(text, link href, link text) of the empty line (DESIGN §6.21); ("", None, "") when there are rows."""
    if shown:
        return "", None, ""
    if q:
        return f"No {'ended session' if view == 'ended' else 'agent'} matches “{q}”.", url_with(params, q=None), \
            "Clear the filter"
    if un:
        return "No unread agents.", url_with(params, un=None), "Show all"
    if view == "ended":
        return "No ended sessions in the last 30 days.", None, ""
    if view != "all":
        return f"No {dict(VIEWS)[view].lower()} agents.", url_with(params, view=None), "Show all"
    return "No agents running.", launch, "Launch an agent"


# ------------------------------------------------------------------ Jinja global
_logged: set[str] = set()


def bind(settings_of: Callable[[], object]):
    """The ``dt_inbox`` global: ``dt_inbox()`` in a template returns the ``Inbox`` of the page being rendered;
    ``settings_of()`` gives the plugin's settings (``DtTheme.configure``). Never raises (None on an error)."""
    @pass_context
    def dt_inbox(ctx):
        try:
            return build(ctx, settings_of())
        except Exception as e:  # noqa: BLE001 - a broken view model must not break the page
            name = type(e).__name__
            if name not in _logged:
                _logged.add(name)
                log.exception("dt inbox view model failed")
            return EMPTY
    return dt_inbox
