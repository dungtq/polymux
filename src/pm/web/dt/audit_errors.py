"""The dt Audit page, Client errors tab (PRD.md §6.10): the view model of ``_client_errors.html`` (also the
core's GET /audit/client-errors), read from the core's ``ce_groups`` / ``ce_kind`` / ``ce_q`` / ``kinds`` context
only (no IO). Kind segments, one row per message and request (the core's ``ClientErrorLog.grouped``), the masked
stack, request and browser in the details, Copy and Save as issue in ⋯ (to one of the core's ``issue_projects``;
none = no Save), the head's Clear all.
``pm.web.dt.audit`` serves it as ``dt_audit("ce")`` and uses ``ce_stats`` / ``clear_action`` for the page head;
``num`` and ``is_swap`` are shared with it (this module imports nothing from ``audit``); ``num`` also with
``agents_usage``.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlencode

from pm.core import timefmt
from pm.web.dt import menus
from pm.web.dt.backlog import plural
from pm.web.dt.ntfy import cut
from pm.web.dt.shell import age

KIND_LABELS = {"js": "Script", "promise": "Promise", "htmx": "htmx", "fetch": "Request", "csp": "CSP"}
KIND_DOTS = {"fetch": "waiting", "htmx": "waiting"}  # a failed request; script errors and CSP are red


def num(v: int) -> str:
    """A count with thousands separators ("1,234")."""
    return f"{int(v or 0):,}"


def is_swap(ctx: Mapping) -> bool:
    """An htmx refresh of a component (not a full page): the head parts go out of band."""
    req = ctx.get("request")
    return req is not None and bool(req.headers.get("hx-request")) and not req.headers.get("hx-boosted")


@dataclass(frozen=True)
class ERow:
    id: str
    msg: str
    kind: str
    kind_label: str
    dot: str
    req: str  # "POST /x → 403" when the message does not say it already
    pages: str
    count: int
    last: str  # age ("5m")
    since: int | None
    stack: str
    kv: list
    meta: str
    issue_title: str
    issue_body: str
    menu: list = field(default_factory=list)


@dataclass(frozen=True)
class Ce:
    url: str
    q: str
    kind: str
    segs: list[dict]
    rows: list[ERow]
    total: int  # rows (messages) after both filters
    entries: int  # entries under the text filter, any kind
    page: int
    newer: str
    older: str
    empty: str
    stats: list
    clear: dict
    swap: bool
    issue: dict | None  # issue_action: the details' Save button (label, title, project / projects); None = none


def _req(g: Mapping) -> str:
    if not (g.get("method") or g.get("url")):
        return ""
    s = " ".join(x for x in (str(g.get("method") or ""), str(g.get("url") or "")) if x)
    return s + (f" → {g['status']}" if g.get("status") else "")


def _issue(g: Mapping, label: str, pages: list[str]) -> tuple[str, str]:
    lines = [f"A browser reported this error {g.get('count', 1)}× between {timefmt.fmt_full(g.get('first'))} and "
             f"{timefmt.fmt_full(g.get('last'))}.", "", f"- Kind: {label}", f"- Message: {g.get('msg') or ''}"]
    if _req(g):
        lines.append(f"- Request: {_req(g)}")
    if g.get("line"):
        lines.append(f"- Source: line {g.get('line')}:{g.get('col') or 0}")
    lines += [f"- Pages: {', '.join(pages) or '–'}", f"- Browser: {g.get('ua') or 'unknown'}"]
    if g.get("stack"):
        lines += ["", "Stack (masked):", "", "```", str(g["stack"]), "```"]
    lines += ["", "From pm › Audit › Client errors (paths only, values masked)."]
    return "Client error: " + cut(str(g.get("msg") or "(no message)"), 90), "\n".join(lines)


def issue_action(targets: Sequence[str]) -> dict | None:
    """The Save-as-issue button of every row (None when no project keeps issues in the tracker). One project: its
    name is in the label and ``project``; several: js/audit.js asks which one from ``projects`` (a JSON list)."""
    names = [str(t) for t in targets]
    if not names:
        return None
    if len(names) == 1:
        return {"label": f"Save as {names[0]} issue", "project": names[0], "projects": "",
                "title": f"Add an issue with this error to the {names[0]} project"}
    return {"label": "Save as issue…", "project": "", "projects": json.dumps(names),
            "title": "Add an issue with this error to a tracker project you pick"}


def erow(g: Mapping, now: float, issue: Mapping | None = None) -> ERow:
    kind = str(g.get("kind") or "")
    label = KIND_LABELS.get(kind, kind)
    pages = [str(p) for p in (g.get("pages") or ())]
    key = "\x1f".join(str(g.get(k, "")) for k in ("kind", "msg", "method", "url", "status"))
    rid = "ce" + hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:12]
    last = float(g.get("last") or 0)
    kv: list = [("Kind", label), ("Count", num(g.get("count", 0))), ("First seen", timefmt.fmt_full(g.get("first"))),
                ("Last seen", timefmt.fmt_full(g.get("last")) + (f" · {age(last, now)} ago" if last else ""))]
    if _req(g):
        kv.append(("Request", _req(g), "mono"))
    if g.get("line"):
        kv.append(("Source", f"line {g.get('line')}:{g.get('col') or 0}", "mono"))
    kv += [("Pages", ", ".join(pages) or "–", "mono"), ("Browser", str(g.get("ua") or "unknown")),
           ("Client", str((g.get("sample") or {}).get("client") or "–"), "mono")]
    title, body = _issue(g, label, pages)
    msg = str(g.get("msg") or "(no message)")
    copy = msg + ("\n" + str(g["stack"]) if g.get("stack") else "")
    menu = menus.menu(menus.details(), menus.SEP, menus.item("copy", "Copy message and stack", "copy", copy=copy),
                      menus.item("issue", issue["label"], "plus", event="au:issue") if issue else None)
    req = _req(g)
    return ERow(id=rid, msg=msg, kind=kind, kind_label=label, dot=KIND_DOTS.get(kind, "failed"),
                req="" if not req or str(g.get("url") or "") in msg else req, pages=", ".join(pages),
                count=int(g.get("count") or 0), last=age(last, now), since=int(last) if last else None,
                stack=str(g.get("stack") or ""), kv=kv,
                meta=" · ".join(x for x in (f"{num(g.get('count', 0))}×", pages[0] if pages else "", g.get("ua")) if x),
                issue_title=title, issue_body=body, menu=menu)


def ce_url(kind: str, q: str, page: int = 1, path: str = "/audit/client-errors") -> str:
    p: dict[str, str] = {}
    if path == "/audit":
        p["tab"] = "errors"
    if kind != "all":
        p["ce_kind"] = kind
    if q:
        p["q"] = q
    if page > 1:
        p["ce_page"] = str(page)
    return path + "?" + urlencode(p) if p else path


def ce_stats(g: Mapping) -> list:
    c = g.get("counts") or {}
    return [plural(c.get("all", 0), "client error"), plural(g.get("total", 0), "message"), "paths only, values masked"]


def clear_action() -> dict:
    """The Client errors tab's head action; js/audit.js runs its item (data-au-item) with the shared menu post
    (pm7.menus.exec: confirm saying the audit log is untouched, POST, toast after OK, pm7:refresh on #au-ce)."""
    it = menus.item("clear", "Clear all", "trash", danger=True, post="/audit/client-errors/clear",
                    confirm="Clear all client errors?", ok="Clear all", done="Client errors cleared", refresh="#au-ce",
                    body="Every report is removed from the client error log. The audit log is not touched.")
    return {"label": "Clear all", "icon": "trash", "danger": True,
            "title": "Remove every client error report (the audit log is not touched)",
            "attrs": {"data-au-clear": "", "data-au-item": json.dumps(it), "id": "au-clear"}}


def ce(ctx: Mapping) -> Ce:
    g = ctx.get("ce_groups") or {}
    kind, q = str(ctx.get("ce_kind") or "all"), str(ctx.get("ce_q") or "")
    counts = g.get("counts") or {}
    kinds = ("all", *(ctx.get("kinds") or ()))
    segs = [{"key": k, "label": "All" if k == "all" else KIND_LABELS.get(k, k), "href": ce_url(k, q, path="/audit"),
             "cur": kind == k, "count": num(counts.get(k, 0))} for k in kinds]
    page = int(g.get("page") or 1)
    now = time.time()
    issue = issue_action(ctx.get("issue_projects") or ())
    rows = [erow(r, now, issue) for r in g.get("rows") or ()]
    empty = ""
    if not rows:
        empty = ("Nothing matches." if q or kind != "all" else
                 "No client errors. Script errors and failed requests in any browser show here as they happen.")
    return Ce(url=ce_url(kind, q, page), q=q, kind=kind, segs=segs, rows=rows, total=int(g.get("total") or 0),
              entries=int(counts.get("all") or 0), page=page,
              newer=ce_url(kind, q, page - 1, "/audit") if page > 1 else "",
              older=ce_url(kind, q, page + 1, "/audit") if g.get("has_older") else "", empty=empty,
              stats=ce_stats(g), clear=clear_action(), swap=is_swap(ctx), issue=issue)
