"""Read-only (tier 0) tmux pages: the session list and its expandable window/pane rows.

Mounted inside ``pages.router`` (see ``web/routes/__init__.py``), not listed in ``ROUTERS`` on its own.
"""

from __future__ import annotations

import os
import re

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from pm.agents import sorting
from pm.infra import snapshot as services
from pm.web import stats
from pm.web.deps import AUTH, snapshot
from pm.web.listing import clean_q, keep, limit_ctx, limit_page, remember, remember_limit, sorted_page, wants_fragment
from pm.web.render import fragment, get_template, html, page_ctx, urlid

SESSION_URL_RE = re.compile(r"^s[0-9]+$")

router = APIRouter()


def _own_session_id(snap: services.Snapshot) -> str | None:
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return None
    for s in snap.sessions:
        for w in s.windows:
            if any(p.id == pane for p in w.panes):
                return s.id
    return None


def _sess_info(s, by_pane: dict) -> dict:
    panes = [p for w in s.windows for p in w.panes]
    ags = [by_pane[p.id] for p in panes if p.id in by_pane]
    last = max((sorting.active_ts(a) for a in ags), default=0.0)
    return {"windows": len(s.windows), "panes": len(panes), "agents": ags, "last": last or None}


@router.get("/tmux", response_class=HTMLResponse, dependencies=AUTH)
async def tmux_page(request: Request) -> Response:
    snap = await snapshot(request)
    legacy = request.query_params.get("s")
    if legacy:  # old/mock style /tmux?s=NAME -> the session page (by name, or by $id)
        hit = next((x for x in snap.sessions if x.name == legacy or x.id == legacy), None)
        if hit is not None:
            return RedirectResponse(_tmux_open_url(hit.id), status_code=303)
    lkey, llinks, lpersist = limit_page(request, "tmux", "/tmux")
    key, cols, self_url, persist = sorted_page(request, "tmux")
    ctx = _tmux_ctx(request, snap, key, lkey, llinks, cols, self_url)
    if wants_fragment(request):  # live filter / refresh: only the list
        return fragment(request, get_template("_tmux_list.html").render(**{**page_ctx(request), **ctx}))
    resp = html(request, "tmux.html", **ctx)
    remember_limit(resp, "tmux", lkey, lpersist)
    return remember(resp, "tmux", key, persist)


def _tmux_ctx(request: Request, snap, key: str, lkey: str, llinks: list, cols: dict, self_url: str) -> dict:
    """Template context of the session list: `q` filters (name, windows, agent labels) and `f` picks one segment
    (``sorting.TMUX_FILTERS``; `tmux_counts` per segment for the `q`) before the Show cap, `?open=sN` pins
    that session on the page even when the cap or q hides it."""
    q = clean_q(request)
    by_pane = {a.pane_id: a for a in snap.agents}
    info = {s.id: _sess_info(s, by_pane) for s in snap.sessions}
    ags = {i: v["agents"] for i, v in info.items()}
    queried = sorting.filter_sessions(snap.sessions, q, ags)
    flag = request.query_params.get("f", "")
    flag = flag if flag in dict(sorting.TMUX_FILTERS) else ""
    matched = sorting.filter_tmux(queried, flag, ags)
    shown = sorting.apply_limit(sorting.sort_sessions(matched, key, info), lkey)
    open_s = request.query_params.get("open", "")
    open_id = "$" + open_s[1:] if SESSION_URL_RE.fullmatch(open_s) else ""
    hit = snap.session(open_id) if open_id else None
    if hit is not None and all(x.id != hit.id for x in shown):
        shown = [*shown, hit]  # the opened session must be on the page even when the Show limit hides it
    return dict(
        nav="tmux", drawer=True, open_sid=hit.id if hit else "", own_sid=_own_session_id(snap), sessions=shown,
        errors=snap.errors, agent_by_pane=by_pane, sess_info=info, sess_rows={s.id: _session_rows(snap, s) for s in shown},
        cols=cols, self_url=self_url, limit_path="/tmux", keep_all=keep(request, "sort", "q", "f"), keep_sort=keep(request, "sort"),
        q=q, stats=stats.stats_tmux(snap), **limit_ctx(llinks, len(shown), len(matched)),
        tmux_filter=flag, tmux_filter_opts=[("", "All"), *sorting.TMUX_FILTERS],
        tmux_counts=sorting.tmux_counts(queried, ags),
    )


def _tmux_open_url(session_id: str) -> str:
    """The /tmux list with that session expanded (the old per-session page redirects here)."""
    return "/tmux?open=s" + session_id[1:]


def _project_for(cwd: str, projects) -> str | None:
    """Name of the project whose path contains cwd (longest match); None when no project does."""
    best = None
    for pr in projects:
        if cwd == pr.path or cwd.startswith(pr.path.rstrip("/") + "/"):
            if best is None or len(pr.path) > len(best.path):
                best = pr
    return best.name if best else None


@router.get("/tmux/{sid}", dependencies=AUTH)
async def tmux_session_page(request: Request, sid: str) -> Response:
    """The per-session page is gone: the windows table lives in the expanded row on /tmux."""
    if not SESSION_URL_RE.fullmatch(sid):
        raise HTTPException(404, "unknown session")
    snap = await snapshot(request)
    session = snap.session("$" + sid[1:])
    if session is None:
        raise HTTPException(404, "unknown session")
    return RedirectResponse(_tmux_open_url(session.id), status_code=303)


def _session_rows(snap: services.Snapshot, session) -> list[dict]:
    """One row per pane. `current` = the window tmux reports as the session's current one (the same
    TmuxSession.current_window_id as every other `*`); `first` = first pane row of its window."""
    by_pane = {a.pane_id: a for a in snap.agents}
    cur = session.current_window_id
    rows = []
    for w in session.windows:
        for i, p in enumerate(w.panes):
            rows.append({
                "window": w, "pane": p, "agent": by_pane.get(p.id),
                "project": _project_for(p.cwd, snap.projects),
                "current": w.id == cur, "first": i == 0,
            })
    return rows


@router.get("/tmux/{sid}/live", response_class=HTMLResponse, dependencies=AUTH)
async def tmux_session_live(request: Request, sid: str) -> Response:
    """The window/pane table of a session as a fragment (ETag, 204 if unchanged): polled by the page so
    the `*` and the highlight follow tmux's current window within a few seconds."""
    if not SESSION_URL_RE.fullmatch(sid):
        raise HTTPException(404, "unknown session")
    snap = await snapshot(request)
    session = snap.session("$" + sid[1:])
    if session is None:
        raise HTTPException(404, "unknown session")
    ctx = {**page_ctx(request), "session": session, "rows": _session_rows(snap, session), "sid_url": urlid(session.id),
           "own_sid": _own_session_id(snap)}  # pm's own session: themes disable its kill
    return fragment(request, get_template("_tmux_session_rows.html").render(**ctx))
