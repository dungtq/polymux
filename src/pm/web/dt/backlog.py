"""The dt Backlog view model (docs/themes/dt/PRD.md §6.5, LAYOUT.md §5; README O7: the project view's Issues / TODO tabs
are this same component scoped to one project). Reads the core's GET /backlog context only (no IO):

- ``issue_list`` (``views.issue_rows``: one page of tracker issues, each with ``agent`` = {id, state} | None),
- ``todo_list`` (``views.todo_groups``: TODO items of every main project, grouped by project then section),
- ``entries`` (every main project's BACKLOG summary: the head's open count; a file-mode project's ``blocks`` are
  its BACKLOG.md items, listed on the Issues tab as rows without a number: tick and edit post the core's
  ``/backlog/toggle`` / ``/backlog/edit`` {project, line, text}, "Edit file" opens ``/backlog?p=<n>&edit=1``),
  ``request`` (page state), ``error``.
- the core's file editor context (``edit``, ``project``, ``content``, ``token``, ``lease_id``, ``lease_other``,
  ``diff``, ``new_token``, ``cancel``): ``backlog/_editor.html`` reads it directly (no view model).

``dt_backlog()`` (Jinja global, ``template_globals()``) returns a ``View``: the URL state, the head / tabs / controls
row items, the issue rows (grouped by project or flat) or the TODO tree (project > section), ⋯ items from ``menus``.
``dt_backlog("reader")`` reads the core's GET /p/{n}/issues/{num} context (``d``, ``it``, ``pname``, ``send``) for
``_issue_detail.html`` (the issue reader).

URL state (GET /backlog): ``tab`` issues|todo · ``status`` (issues) / ``todo`` (TODO tab) open|done|all · ``project``
· ``group`` project|none · ``q`` · ``limit`` (Show all) · ``scope=project`` (the project view's tabs: no head, no
tabs, no Project / Group select, the TODO grouped by section). A request whose ``HX-Target`` is the component
(``bl-issues`` / ``bl-todo``) gets the component alone (``frag``): the project tabs lazy-load it that way and every
control of the component swaps it in place (backlog.html dispatches; no route of our own).
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

from jinja2 import pass_context

from pm.core import timefmt
from pm.web.dt import menus
from pm.web.dt.shell import age

log = logging.getLogger(__name__)

TABS = ("issues", "todo")
TARGET = {"issues": "bl-issues", "todo": "bl-todo"}
STATUSES = (("open", "Open"), ("done", "Done"), ("all", "All"))
GROUPS = (("project", "Group: project"), ("none", "Group: none"))
SHOW_ALL = 500  # the core's views.LIST_MAX
AGENT_WORD = {"running": "working on it", "waiting": "waiting for you", "idle": "idle", "failed": "failed",
              "starting": "starting"}
TRIGGER = ("pm7:refresh, pm7:resync from:document, pm:agent_added from:document, pm:agent_removed from:document,"
           " pm:agent_state from:document throttle:3s")


def _p(name: str) -> str:
    return "/p/" + quote(name, safe="")


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


# ------------------------------------------------------------------ URL state
@dataclass(frozen=True)
class State:
    scope: str  # "page" | "project"
    tab: str  # issues | todo
    status: str  # open | done | all
    project: str  # "" = every project
    group: str  # project | none (page scope; the project scope groups the TODO by section)
    q: str
    limit: int  # 0 = the core's page size

    @property
    def target(self) -> str:
        return TARGET[self.tab]

    def url(self, **kw) -> str:
        """``/backlog?...`` for this state with ``kw`` changed; defaults are left out."""
        s = dataclasses.replace(self, **kw)
        p: dict[str, str] = {}
        if s.scope == "project":
            p["scope"] = "project"
        if s.tab != "issues":
            p["tab"] = s.tab
        if s.status != "open":
            p["todo" if s.tab == "todo" else "status"] = s.status
        if s.project:
            p["project"] = s.project
        if s.scope == "page" and s.group != "project":
            p["group"] = s.group
        if s.q:
            p["q"] = s.q
        if s.limit:
            p["limit"] = str(s.limit)
        return "/backlog" + ("?" + urlencode(p) if p else "")


def state(ctx: Mapping) -> State:
    req = ctx.get("request")
    qp = req.query_params if req is not None else {}
    scope = "project" if qp.get("scope") == "project" else "page"
    tab = qp.get("tab") if qp.get("tab") in TABS else "issues"
    lst = ctx.get("todo_list" if tab == "todo" else "issue_list") or {}
    raw = qp.get("todo" if tab == "todo" else "status") or "open"
    status = lst.get("status") or (raw if raw in dict(STATUSES) else "open")
    group = qp.get("group") if qp.get("group") in dict(GROUPS) else "project"
    q = (ctx.get("issue_list") or {}).get("q", "") if tab == "issues" else " ".join((qp.get("q") or "").split())[:200]
    try:
        limit = max(0, min(int(qp.get("limit") or 0), SHOW_ALL))
    except ValueError:
        limit = 0
    return State(scope=scope, tab=tab, status=status, project=(qp.get("project") or "")[:64],
                 group="none" if scope == "project" else group, q=q, limit=limit if tab == "issues" else 0)


def is_frag(ctx: Mapping) -> bool:
    req = ctx.get("request")
    return req is not None and (req.headers.get("hx-target") or "") in TARGET.values()


# ------------------------------------------------------------------ issues
@dataclass(frozen=True)
class IRow:
    id: str  # "<project>:<num>" (data-row-id); a BACKLOG.md item: "<project>:l<line>"
    project: str
    num: int
    title: str  # plain (title attribute)
    html: str  # inline Markdown of the title, safe (views.issue_row)
    done: bool
    dot: str  # agent state on the issue, "" = no agent
    dot_title: str
    agent: str  # the agent's id, "" = none
    att: list  # [(icon, count, title)] notes / images
    age: str
    since: float | None
    age_title: str
    meta: str  # phone line 2
    act: dict  # the one visible action: {label, icon, run | href, title}
    menu: list = field(default_factory=list)
    line: int = -1  # a BACKLOG.md item (file-mode project): its line index (num is 0), else -1
    text: str = ""  # a BACKLOG.md item's raw text (what /backlog/toggle and /backlog/edit check)


@dataclass(frozen=True)
class IGroup:
    key: str
    label: str
    count: str
    note: str
    rows: list[IRow]
    actions: list = field(default_factory=list)  # group head buttons (a BACKLOG.md project: Edit file)


def _imenu(r: Mapping, ag: Mapping | None, others: bool) -> list[dict]:
    p, n, done = r["project"], r["num"], bool(r["done"])
    base = f"{_p(p)}/issues/{n}"
    if done or ag:
        disp = menus.item("dispatch", "Dispatch", "send", disabled=True,
                          why="done" if done else f"{ag['id']} is on it")
    else:
        disp = menus.item("dispatch", "Dispatch", "send", note="new agent", event="bl:dispatch")
    return menus.menu(
        disp,
        menus.item("state", "Reopen" if done else "Mark done", "check", key="E",
                   post=f"{base}/{'reopen' if done else 'done'}", done=f"#{n} " + ("reopened" if done else "done"),
                   refresh="#bl-issues"),
        menus.item("select", "Select / unselect", "check", key="X", event="bl:select"),
        menus.item("edit", "Edit…", "edit", key="R", event="bl:edit"),
        menus.SEP,
        menus.item("copy", "Copy pmctl command", "copy", note="issue show", copy=f"pmctl --project {p} issue show {n}"),
        menus.item("project", "Open project", "folder", href=f"{_p(p)}#issues"),
        menus.item("move", "Move to project…", "swap", event="bl:move") if others else
        menus.item("move", "Move to project…", "swap", disabled=True, why="no other tracker project"))


def irow(r: Mapping, st: State, others: bool, now: float) -> IRow:
    p, n, done = r["project"], int(r["num"]), bool(r["done"])
    ag = r.get("agent") or None
    dot = str(ag["state"]) if ag else ""
    word = AGENT_WORD.get(dot, dot)
    att = []
    if r.get("n_notes"):
        att.append(("chat", r["n_notes"], plural(r["n_notes"], "note")))
    if r.get("n_img"):
        att.append(("img", r["n_img"], plural(r["n_img"], "image")))
    at = r.get("age_at") or 0
    meta = [p] if st.scope == "page" else []
    meta.append(f"#{n}")
    if ag:
        meta.append(f"{ag['id']} {word}")
    meta += [t for _, _, t in att] + (["done"] if done else [])
    if done:
        act = {"label": "Reopen", "icon": "refresh", "run": "state", "title": f"Reopen #{n}"}
    elif ag:
        act = {"label": "Agent", "icon": "chat", "href": "/a/" + quote(ag["id"], safe=""),
               "title": f"Open {ag['id']} ({word})"}
    else:
        act = {"label": "Dispatch", "icon": "send", "run": "dispatch", "title": f"Start an agent on #{n}"}
    return IRow(id=f"{p}:{n}", project=p, num=n, title=r.get("title") or "", html=r.get("html") or "", done=done,
                dot=dot, dot_title=f"{ag['id']} {word}" if ag else "", agent=ag["id"] if ag else "", att=att,
                age=age(at, now) if at else "–", since=at or None,
                age_title=(("closed " if done else "opened ") + timefmt.fmt_dt(at)) if at else "date unknown",
                meta=" · ".join(meta), act=act, menu=_imenu(r, ag, others))


def igroups(lst: Mapping, st: State, now: float, frows: list[IRow] | None = None) -> list[IGroup]:
    """Issue rows grouped by project (page scope, Group: project) or one flat group (key ""); ``frows`` = the
    BACKLOG.md items of file-mode projects (after the tracker rows; their bands say BACKLOG.md and carry Edit file)."""
    projects = list(lst.get("projects") or ())
    rows = [irow(r, st, len(projects) > 1, now) for r in lst.get("rows") or ()] + list(frows or ())
    if st.scope == "project" or st.group == "none":
        return [IGroup("", "", "", "", rows)] if rows else []
    word = {"open": "open", "done": "done"}.get(st.status, "shown")
    out = []
    for name in sorted({r.project for r in rows}, key=str.lower):
        rs = [r for r in rows if r.project == name]
        if rs[0].line >= 0:
            out.append(IGroup("f:" + name, name, f"{len(rs)} {word}", "BACKLOG.md", rs, [_edit_file_btn(name)]))
            continue
        busy = sum(1 for r in rs if r.agent and not r.done)
        out.append(IGroup("i:" + name, name, f"{len(rs)} {word}", f"{busy} with an agent" if busy else "", rs))
    return out


# ------------------------------------------------------------------ BACKLOG.md items of file-mode projects
def file_url(name: str) -> str:
    """The core's whole-file editor of ``name``'s BACKLOG.md."""
    return "/backlog?" + urlencode({"p": name, "edit": 1})


def _edit_file_btn(name: str) -> dict:
    return {"label": "Edit file", "icon": "edit", "title": f"Edit {name}/BACKLOG.md as one text",
            "attrs": {"data-bl-editfile": file_url(name)}}


def _cut(text: str, n: int = 60) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"


def _fmenu(name: str, b: Mapping) -> list[dict]:
    done, text = bool(b["done"]), str(b["text"])
    return menus.menu(
        menus.item("state", "Reopen" if done else "Mark done", "check", key="E", post="/backlog/toggle",
                   vals={"project": name, "line": int(b["line"]), "text": text},
                   done=("Reopened: " if done else "Done: ") + _cut(text), refresh="#bl-issues"),
        menus.item("edit", "Edit…", "edit", key="R", event="bl:fedit"),
        menus.SEP,
        menus.item("copy", "Copy text", "copy", copy=text),
        menus.item("file", "Edit BACKLOG.md", "edit", note="whole file", href=file_url(name)),
        menus.item("project", "Open project", "folder", href=f"{_p(name)}#issues"))


def frow(name: str, b: Mapping, st: State) -> IRow:
    done, line, text = bool(b["done"]), int(b["line"]), str(b["text"])
    meta = ([name] if st.scope == "page" else []) + ["BACKLOG.md"] + (["done"] if done else [])
    act = ({"label": "Reopen", "icon": "refresh", "run": "state", "title": "Reopen this item"} if done else
           {"label": "Done", "icon": "check", "run": "state", "title": "Mark this item done"})
    return IRow(id=f"{name}:l{line}", project=name, num=0, title=text, html=str(b.get("html") or ""), done=done,
                dot="", dot_title="", agent="", att=[], age="–", since=None, age_title="a BACKLOG.md item (no date)",
                meta=" · ".join(meta), act=act, menu=_fmenu(name, b), line=line, text=text)


def _fold(s: str) -> str:
    return " ".join((s or "").lower().split())


def file_items(ctx: Mapping, st: State) -> tuple[list[IRow], dict]:
    """(rows for the status, {open, done, all} counts) of the file-mode projects' BACKLOG.md items, filtered by the
    project and the text filter like the tracker's list (the counts ignore the status, never the filter)."""
    rows: list[IRow] = []
    counts = {"open": 0, "done": 0, "all": 0}
    q = _fold(st.q)
    for e in ctx.get("entries") or ():
        name = e["name"]
        if e.get("tracker") or (st.project and name != st.project):
            continue
        for b in e.get("blocks") or ():
            if b.get("kind") != "item" or (q and q not in _fold(f"{name} {b['text']}")):
                continue
            counts["done" if b["done"] else "open"] += 1
            if st.status == "all" or bool(b["done"]) == (st.status == "done"):
                rows.append(frow(name, b, st))
    counts["all"] = counts["open"] + counts["done"]
    return rows, counts


def file_projects(ctx: Mapping) -> list[str]:
    return [e["name"] for e in ctx.get("entries") or () if not e.get("tracker")]


# ------------------------------------------------------------------ TODO
@dataclass(frozen=True)
class TItem:
    id: str  # "<project>:t<num>" (tracker) | "<project>:l<line>" (file)
    project: str
    num: int  # 0 = a TODO.md item (no number)
    line: int  # what the toggle posts back (tracker: the number; file: the line index)
    index: int | None  # position among the project's open items (POST /p/{n}/dispatch), None = done
    text: str
    html: str
    done: bool
    section: str
    q: str
    meta: str
    menu: list = field(default_factory=list)
    attrs: dict = field(default_factory=dict)  # the row's data-bl-* (p, line, text; ix when open), tb.row attrs


@dataclass(frozen=True)
class TSection:
    key: str
    heading: str
    note: str
    items: list[TItem]


@dataclass(frozen=True)
class TGroup:
    key: str
    name: str
    note: str
    sections: list[TSection]


def _tmenu(name: str, it: Mapping, index: int | None) -> list[dict]:
    done = bool(it["done"])
    return menus.menu(
        menus.item("dispatch", "Dispatch", "send", note="Launch with the task", event="bl:tdispatch")
        if index is not None else menus.item("dispatch", "Dispatch", "send", disabled=True, why="done"),
        menus.item("state", "Reopen" if done else "Tick (done)", "check", key="E", event="bl:tick"),
        menus.item("edit", "Edit…", "edit", key="R", event="bl:tedit") if it.get("tracker") else
        menus.item("edit", "Edit…", "edit", disabled=True, why="TODO.md project: edit the file"),
        menus.SEP,
        menus.item("copy", "Copy task text", "copy", copy=it["text"]),
        menus.item("project", "Open project", "folder", href=f"{_p(name)}#todo"))


def titem(name: str, it: Mapping, section: str, index: int | None, st: State) -> TItem:
    num = int(it.get("num") or 0) if it.get("tracker") else 0
    line = int(it["line"])
    sec = section or "(no section)"
    flat = st.scope == "page" and st.group == "none"
    meta = ([name, sec] if flat else []) + (["done"] if it["done"] else [])
    attrs = {"data-bl-p": name, "data-bl-line": line, "data-bl-text": it["text"]}
    if index is not None:
        attrs["data-bl-ix"] = index
    return TItem(id=f"{name}:{'t' if num else 'l'}{num or line}", project=name, num=num, line=line, index=index,
                 text=it["text"], html=it.get("html") or "", done=bool(it["done"]), section=sec,
                 q=" ".join(x for x in (f"#{num}" if num else "", it["text"], name, section) if x),
                 meta=" · ".join(meta), menu=_tmenu(name, it, index), attrs=attrs)


def tgroups(lst: Mapping) -> list[tuple[str, Mapping, list[tuple[Mapping, list[tuple[Mapping, int | None]]]]]]:
    """(project, group, [(section, [(item, open index)])]): the open index counts the project's open items in file
    order (the core's parse_todo order), so it is only right when every open item is listed (status open / all)."""
    out = []
    for g in lst.get("groups") or ():
        n, secs = 0, []
        for s in g["sections"]:
            items = []
            for it in s["items"]:
                items.append((it, None if it["done"] else n))
                n += 0 if it["done"] else 1
            secs.append((s, items))
        out.append((g["name"], g, secs))
    return out


def todo_tree(lst: Mapping, st: State) -> list[TGroup]:
    exact = st.status in ("open", "all")
    out = []
    for name, g, secs in tgroups(lst):
        sections = []
        for s, items in secs:
            rows = [titem(name, it, s["heading"], ix if exact else None, st) for it, ix in items]
            sections.append(TSection(f"s:{name}:{s['heading']}", s["heading"] or "(no section)",
                                     f"{s['open_n']} open of {s['open_n'] + s['done_n']}", rows))
        out.append(TGroup("t:" + name, name, f"{g['open_n']} open", sections))
    return out


# ------------------------------------------------------------------ the page
@dataclass(frozen=True)
class View:
    st: State
    frag: bool
    ok: bool  # False: pm.db was busy (the core handed None)
    error: str
    title: str
    stats: list
    tabs: list[dict]
    segs: list[dict]
    proj_opts: list
    group_opts: tuple
    projects: list[str]  # tracker projects (Add issue, Move)
    add: dict | None  # the Add button: {label, kind: issue | task}; None = no tracker project to add to
    issues: list[IGroup]
    todo: list[TGroup]
    shown: int
    total: int
    more: str  # Show all URL, "" = everything shown
    empty: str
    clear: str  # "Clear the filter" URL when a filter hides rows, else ""
    trigger: str = TRIGGER
    hints: list = field(default_factory=list)
    todo_nums: bool = False  # some listed task has a tracker number: the TODO table has its "#" column
    file_edit: str = ""  # project scope on a file-mode project: its whole-file editor URL (the tools row's button)


def _counts(ctx: Mapping, st: State) -> tuple[int, int, list[str]]:
    """(open issues, open tasks, tracker projects) for the Project select only (never the text filter)."""
    il, tl = ctx.get("issue_list") or {}, ctx.get("todo_list") or {}
    projects = list(il.get("projects") or ())
    ents = [e for e in ctx.get("entries") or () if not e.get("tracker") or e["name"] in projects]
    issues = sum(e["open_n"] for e in ents if not st.project or e["name"] == st.project)
    return issues, int((tl.get("counts") or {}).get("open") or 0), projects


def _segs(ctx: Mapping, st: State, extra: Mapping | None = None) -> list[dict]:
    """Status segments; ``extra`` = the BACKLOG.md items' counts, added to the tracker's."""
    lst = ctx.get("todo_list" if st.tab == "todo" else "issue_list") or {}
    c, x = lst.get("counts") or {}, extra or {}
    return [{"key": k, "label": label, "href": st.url(status=k, limit=0), "cur": k == st.status,
             "count": c.get(k, 0) + x.get(k, 0) if lst or x.get("all") else None} for k, label in STATUSES]


def _proj_opts(ctx: Mapping, st: State, projects: list[str]) -> list:
    names = set(projects) | {g["name"] for g in (ctx.get("todo_list") or {}).get("groups") or ()}
    names |= {e["name"] for e in ctx.get("entries") or () if st.tab == "todo" or not e.get("tracker")}
    if st.project:
        names.add(st.project)
    return [("", "All projects")] + [(n, n) for n in sorted(names, key=str.lower)]


def _empty(ctx: Mapping, st: State, projects: list[str], n: int) -> tuple[str, str]:
    """(empty line, clear-filter URL)."""
    if n:
        return "", ""
    if st.q or (st.project and st.scope == "page"):
        return ("No issue matches." if st.tab == "issues" else "No task matches."), st.url(q="", project="", limit=0)
    if st.tab == "issues" and st.project in file_projects(ctx):
        return {"open": "No open items in BACKLOG.md.", "done": "No ticked items in BACKLOG.md."}.get(
            st.status, "No items in BACKLOG.md."), ""
    if st.scope == "project" and st.project not in projects:
        return (f"{st.project} keeps its {'issues in BACKLOG.md' if st.tab == 'issues' else 'tasks in TODO.md'}, not in"
                " the tracker; import it with pmctl import to work on them here."), ""
    if st.tab == "issues":
        if not projects and not file_projects(ctx):
            return "No project uses the issue tracker yet. Import one with pmctl import.", ""
        return {"open": 'No open issues. Add one with Add issue, or pmctl issue add "title".',
                "done": "No closed issues yet."}.get(st.status, "No issues yet."), ""
    return {"open": "No open tasks. TODO.md files with - [ ] items show here.",
            "done": "No ticked tasks."}.get(st.status, "No tasks."), ""


def _add(st: State, projects: list[str]) -> dict | None:
    if not projects or (st.scope == "project" and st.project not in projects):
        return None
    return {"label": "Add issue", "kind": "issue"} if st.tab == "issues" else {"label": "Add task", "kind": "task"}


def page(ctx: Mapping, now: float | None = None) -> View:
    st, now = state(ctx), time.time() if now is None else now
    il, tl = ctx.get("issue_list"), ctx.get("todo_list")
    oi, ot, projects = _counts(ctx, st)
    where = f" in {st.project}" if st.project and st.scope == "page" else ""
    frows, fcounts = file_items(ctx, st) if st.tab == "issues" else ([], {})
    issues = igroups(il or {}, st, now, frows) if (il or frows) and st.tab == "issues" else []
    todo = todo_tree(tl, st) if tl and st.tab == "todo" else []
    tshown = len((il or {}).get("rows") or ())
    shown = tshown + len(frows) if st.tab == "issues" else sum(len(s.items) for g in todo for s in g.sections)
    ttotal = int((il or {}).get("total") or 0)
    total = ttotal + len(frows) if st.tab == "issues" else shown
    empty, clear = _empty(ctx, st, projects, shown)
    tabs = [{"key": "issues", "label": "Issues", "href": st.url(tab="issues", status="open", limit=0),
             "cur": st.tab == "issues", "count": oi},
            {"key": "todo", "label": "TODO", "href": st.url(tab="todo", status="open", limit=0),
             "cur": st.tab == "todo", "count": ot}]
    hints = ([("J K", "move"), ("Enter", "open"), ("X", "select"), ("E", "done / reopen"), ("R", "edit"),
              ("/", "filter")]
             if st.tab == "issues" else [("J K", "move"), ("E", "tick / reopen"), ("R", "edit"), ("/", "filter")])
    return View(
        st=st, frag=is_frag(ctx), ok=(il if st.tab == "issues" else tl) is not None, error=str(ctx.get("error") or ""),
        title="Backlog", stats=[plural(oi, "open issue") + where, plural(ot, "open task") + where,
                                plural(len(projects), "tracker project")],
        tabs=tabs, segs=_segs(ctx, st, fcounts), proj_opts=_proj_opts(ctx, st, projects), group_opts=GROUPS,
        projects=projects,
        add=_add(st, projects),
        issues=issues, todo=todo, shown=shown, total=total,
        more=st.url(scope="page", limit=SHOW_ALL) if st.tab == "issues" and tshown < ttotal else "", empty=empty,
        clear=clear,
        hints=hints, todo_nums=any(r.num for g in todo for sec in g.sections for r in sec.items),
        file_edit=file_url(st.project) if st.scope == "project" and st.project in file_projects(ctx) else "")


# ------------------------------------------------------------------ the issue reader (GET /p/{n}/issues/{num})
@dataclass(frozen=True)
class Reader:
    project: str
    num: int
    done: bool
    title_html: str
    title: str
    meta: str
    body_html: str
    images: list  # [{url, label}]
    notes: list  # [{author, age, when, html}]
    pmctl: str
    send: list  # running agents of the project: [{id, label, state, href}]
    base: str  # /p/<n>/issues/<num>
    title_sfx: str = ""  # the stored title's " (YYYY-MM-DD)" suffix: hidden in the Edit form, sent back on save
    body: str = ""  # the raw Markdown body: the Edit form's value


def reader(ctx: Mapping, now: float | None = None) -> Reader:
    d, it, name = ctx["d"], ctx["it"], str(ctx["pname"])
    now = time.time() if now is None else now
    made = d.get("created_at") or 0
    meta = "Added" + (f" by {it.created_by}" if it.created_by else "")
    meta += f" {age(made, now)} ago" if made else " (date unknown)"
    if it.done:
        meta += " · done" + (f" by {it.closed_by}" if it.closed_by else "")
        meta += f" {age(it.closed_at, now)} ago" if it.closed_at else ""
    if it.commit:
        meta += f" · {it.commit[:10]}"
    notes = [{"author": n["author"] or "?", "age": age(n["ts"], now), "when": timefmt.fmt_dt(n["ts"]),
              "html": n["html"]} for n in d.get("notes") or ()]
    return Reader(project=name, num=int(it.num), done=bool(it.done), title_html=d.get("title_html") or "",
                  title=it.title_short, meta=meta, body_html=d.get("body_html") or "" if it.body_md.strip() else "",
                  images=[{"url": i["url"], "label": i.get("label") or i["name"]} for i in d.get("images") or ()],
                  notes=notes, pmctl=f"pmctl --project {name} issue show {it.num}", send=list(ctx.get("send") or ()),
                  base=f"{_p(name)}/issues/{int(it.num)}", body=it.body_md,
                  title_sfx=it.title[len(it.title_short):] if it.title != it.title_short else "")


# ------------------------------------------------------------------ Jinja global
PARTS = {"page": page, "reader": reader}
_logged: set[str] = set()


@pass_context
def dt_backlog(ctx, part: str = "page"):
    """``dt_backlog()`` / ``dt_backlog("reader")``; None on an error (logged once per error type)."""
    try:
        return PARTS[part](ctx)
    except Exception as e:  # noqa: BLE001 - a broken view model must not break the page
        name = type(e).__name__
        if name not in _logged:
            _logged.add(name)
            log.exception("dt backlog view model failed")
        return None
