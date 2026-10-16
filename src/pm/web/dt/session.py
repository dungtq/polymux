"""The dt session tab's view model (PRD.md §6.3, LAYOUT.md §5): which sub-tab shows first, the flat tabs, the
classic radios behind them, the ⋯ menu and the Builder toggle of ``agent.html`` (live), ``builder.html`` and
``agent_ended.html``; ``session/_frame.html`` draws them.

``dt_session(mode, resumable)`` is a Jinja global (``template_globals()``): ``mode`` = live | builder | ended,
``resumable`` = the agent kinds that can resume / fork (the composer macros' ``RESUMABLE``, so the menu and the
composer agree). It reads the route's context: ``agent``, ``embed``, ``request`` (``?st=``), ``term_embed``,
``subagents_running``, ``clear_cmd``, ``project`` (agent page); ``live``, ``rec``, ``csrf`` (Builder, ended page).
No IO.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import quote

from jinja2 import pass_context

from pm.web.dt import menus

log = logging.getLogger(__name__)

TAB_ICONS = {"chat": "chat", "sub": "bot", "pane": "tmux", "term": "term"}  # sprite ids, embed only
RADIO_ORDER = ("chat", "pane", "term", "sub")  # the classic radios' DOM order (classic chat.js reads them)


@dataclass(frozen=True)
class Session:
    kind: str  # agent kind word
    state: str  # running | waiting | failed | idle | unknown | ended
    st: str  # the sub-tab shown first: chat | sub | pane | term
    tabs: list[dict]  # pt.tabs items (shown when more than one)
    radios: list[tuple[str, bool]]  # (key, checked) of the classic radios #tab-<key>; [] in Builder / ended
    menu: list[dict] = field(default_factory=list)
    bld: dict | None = None  # Builder toggle {"href", "on"}
    can_act: bool = False  # ended record / ended Builder: Resume / Fork work (a project and a resumable kind)


def _kind(agent) -> str:
    k = getattr(agent, "kind", "") or ""
    return str(getattr(k, "value", k))


def _state(agent) -> str:
    s = getattr(agent, "state", None)
    return str(getattr(s, "value", s or "unknown"))


def _review(agent) -> str:
    if not (getattr(agent, "project", None) and getattr(agent, "cwd", None)):
        return ""
    return f"/p/{quote(agent.project, safe='')}/launch?review={quote(agent.id, safe='')}"


# ------------------------------------------------------------------ ⋯ menus
def live_menu(agent, clear: str, resumable: bool) -> list[dict]:
    """⋯ of a live agent. New chat clicks the composer's own form (classic newchat.js: confirm, then it follows the
    new agent id); Fork / Exit / Exit and kill window post after an in-page question (kill: typed agent id,
    V7-SEC-6); Continue…, Usage and btw are page events (js/session.js)."""
    base, aid = f"/a/{agent.id}", agent.id
    review = _review(agent)
    return menus.menu(
        menus.item("newchat", "New chat", "plus", note="clears", click="form[data-newchat] button[type=submit]")
        if clear else None,
        menus.item("continue", "Continue… in a new agent", "right", event="c:continue"),
        menus.item("fork", "Fork", "branch", post=f"{base}/fork", confirm=f"Fork {aid} into a new tmux window?",
                   body="Starts a copy of this conversation in the background. This chat stays as it is.", ok="Fork",
                   done="Fork started") if resumable else None,
        menus.item("review", "Review the changes", "shield", href=review) if review else None,
        menus.SEP,
        menus.item("usage", "Usage of this agent", "cpu", event="c:usage"),
        menus.item("btw", "btw: ask about this chat", "chat", note="not sent", event="c:btw"),
        menus.copy_id(aid),
        menus.SEP,
        menus.item("exit", "Exit", "power", post=f"{base}/exit", confirm=f"Exit {aid}?", ok="Exit", done="Exit sent",
                   body="Sends the agent's quit command. The session id is saved, so you can resume it."),
        menus.item("exitkill", "Exit and kill window…", "trash", danger=True, note="typed", post=f"{base}/exitkill",
                   typed=aid, confirm=f"Exit {aid} and kill its tmux pane?", ok="Kill", done="Pane killed",
                   body="The pane closes at once, no graceful exit (the window closes with its last pane)."))


def ended_menu(agent, rec, can_act: bool) -> list[dict]:
    """⋯ of an ended record: Resume (needs a recorded session id), Fork, Copy id."""
    base, aid = f"/a/{agent.id}", agent.id
    resume = None
    if can_act and getattr(rec, "resume_id", None):
        resume = menus.item("resume", "Resume", "play", post=f"{base}/resume",
                            confirm=f"Resume {aid} in a new tmux window?", ok="Resume", done="Resuming…")
    elif can_act:
        resume = menus.item("resume", "Resume", "play", disabled=True, why="no session id recorded")
    return menus.menu(
        resume,
        menus.item("fork", "Fork", "branch", post=f"{base}/fork", confirm=f"Fork {aid} into a new tmux window?",
                   body="The original is left untouched.", ok="Fork", done="Fork started") if can_act else None,
        menus.copy_id(aid))


# ------------------------------------------------------------------ the three pages
def live(ctx: Mapping, resumable: Sequence[str]) -> Session:
    """The live agent page: first tab from ``?st=`` (only a tab that exists; the chat aside's embed has the same
    tabs, with icons for its narrow header)."""
    agent, embed = ctx["agent"], bool(ctx.get("embed"))
    kind = _kind(agent)
    term, claude = bool(ctx.get("term_embed")), kind == "claude"
    keys = ["chat", "pane"] + (["sub"] if claude else []) + (["term"] if term else [])
    req = ctx.get("request")
    want = req.query_params.get("st", "chat") if req is not None else "chat"
    st = want if want in keys else "chat"
    nsub = int(ctx.get("subagents_running") or 0)
    tabs = [{"key": "chat", "label": "Chat", "controls": "c-pn-chat", "cur": st == "chat", "title": "Chat"}]
    if claude:
        tabs.append({"key": "sub", "label": "Sub-agents", "controls": "c-pn-sub", "cur": st == "sub",
                     "count": nsub or None,
                     "title": f"{nsub} running" if nsub else "Work done by this session's sub-agents"})
    tabs.append({"key": "pane", "label": "Pane", "controls": "c-pn-pane", "cur": st == "pane",
                 "title": "Pane: the agent's tmux pane, keys and snippets"})
    if term:
        tabs.append({"key": "term", "label": "Terminal", "controls": "c-pn-term", "cur": st == "term",
                     "title": "Terminal (read-only)"})
    if embed:  # the aside's header is 360px wide: the tabs can show as icons only (css/session.css)
        for t in tabs:
            t["icon"] = TAB_ICONS[t["key"]]
    radios = [(k, k == st) for k in RADIO_ORDER if k in keys]
    bld = {"href": f"/a/{agent.id}/builder", "on": False} if (agent.cwd or ctx.get("project")) else None
    return Session(kind=kind, state=_state(agent), st=st, tabs=tabs, radios=radios, bld=bld,
                   menu=live_menu(agent, ctx.get("clear_cmd") or "", kind in resumable))


def builder(ctx: Mapping, resumable: Sequence[str]) -> Session:
    """Builder mode: the tabs link back to the agent page (Chat is current); an ended record gets the ended menu."""
    agent, rec, on = ctx["agent"], ctx.get("rec"), bool(ctx.get("live"))
    kind, aid = _kind(agent), f"/a/{agent.id}"
    can_act = bool(rec and ctx.get("csrf") and rec.project and rec.kind in resumable)
    tabs = []
    if on:
        tabs = [{"key": "chat", "label": "Chat", "href": aid, "cur": True, "title": "Leave Builder: full chat"}]
        if kind == "claude":
            tabs.append({"key": "sub", "label": "Sub-agents", "href": aid + "?st=sub"})
        tabs.append({"key": "pane", "label": "Pane", "href": aid + "?st=pane"})
    menu = live_menu(agent, ctx.get("clear_cmd") or "", kind in resumable) if on else ended_menu(agent, rec, can_act)
    return Session(kind=kind, state=_state(agent) if on else "ended", st="chat", tabs=tabs, radios=[], menu=menu,
                   bld={"href": aid, "on": True}, can_act=can_act)


def ended(ctx: Mapping, resumable: Sequence[str]) -> Session:
    """The ended record: no sub-tabs, no Builder toggle; Resume / Fork when the record allows it."""
    agent, rec = ctx["agent"], ctx["rec"]
    can_act = bool(ctx.get("csrf") and rec.project and rec.kind in resumable)
    return Session(kind=rec.kind or "", state="ended", st="chat", tabs=[], radios=[],
                   menu=ended_menu(agent, rec, can_act), can_act=can_act)


# ------------------------------------------------------------------ Jinja global
MODES = {"live": live, "builder": builder, "ended": ended}
_logged: set[str] = set()


@pass_context
def dt_session(ctx, mode: str, resumable: Sequence[str] = ()):
    """``dt_session("live" | "builder" | "ended", RESUMABLE)``; None on an error (logged once per error type)."""
    try:
        return MODES[mode](ctx, tuple(resumable or ()))
    except Exception as e:  # noqa: BLE001 - a broken view model must not break the page
        name = type(e).__name__
        if name not in _logged:
            _logged.add(name)
            log.exception("dt session view model failed")
        return None
