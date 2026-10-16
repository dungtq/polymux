"""The dt Notifications view model (PRD.md §6.7, LAYOUT.md §4-5): the list (``_ntfy.html``, the body of the
core's GET /ntfy/panel for every scope) and the page state (``ntfy.html`` for GET /ntfy and /notifications).

``dt_ntfy(part)`` is a Jinja global (``template_globals()``):

- ``dt_ntfy("panel")`` reads the core's ``build_panel`` context (rows, pins, scope, ok, total, unread) and returns a
  ``Panel``: the Pinned band and the day bands (Today · Yesterday · Earlier) with their rows ready to render (row
  classes, ⋯ items from ``menus``, detail facts, links). No IO.
- ``dt_ntfy("page")`` reads ``nav`` and ``request`` and returns a ``Page``: Source / Unread / Period from the URL
  (``?src= all|agent|system|pinned``, ``?un=1``, ``?per=<period>``, ``?q=``), the segments and the period options.
  /notifications preselects Source System for that load only (the saved view, ``pm.v7.notifs.*`` in
  js/notifs.js, is shared by both URLs).

Row classes are the page's filter contract with css/notifs.css and js/notifs.js: ``n-row``, ``n-agent`` |
``n-system`` (source), ``un`` (unread), ``n-pp`` + ``n-p-<period>`` (only rows that carry periods: the scope-all
list; the pins and the scope-system list are never period-filtered).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import quote

from jinja2 import pass_context

from pm.core import timefmt
from pm.web.dt import menus

log = logging.getLogger(__name__)

# the core's period tabs (pm.web.ntfypanel.TABS: the periods every scope-all row carries); a test keeps them equal
PERIODS = (("today", "Today"), ("week", "This week"), ("last_week", "Last week"), ("month", "This month"),
           ("last_month", "Last month"), ("year", "This year"), ("all", "All time"))
SOURCES = (("all", "All", "Agent and system messages"), ("agent", "Agents", "Messages from agents"),
           ("system", "System", "pm's own alerts, not tied to an agent"),
           ("pinned", "Pinned", "Pinned messages, kept after they leave the history"))
DAYS = (("today", "Today"), ("yesterday", "Yesterday"), ("earlier", "Earlier"))
DEFAULT_PER = "week"
REFRESH = "#n-panel"


def cut(text: str, n: int) -> str:
    """``text`` cut to ``n`` characters with an ellipsis (Jinja's ``truncate(n, true, "…")``, leeway 5)."""
    return text if len(text) <= n + 5 else text[:n - 1] + "…"


@dataclass(frozen=True)
class NRow:
    """One message row and its details. ``key`` = the inbox key (pins: ``pin:<message id>``), never an index."""
    key: str
    cls: str  # row classes (module doc)
    kind: str | None  # agent kind of the owner (kind edge), None = system
    q: str  # filter text: names, never the message
    unread: bool
    pin: bool  # a row of the Pinned band
    pinned: bool  # the message is pinned (Pinned band, or a day row that is also pinned)
    title: str
    pri: int  # ntfy priority; 3 = default (no tag)
    pri_cls: str  # "bad" (5) | "warn" (4) | ""
    n: int  # messages folded into this row
    recovered: str  # "3m" for a "pm recovered" message, else ""
    aid: str  # the owning agent id, "" = none
    who_title: str  # the agent link's title
    who: str  # shown instead of an agent: "pm" (system) or "–"
    proj: str
    tag_raw: str
    age: str  # "5m" (ticker.js keeps it with ``since``)
    since: int | None
    meta: str  # phone line 2
    message: str
    kv: list  # detail facts: (key, value) or (key, value, cls)
    review: str  # Review link (a reviewer agent for this agent's changes), "" = none
    click: str  # the message's safe click URL, "" = none
    attrs: dict  # extra row attributes for the bell (data-n-abs, data-n-click, data-n-pri)
    menu_label: str  # the ⋯ trigger's label
    menu: list = field(default_factory=list)


@dataclass(frozen=True)
class NGroup:
    key: str  # pinned | today | yesterday | earlier
    label: str
    rows: list[NRow]
    unread: int
    icon: str = ""
    note: str = ""
    actions: list | None = None  # the day band's Mark read (tb.group actions)


@dataclass(frozen=True)
class Panel:
    scope: str
    total: int
    unread: int
    ok: bool
    groups: list[NGroup]
    empty: str  # "" when there are rows, else the empty line's text


@dataclass(frozen=True)
class Page:
    system: bool  # GET /notifications
    src: str
    per: str
    un: bool
    q: str
    dsrc: str  # the page's default source (not written to the URL)
    dper: str
    segs: list[dict]  # ps.seg items
    periods: tuple


# ------------------------------------------------------------------ rows
def _menu(r: Mapping, key: str, aid: str, pin: bool, pinned: bool) -> list[dict]:
    """⋯ items: Open chat / Open in a tab (with an agent), Details, Mark read / unread (not on a pin), Pin, Mute."""
    i = r["item"]
    unread = bool(r.get("unread"))
    mute = None
    if i.title:
        mute = menus.item("mute", "Mute this title", "bell", note="24 h", post="/ntfy/mute", vals={"title": i.title},
                          confirm="Mute “" + cut(i.title, 60) + "” for 24 hours?", ok="Mute 24 h",
                          body="Messages with this title leave every list, count and the bell until then. They stay"
                               " on the ntfy server.", done="Muted for 24 h", refresh=REFRESH)
    return menus.menu(
        menus.item("chat", "Open chat", "side-r", key="Enter", event="n:chat") if aid else None,
        menus.item("tab", "Open in a tab", "tabs", key="O", event="n:tab") if aid else None,
        menus.details(),
        menus.SEP,
        None if pin else menus.item("read", "Mark read" if unread else "Mark unread", "check", key="E",
                                    post="/inbox/read" if unread else "/inbox/mark-unread", vals={"key": key},
                                    done="Marked read" if unread else "Marked unread", refresh=REFRESH),
        menus.item("pin", "Unpin" if pinned else "Pin", "pin", key="P", post="/ntfy/unpin" if pinned else "/ntfy/pin",
                   vals={"id": i.id}, done="Unpinned" if pinned else "Pinned", refresh=REFRESH),
        menus.SEP, mute)


def _kv(r: Mapping, o, aid: str, proj: str) -> list:
    i = r["item"]
    kv: list = [("Sent", (r.get("abs") or "") + (" · " + r["ago"] if r.get("ago") else ""))]
    if i.priority != 3:
        kv.append(("Priority", f"{i.priority} ({'high' if i.priority > 3 else 'low'})"))
    if o:
        kv.append(("Agent", aid + (f" · tmux pane {o.pane}" if o.pane else "") + ("" if o.live else " · ended")))
    if proj:
        kv.append(("Project", proj))
    if i.tags:
        kv.append(("Tags", ", ".join(i.tags), "mono"))
    if (r.get("n") or 1) > 1:
        kv.append(("Repeats", f"the last of {r['n']} messages with this title"))
    if r.get("recovered_after_s"):
        kv.append(("Recovered", "after " + timefmt.duration(r["recovered_after_s"]), "ok"))
    return kv


def row(r: Mapping, pin: bool) -> NRow:
    """One row from the core's row dict (``ntfypanel._rows``); ``pin`` = a row of the Pinned band."""
    i, o = r["item"], r.get("owner")
    aid = o.agent_id if o else ""
    src = r.get("src") or ("agent" if o else "system")
    pinned = pin or bool(r.get("pinned"))
    title = r.get("title") or "(no title)"
    proj = r.get("proj_name") or r.get("tag_label") or ""
    per = (r.get("p") or "").split()
    cls = ["n-row", "n-" + src] + (["un"] if r.get("unread") else [])
    if per:
        cls += ["n-pp"] + [f"n-p-{p}" for p in per]
    head = aid or ("pm" if src == "system" else "")
    attrs = {"data-n-abs": r.get("abs") or ""}
    if r.get("click"):
        attrs["data-n-click"] = r["click"]
    if i.priority != 3:
        attrs["data-n-pri"] = str(i.priority)
    who_title = ""
    if o:
        who_title = ((o.label or aid) + (f" · tmux pane {o.pane}" if o.pane else "") + ("" if o.live else " · ended")
                     + " (opens the chat)")
    review = ""
    if o and r.get("proj_name"):
        review = f"/p/{quote(r['proj_name'], safe='')}/launch?review={quote(aid, safe='')}"
    rec = r.get("recovered_after_s")
    return NRow(
        key=r["key"], cls=" ".join(cls), kind=o.kind if o else None, q=r.get("q") or "", unread=bool(r.get("unread")),
        pin=pin, pinned=pinned, title=title, pri=i.priority,
        pri_cls="bad" if i.priority >= 5 else "warn" if i.priority == 4 else "", n=int(r.get("n") or 1),
        recovered=timefmt.duration(rec) if rec else "", aid=aid, who_title=who_title,
        who="pm" if src == "system" else "–", proj=proj, tag_raw=r.get("tag_raw") or "",
        age=(r.get("ago") or "").replace(" ago", ""), since=int(i.time) if i.time else None,
        meta=head + (" · " if head and proj else "") + proj, message=i.message or "", kv=_kv(r, o, aid, proj),
        review=review, click=r.get("click") or "", attrs=attrs, menu_label="Actions for " + cut(title, 40),
        menu=_menu(r, r["key"], aid, pin, pinned))


# ------------------------------------------------------------------ the list
def panel(ctx: Mapping) -> Panel:
    """The list for a ``build_panel`` context: Pinned (scope all) on top, then the day bands in order."""
    rows = list(ctx.get("rows") or ())
    pins = list(ctx.get("pins") or ())
    groups: list[NGroup] = []
    if pins:
        groups.append(NGroup("pinned", "Pinned", [row(r, True) for r in pins], 0, icon="pin",
                             note="kept after they leave the history"))
    for key, label in DAYS:
        rs = [row(r, False) for r in rows if r.get("day") == key]
        if not rs:
            continue
        un = sum(1 for r in rs if r.unread)
        act = [{"label": "Mark read", "icon": "check", "hidden": not un, "attrs": {"data-n-dayread": key},
                "title": f"Mark the unread messages shown under {label} read"}]
        groups.append(NGroup(key, label, rs, un, note=f"{un} unread" if un else "", actions=act))
    ok = bool(ctx.get("ok", True))
    empty = "" if rows or pins else ("No messages yet" + ("" if ok else " (ntfy is unreachable)")
                                     + ". Agents and pm's alerts post them to ntfy; they show here.")
    return Panel(scope=str(ctx.get("scope") or "all"), total=int(ctx.get("total") or 0),
                 unread=int(ctx.get("unread") or 0), ok=ok, groups=groups, empty=empty)


# ------------------------------------------------------------------ the page
def page(ctx: Mapping) -> Page:
    """Page state from the URL; /notifications (nav "notifications") preselects Source System."""
    system = ctx.get("nav") == "notifications"
    req = ctx.get("request")
    qp = req.query_params if req is not None else {}
    dsrc = "system" if system else "all"
    src = qp.get("src") if qp.get("src") in {k for k, _, _ in SOURCES} else dsrc
    per = qp.get("per") if qp.get("per") in dict(PERIODS) else DEFAULT_PER
    here = "/notifications" if system else "/ntfy"
    segs = [{"key": k, "label": label, "href": f"{here}?src={k}", "cur": k == src, "count": "", "title": tip}
            for k, label, tip in SOURCES]
    return Page(system=system, src=src, per=per, un=qp.get("un") == "1", q=qp.get("q") or "", dsrc=dsrc,
                dper=DEFAULT_PER, segs=segs, periods=PERIODS)


# ------------------------------------------------------------------ Jinja global
PARTS = {"panel": panel, "page": page}
_logged: set[str] = set()


@pass_context
def dt_ntfy(ctx, part: str):
    """``dt_ntfy("panel")`` / ``dt_ntfy("page")``; None on an error (logged once per error type)."""
    try:
        return PARTS[part](ctx)
    except Exception as e:  # noqa: BLE001 - a broken view model must not break the page
        name = type(e).__name__
        if name not in _logged:
            _logged.add(name)
            log.exception("dt notifications view model failed")
        return None
