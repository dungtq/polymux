"""Read-only (tier 0) HTML pages. Mount ``STATIC_DIR`` at ``/static`` in app.py.

``router`` also carries the tmux pages (``tmuxpages``) and the project doc pane (``projectdocs``):
``web/routes/__init__.py`` includes them into it, so ``pages.router`` alone serves every page.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from pm.agents import head as agenthead
from pm.agents import inbox, lineage, pinned, registry, sorting
from pm.agents import launch as launch_mod
from pm.agents.message import QUICK_KEYS
from pm.core import executors
from pm.core.config import Settings
from pm.core.models import AgentKind
from pm.infra import snapshot as services
from pm.infra import vmops
from pm.projects import projects as projects_mod
from pm.projects import settings as pset
from pm.projects.tracker import views as tracker_views
from pm.web import deps, plugins, settings_panel, stats, themes
from pm.web.chatctx import composer_ctx
from pm.web.deps import AGENT_ID_RE, AUTH, agent_or_404, project_sidebar, side_unread, snapshot
from pm.web.docpane import project_file_html as _project_file_html
from pm.web.launchctx import handoff_prompt as _handoff_prompt
from pm.web.launchctx import launch_data as _launch_data
from pm.web.launchctx import remembered as _remembered_launch
from pm.web.launchctx import todo_rows as _launch_todo_rows
from pm.web.listing import clean_q as _clean_q
from pm.web.listing import keep as _keep
from pm.web.listing import limit_ctx as _limit_ctx
from pm.web.listing import limit_page as _limit_page
from pm.web.listing import remember as _remember
from pm.web.listing import remember_limit as _remember_limit
from pm.web.listing import sorted_page as _sorted_page
from pm.web.listing import wants_fragment as _wants_fragment
from pm.web.render import STATIC_DIR, fragment, get_template, html, is_embed, page_ctx, term_ok, urlid

HASH_RE = re.compile(r"^[0-9a-f]{8,64}$")

router = APIRouter()


# Chat tab shows only the essentials; the Pane tab shows the full pad (QUICK_KEYS).
KILL_KEYS = tuple(k for k in ("y", "n", "1", "2", "3", "Enter", "Escape", "C-c") if k in QUICK_KEYS)
_KEY_LABELS = {
    "Escape": "Esc", "C-c": "Ctrl-C", "C-d": "Ctrl-D", "C-l": "Ctrl-L", "C-x": "Ctrl-X", "C-s": "Ctrl-S", "BTab": "Shift-Tab",
    "BSpace": "Bksp", "PageUp": "PgUp", "PageDown": "PgDn",
}
_KEY_GROUPS = (
    ("Answers", ("y", "n", "1", "2", "3", "4", "5", "6", "7", "8", "9")),
    ("Navigation", ("Up", "Down", "Left", "Right", "Home", "End", "PageUp", "PageDown")),
    ("Editing", ("Enter", "Escape", "Tab", "BTab", "BSpace", "Space")),
    ("Control", ("C-c", "C-d", "C-l", "C-x", "C-s")),
)


def key_groups() -> list[tuple[str, list[tuple[str, str]]]]:
    """Group every key in QUICK_KEYS (never a second copy); leftovers go to Other."""
    out, seen = [], set()
    for title, keys in _KEY_GROUPS:
        items = [(k, _KEY_LABELS.get(k, k)) for k in keys if k in QUICK_KEYS]
        seen.update(k for k, _ in items)
        if items:
            out.append((title, items))
    rest = [(k, _KEY_LABELS.get(k, k)) for k in QUICK_KEYS if k not in seen]
    if rest:
        out.append(("Other", rest))
    return out


def trim_pane(text: str | None) -> str | None:
    """Drop the blank rows tmux pads below a TUI (they pushed the pinned view to an empty box)."""
    if text is None:
        return None
    return text.rstrip()


def _snippets(settings: Settings) -> list[str]:
    try:
        from pm.agents.message import load_snippets

        return [label for label, _ in load_snippets(settings)]
    except Exception:  # noqa: BLE001 - the page works without snippets
        return []


NTFY_COUNTS_TIMEOUT_S = 1.0


async def _ntfy_counts(settings: Settings) -> dict[str, int]:
    """{project path: ntfy message count}; {} when the feed is missing, slow or broken."""
    def _call() -> dict[str, int]:
        from pm.notify import feed as ntfy_feed  # lazy: sibling module may not exist yet

        return dict(ntfy_feed.counts_by_project(settings))

    try:
        res = await asyncio.wait_for(executors.run_collectors(_call), NTFY_COUNTS_TIMEOUT_S)
        return {str(k): int(v) for k, v in res.items()}
    except Exception:  # noqa: BLE001 - ImportError, timeout, bad data: page still works
        return {}


INBOX_VIEWS = ("active", "idle", "ended")


@router.get("/", response_class=HTMLResponse, dependencies=AUTH)
async def inbox_page(request: Request) -> Response:
    snap = await snapshot(request)
    key, cols, self_url, persist = _sorted_page(request, "inbox")
    lkey, llinks, lpersist = _limit_page(request, "inbox", "/")
    counts = await _ntfy_counts(deps.settings(request))
    q = _clean_q(request)
    matched = sorting.filter_agents(snap.agents, q, _model_of)
    agents = sorting.sort_agents(matched, key)
    from pm.web.ntfypanel import needs_you

    ended = await _ended_ctx(deps.settings(request), snap, live_ids=[a.id for a in snap.agents], q=q)
    need = await needs_you(request, snap, matched, key, lkey, ended["agent_feed"])
    sections, shown = need["sections"], need["shown"]
    waiting = sum(1 for a in agents if a.state.value == "waiting")
    view = request.query_params.get("view", "")
    view = view if view in INBOX_VIEWS else ""  # "" = everything; else one section as its own page
    ctx = dict(
        nav="inbox", inbox_view=view, agents=agents, sections=sections, errors=snap.errors, now=time.time(),
        cols=cols, sort_url=self_url, waiting_count=waiting, limit_path="/", keep_all=_keep(request, "sort", "q", "view"), stop_agents=list(snap.agents),
        waiting_href="/?" + urlencode({"sort": "state", **({"q": q} if q else {})}),
        stats=stats.stats_inbox(replace(snap, agents=tuple(matched)), counts,
                                len(snap.agents) if q else None, inbox.idle_window(deps.settings(request))),
        q=q, q_hidden=_keep(request, "sort", "view"),
        inbox_meta=need["inbox_meta"], unread=need["unread"], csrf=need["csrf"], launch_href=need["launch_href"],
        **_limit_ctx(llinks, shown, len(agents)), **ended,
    )
    if _wants_fragment(request):  # poll/SSE refresh: only the list
        return fragment(request, get_template("_inbox.html").render(**ctx))
    resp = html(request, "inbox.html", drawer=True, **ctx)
    _remember_limit(resp, "inbox", lkey, lpersist)
    return _remember(resp, "inbox", key, persist)


@router.get("/p", response_class=HTMLResponse, dependencies=AUTH)
async def projects_page(request: Request) -> Response:
    snap = await snapshot(request)
    key, cols, self_url, persist = _sorted_page(request, "projects")
    lkey, llinks, lpersist = _limit_page(request, "projects", "/p")
    ctx = await _projects_ctx(request, snap, cols, key, lkey, llinks, self_url)
    if _wants_fragment(request):  # live filter / refresh: only the list
        return fragment(request, get_template("_projects_list.html").render(**{**page_ctx(request), **ctx}))
    resp = html(request, "projects.html", **ctx)
    _remember_limit(resp, "projects", lkey, lpersist)
    return _remember(resp, "projects", key, persist)


async def _projects_ctx(request: Request, snap, cols: dict, key: str, lkey: str, llinks: list, self_url: str) -> dict:
    by_id = {a.id: a for a in snap.agents}
    counts = await _ntfy_counts(deps.settings(request))
    settings = deps.settings(request)
    pins = projects_mod.load_pins(settings)
    q = _clean_q(request)
    flags = [f for f in (request.query_params.get("f") or "").split(",") if f in dict(sorting.FILTERS)]
    flags = list(dict.fromkeys(flags))
    visible = sorting.filter_projects(snap.projects, q, flags, pins)
    issue_counts = await executors.run_collectors(tracker_views.open_issue_counts, settings)
    ordered = sorting.pinned_first(sorting.sort_projects(visible, key, by_id, counts, issue_counts), pins)
    shown = sorting.apply_limit(ordered, lkey)
    cur = ",".join(flags)
    filter_opts = [("", "All")] + list(sorting.FILTERS)
    if len(flags) > 1:  # legacy multi-filter URL: keep it selectable
        filter_opts.append((cur, " + ".join(dict(sorting.FILTERS)[f] for f in flags)))
    return dict(
        nav="projects", projects=shown, cols=cols, self_url=self_url,
        activity={p.name: sorting.activity(p, by_id) for p in shown},
        limit_path="/p", keep_all=_keep(request, "sort", "q", "f"), keep_sort=_keep(request, "sort"),
        ntfy_counts=counts, stats=stats.stats_projects(snap, counts), pins=pins,
        q=q, flags=flags,
        filter_opts=filter_opts, filter_cur=cur, total_all=len(snap.projects),
        seg_counts=sorting.filter_counts(snap.projects, q, pins), issue_counts=issue_counts,
        **_limit_ctx(llinks, len(shown), len(ordered)),
    )


def _model_of(x) -> str:
    """Short model name of a live Agent or an ended Record, for the list filter text."""
    return agenthead.pill_model(x.transcript)


async def _ended_ctx(settings, snap, project: str | None = None, limit: int = 50, live_ids=(), q: str = "") -> dict:
    """Ended sessions (matching `q` before the cap) plus `agent_ntfy` ({live agent id: NtfyItem}) and `agent_feed`
    ({live agent id: notify.owner.AgentFeed}, for ``ntfypanel.row_meta``); one feed fetch and owner mapping serve all."""
    live = {i for a in snap.agents for i in (a.ident, a.session_uuid) if i}
    rows, total = registry.ended_sessions(settings, project=project, live_idents=live, limit=limit,
                                          q=q, model_of=_model_of)
    feed: dict = {}
    live_ids = set(live_ids)
    if rows or live_ids:
        try:
            from pm.web.ntfypanel import agent_feeds

            store = inbox.load(settings.state_dir)
            feed = await asyncio.wait_for(
                executors.run_collectors(agent_feeds, settings, {r.id for r in rows} | live_ids, store, time.time()),
                NTFY_COUNTS_TIMEOUT_S * 2)
        except Exception:  # noqa: BLE001 - timeout, bad feed: rows just show no ntfy text
            feed = {}
    return {"ended": rows, "ended_total": total, "ended_ntfy": {r.id: feed[r.id].item for r in rows if r.id in feed},
            "agent_ntfy": {i: feed[i].item for i in live_ids if i in feed},
            "agent_feed": {i: feed[i] for i in live_ids if i in feed},
            "ended_models": {r.id: agenthead.pill_model(r.transcript) for r in rows}}


def _split_idle(request, agents):
    """(active, idle) by the Inbox rule (inbox.bucket): idle = last response >= settings.idle_after_s ago."""
    settings = deps.settings(request)
    store, now, win = inbox.load(settings.state_dir), time.time(), inbox.idle_window(settings)
    idle = [a for a in agents if inbox.bucket(a, store, now, win) == "idle"]
    ids = {a.id for a in idle}
    return [a for a in agents if a.id not in ids], idle


def _project_live_ctx(snap, project):
    agents = [a for a in snap.agents if a.id in project.agent_ids]
    # a session is shown when it holds a live pane of this project; all its windows are listed
    live = set(project.pane_ids)
    sessions = []
    for s in (snap.session(i) for i in project.sessions):
        if s is None:
            continue
        if any(p.id in live for w in s.windows for p in w.panes):
            sessions.append(s)
    return agents, sessions


@router.get("/p/{name}/dirty", response_class=HTMLResponse, dependencies=AUTH)
async def project_dirty(request: Request, name: str) -> Response:
    """Changed files of one project (lazy fragment for the expandable list)."""
    snap = await snapshot(request)
    project = snap.project(name)
    if project is None:
        raise HTTPException(404, "unknown project")
    from pm.git import ctl as gitctl

    files, total = await executors.run_collectors(gitctl.dirty_files, project.path)
    return HTMLResponse(
        get_template("_dirty.html").render(files=files, total=total),
        headers={"Cache-Control": "no-store"},
    )


async def _project_rows_ctx(request: Request, snap, project) -> dict:
    """Agent rows of the project page and its live fragment: pinned / active / idle (Inbox rule), the shared row meta
    (unread, pending question, pinned), tmux sessions and Ended sessions."""
    from pm.web.ntfypanel import row_meta

    settings = deps.settings(request)
    agents, sessions = _project_live_ctx(snap, project)
    pins = await executors.run_collectors(pinned.load, settings)
    active, idle = _split_idle(request, [a for a in agents if a.id not in pins])
    inbox.ensure_baseline(settings.state_dir)
    store, now = inbox.load(settings.state_dir), time.time()
    ended = await _ended_ctx(settings, snap, project.name, live_ids=[a.id for a in agents])
    meta = await row_meta(request, agents, store, now, inbox.idle_window(settings), pins, ended["agent_feed"])
    return {"agents": active, "idle_agents": idle, "pinned_agents": [a for a in agents if a.id in pins],
            "inbox_meta": meta, "sessions": sessions, "all_agents": snap.agents, "now": now, **ended}


@router.get("/p/{name}/live", response_class=HTMLResponse, dependencies=AUTH)
async def project_live(request: Request, name: str) -> Response:
    """Agents + tmux fragment of the project page; refreshed on pm:agent_state etc. (ETag, 204 if unchanged)."""
    snap = await snapshot(request)
    project = snap.project(name)
    if project is None:
        raise HTTPException(404, "unknown project")
    return fragment(request, get_template("_project_live.html").render(
        project=project, csrf=page_ctx(request)["csrf"], hid=pset.load(deps.settings(request), project.name).hidden,
        **await _project_rows_ctx(request, snap, project)))


async def _project_summaries(settings, project) -> dict:
    """Backlog (or Issues)/TODO/plans summaries and the last commits for the project page."""
    from pm.git import ctl as gitctl
    from pm.git import remote as gitremote
    from pm.projects.plans import summary as plans_summary
    from pm.projects.tracker.views import issues_summary as project_summary
    from pm.projects.tracker.views import todo_summary

    return {
        "backlog": await executors.run_collectors(project_summary, settings, project.name),
        "todo": await executors.run_collectors(todo_summary, settings, project.name),
        "plans": await executors.run_collectors(plans_summary, settings, project.name),
        "commits": await executors.run_collectors(gitctl.commit_log, project.path, 5),
        "commit_base": await executors.run_collectors(gitremote.commit_url_base, project.path,
                                                      settings.git_allowed_hosts, remote=settings.push_remote),
    }


async def _project_tabs(path: str, hidden=frozenset()) -> dict:
    """Doc pane tabs: the default one (CHANGELOG, then README; hidden ones skipped) and which optional files exist."""
    first_key, first_html = "", ""
    keys = [k for k in ("changelog", "readme") if k not in hidden]  # default tab: CHANGELOG, then README
    for k in keys:
        h, found = await executors.run_collectors(_project_file_html, path, k)
        if found:
            first_key, first_html = k, h
            break
    else:
        if keys:
            first_key, first_html = keys[0], (await executors.run_collectors(_project_file_html, path, keys[0]))[0]
    has = {k: (await executors.run_collectors(_project_file_html, path, k))[1] for k in ("features", "claude", "agents")}
    return {"has_features": has["features"], "has_claude": has["claude"], "has_agents": has["agents"],
            "first_key": first_key, "first_html": first_html}


@router.get("/p/{name}", response_class=HTMLResponse, dependencies=AUTH)
async def project_page(request: Request, name: str) -> Response:
    snap = await snapshot(request)
    project = snap.project(name)  # only known names; anything else is 404
    if project is None:
        raise HTTPException(404, "unknown project")
    settings = deps.settings(request)
    summaries = await _project_summaries(settings, project)
    ps = pset.load(settings, project.name)
    tabs = await _project_tabs(project.path, ps.hidden)
    return html(
        request,
        "project.html",
        **tabs,
        **summaries,
        nav="projects",
        project=project,
        sidebar=project_sidebar(snap, project.name, await side_unread(request, snap, "projects")),
        drawer=True,
        **await _project_rows_ctx(request, snap, project),
        live_url=f"/p/{quote(project.name)}/live",
        ext_groups=plugins.project_link_groups(plugins.of(request), project),
        stop_agents=[a for a in snap.agents if a.project == project.name],
        ps=ps,
        linked=[n for n in ps.links if snap.project(n) is not None],
        vm_count=vmops.cached_count(ps.vms),  # None: no VM patterns or no cached listing yet
    )


def _term_embed(settings, session, sid, wid) -> str | None:
    """Same-page terminal tab: the read-only /term page in an iframe (embed=1 hides its chrome)."""
    from urllib.parse import urlencode

    if not getattr(settings, "terminal_enabled", False) or session is None or not term_ok(session.name):
        return None
    q = {"embed": "1"}
    if isinstance(wid, str) and wid[:1] == "@" and wid[1:].isdigit():
        q["w"] = urlid(wid)
    return f"/term/{urlid(sid)}?{urlencode(q)}"


def _composer(settings) -> dict:
    from pm.core import prefs  # noqa: PLC0415

    return prefs.composer_prefs(settings)


async def _ended_agent_page(request: Request, snap, settings, res) -> Response:
    """The page of an agent that has exited (resume / now-in-pane links)."""
    rec = res.ended
    from pm.web.agentsidebar import agent_sidebar

    ended = registry.ended_agent(rec)
    return html(request, "agent_ended.html", nav="inbox",
                 embed=is_embed(request), agent=ended, rec=rec,
                 sidebar=None if is_embed(request) else await agent_sidebar(snap, settings, ended),
                 now_in_pane=res.now_in_pane,
                 origin=await executors.run_collectors(lineage.of, settings, rec.id),
                 resumable=bool(registry.UUID_RE.fullmatch(rec.ident or "")),
                 project=snap.project(rec.project) if rec.project else None)


def _term_ids(snap, agent) -> tuple[str, str | None]:
    """(session id, window id) holding the agent's pane, for the Terminal tab."""
    term_session_id, term_window_id = agent.session_id, None
    for sess in snap.sessions:
        for win in sess.windows:
            if any(pn.id == agent.pane_id for pn in win.panes):
                term_session_id, term_window_id = sess.id, win.id
    return term_session_id, term_window_id


@router.get("/a/{agent_id}", response_class=HTMLResponse, dependencies=AUTH)
async def agent_page(request: Request, agent_id: str) -> Response:
    snap = await snapshot(request)
    settings = deps.settings(request)
    res = registry.resolve(settings, snap, agent_id) if AGENT_ID_RE.fullmatch(agent_id) else None
    if res is None:
        raise HTTPException(404, "unknown agent")
    if res.redirect:  # old pane based id (bookmark, ntfy link): go to the permanent one
        qs = request.url.query
        return RedirectResponse(f"/a/{res.redirect}" + (f"?{qs}" if qs else ""), status_code=303)
    if res.ended is not None:
        return await _ended_agent_page(request, snap, settings, res)
    agent = res.agent
    raw = trim_pane(await executors.run_tmux(services.pane_text, settings, agent.pane_id))
    text = raw or ""
    transcript = plugins.first_url(plugins.of(request), "agent_transcript_url", agent)
    term_session_id, term_window_id = _term_ids(snap, agent)
    from pm.web.agentsidebar import agent_sidebar

    cctx = await composer_ctx(request, agent)
    session = snap.session(agent.session_id)
    project = snap.project(agent.project) if agent.project else None
    return html(
        request, "agent.html", **cctx, embed=is_embed(request),
        sidebar=None if is_embed(request) else await agent_sidebar(snap, settings, agent),
        term_session_id=term_session_id, term_window_id=term_window_id, term_back=f"/a/{agent.id}",
        keys=KILL_KEYS, key_groups=key_groups(), snippets=_snippets(settings), nav="inbox", agent=agent,
        head=agenthead.heading(agent), session=session, project=project,
        term_embed=_term_embed(settings, session=session, sid=term_session_id, wid=term_window_id),
        cwd_url=plugins.first_url(plugins.of(request), "agent_cwd_url", agent, project),
        origin=await executors.run_collectors(lineage.of, settings, agent.id), text=text, pane_loading=raw is None,
        digest=hashlib.sha256(text.encode()).hexdigest()[:16], transcript=transcript,
    )


@router.get("/a/{agent_id}/pane", dependencies=AUTH)
async def agent_pane(request: Request, agent_id: str, h: str | None = None) -> Response:
    snap = await snapshot(request)
    agent = agent_or_404(snap, agent_id)
    text = trim_pane(await executors.run_tmux(services.pane_text, deps.settings(request), agent.pane_id))
    if text is None:
        raise HTTPException(404, "pane gone")
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    etag = f'"{digest}"'
    seen = request.headers.get("if-none-match", "")
    if seen == etag or (h and HASH_RE.fullmatch(h) and h == digest):
        return Response(status_code=204, headers={"ETag": etag, "Cache-Control": "no-store"})
    resp = html(request, "_pane.html", agent=agent, text=text, digest=digest, oob=True)
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _review_launch(snap, project, remembered: dict, review: str) -> str:
    """Review prefill: a new Claude reviewer in plan mode, prompt built for the agent's cwd. Returns the prompt."""
    a = snap.agent(review) if AGENT_ID_RE.fullmatch(review) else None
    if a is None or not a.cwd or a.project != project.name:
        return ""
    if remembered.get("kind") != "claude":
        remembered.clear()
    remembered.update(kind="claude", mode="plan")
    return launch_mod.review_defaults(a.cwd)


@router.get("/p/{name}/launch", response_class=HTMLResponse, dependencies=AUTH)
async def launch_page(request: Request, name: str) -> Response:
    snap = await snapshot(request)
    project = snap.project(name)
    if project is None:
        raise HTTPException(404, "unknown project")
    kinds = [k.value for k in AgentKind if k is not AgentKind.OTHER]
    st = request.app.state.settings
    remembered = _remembered_launch(st, project)
    review_text = await executors.run_collectors(
        _review_launch, snap, project, remembered, request.query_params.get("review", ""))
    if review_text:
        remembered["prompt"] = review_text
    elif request.query_params.get("handoff"):  # Switch agent: this chat as the hand-off prompt
        remembered["prompt"] = await executors.run_collectors(
            _handoff_prompt, st, snap, project, request.query_params["handoff"]) or remembered.get("prompt", "")
    launch_data = await _launch_data(st, project, kinds, remembered, [p.name for p in snap.projects])
    running = [a for a in snap.agents if a.project == project.name]
    todo_rows = await _launch_todo_rows(st, snap, project)
    return html(
        request,
        "launch.html",
        running=running,
        nav="projects",
        embed=is_embed(request),
        from_inbox=is_embed(request) and request.query_params.get("from") == "inbox",
        launch_projects=sorted(p.name for p in snap.projects),
        sidebar=None if is_embed(request) else project_sidebar(
            snap, project.name, await side_unread(request, snap, "projects")),
        project=project,
        stop_agents=[a for a in snap.agents if a.project == project.name],
        kinds=kinds,
        launch_data=launch_data,
        composer_paste_lines=_composer(st)["paste_lines"],
        composer_accept=",".join("." + e for e in _composer(st)["exts"]),
        items=todo_rows,
        review_text=review_text,
    )


@router.get("/settings", response_class=HTMLResponse, dependencies=AUTH)
async def settings_page(request: Request) -> Response:
    """Settings. Classic loads the panel from GET /settings/panel; a theme (or plugin) with its own settings.html
    also gets the panel's data on first paint (``settings_panel.context``)."""
    own = not Path(get_template("settings.html").filename or "").is_relative_to(themes.CLASSIC.templates)
    ctx = await executors.run_collectors(settings_panel.context, request) if own else {}
    return html(request, "settings.html", nav="settings", **ctx)


@router.get("/sw.js")
async def service_worker() -> Response:
    """Public on purpose (no secrets); scope `/` needs this path, not /static."""
    return PlainTextResponse(
        (STATIC_DIR / "sw.js").read_text(),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )
