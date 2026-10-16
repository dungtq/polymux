"""The dt tmux page's view model (PRD.md §6.6, LAYOUT.md §5 tmux): the sessions table (state dot = the worst
agent state of the session, lock only on protected sessions and pm's own, Windows, Clients, Agents, Active), the
segments All · With agents · Attached · Protected with the core's counts, header sort links, the session ⋯ menu,
and the windows of one session (row details: window, pane, command, path, agent; ⋯ Open terminal / Kill window /
Kill pane).

``dt_tmux`` is a Jinja global (``template_globals()``, bound to the plugin's settings). Called with no argument it
returns the ``Page`` of the list (``tmux.html`` / ``_tmux_list.html``; context of the core's ``GET /tmux``:
sessions, sess_info, sess_rows, own_sid, open_sid, cols, self_url, q, stats, tmux_filter, tmux_counts, limit_*,
errors, terminal_enabled). ``dt_tmux("windows")`` returns the ``Windows`` of one session for the row details
(``_tmux_session_rows.html``, the core's ``GET /tmux/{sid}/live``: session, rows, terminal_enabled). It reads only
the route's context and the poll loop's published snapshot (the project folders of the New session dialog); no file,
tmux or network IO.

Kills post to the core's routes with the typed name the core checks (``pm.tmux.kill``: session name, window name,
pane id) and ``force_attached=1`` when clients are attached (the question says so). The windows fragment's context
has no ``own_sid``: pm's own session is refused by the core's kill guard (403, the error toast says why).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from jinja2 import pass_context

from pm.infra import snapshot as services
from pm.web.dt import menus
from pm.web.dt.shell import age
from pm.web.render import term_ok, urlid

log = logging.getLogger(__name__)

SEGS = (("", "All"), ("agents", "With agents"), ("attached", "Attached"), ("protected", "Protected"))
RANK = {"failed": 0, "waiting": 1, "running": 2, "idle": 3, "unknown": 4}  # worst first
STATE_WORD = {"running": "Running", "waiting": "Needs you: waiting for an answer", "failed": "Needs you: failed",
              "idle": "Idle", "unknown": "State unknown"}
REFRESH = "#tm-list"
# header column -> (core sort key ascending-looking first, its reverse); ascending = the arrow up
SORTS = {"name": ("name", "-name"), "windows": ("-counts", "counts"), "agents": ("-agents", "agents"),
         "active": ("newest", "oldest")}
CORE_COL = {"name": "main", "windows": "age", "agents": "agents", "active": "act"}


@dataclass(frozen=True)
class AgentRef:
    id: str
    kind: str
    state: str
    word: str


@dataclass(frozen=True)
class Sess:
    """One session row. ``id`` = tmux ``$n`` (row id), ``sid`` = its URL form ``sN``."""
    id: str
    sid: str
    name: str
    state: str  # worst agent state, "" = no agents
    word: str
    windows: int
    panes: int
    clients: int
    agents: list[AgentRef]
    more: str  # "+N" when the session has more agents than the one shown
    more_title: str
    since: int | None
    age: str
    lock: str  # "" | "protected" | "pm" (pm's own session)
    project: str  # project of the session's current pane ("" = none)
    launch: str  # that project's Launch page (the fallback when the Launch dialog is not loaded)
    term: str  # read-only terminal href, "" = none
    meta: str  # phone line 2
    open: bool
    src: str  # GET /tmux/{sid}/live (row details)
    wins: Windows | None  # rendered in place when the session is opened by the URL (?open=sN)
    menu: list = field(default_factory=list)


@dataclass(frozen=True)
class Win:
    """One pane row of a session's windows. ``id`` = the pane id (row id)."""
    id: str
    win_id: str
    index: int
    name: str
    first: bool  # first pane of its window (the window's name and index show only here)
    current: bool  # tmux's current window of the session
    command: str
    path: str
    project: str
    agent: AgentRef | None
    term: str
    meta: str
    menu: list = field(default_factory=list)


@dataclass(frozen=True)
class Windows:
    sid: str
    name: str
    rows: list[Win]
    note: str  # what can be killed here
    lock: str
    attach: str  # the attach command


@dataclass(frozen=True)
class Page:
    stats: list
    segs: list[dict]
    cols: list[dict]
    sort: str | None
    rows: list[Sess]
    shown: int
    total: int
    more_url: str | None
    self_url: str
    empty: str
    empty_url: str | None
    empty_link: str
    folders: list[str]  # project folders: suggestions of the New session dialog
    q: str


# ------------------------------------------------------------------ helpers
def params_of(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(url or "/tmux").query, keep_blank_values=False))


def url_with(params: Mapping[str, str], **kw) -> str:
    """``/tmux`` with ``params`` changed by ``kw`` (None / "" drops a parameter)."""
    p = {k: v for k, v in {**params, **kw}.items() if v not in (None, "")}
    return "/tmux?" + urlencode(p) if p else "/tmux"


def agent_ref(a) -> AgentRef:
    st = a.state.value
    return AgentRef(a.id, a.kind.value, st, STATE_WORD.get(st, st))


def worst(refs: list[AgentRef]) -> list[AgentRef]:
    """The agents worst state first (failed, waiting, running, idle), then by id."""
    return sorted(refs, key=lambda r: (RANK.get(r.state, 9), r.id))


def term_href(session, win_id: str | None, on: bool) -> str:
    if not on or not term_ok(session.name):
        return ""
    return f"/term/{urlid(session.id)}" + (f"?w={urlid(win_id)}" if win_id else "")


def attach_cmd(name: str, settings) -> str:
    sock = getattr(settings, "tmux_socket", None) if settings is not None else None
    return f"tmux -L {sock} attach -t {name}" if sock else f"tmux attach -t {name}"


def _attached(n: int) -> str:
    return f"{n} attached client{'s' if n != 1 else ''}"


def _kill_body(what: str, attached: int) -> str:
    lost = f" {_attached(attached).capitalize()} lose{'s' if attached == 1 else ''} it." if attached else ""
    return (f"Everything running in this {what} stops.{lost} Agents in it end and keep their Resume in the Inbox's "
            "Ended view.")  # the typed confirm adds "This cannot be undone."


def _session_project(rows) -> str:
    """Project of the session's current window (its active pane), else of its first pane with one."""
    cur = [r for r in rows if r.get("current")]
    for r in (*[r for r in cur if r["pane"].active], *cur, *rows):
        if r.get("project"):
            return r["project"]
    return ""


# ------------------------------------------------------------------ windows (row details)
def _win_menu(session, w, p, attached: int, term: str, term_why: str, own: bool) -> list[dict]:
    label = f"{session.name}:{w.index}"
    force = {"force_attached": "1"} if attached else {}
    if session.protected or own:
        why = "pm's own session" if own else "protected session"
        kills = [menus.item("killw", "Kill window…", "trash", danger=True, disabled=True, why=why),
                 menus.item("killp", "Kill pane…", "x", danger=True, disabled=True, why=why)]
    else:
        kills = [
            menus.item("killw", f"Kill window {label}…", "trash", danger=True,
                       post=f"/tmux/window/{urlid(w.id)}/kill", vals={"confirm": w.name, **force}, typed=w.name,
                       confirm=f"Kill window {w.name} ({label})?", body=_kill_body("window", attached),
                       ok="Kill window",
                       done=f"Killed window {label}", refresh=REFRESH),
            menus.item("killp", f"Kill pane {p.id}…", "x", danger=True, post=f"/tmux/pane/{urlid(p.id)}/kill",
                       vals={"confirm": p.id, **force}, typed=p.id, confirm=f"Kill pane {p.id} of window {label}?",
                       body=_kill_body("pane", attached), ok="Kill pane", done=f"Killed pane {p.id}",
                       refresh=REFRESH)]
    opn = (menus.item("term", "Open terminal", "tmux", key="O", note="read-only, as a tab", event="tm:term") if term
           else menus.item("term", "Open terminal", "tmux", disabled=True, why=term_why))
    return menus.menu(opn, menus.SEP, kills)


def _term_why(session, on: bool) -> str:
    if not on:
        return "the terminal is off (pm.toml)"
    return "" if term_ok(session.name) else "no terminal for pm's sessions"


def windows(session, rows, term_on: bool, settings=None, own: bool = False) -> Windows:
    """The windows of one session from the core's pane rows (``_session_rows``: window, pane, agent, project,
    current, first)."""
    out = []
    why = _term_why(session, term_on)
    for r in rows:
        w, p, a = r["window"], r["pane"], r.get("agent")
        ref = agent_ref(a) if a is not None else None
        term = term_href(session, w.id, term_on)
        meta = " · ".join(x for x in (p.command, p.cwd, ref.id if ref else "") if x)
        out.append(Win(id=p.id, win_id=w.id, index=w.index, name=w.name, first=bool(r.get("first", True)),
                       current=bool(r.get("current")), command=p.command, path=p.cwd, project=r.get("project") or "",
                       agent=ref, term=term, meta=meta,
                       menu=_win_menu(session, w, p, session.attached, term, why, own)))
    if own:
        note, lock = "pm's own session: never killed from pm.", "pm"
    elif session.protected:
        note, lock = "Protected session: nothing in it can be killed from pm.", "protected"
    else:
        note, lock = "Windows and panes can be killed (type the name to confirm).", ""
    return Windows(session.id, session.name, out, note, lock, attach_cmd(session.name, settings))


# ------------------------------------------------------------------ session rows
def _sess_menu(s, own: bool, term: str, term_why: str, project: str, settings, refresh: str = REFRESH) -> list[dict]:
    if own or s.protected:
        kill = menus.item("kill", "Kill session…", "trash", danger=True, disabled=True,
                          why="pm's own session" if own else "protected session")
    else:
        force = {"force_attached": "1"} if s.attached else {}
        kill = menus.item("kill", f"Kill session {s.name}…", "trash", danger=True,
                          post=f"/tmux/session/{urlid(s.id)}/kill", vals={"confirm": s.name, **force}, typed=s.name,
                          confirm=f"Kill session {s.name}?", body=_kill_body("session", s.attached),
                          ok="Kill session", done=f"Killed session {s.name}", refresh=refresh)
    return menus.menu(
        menus.item("term", "Open terminal", "tmux", key="O", note="read-only, as a tab", event="tm:term") if term
        else menus.item("term", "Open terminal", "tmux", disabled=True, why=term_why),
        menus.item("det", "Windows", "list", key="X", det=True),
        menus.item("neww", "New window", "plus", disabled=True, why="pm's own session") if own
        else menus.item("neww", "New window", "plus", post="/tmux/window/new", vals={"session": s.id},
                        done=f"New window in {s.name}", refresh=refresh),
        menus.item("launch", "Launch an agent here…", "bot", event="tm:launch", note=project) if project
        else menus.item("launch", "Launch an agent here…", "bot", disabled=True, why="folder is in no project"),
        menus.item("copy", "Copy attach command", "copy", copy=attach_cmd(s.name, settings)),
        menus.SEP, kill)


def sess_row(s, info: Mapping, rows, own_sid: str, open_sid: str, term_on: bool, settings, now: float, *,
             refresh: str = REFRESH, project: str = "") -> Sess:
    """One session row. ``refresh``: the box its ⋯ items refresh (the project view: ``#pj-agents``); ``project``:
    the session's project when the caller knows it (else the project of its current pane, from ``rows``)."""
    refs = worst([agent_ref(a) for a in info.get("agents") or ()])
    own = bool(own_sid) and s.id == own_sid
    project = project or _session_project(rows or ())
    term = term_href(s, None, term_on)
    last = info.get("last")
    n_win = int(info.get("windows") or 0)
    meta = [f"{n_win} window{'s' if n_win != 1 else ''}", f"{s.attached} attached"]
    if refs:
        meta.append(refs[0].id + (f" +{len(refs) - 1}" if len(refs) > 1 else ""))
    is_open = bool(open_sid) and s.id == open_sid
    return Sess(
        id=s.id, sid=urlid(s.id), name=s.name, state=refs[0].state if refs else "",
        word=refs[0].word if refs else "", windows=n_win, panes=int(info.get("panes") or 0), clients=s.attached,
        agents=refs[:1], more=f"+{len(refs) - 1}" if len(refs) > 1 else "",
        more_title=", ".join(r.id for r in refs[1:]), since=int(last) if last else None, age=age(last, now) or "–",
        lock="pm" if own else "protected" if s.protected else "", project=project, launch=launch_href(project),
        term=term, meta=" · ".join(meta),
        open=is_open, src=f"/tmux/{urlid(s.id)}/live",
        wins=windows(s, rows, term_on, settings, own) if is_open and rows is not None else None,
        menu=_sess_menu(s, own, term, _term_why(s, term_on), project, settings, refresh))


# ------------------------------------------------------------------ the page
def header(cols: Mapping, key: str) -> tuple[list[dict], str | None]:
    """tb.gh columns with the core's sort links (``cols``: main, age, agents, act) and the order for aria-sort."""
    order = None
    for k, (up, down) in SORTS.items():
        if key == up:
            order = k
        elif key == down:
            order = "-" + k

    def href(k: str) -> str | None:
        c = cols.get(CORE_COL[k]) if isinstance(cols, Mapping) else None
        return c.get("href") if isinstance(c, Mapping) else None

    return [{"sr": "State"}, {"label": "Session", "key": "name", "href": href("name")},
            {"label": "Windows", "key": "windows", "href": href("windows"), "num": True, "title": "Windows (panes)"},
            {"label": "Clients", "num": True, "cls": "hide-n", "title": "Attached clients"},
            {"label": "Agents", "key": "agents", "href": href("agents"), "cls": "hide-s"},
            {"label": "Active", "key": "active", "href": href("active"), "num": True,
             "title": "Last agent activity"},
            {"sr": "Actions"}], order


def _stats(text: str) -> list:
    """The core's one-line stats ("tmux · 3 sessions · 4 windows · …") without the title."""
    parts = [p for p in (text or "").split(" · ") if p]
    return parts[1:] if parts and parts[0] == "tmux" else parts


def _folders(settings) -> list[str]:
    snap = services.published(settings) if settings is not None else None
    return sorted({p.path for p in getattr(snap, "projects", ()) or ()})


def build(ctx: Mapping, settings, now: float | None = None) -> Page:
    """The list for a ``GET /tmux`` context."""
    now = time.time() if now is None else now
    self_url = ctx.get("self_url") or "/tmux"
    params = params_of(self_url)
    params.pop("open", None)
    flag = ctx.get("tmux_filter") or ""
    key = params.get("sort", "name")
    counts = ctx.get("tmux_counts") or {}
    segs = [{"key": k or "all", "label": lbl, "href": url_with(params, f=k or None, n=None), "cur": k == flag,
             "count": counts.get(k)} for k, lbl in SEGS]
    info, rows = ctx.get("sess_info") or {}, ctx.get("sess_rows") or {}
    term_on = bool(ctx.get("terminal_enabled"))
    own, opened = ctx.get("own_sid") or "", ctx.get("open_sid") or ""
    out = [sess_row(s, info.get(s.id) or {}, rows.get(s.id), own, opened, term_on, settings, now)
           for s in ctx.get("sessions") or ()]
    shown, total = int(ctx.get("limit_shown") or len(out)), int(ctx.get("limit_total") or len(out))
    more = url_with(params, n="all") if shown < total else None
    q = ctx.get("q") or ""
    empty, eurl, elink = "", None, ""
    if not out:
        if q:
            empty, eurl, elink = f"No session matches “{q}”.", url_with(params, q=None), "Clear the filter"
        elif flag:
            empty, eurl, elink = f"No {dict(SEGS)[flag].lower()} sessions.", url_with(params, f=None), "Show all"
        else:
            empty = "No tmux sessions. Start one with New session."
    cols, order = header(ctx.get("cols") or {}, key)
    return Page(stats=_stats(ctx.get("stats") or ""), segs=segs, cols=cols, sort=order, rows=out, shown=shown,
                total=total, more_url=more, self_url=self_url, empty=empty, empty_url=eurl, empty_link=elink,
                folders=_folders(settings), q=q)


def launch_href(project: str) -> str:
    return f"/p/{quote(project, safe='')}/launch" if project else ""


# ------------------------------------------------------------------ Jinja global
_logged: set[str] = set()


def bind(settings_of: Callable[[], object]):
    """The ``dt_tmux`` global: ``dt_tmux()`` = the list's ``Page``, ``dt_tmux("windows")`` = the ``Windows`` of the
    fragment's session. Never raises (None on an error; the template says so)."""
    @pass_context
    def dt_tmux(ctx, part: str = "page"):
        try:
            settings = settings_of()
            if part == "windows":
                s = ctx.get("session")
                return windows(s, ctx.get("rows") or (), bool(ctx.get("terminal_enabled")), settings) \
                    if s is not None else None
            return build(ctx, settings)
        except Exception as e:  # noqa: BLE001 - a broken view model must not break the page
            name = type(e).__name__
            if name not in _logged:
                _logged.add(name)
                log.exception("dt tmux view model failed")
            return None
    return dt_tmux
