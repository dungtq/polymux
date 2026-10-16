"""The dt Audit page view model (docs/themes/dt/PRD.md §6.10, LAYOUT.md §5 Audit). Read-only, no IO: it reads the core's
GET /audit and GET /audit/client-errors contexts only.

``dt_audit(part)`` is a Jinja global (``template_globals()``):

- ``"page"`` (audit.html): the head's stats line and actions (Export CSV on Activity, Clear all on Client errors) and
  the tabs (Activity · Client errors, links ``?tab=``, counts from the core's ``tab_counts``).
- ``"act"`` (``_audit_list.html``: the Activity component, also the core's HX answer of GET /audit): URL state,
  result segments with counts, category options, Attempts switch, Grouped / Raw toggle, the day bands with their rows,
  the foot (Newer / Load older, notes) and the head parts a live swap sends out of band.
- ``"ce"`` (``_client_errors.html``: the Client errors component, also GET /audit/client-errors): built in
  ``pm.web.dt.audit_errors`` (kind segments, one row per message and request, the masked stack in the details).

Activity URL state: ``result`` all|ok|refused|failed|attempt, ``q``, ``cat``, ``attempts=1``, ``page`` (core) and
``view=raw`` (read here only; Grouped is the default). Grouped folds consecutive entries of one day with the same
client, action, target and result class into one row (×N, first and last time, the raw entries kept); a fold never
crosses a day band or the page's 50 rows. Row id = the entry's ``ts`` (a fold: its newest entry), never an index.
Targets link by pattern (the log has no typed targets): an agent id to ``/a/<id>``, a tmux session ``$n`` to
``/term/s<n>``, an issue action to ``/p/<project>#issue=<num>`` from its detail. The log stores no session: "—".
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from urllib.parse import quote, urlencode

from jinja2 import pass_context

from pm.agents.lineage import AGENT_ID_RE
from pm.core import timefmt
from pm.core.models import AgentKind
from pm.security.audit_view import category, result_class
from pm.web.dt import menus
from pm.web.dt.audit_errors import ce, ce_stats, clear_action, is_swap
from pm.web.dt.audit_errors import num as _n
from pm.web.dt.backlog import plural
from pm.web.dt.ntfy import cut
from pm.web.render import urlid

log = logging.getLogger(__name__)

TABS = (("activity", "Activity"), ("errors", "Client errors"))
RESULTS = (("all", "All", "Every entry these filters select"),
           ("ok", "ok", "Changes pm made"),
           ("refused", "Refused", "Refused by a check (login, CSRF, origin, tier, a protected name)"),
           ("failed", "Failed", "Allowed, then failed while running"),
           ("attempt", "attempt", "The line pm writes before each change"))
CAT_LABELS = {"agents": "Agents", "terminal": "Terminal", "control": "Control (kill, stop, reload)", "login": "Logins",
              "settings": "Settings", "files": "Issues and files", "other": "Other"}
DOTS = {"ok": "running", "refused": "failed", "failed": "failed", "attempt": "idle"}
AGENT_KINDS = frozenset(k.value for k in AgentKind)
_TMUX = re.compile(r"\$\d+")
DETAIL_MAX = 300


def _ts(e: Mapping) -> float:
    try:
        return float(e.get("ts") or 0)
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------------ URL state
@dataclass(frozen=True)
class State:
    result: str = "all"
    q: str = ""
    cat: str = ""
    attempts: bool = False
    raw: bool = False
    page: int = 1

    def _params(self) -> dict[str, str]:
        p: dict[str, str] = {}
        if self.result != "all":
            p["result"] = self.result
        if self.q:
            p["q"] = self.q
        if self.cat:
            p["cat"] = self.cat
        if self.attempts:
            p["attempts"] = "1"
        return p

    def url(self, **kw) -> str:
        """``/audit?...`` for this state with ``kw`` changed; defaults are left out."""
        s = replace(self, **kw)
        p = s._params()
        if s.raw:
            p["view"] = "raw"
        if s.page > 1:
            p["page"] = str(s.page)
        return "/audit" + ("?" + urlencode(p) if p else "")

    def export(self) -> str:
        """The CSV download of the rows these filters select (every page; not the view or the page)."""
        p = self._params()
        return "/audit/export.csv" + ("?" + urlencode(p) if p else "")

    @property
    def filtered(self) -> bool:
        return bool(self.q or self.cat or self.result != "all")


def state_of(ctx: Mapping) -> State:
    req = ctx.get("request")
    qp = req.query_params if req is not None else {}
    ap = ctx.get("ap")
    result = str(ctx.get("f_result") or "all")
    return State(result=result if result in {k for k, _, _ in RESULTS} else "all", q=str(ctx.get("f_q") or ""),
                 cat=str(ctx.get("f_cat") or ""), attempts=bool(ctx.get("f_attempts")), raw=qp.get("view") == "raw",
                 page=int(getattr(ap, "page", 1) or 1))


# ------------------------------------------------------------------ targets
@dataclass(frozen=True)
class Link:
    text: str
    href: str
    title: str


def target_link(action: str, target: str, detail: object, term: bool = True) -> Link | None:
    """The target as a link when its shape is known: agent id, tmux session ``$n`` (when the terminal is on), or an
    issue action's project and number (its detail); None otherwise (container, VM and other names stay plain)."""
    t = str(target or "")
    if AGENT_ID_RE.fullmatch(t) and t.rsplit("-", 1)[0] in AGENT_KINDS:
        return Link(t, "/a/" + quote(t, safe=""), f"Open {t}")
    if term and _TMUX.fullmatch(t):
        return Link(t, "/term/" + urlid(t), f"Open the read-only terminal of tmux session {t} as a tab")
    if str(action).startswith("issue_") and isinstance(detail, Mapping):
        p, n = str(detail.get("project") or ""), str(detail.get("num") or "")
        if p and n.isdigit():
            return Link(f"{p} #{n}", f"/p/{quote(p, safe='')}#issue={n}", f"Open issue #{n} of {p}")
    return None


def detail_text(detail: object) -> str:
    if not isinstance(detail, Mapping):
        return ""
    return cut(" ".join(f"{k}={v}" for k, v in detail.items()), DETAIL_MAX)


# ------------------------------------------------------------------ Activity rows
@dataclass(frozen=True)
class ARow:
    id: str
    action: str
    result: str  # ok | refused | failed | attempt
    dot: str
    word: str  # the dot's title and screen-reader word
    hidden: bool  # an attempt the Attempts switch shows (its outcome follows)
    n: int  # entries folded into this row
    target: str
    link: Link | None
    detail: str
    time: str  # HH:MM:SS of the newest entry
    span: str  # "first – last" of a fold, else ""
    meta: str  # phone line 2
    kv: list
    raw: list  # a fold's entries: {"t", "r", "cls", "d"}
    raw_href: str
    only_href: str
    menu: list = field(default_factory=list)


@dataclass(frozen=True)
class Day:
    key: str
    label: str
    rows: list[ARow]
    n: int  # entries
    bad: int  # refused or failed entries
    note: str
    cls: str


def _date(ts: float) -> date | None:
    try:
        return datetime.fromtimestamp(ts, timefmt.tz()).date() if ts > 0 else None
    except (OverflowError, OSError, ValueError):
        return None


def day_of(ts: float, today: date) -> tuple[str, str]:
    """(band key, label): "Today · 10-15", "Yesterday · 10-14", "Mon · 10-12" within a week, else the ISO date."""
    d = _date(ts)
    if d is None:
        return "unknown", "Unknown time"
    ago_days, md = (today - d).days, d.strftime("%m-%d")
    label = (f"Today · {md}" if ago_days <= 0 else f"Yesterday · {md}" if ago_days == 1
             else f"{d.strftime('%a')} · {md}" if ago_days < 7 else d.isoformat())
    return "d" + d.isoformat(), label


def fold(entries: list[dict], raw: bool) -> list[list[dict]]:
    """Runs of consecutive entries with the same client, action, target, result class and day (Grouped); one entry
    per run in Raw."""
    runs: list[list[dict]] = []
    last = None
    for e in entries:
        key = (e.get("client"), e.get("action"), e.get("target"), result_class(e.get("result")),
               _date(_ts(e)), bool(e.get("_hidden")))
        if not raw and runs and key == last:
            runs[-1].append(e)
        else:
            runs.append([e])
        last = key
    return runs


def _clean(e: Mapping) -> dict:
    return {k: v for k, v in e.items() if not str(k).startswith("_")}


def _raw_line(e: Mapping) -> dict:
    r = result_class(e.get("result"))
    d = " ".join(x for x in (str(e.get("target") or ""), detail_text(e.get("detail"))) if x)
    return {"t": timefmt.fmt_time(_ts(e)), "r": r, "cls": "bad" if r in ("refused", "failed") else "", "d": d}


def _kv(run: list[dict], link: Link | None, cat: str) -> list:
    e, old = run[0], run[-1]
    r = result_class(e.get("result"))
    when = timefmt.fmt_full(_ts(e))
    if len(run) > 1:
        when = f"{timefmt.fmt_full(_ts(old))} – {timefmt.fmt_time(_ts(e))}"
    kv: list = [("Result", r + (" (hidden attempt: its outcome follows)" if e.get("_hidden") else ""),
                 "bad" if r in ("refused", "failed") else ""),
                ("Time", when), ("Client", str(e.get("client") or "–"), "mono"), ("Session", "—"),
                ("Action", str(e.get("action") or ""), "mono"), ("Category", CAT_LABELS.get(cat, cat)),
                ("Target", link.text if link else str(e.get("target") or "–"), "mono")]
    d = e.get("detail")
    if isinstance(d, Mapping):
        kv += [(str(k), str(v), "mono") for k, v in d.items()]
    if len(run) > 1:
        kv.append(("Entries", f"{len(run)} in a row on this page"))
    return kv


def arow(run: list[dict], st: State, term: bool) -> ARow:
    """One row of a run (newest entry first): its details, raw entries and ⋯ items."""
    e = run[0]
    action, target = str(e.get("action") or ""), str(e.get("target") or "")
    r = result_class(e.get("result"))
    link = target_link(action, target, e.get("detail"), term)
    n, ts = len(run), _ts(e)
    rid = f"{ts:.6f}"
    span = f"{timefmt.fmt_time(_ts(run[-1]))} – {timefmt.fmt_time(ts)}" if n > 1 else ""
    det = detail_text(e.get("detail"))
    raw_href = st.url(raw=True) if n > 1 else ""
    only = st.url(q=action, page=1)
    copy = json.dumps([_clean(x) for x in run] if n > 1 else _clean(e), indent=2, sort_keys=True, default=str)
    word = r + (", hidden attempt" if e.get("_hidden") else "")
    menu = menus.menu(
        menus.details(), menus.SEP,
        menus.item("copy", "Copy as JSON", "copy", copy=copy),
        menus.item("only", "Only " + cut(action, 40), "filter", href=only),
        menus.item("raw", f"Show the {n} raw entries", "list", href=raw_href) if n > 1 else None,
        menus.item("target", "Open " + cut(link.text, 40), "open", href=link.href) if link else None)
    return ARow(id=rid, action=action, result=r, dot=DOTS[r], word=word, hidden=bool(e.get("_hidden")), n=n,
                target=target, link=link, detail=det, time=timefmt.fmt_time(ts), span=span,
                meta=" · ".join(x for x in (link.text if link else target, det) if x),
                kv=_kv(run, link, category(action)), raw=[_raw_line(x) for x in run] if n > 1 else [],
                raw_href=raw_href, only_href=only, menu=menu)


def days(entries: list[dict], st: State, term: bool, today: date) -> list[Day]:
    """The page's entries as day bands of rows (folded unless Raw); ids made unique within the page."""
    out: list[Day] = []
    seen: set[str] = set()
    for run in fold(entries, st.raw):
        key, label = day_of(_ts(run[0]), today)
        if not out or out[-1].key != key:
            out.append(Day(key, label, [], 0, 0, "", ""))
        row = arow(run, st, term)
        rid, i = row.id, 1
        while rid in seen:
            rid, i = f"{row.id}-{i}", i + 1
        seen.add(rid)
        d = out[-1]
        bad = sum(1 for x in run if result_class(x.get("result")) in ("refused", "failed"))
        out[-1] = replace(d, rows=[*d.rows, replace(row, id=rid)], n=d.n + len(run), bad=d.bad + bad)
    return [replace(d, note=f"{d.bad} refused or failed" if d.bad else "", cls="au-bad" if d.bad else "")
            for d in out]


# ------------------------------------------------------------------ Activity component
@dataclass(frozen=True)
class Act:
    st: State
    url: str
    segs: list[dict]
    cats: list[tuple[str, str]]
    ctl_url: str  # the Attempts switch + category form's base URL (they add attempts= and cat=)
    hidden: int
    views: list[dict]
    days: list[Day]
    total: int
    truncated: bool
    first: int  # 1-based number of the page's first row
    last: int
    newer: str
    older: str
    older_n: int
    notes: list[str]
    empty: str
    clear: str
    counts: dict
    stats: list
    export: dict
    swap: bool


def act_stats(ap) -> list:
    c = getattr(ap, "counts", None) or {}
    n = _n(c.get("all", 0)) + ("+" if getattr(ap, "truncated", False) else "")
    out: list = [f"{n} entries"]
    for k, word in (("refused", "refused"), ("failed", "failed")):
        if c.get(k):
            out.append({"text": f"{_n(c[k])} {word}", "cls": "bad"})
    out.append("times " + timefmt.label())
    return out


def export_action(st: State) -> dict:
    return {"label": "Export CSV", "icon": "file", "href": st.export(),
            "title": "Download the entries these filters select (every page) as CSV",
            "attrs": {"id": "au-export", "download": ""}}


def _segs(st: State, counts: Mapping) -> list[dict]:
    return [{"key": k, "label": label, "href": st.url(result=k, page=1), "cur": st.result == k, "title": tip,
             "count": _n(counts.get(k, 0)), "bad": k in ("refused", "failed") and bool(counts.get(k))}
            for k, label, tip in RESULTS]


def _empty(ap, st: State) -> tuple[str, str]:
    if ap.rows:
        return "", ""
    if st.filtered:
        return "No entry matches these filters.", State(raw=st.raw).url()
    if ap.page > 1:
        return "No older entries.", st.url(page=1)
    return "Nothing in the audit log yet: every change pm makes or refuses is written here.", ""


def act(ctx: Mapping) -> Act:
    ap = ctx["ap"]
    st = state_of(ctx)
    counts = dict(getattr(ap, "counts", None) or {})
    size = int(ctx.get("page_size") or 50)
    cats = [("", "All actions")] + [(c, CAT_LABELS.get(c, c.title())) for c in (ctx.get("categories") or ())]
    rows = list(ap.rows)
    first = (ap.page - 1) * size + 1 if rows else 0
    notes = []
    if ap.hidden and not st.attempts and st.result != "attempt":
        notes.append(f"{plural(ap.hidden, 'attempt line')} hidden; their results are shown.")
    if ap.truncated:
        notes.append("Only the newest 50,000 lines were searched.")
    notes.append("Audit is append-only: pm never edits or deletes it.")
    empty, clear = _empty(ap, st)
    return Act(
        st=st, url=st.url(), segs=_segs(st, counts), cats=cats, ctl_url=st.url(cat="", attempts=False, page=1),
        hidden=int(ap.hidden or 0),
        views=[{"key": "grouped", "label": "Grouped", "href": st.url(raw=False), "on": not st.raw,
                "title": "Repeats in a row fold into one line"},
               {"key": "raw", "label": "Raw", "href": st.url(raw=True), "on": st.raw, "title": "One line per entry"}],
        days=days(rows, st, bool(ctx.get("terminal_enabled", True)), timefmt.today()),
        total=int(ap.total or 0), truncated=bool(ap.truncated), first=first, last=first + len(rows) - 1 if rows else 0,
        newer=st.url(page=ap.page - 1) if ap.page > 1 else "", older=st.url(page=ap.page + 1) if ap.has_older else "",
        older_n=max(0, int(ap.total or 0) - ap.page * size), notes=notes, empty=empty, clear=clear, counts=counts,
        stats=act_stats(ap), export=export_action(st), swap=is_swap(ctx))


# ------------------------------------------------------------------ the page
@dataclass(frozen=True)
class Page:
    tab: str
    tabs: list[dict]
    stats: list
    actions: list[dict]


def page(ctx: Mapping) -> Page:
    tab = ctx.get("tab") if ctx.get("tab") in dict(TABS) else "activity"
    tc = ctx.get("tab_counts") or {}
    tabs = [{"key": k, "label": label, "href": "/audit" + ("?tab=errors" if k == "errors" else ""),
             "cur": k == tab, "count": _n(tc.get(k, 0)), "bad": k == "errors" and bool(tc.get(k))}
            for k, label in TABS]
    if tab == "errors":
        g = ctx.get("ce_groups") or {}
        return Page(tab, tabs, ce_stats(g), [clear_action()])
    return Page(tab, tabs, act_stats(ctx.get("ap")), [export_action(state_of(ctx))])


# ------------------------------------------------------------------ Jinja global
PARTS = {"page": page, "act": act, "ce": ce}
_logged: set[str] = set()


@pass_context
def dt_audit(ctx, part: str):
    """``dt_audit("page" | "act" | "ce")``; None on an error (logged once per error type)."""
    try:
        return PARTS[part](ctx)
    except Exception as e:  # noqa: BLE001 - a broken view model must not break the page
        name = type(e).__name__
        if name not in _logged:
            _logged.add(name)
            log.exception("dt audit view model failed")
        return None
