"""Web helpers for tracker projects (docs/TRACKER-PLAN.md) shared by the route modules (issues, backlog, todo):
item numbers posted by the pages, the ``audit=`` callable the routes hand to the tracker mutators, and the
"Send to agent" links, and which agent an issue was dispatched to. The domain (summaries, toggle, project checks, issue creation) is ``pm.projects.tracker``."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from urllib.parse import quote

from fastapi import Request

from pm.agents import launch as launch_mod
from pm.core import prefs
from pm.core.errors import Invalid
from pm.security.guard import audit_result

FILE = "backlog"


def num_of(value) -> int:
    """An item number posted by a page (the ``line`` field of the checkbox forms)."""
    try:
        n = int(str(value))
    except ValueError:
        raise Invalid("bad item number") from None
    if n < 1:
        raise Invalid("bad item number")
    return n


def audit_ok(request: Request, action: str, target: str, detail: dict,
             keep: tuple[str, ...] = ("num", "images")) -> Callable[[str, dict], None]:
    """The ``audit=`` callable for a tracker mutator: writes the route's one "ok" line (``action``, ``detail``
    plus the ``keep`` keys of the store's detail: issue number, image count, never text) inside the store's
    transaction, so a failed audit write (AuditError) rolls the change back."""
    def write(_action: str, d: dict) -> None:
        audit_result(request, action, target, "ok", {**detail, **{k: d[k] for k in keep if k in d}})
    return write


def send_links(agents, name: str, num: int, title: str) -> list[dict]:
    """"Send to agent": the running agents of the project, each with a composer draft link."""
    draft = quote(f"Fix issue #{num}: {title}\n\nDetails: pmctl issue show {num}", safe="")
    return [{"id": a.id, "label": a.label or a.id, "state": a.state.value, "href": f"/a/{a.id}#draft={draft}"}
            for a in agents if a.project == name]


DISPATCH_GRACE_S = 120  # a just launched agent counts as on the issue before the snapshot shows it


def issue_key(num: int) -> str:
    """Dispatch mark key of issue ``num`` (``prefs.mark_dispatched``: per project, 16 hex)."""
    return launch_mod.item_key(f"issue #{int(num)}")


def agents_on(settings, agents: Iterable, rows: Iterable[dict], now: float | None = None) -> dict:
    """{(project, num): {id, state}} of the issue rows (``project``, ``num``) dispatched to an agent that is
    still alive (by pane or id in ``agents``; state "starting" within ``DISPATCH_GRACE_S`` of the dispatch
    while the snapshot does not show it yet). One read of the dispatch marks for all rows."""
    marks = prefs.dispatched_all(settings.prefs_file)
    if not marks:
        return {}
    now = time.time() if now is None else now
    by_pane = {a.pane_id: a for a in agents}
    by_id = {a.id: a for a in agents}
    out: dict = {}
    for r in rows:
        m = marks.get(r["project"], {}).get(issue_key(r["num"]))
        if not isinstance(m, dict):
            continue
        aid = str(m.get("agent") or "")
        a = by_pane.get(str(m.get("pane") or "")) or by_id.get(aid)
        if a is not None:
            out[(r["project"], r["num"])] = {"id": a.id, "state": a.state.value}
        elif aid and now - float(m.get("ts") or 0) < DISPATCH_GRACE_S:
            out[(r["project"], r["num"])] = {"id": aid, "state": "starting"}
    return out
