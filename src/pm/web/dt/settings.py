"""The dt Settings page's view model (docs/themes/dt/PRD.md §6.11, LAYOUT.md §5 Settings).

``dt_settings`` is a Jinja global (``template_globals()``, bound to the plugin's settings). It reads the context of the
core's ``GET /settings`` / ``GET /settings/panel`` (``settings_panel.context``: sessions, passkeys, prefs, agents,
pm_key, user_keys, servers, srv_results, git_hosts, notify_rules, composer + page_ctx ui_theme, ui_themes,
require_unlock) and returns a ``Page``: the section nav (groups You · Work · Security · System), the current section
(``?s=<id>``; none = the phone's section list, the first section on wider screens), the table rows of Access and
Remote servers with their ⋯ items, and the read-only System lines. It also reads the v7 "all devices" prefs
(``pm.web.dt.prefs``, stat-cached) and the servers panel's cached "pm service" metric; no other IO.

Scopes (PRD §6.11): ``browser`` = this browser (``pm.v7.*`` storage, js/head.js), ``all`` = all devices (the core's
prefs and stores, or ``/dt/prefs``), ``file`` = pm.toml, read-only. Every mutation is a core or /dt POST; the page's
JS saves on change and toasts after the server's ``{ok: true}`` (V7-CODE-5).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import pass_context

from pm.agents.inventory.fsread import display_path
from pm.git import ctl as gitctl
from pm.infra import servers as srvpanel
from pm.infra import unitctl
from pm.infra.servers_model import fmt_bytes
from pm.web.dt import menus, stores
from pm.web.dt import prefs as dtprefs

log = logging.getLogger(__name__)

# (id, label, icon, group, lead) in nav order; the ids are the ?s= values and the #s-sec-<id> anchors
SECTIONS = (
    ("appearance", "Appearance", "moon", "You", "How pm looks in this browser. Each device keeps its own."),
    ("display", "Display", "panel", "You",
     "What pm shows around your agents: the running status, state dots and counts, source pills, the model id, "
     "where the project quick look opens files."),
    ("colors", "Chat colours", "sliders", "You",
     "Colours of agent messages, agent kinds and tool calls in every chat. Default keeps the light and dark theme "
     "colours; a picked colour is used in both themes."),
    ("agents", "Agents", "bot", "Work", "Launch defaults, what sends a notification, and idle clean-up."),
    ("composer", "Composer", "chat", "Work", "Pasting and attaching in every message box."),
    ("qchat", "Quick chat", "sparkle", "Work",
     "The Quick chat panel. It runs without your own hooks and settings from ~/.claude."),
    ("access", "Access", "shield", "Security", "Who can sign in and where you are signed in."),
    ("ssh", "SSH keys", "lock", "Security", "pm's own key for remote servers, and the keys already in ~/.ssh."),
    ("servers", "Remote servers", "server", "Security",
     "pm connects only to servers listed here, with its own key and a pinned host key. A password is used once to "
     "install the key and is never stored, logged or audited."),
    ("git", "Git", "branch", "System",
     "pm pushes only to the default remote; other remotes are never fetched or pushed by pm. Remotes are edited on "
     "each project page."),
    ("system", "pm service", "gear", "System", "Read-only view of pm.toml and the running service. Edit the file, "
     "then reload pm."),
)
IDS = tuple(s[0] for s in SECTIONS)
# hash / group aliases (/settings#security): the group's first section
ALIASES = {"you": "appearance", "work": "agents", "security": "access", "system": "system", "colours": "colors",
           "ui-theme": "appearance", "remote": "servers", "qchat-settings": "qchat", "autoexit": "agents"}
NOTIFY_HELP = {"waiting": "Permission requests and questions.",
               "health": "Thresholds in pm.toml, with hysteresis and a recovery notice."}
MODES = ("default", "acceptEdits", "plan", "bypassPermissions")
FONTS = ("system", "sans", "serif", "mono")
UI_SIZES = tuple((v, v) for v in ("90", "100", "110", "125"))  # (value, label) of the segments
ASIDE_PCTS = tuple((v, v) for v in ("25", "30", "35", "40", "50"))
# Display › colours: (prefs key, label, help)
DOT_COLOURS = (
    ("col_dot_run", "Running dot", ""), ("col_dot_wait", "Waiting / needs you dot", ""),
    ("col_dot_fail", "Failed dot", ""), ("col_dot_idle", "Idle dot", ""),
    ("col_dot_end", "Ended dot", "The ring of an ended or queued agent."),
    ("col_badge_un", "Unread count badge", "The number next to an agent with new messages, and a tab's unread mark."),
    ("col_badge_need", "Needs you badge", "The yellow number on Inbox and on a project with agents waiting for you."),
)
SOURCES = (("you", "“You” pill", "The last message came from you."),
           ("ntfy", "“ntfy” pill", "The last message is the agent's ntfy (finish) note."))
KIND_COLOURS = (("col_k_claude", "Claude"), ("col_k_codex", "Codex"), ("col_k_devin", "Devin"),
                ("col_k_cursor_agent", "Cursor Agent"), ("col_k_antigravity", "Antigravity"))
TOOL_COLOURS = (("col_tool_shell", "Shell", "Bash, BashOutput, KillShell"), ("col_tool_read", "Read",
                "Read, Grep, Glob, LS"), ("col_tool_edit", "Edit", "Edit, Write, MultiEdit, NotebookEdit"),
                ("col_tool_web", "Web", "WebFetch, WebSearch"), ("col_tool_agent", "Agent", "Task, Agent, TodoWrite"),
                ("col_tool_mcp", "MCP", "mcp__* tools"))
RESET_DOTS = tuple(k for k, _, _ in DOT_COLOURS)
RESET_SRC = ("src_you", "src_ntfy")
RESET_CHAT = ("msg_bg", "col_msg_bg", "kind_edge", "kind_pill", "tool_pill", "col_user_bg", "col_user_bd",
              *(k for k, _ in KIND_COLOURS), *(k for k, _, _ in TOOL_COLOURS))


@dataclass(frozen=True)
class Sec:
    id: str
    label: str
    icon: str
    group: str
    lead: str
    href: str
    cur: bool


@dataclass(frozen=True)
class NavGroup:
    label: str
    secs: tuple[Sec, ...]


@dataclass(frozen=True)
class SessRow:
    h: str
    client: str
    created: float
    last_seen: float
    current: bool
    menu: list


@dataclass(frozen=True)
class KeyRow:
    id: str
    name: str
    created: float
    menu: list


@dataclass(frozen=True)
class SrvRow:
    id: str
    name: str
    addr: str
    user: str
    fp: str
    pinned: bool
    state: str  # dot: running (key works) | waiting (not pinned / not tested) | failed (last test failed)
    word: str
    menu: list


@dataclass(frozen=True)
class Line:
    label: str
    value: str
    help: str = ""


@dataclass(frozen=True)
class Page:
    nav: tuple[NavGroup, ...]
    secs: Mapping[str, Sec]
    cur: str
    view: str  # "list" (no ?s=: the phone's section list) | "sec"
    sessions: tuple[SessRow, ...]
    passkeys: tuple[KeyRow, ...]
    servers: tuple[SrvRow, ...]
    git: tuple[Line, ...]
    system: tuple[Line, ...]
    prefs: dtprefs.Prefs
    launch: Mapping[str, str]
    model_opts: tuple[str, ...]
    mode_opts: tuple[tuple[str, str], ...]
    agent_opts: tuple[tuple[str, str], ...]
    notify: tuple[tuple[str, str, str, bool], ...]  # (key, label, help, on)
    composer: Mapping[str, object]
    ui_opts: tuple[tuple[str, str], ...]
    ui_theme: str
    tabs_open: int
    acts: Mapping[str, dict]  # standalone buttons: menu items run by js/settings-forms.js (pm7.menus.exec)
    pm_key_menu: list
    stats: tuple[str, ...]
    reload_ok: bool
    reload_why: str
    require_unlock: bool
    pm_key: object = None  # sshkeys key (name, type, fingerprint, line) or None
    user_keys: tuple = ()
    key_menus: Mapping[str, list] = None  # ~/.ssh key name -> ⋯ (Copy)
    # Display / Chat colours rows and their Reset lists (comma-joined prefs keys)
    dots: tuple = DOT_COLOURS
    srcs: tuple = SOURCES
    kinds: tuple = KIND_COLOURS
    tools: tuple = TOOL_COLOURS
    resets: Mapping[str, str] = None
    fonts: tuple[str, ...] = FONTS
    ui_sizes: tuple[tuple[str, str], ...] = UI_SIZES
    aside_pcts: tuple[tuple[str, str], ...] = ASIDE_PCTS


def sec_of(raw: str | None) -> str | None:
    """A section id from ``?s=`` or a hash alias; None when unknown or empty."""
    s = (raw or "").strip().lower()
    return s if s in IDS else ALIASES.get(s)


def nav(cur: str) -> tuple[tuple[NavGroup, ...], dict[str, Sec]]:
    secs = {i: Sec(i, label, icon, grp, lead, f"/settings?s={i}", i == cur) for i, label, icon, grp, lead in SECTIONS}
    groups: list[NavGroup] = []
    for s in secs.values():
        if not groups or groups[-1].label != s.group:
            groups.append(NavGroup(s.group, ()))
        groups[-1] = NavGroup(s.group, (*groups[-1].secs, s))
    return tuple(groups), secs


def _refresh(sec: str) -> str:
    return f"#s-sec-{sec}"


def session_rows(sessions) -> tuple[SessRow, ...]:
    """Current session first, then the others by last seen (the core's order). ⋯: Log out (current) or Revoke."""
    out: list[SessRow] = []
    for s in sorted(sessions or (), key=lambda x: not x.get("current")):
        h, client = str(s.get("h", "")), str(s.get("client") or "?")
        if s.get("current"):
            it = menus.item("logout", "Log out of this session", "logout", danger=True,
                            post="/settings/sessions/revoke", vals={"h": h, "confirm": "1"},
                            confirm="Log out of this session?", body="You need the token or a passkey to sign in "
                            "again.", ok="Log out")
        else:
            it = menus.item("revoke", "Revoke", "x", danger=True, post="/settings/sessions/revoke", vals={"h": h},
                            confirm=f"Revoke the session at {client}?", body="That browser is signed out.",
                            ok="Revoke", done="Session revoked", refresh=_refresh("access"))
        out.append(SessRow(h, client, float(s.get("created") or 0), float(s.get("last_seen") or 0),
                           bool(s.get("current")), menus.menu(it)))
    return tuple(out)


def passkey_rows(keys) -> tuple[KeyRow, ...]:
    out = []
    for k in keys or ():
        kid, name = str(k.get("id", "")), str(k.get("name") or "passkey")
        it = menus.item("remove", "Remove", "trash", danger=True, post="/settings/passkeys/revoke", vals={"id": kid},
                        confirm=f"Remove passkey {name}?", body="That device can no longer sign in with it.",
                        ok="Remove", done=f"Removed {name}", refresh=_refresh("access"))
        out.append(KeyRow(kid, name, float(k.get("created") or 0), menus.menu(it)))
    return tuple(out)


def server_rows(servers, results: Mapping[str, str], has_key: bool) -> tuple[SrvRow, ...]:
    out = []
    for s in servers or ():
        sid, name = str(s.get("id", "")), str(s.get("name", ""))
        port = s.get("port") or 22
        addr = f"{s.get('user', '')}@{s.get('host', '')}" + (f":{port}" if port != 22 else "")
        fp = str(s.get("hostkey_fp") or "")
        test = (s.get("last_test") or {}).get("reason", "") if isinstance(s.get("last_test"), dict) else ""
        if not fp:
            state, word = "waiting", "host key not pinned"
        elif not test:
            state, word = "idle", "not tested"
        else:
            state, word = ("running" if test == "ok" else "failed"), results.get(test, test)
        base = f"/settings/servers/{sid}"
        why_test = "" if fp and has_key else ("pin the host key first" if not fp else "generate pm's key first")
        out.append(SrvRow(sid, name, addr, str(s.get("user", "")), fp, bool(fp), state, word, menus.menu(
            menus.item("pin", "Pin host key…", "shield", event="s:pin") if not fp else None,
            menus.item("test", "Test connection", "refresh", post=base + "/test", refresh=_refresh("servers"))
            if not why_test else menus.item("test", "Test connection", "refresh", disabled=True, why=why_test),
            menus.item("install", "Install pm key…", "lock", note="one-time password", event="s:install")
            if not why_test else menus.item("install", "Install pm key…", "lock", disabled=True, why=why_test),
            menus.item("repin", "Re-pin host key…", "shield", event="s:repin") if fp else None,
            menus.SEP,
            menus.item("remove", "Remove", "trash", danger=True, post=base + "/remove", confirm=f"Remove {name}?",
                       body="pm forgets the server and its pinned host key. The key on the server stays.",
                       ok="Remove", done=f"Removed {name}", refresh=_refresh("servers")))))
    return tuple(out)


def _topic(url: str) -> str:
    """The ntfy topic (last path part) of a topic URL; "" when unset."""
    return urlsplit(url or "").path.rstrip("/").rsplit("/", 1)[-1]


def _home(p) -> str:
    """A path with the home folder as ~ (the page shows where, never whose)."""
    try:
        return display_path(Path(p), Path.home())
    except RuntimeError:
        return str(p)


def system_lines(settings) -> tuple[Line, ...]:
    if settings is None:
        return ()
    panel = srvpanel.cached_panel(settings)
    status = next((m.text for m in (panel.metrics if panel else ()) if m.label == "pm service"), "running")
    binds = ", ".join(f"{b}:{settings.port}" for b in settings.binds)
    topics = " · ".join(t for t in (_topic(settings.ntfy_topic_url), _topic(settings.ntfy_pm_topic_url)) if t)
    cfg = settings.config_file
    return (
        Line("Status", f"{status} · {binds}"),
        Line("Config file", _home(cfg) if cfg else "none (built-in defaults)"),
        Line("State folder", _home(settings.state_dir)),
        Line("ntfy topics", topics or "none", "Agent finish notes · pm's own alerts."),
        Line("Tier ceiling", getattr(settings.tier_ceiling, "name", str(settings.tier_ceiling)).lower(),
             "The most a session may do."),
        Line("Alert thresholds", f"{settings.alerts_load_per_cpu:g} · {settings.alerts_mem_pct:g}% · "
             f"{settings.alerts_swap_pct:g}% · {settings.alerts_disk_pct:g}%"
             + ("" if settings.alerts_enabled else " (alerts off)"), "load/CPU · memory · swap · disk"),
    )


def git_lines(ctx, settings) -> tuple[Line, ...]:
    hosts = tuple(ctx.get("git_hosts") or ()) or tuple(gitctl.DEFAULT_GIT_HOSTS)
    remote = getattr(settings, "push_remote", None) or ctx.get("push_remote") or "—"
    return (Line("Default remote", remote, "push_remote"),
            Line("Allowed remote hosts", ", ".join(hosts) or "any host (none set)", "git_allowed_hosts"))


def _actions(tabs_open: int, reload_ok: bool) -> dict[str, dict]:
    n = f"{tabs_open} session tab{'' if tabs_open == 1 else 's'}"
    return {
        "close_tabs": menus.item("close_tabs", "Close all session tabs", "x", post="/dt/tabs/close-all",
                                 confirm=f"Close all {n}?", body="On every device. The page tab stays; ⌥⇧T reopens "
                                 "them one by one.", ok="Close tabs", done=f"Closed {n}",
                                 refresh=_refresh("appearance")),
        "revoke_others": menus.item("revoke_others", "Revoke all others", "x", danger=True,
                                    post="/settings/sessions/revoke-others", confirm="Revoke all other sessions?",
                                    body="Every other browser and device is signed out. This one stays.",
                                    ok="Revoke all", refresh=_refresh("access")),
        "keygen": menus.item("keygen", "Generate key", "plus", post="/settings/ssh/keygen",
                             confirm="Generate pm's SSH key?", body="An ed25519 key without a passphrase in pm's "
                             "state folder. It never overwrites a key.", ok="Generate", refresh=_refresh("ssh")),
        "reload": menus.item("reload", "Reload pm", "refresh", post="/service/reload", confirm="Reload pm?",
                             body="Restarts the service; pages reconnect in a few seconds. Agents keep running in "
                             "tmux.", ok="Reload", done="pm is reloading") if reload_ok else
        menus.item("reload", "Reload pm", "refresh", disabled=True, why="not on this instance"),
        "rescan_mcp": menus.item("rescan_mcp", "Re-read ~/.claude.json", "refresh",
                                 post="/settings/qchat/mcp/rescan", refresh="#s-qchat"),
        "rescan_skills": menus.item("rescan_skills", "Re-scan", "refresh", post="/settings/qchat/skills/rescan",
                                    refresh="#s-qchat"),
        "delete_history": menus.item("delete_history", "Delete all history", "trash", danger=True,
                                     post="/settings/qchat/history/delete-all",
                                     confirm="Delete all quick chat history? Type delete to confirm.",
                                     typed="delete", body="All conversations and messages are removed.",
                                     ok="Delete all", refresh="#s-qchat"),
    }


def build(ctx, settings) -> Page:
    req = ctx.get("request")
    raw = req.query_params.get("s") if req is not None else None
    chosen = sec_of(raw)
    cur = chosen or IDS[0]
    groups, secs = nav(cur)
    p = ctx.get("prefs") or {}
    launch = {k: str((p.get("launch") or {}).get(k) or "") for k in ("agent", "model", "mode")}
    notify = tuple((k, label, NOTIFY_HELP.get(k, ""), bool((p.get("notify") or {}).get(k, True)))
                   for k, label in (ctx.get("notify_rules") or ()))
    modes = (("", "(agent default)"), *((m, m) for m in MODES))
    if launch["mode"] and launch["mode"] not in MODES:
        modes += ((launch["mode"], launch["mode"]),)
    models: list[str] = []
    for v in (getattr(settings, "agent_models", None) or {}).values():
        models += [str(m) for m in (v if isinstance(v, list | tuple) else ()) if str(m) not in models]
    tabs_open = len(stores.read_cached(settings).tabs) if settings is not None else 0
    reload_ok = bool(settings is not None and unitctl.reload_allowed(settings))
    pm_key = ctx.get("pm_key")
    sessions = session_rows(ctx.get("sessions"))
    servers = server_rows(ctx.get("servers"), ctx.get("srv_results") or {}, bool(pm_key))
    return Page(
        nav=groups, secs=secs, cur=cur, view="sec" if chosen else "list", sessions=sessions,
        passkeys=passkey_rows(ctx.get("passkeys")), servers=servers, git=git_lines(ctx, settings),
        system=system_lines(settings),
        prefs=dtprefs.read_cached(settings) if settings is not None else dtprefs.EMPTY,
        launch=launch, model_opts=tuple(models), mode_opts=modes,
        agent_opts=(("", "(none)"), *((a, a) for a in (ctx.get("agents") or ()))),
        notify=notify, composer=ctx.get("composer") or {},
        ui_opts=tuple((str(n), str(lb)) for n, lb in (ctx.get("ui_themes") or ())),
        ui_theme=str(ctx.get("ui_theme") or ""), tabs_open=tabs_open, acts=_actions(tabs_open, reload_ok),
        pm_key_menu=menus.menu(menus.item("copy", "Copy public key", "copy", copy=pm_key.line)) if pm_key else [],
        stats=("Changes save as you make them", "this browser · all devices · pm.toml (read-only)"),
        reload_ok=reload_ok, reload_why="" if reload_ok else "Reload is off on this instance (dev, or no unit).",
        require_unlock=bool(ctx.get("require_unlock")), pm_key=pm_key,
        user_keys=tuple(ctx.get("user_keys") or ()),
        key_menus={k.name: menus.menu(menus.item("copy", "Copy public key", "copy", copy=k.line))
                   for k in (ctx.get("user_keys") or ())},
        resets={"dots": ",".join(RESET_DOTS), "src": ",".join(RESET_SRC), "chat": ",".join(RESET_CHAT)},
    )


@dataclass(frozen=True)
class McpRow:
    name: str
    source: str
    type: str
    target: str
    on: bool
    fixed: bool  # a claude.ai connector: listed only, never passed to Quick chat
    menu: list


@dataclass(frozen=True)
class QChat:
    """Settings › Quick chat (the core's GET /settings/qchat fragment: qc, qc_mcp, qc_skills, qc_folders,
    qc_profiles, qc_modes, qc_stats)."""
    tabs: tuple[dict, ...]
    mcp: tuple[McpRow, ...]
    acts: Mapping[str, dict]
    folders: tuple[tuple[str, str], ...]
    profiles: tuple[tuple[str, str], ...]
    modes: tuple[tuple[str, str], ...]
    size: str


def qchat(ctx) -> QChat:
    rows = []
    for m in ctx.get("qc_mcp") or ():
        name = str(m.get("name", ""))
        fixed = m.get("type") == "connector"
        rows.append(McpRow(name, str(m.get("source", "")), str(m.get("type", "")), str(m.get("target") or ""),
                           bool(m.get("enabled")), fixed, menus.menu(
            menus.item("test", "Test", "refresh", post="/settings/qchat/mcp/test", vals={"name": name})
            if m.get("testable") else None,
            menus.item("remove", "Remove", "trash", danger=True, post="/settings/qchat/mcp/remove",
                       vals={"name": name}, confirm=f"Remove MCP server {name}?", ok="Remove",
                       refresh="#s-qchat") if m.get("manual") else None)))
    skills = ctx.get("qc_skills") or ()
    tabs = ({"key": "qc-tools", "label": "Tools", "controls": "s-qc-tools"},
            {"key": "qc-mcp", "label": "MCP servers", "controls": "s-qc-mcp", "count": len(rows)},
            {"key": "qc-skills", "label": "Skills", "controls": "s-qc-skills", "count": len(skills)},
            {"key": "qc-history", "label": "History", "controls": "s-qc-history"})
    acts = _actions(0, False)
    return QChat(tabs, tuple(rows), {k: acts[k] for k in ("rescan_mcp", "rescan_skills", "delete_history")},
                 (("", "(none)"), *((str(f), str(f)) for f in (ctx.get("qc_folders") or ()))),
                 tuple((str(x), str(x)) for x in (ctx.get("qc_profiles") or ())),
                 tuple((str(x), str(x)) for x in (ctx.get("qc_modes") or ())),
                 fmt_bytes(int((ctx.get("qc_stats") or {}).get("size") or 0)))


_logged: set[str] = set()


def bind(settings_of: Callable[[], object]):
    """The ``dt_settings`` global: ``dt_settings()`` = the page's ``Page``, ``dt_settings("qchat")`` = the Quick
    chat fragment's ``QChat``. Never raises (None on an error; the
    template says so)."""
    @pass_context
    def dt_settings(ctx, part: str = "page"):
        try:
            if part == "qchat":
                return qchat(ctx)
            req = ctx.get("request")
            st = getattr(getattr(getattr(req, "app", None), "state", None), "settings", None) or settings_of()
            return build(ctx, st)
        except Exception as e:  # noqa: BLE001 - a broken view model must not break the page
            name = type(e).__name__
            if name not in _logged:
                _logged.add(name)
                log.exception("dt settings view model failed")
            return None
    return dt_settings
