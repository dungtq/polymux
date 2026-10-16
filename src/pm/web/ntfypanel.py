"""Ntfy panel and needs-you inbox context, shared by web.routes.ntfy and web.routes.pages."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, urlsplit

from fastapi import HTTPException, Request

from pm.agents import detect, inbox, pinned, sorting
from pm.agents import head as agenthead
from pm.chat import live
from pm.chat import stats as chat_stats
from pm.core import executors, timefmt
from pm.core.models import AgentState
from pm.core.procstat import PROC
from pm.infra import snapshot as services
from pm.notify import alerts
from pm.notify import feed as ntfy_feed
from pm.notify import owner as ntfy_owner
from pm.notify import pins as ntfy_pins
from pm.projects import projects
from pm.security.guard import get_csrf
from pm.web import deps
from pm.web.render import ago, env, get_template, ts
from pm.web.stats import stats_ntfy

MAX_ROWS = 50  # flat lists (inbox/system), rendered server side; rows past the default count hide behind Expand (ended.js)
MAX_ROWS_ALL = 500  # rows on /ntfy (scope all); the "Show" select (ended.js) trims further, "All" is this cap
env.filters["ntfy_ago"] = ago


def safe_click(url: str, base_url: str) -> str:
    """Only https links on the same host as base_url; anything else is plain text."""
    try:
        u, b = urlsplit(url or ""), urlsplit(base_url)
    except ValueError:
        return ""
    if u.scheme != "https" or not u.hostname or u.hostname != b.hostname or u.username:
        return ""
    return url


def tag_info(item, proj_name: str | None) -> dict:
    """Raw tag (full, for the title attr) and the short label shown as a chip."""
    raw = next((t for t in item.tags if t.startswith("/")), None)
    if raw is None:
        raw = item.tags[0] if item.tags else ""
    parts = [x for x in raw.split("/") if x]
    short = "/".join(parts[-2:]) if raw.startswith("/") else raw
    return {"tag_raw": raw, "tag_label": proj_name or short, "tag_proj": bool(proj_name)}


def csrf(request: Request) -> str:
    sess = request.app.state.sessions.from_request(request)
    return get_csrf(request).token(sess.id) if sess else ""


def shown_title(title: str, owner) -> str:
    """Title without its leading `<agent id> - ` when the row already links that agent."""
    head = f"{owner.agent_id} - " if owner else ""
    return title[len(head):] if head and title.startswith(head) else title


# "all" scope: one list and a tab bar (ended.js). Every row carries the periods it falls in (data-p); the active
# tab hides the rows without it client side. Cumulative, except last_week and last_month, which exclude
# this week / this month.
TABS = (("today", "Today"), ("week", "This week"), ("last_week", "Last week"), ("month", "This month"),
        ("last_month", "Last month"), ("year", "This year"), ("all", "All time"))
SHOW_COUNTS = (10, 20, 50)


def _rows(groups, owners: dict, store, names: dict, settings, recovered: dict | None = None) -> dict:
    """{group key: row dict} for the template. Besides what classic shows, a row has ``day`` ('today' |
    'yesterday' | 'earlier', display tz), ``src`` ('agent' | 'system') and ``recovered_after_s`` (a "pm recovered"
    message: seconds since its alert, ``alerts.recovered_after``; else None)."""
    rows, now, recovered = {}, time.time(), recovered or {}
    for g in groups:
        o, i = owners.get(g.key), g.item
        r = {
            "owner": o, "title": shown_title(i.title, o),
            "item": i, "click": safe_click(i.click, settings.base_url),
            "proj_name": names.get(i.project or ""), "abs": ts(i.time), "stamp": timefmt.fmt_dt(i.time) if i.time else "",
            "ago": ago(i.time) if i.time else "",
            "key": g.key, "n": g.count, "unread": store.unread(g.key, g.ts),
            "day": timefmt.day_of(i.time, now), "src": "system" if inbox.is_system(i) else "agent",
            "recovered_after_s": recovered.get(i.id),
            **tag_info(i, names.get(i.project or "")),
        }
        r["q"] = sorting.q_text(i.title, r["proj_name"], r["tag_label"],  # filter text: names, never the message
                                getattr(o, "label", None), getattr(o, "short", None), getattr(o, "agent_id", None))
        rows[g.key] = r
    return rows


async def _pin_rows(settings, scope: str, items: list, store, names: dict) -> tuple[list, set]:
    """(pinned rows, pinned ids) for the "all" scope. Rows are keyed "pin:<id>" so they never clash with the list;
    only pins inside the feed window get an agent link. Pins are not unread and not counted."""
    if scope != "all":
        return [], set()
    pinned = await executors.run_collectors(ntfy_pins.load, settings)
    if not pinned:
        return [], set()
    oldest = min((i.time for i in items), default=None)
    inside = [p for p in pinned if oldest is not None and p.time >= oldest]

    def owner_map():
        index = ntfy_owner.build_index(settings, min(p.time for p in inside))
        recs = ntfy_owner.title_records(settings, (p.title for p in inside))
        return {p.id: ntfy_owner.owner_for(settings, index, p.title, p.time, recs) for p in inside}
    owners = await executors.run_collectors(owner_map) if inside else {}
    groups = [SimpleNamespace(key="pin:" + p.id, item=p, count=1, ts=p.time) for p in pinned]
    rows = _rows(groups, {"pin:" + k: o for k, o in owners.items()}, store, names, settings)
    for r in rows.values():
        r["unread"] = False
    return list(rows.values()), {p.id for p in pinned}


async def build_panel(request: Request, scope: str = "inbox",
                      n: str | None = None, all: str | None = None) -> tuple[str, str | None]:
    """Rendered panel HTML plus, for scope "all", the heading stats (n/all params are old links, ignored)."""
    if scope not in ("inbox", "all", "system"):
        raise HTTPException(404, "unknown scope")
    settings = deps.settings(request)
    services.touch()
    visible = max(1, settings.ntfy_inbox_count)

    def collect():
        """One hop to the collectors pool (the feed may wait up to a second on its first fetch)."""
        plist = projects.discover(settings)
        return plist, ntfy_feed.recent(settings), ntfy_feed.fetch_ok(settings)

    plist, items, ok = await executors.run_collectors(collect)
    store = inbox.load(settings.state_dir)
    groups = inbox.group_ntfy(items)  # one row per agent+event
    if scope != "all":  # "all" keeps agent and system groups; system messages otherwise live in the bell
        groups = [g for g in groups if inbox.is_system(g.item) == (scope == "system")]
    if scope in ("inbox", "all"):
        groups = [g for g in groups if not store.dismissed(g.key, g.ts)]
    total, now = len(groups), time.time()
    sets = {g.key: timefmt.periods_of(g.ts, now) for g in groups} if scope == "all" else {}
    shown = groups[:MAX_ROWS_ALL if scope == "all" else MAX_ROWS]
    owners, recovered = {}, {}
    if shown:
        def owner_map():
            index = ntfy_owner.build_index(settings, min(g.item.time for g in shown))
            recs = ntfy_owner.title_records(settings, (g.item.title for g in shown))
            own = {g.key: ntfy_owner.owner_for(settings, index, g.item.title, g.item.time, recs) for g in shown}
            return own, alerts.recovered_after(items, {g.item.id for g in shown})
        owners, recovered = await executors.run_collectors(owner_map)
    names = {p.path: p.name for p in plist}
    rows = _rows(shown, owners, store, names, settings, recovered)
    pins, pinned_ids = await _pin_rows(settings, scope, items, store, names)
    for k, r in rows.items():
        r["p"] = " ".join(sets.get(k, ()))
        r["pinned"] = r["item"].id in pinned_ids
    unread = sum(1 for g in groups if store.unread(g.key, g.ts)) if scope == "all" else sum(1 for r in rows.values() if r["unread"])
    html = get_template("_ntfy.html").render(
        rows=list(rows.values()), pins=pins, scope=scope, visible=visible, ok=ok, total=total, csrf=csrf(request), unread=unread,
        base=f"/ntfy/panel?scope={scope}", now=now, tabs=TABS,
        counts=sorted({*SHOW_COUNTS, visible}),
    )
    stats = stats_ntfy(total, unread, sum(1 for k in sets if "today" in sets[k])) if scope == "all" else None
    return html, stats


BELL_ROWS = 5
BELL_MAX_ROWS = 10


async def build_bell(request: Request, n: int = BELL_ROWS, unread_first: bool = False) -> tuple[str, int]:
    """Topbar bell dropdown: the last ``n`` system messages (no agent; unread ones first with ``unread_first``)
    plus the unread count. Classic asks for the defaults."""
    settings = deps.settings(request)
    inbox.ensure_baseline(settings.state_dir)
    items = await executors.run_collectors(lambda: ntfy_feed.recent(settings))
    store = inbox.load(settings.state_dir)
    now = time.time()
    groups = [g for g in inbox.group_ntfy(items) if inbox.is_system(g.item)]
    unread = sum(1 for g in groups if inbox.ntfy_unread(store, g.key, g.ts, now))
    pick = sorted(groups, key=lambda g: not store.unread(g.key, g.ts)) if unread_first else groups  # stable
    rows = [{"key": g.key, "title": g.item.title, "message": g.item.message, "priority": g.item.priority,
             "ago": ago(g.item.time), "abs": ts(g.item.time), "unread": store.unread(g.key, g.ts),
             "click": safe_click(g.item.click, settings.base_url)} for g in pick[:max(1, min(n, BELL_MAX_ROWS))]]
    html = get_template("_bell.html").render(rows=rows, unread=unread, total=len(groups), csrf=csrf(request))
    return html, unread


# ------------------------------------------------------------------ needs-you inbox
MAX_QUESTIONS = 8


def agent_feeds(settings, ids, store, now: float) -> dict:
    """{agent id: notify.owner.AgentFeed (newest item, unread count)}: one owner-mapping pass for the agent rows."""
    return ntfy_owner.agent_feed(settings, ids, unread=lambda i: inbox.item_unread(store, i, now))


async def _pane_lines(settings, agents) -> tuple[dict, dict]:
    """({id: (pending question, its numbered options)}, {id: error line}) of the first MAX_QUESTIONS waiting and
    failed agents (each costs a pane read; cached per event, pane and state start). Masked, ANSI-stripped pane
    text only."""
    async def line(a, event):
        empty = "" if event == "failed" else ("", [])

        def fetch():
            pane = services.pane_text(settings, a.pane_id, live.PANE_LINES)
            if not pane:
                return empty
            if event == "failed":
                return inbox.error_of(pane)
            # chat_view merges the transcript's AskUserQuestion form: the current question, not the footer
            return inbox.ask_of(pane, asking=live.chat_view(settings, a, pane).asking)
        try:
            return a.id, await executors.run_tmux(
                inbox.cached_question, a.pane_id, a.state_since, fetch, event=event)
        except Exception:  # noqa: BLE001 - the row works without the question
            return a.id, empty

    waiting = [a for a in agents if a.state is AgentState.WAITING][:MAX_QUESTIONS]
    failed = [a for a in agents if a.state is AgentState.FAILED][:MAX_QUESTIONS]
    got = await asyncio.gather(*(line(a, "waiting") for a in waiting), *(line(a, "failed") for a in failed))
    return dict(got[:len(waiting)]), dict(got[len(waiting):])


def row_model(a) -> str:
    """The short model the agent page heading shows (``agents.head.live_pill``: transcript, process args, Claude's
    settings; never a pane read); '' when only the kind is known."""
    m = agenthead.live_pill(a)
    return "" if m == agenthead.model_short(a.kind.value) else m


async def _row_extras(settings, agents, feed: dict) -> dict:
    """{id: (ctx percent or None, last message dict or None, model)} in one hop to the collectors pool (file reads).
    The Devin lock map is read at most once for all rows (only when a Devin row is shown)."""
    def work():
        out, locks = {}, detect.Locks(Path.home(), PROC)
        for a in agents:
            f = feed.get(a.id)
            out[a.id] = (chat_stats.ctx_pct(a, locks.for_kind(a.kind)), inbox.last_message(a, f.item if f else None),
                         row_model(a))
        return out
    try:
        return await executors.run_collectors(work)
    except Exception:  # noqa: BLE001 - the rows work without them
        return {}


async def row_meta(request: Request, agents, store, now: float, win: float, pins=(), feed: dict | None = None) -> dict:
    """{agent id: meta} for the shared agent row (Inbox and project page). meta: ``key`` (read key), ``unread``
    (waiting / finished bucket and not read since, or marked unread in any bucket), ``q`` (pending question of a
    waiting agent) and ``err`` (error line of a failed one; the first MAX_QUESTIONS of each), ``pinned`` (id in
    ``pins``), ``ctx_pct`` (context fill, None = unknown), ``unread_n`` (unread ntfy messages of the agent),
    ``last_src`` ('user' | 'ntfy' | ''), ``last_text`` and ``last_ts`` (``inbox.last_message``), ``model`` (short
    model of the page heading, '' = unknown, ``row_model``) and ``options`` (numbered options of ``q``:
    ``[{"n", "label"}]``, ``inbox.options_of``). ``feed``: the caller's ``agent_feeds`` result (one
    owner-mapping pass per page); None maps the feed here."""
    settings = deps.settings(request)
    agents = list(agents)
    if feed is None and agents:
        try:
            feed = await executors.run_collectors(agent_feeds, settings, {a.id for a in agents}, store, now)
        except Exception:  # noqa: BLE001 - bad feed: no counts, no ntfy text
            feed = {}
    feed = feed or {}
    questions, errors = await _pane_lines(settings, agents)
    extras = await _row_extras(settings, agents, feed)
    meta = {}
    for a in agents:
        key = inbox.agent_key(a)
        f = feed.get(a.id)
        pct, last, model = extras.get(a.id, (None, None, ""))
        q, options = questions.get(a.id, ("", []))
        meta[a.id] = {"key": key, "q": q, "options": options, "model": model, "err": errors.get(a.id, ""),
                      "pinned": a.id in pins,
                      "unread": store.marked_unread(key) or (inbox.bucket(a, store, now, win) in ("waiting", "finished")
                                                             and store.unread(key, inbox.agent_ts(a))),
                      "ctx_pct": pct, "unread_n": f.unread if f else 0, "last_src": last["src"] if last else "",
                      "last_text": last["text"] if last else "", "last_ts": last["ts"] if last else 0.0}
    return meta


async def needs_you(request: Request, snap, matched, sort_key: str, limit_key: str, feed: dict | None = None) -> dict:
    """Context for the state-bucketed Inbox: sections (pinned first), row meta, unread count. ``feed``: see
    ``row_meta``."""
    settings = deps.settings(request)
    inbox.ensure_baseline(settings.state_dir)
    store = inbox.load(settings.state_dir)
    now = time.time()
    win = inbox.idle_window(settings)
    pins = await executors.run_collectors(pinned.load, settings)
    sections = inbox.group_agents(matched, store, now, lambda xs: sorting.sort_agents(xs, sort_key), win, pins)
    for sec in sections:
        sec["agents"] = sorting.apply_limit(sec["agents"], limit_key)
    sections = [sec for sec in sections if sec["total"]]
    meta = await row_meta(request, [a for sec in sections for a in sec["agents"]], store, now, win, pins, feed)
    items = await executors.run_collectors(lambda: ntfy_feed.recent(settings))
    unread = inbox.unread_count(snap.agents, inbox.group_ntfy(items), store, now, win)
    known = {p.name for p in snap.projects}
    recent = sorted((a for a in snap.agents if a.project in known),
                    key=lambda a: -(a.last_active or a.state_since))
    launch = f"/p/{quote(recent[0].project)}/launch?from=inbox" if recent else "/p"
    return {"sections": sections, "inbox_meta": meta, "unread": unread, "launch_href": launch,
            "csrf": csrf(request), "shown": sum(len(s["agents"]) for s in sections)}
