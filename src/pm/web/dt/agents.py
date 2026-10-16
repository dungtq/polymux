"""The dt Agents page view models (PRD.md §6.8, LAYOUT.md §5 "Agents"): the page frame (head, stats, tabs,
findings line), the Installed list and one agent's side detail (Skills · Rules · MCP · Scripts and the editor modes).
Library and MCP servers: ``pm.web.dt.agents_library``; Usage and Findings: ``pm.web.dt.agents_usage``.

``dt_agents(part)`` is a Jinja global (``template_globals()``) read by the dt ``agents.html``, ``agents_detail.html``
and their partials. It reads the core routes' context (``pm.web.routes.agentspage`` / ``agentsedit``: ``inv``,
``view``, ``stats``, ``agents``, ``cols`` / ``icols``, ``agent``, ``tab``, ``base``, ``rows``, ``counts``, ``mode``,
...). No IO: login states are filled in by js/agents.js from ``GET /agents/auth.json`` (the status cache).

Every change goes through the core's two steps (``POST /agents/review`` → the exact file operations →
``POST /agents/apply`` with the token). A row's ⋯ items that change files are ``event: "a:plan"`` items; the row
carries their fields in ``data-a-plans`` = {item id: Plan dict} (``plan()``), which js/agents.js posts.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

from jinja2 import pass_context

from pm.web.dt import agents_library, agents_usage, menus
from pm.web.dt.agents_base import BACKED, BACKUPS, FINDING_KINDS, PATH, col, gh, kind, plan, plans_attr

log = logging.getLogger(__name__)

VIEWS = (("installed", "Installed"), ("library", "Library"), ("mcp", "MCP servers"), ("usage", "Usage"),
         ("findings", "Findings"))
DTABS = (("skills", "Skills"), ("rules", "Rules"), ("mcp", "MCP"), ("scripts", "Scripts"))
NEW = {"skills": "skill", "rules": "rule", "scripts": "script"}
VAL_DOT = {"ok": "running", "warn": "waiting", "error": "failed"}
NEW_NOTE = {"skill": ("Goes to ~/.agents/skills/<name>/SKILL.md and is linked by the agents that read shared skills.",
                      "my-skill"),
            "rule": ("Goes to ~/agents/rules/<name>.", "my-rule.md"),
            "script": ("Goes to ~/.agents/hooks/<name> (executable).", "my-script.sh")}
RULE_DOT = {"ok": ("running", "same as its source"), "drift": ("waiting", "differs from the canonical"),
            "broken": ("failed", "broken"), "missing": ("failed", "missing")}


def stats_of(text: str) -> list:
    """The core's stats line ("1 installed · 4 skills · ...") as head stats."""
    return [s for s in (text or "").split(" · ") if s]


# ------------------------------------------------------------------ frame: head, tabs, findings line
@dataclass(frozen=True)
class FindLine:
    total: int
    warn: bool
    parts: list[str]  # "2 security", "3 rules", ...
    href: str


@dataclass(frozen=True)
class Frame:
    view: str
    stats: list
    tabs: list[dict]
    actions: list[dict]
    find: FindLine | None


def open_findings(inv) -> list:
    """Findings that need a look (everything but the notes)."""
    return [f for f in getattr(inv, "findings", ()) or () if (f.kind or "notes") != "notes"]


def find_line(inv) -> FindLine | None:
    fs = open_findings(inv)
    if not fs:
        return None
    counts: dict[str, int] = {}
    for f in fs:
        counts[f.kind] = counts.get(f.kind, 0) + 1
    parts = [f"{counts[k]} {lab.lower()}" for k, lab in FINDING_KINDS if counts.get(k)]
    return FindLine(len(fs), any(f.severity == "warn" for f in fs), parts, PATH + "?t=findings")


def frame(ctx: Mapping) -> Frame:
    inv = ctx.get("inv")
    view = ctx.get("view") or ctx.get("section") or "installed"
    if ctx.get("agent") is not None:
        view = "installed"
    fs = open_findings(inv)
    counts = {"installed": sum(1 for a in getattr(inv, "agents", ()) if a.installed),
              "library": len(agents_library.items(inv)), "mcp": len({m.name for m in getattr(inv, "mcp", ())}),
              "findings": len(fs)}
    tabs = [{"key": k, "label": lab, "href": f"{PATH}?t={k}", "cur": k == view, "count": counts.get(k),
             "bad": k == "findings" and any(f.severity == "warn" for f in fs)} for k, lab in VIEWS]
    actions = [{"label": "Re-scan", "icon": "refresh", "attrs": {"data-a-rescan": ""},
                "title": "Read every agent's files and login state again"}]
    return Frame(view=view, stats=stats_of(ctx.get("stats") or ""), tabs=tabs, actions=actions,
                 find=find_line(inv) if view == "installed" else None)


# ------------------------------------------------------------------ Installed
@dataclass(frozen=True)
class Inst:
    name: str
    label: str
    kind: str
    href: str
    installed: bool
    version: str
    skills: int
    rules: int
    mcp: int
    scripts: int
    findings: int
    warn: bool
    sel: bool
    meta: str
    q: str
    menu: list = field(default_factory=list)


@dataclass(frozen=True)
class Installed:
    cols: list[dict]
    sort: str | None
    rows: list[Inst]
    off: list[Inst]  # not installed: a folded band
    narrow: bool  # the list beside an agent's detail: fewer columns
    total: int


INST_SPEC = (col("", sr="State"), col("c-ag", "agent", "Agent"), col("c-ver", "version", "Version", "hide-n"),
             col(None, label="Login", cls="hide-n"), col("c-sk", "skills", "Skills", num=True),
             col(None, label="Rules", cls="hide-n", num=True), col("c-mc", "mcp", "MCP", num=True),
             col(None, label="Scripts", cls="hide-n", num=True), col(None, label="Findings", num=True),
             col("", sr="Actions"))
NARROW_SPEC = (col("", sr="State"), col("c-ag", "agent", "Agent"), col(None, label="Findings", num=True))


def _inst_menu(a) -> list[dict]:
    enc = quote(a.name, safe="")
    return menus.menu(
        menus.item("open", "Open details", "panel", key="Enter", href=f"{PATH}/{enc}"),
        menus.item("lib", "Show in Library", "list", href=f"{PATH}?" + urlencode({"t": "library", "a": a.name})),
        menus.SEP,
        menus.item("copy", "Copy binary path", "copy", copy=a.binary) if a.binary else None)


def inst_row(a, inv, cur: str) -> Inst:
    rules = sum(1 for r in inv.rules if r.agent == a.name)
    scripts = sum(1 for s in inv.scripts if a.name in s.used_by)
    fs = [f for f in open_findings(inv) if f.agent == a.name]
    version = a.version or ("–" if a.installed else "not installed")
    meta = " · ".join(x for x in (a.version, f"{a.skills_enabled} skills", f"{a.mcp_count} MCP",
                                  f"{len(fs)} findings" if fs else "") if x)
    return Inst(name=a.name, label=a.label or a.name, kind=kind(a.name), href=f"{PATH}/{quote(a.name, safe='')}",
                installed=a.installed, version=version, skills=a.skills_enabled, rules=rules, mcp=a.mcp_count,
                scripts=scripts, findings=len(fs), warn=any(f.severity == "warn" for f in fs), sel=a.name == cur,
                meta=meta if a.installed else "not installed", q=f"{a.name} {a.label}", menu=_inst_menu(a))


def installed(ctx: Mapping) -> Installed:
    inv = ctx.get("inv")
    agent = ctx.get("agent")
    narrow = agent is not None
    cols = ctx.get("icols") if narrow else ctx.get("cols")
    head, order = gh(cols, NARROW_SPEC if narrow else INST_SPEC)
    cur = agent.name if agent is not None else ""
    rows = [inst_row(a, inv, cur) for a in ctx.get("agents") or ()]
    return Installed(cols=head, sort=order, rows=[r for r in rows if r.installed],
                     off=[r for r in rows if not r.installed], narrow=narrow, total=len(rows))


# ------------------------------------------------------------------ one agent: the side detail
@dataclass(frozen=True)
class Item:
    """One row of a detail tab (skill, rule, MCP server, script)."""
    id: str
    name: str
    desc: str
    meta: str
    q: str
    state: str = ""  # dot state ("" = no dot)
    word: str = ""
    on: bool = False  # skills: the switch
    why: str = ""  # skills: why the switch is off-limits
    sw: str = ""  # skills: the switch's plan as JSON (enable is added from the switch; done = "{} x" with Enabled …)
    cells: tuple = ()  # extra cells, in column order
    warn: str = ""  # a warning chip ("key in args")
    menu: list = field(default_factory=list)
    attrs: dict | None = None


@dataclass(frozen=True)
class Detail:
    name: str
    label: str
    kind: str
    installed: bool
    base: str
    kv: list
    tabs: list[dict]
    tab: str
    mode: str  # "" | edit | review | result | notice
    cols: list[dict]
    sort: str | None
    items: list[Item]
    total: int
    new: str  # "skill" | "rule" | "script" | "" (the New … button and dialog)
    note: str  # a line above the table (MCP: where it is written)
    segs: list[dict]
    q: str
    self_url: str
    lib_href: str
    sync_sources: list
    sync_targets: list
    validation: list | None
    validate_href: str
    new_note: str  # the New dialog: where the item goes
    new_hint: str  # the New dialog: a name placeholder
    back: str  # editor modes: the tab to go back to
    rm: str  # editor mode: the Remove plan as JSON ("" = the core does not allow it)


def _edit_href(base: str, kind_: str, name: str) -> str:
    return f"{base}/edit?" + urlencode({"kind": kind_, "name": name})


def _delete(kind_: str, name: str) -> dict:
    return plan([("action", f"{kind_}_delete"), ("name", name), ("confirm", name)], f"Remove {kind_} {name}",
                "Remove", f"Removed {name} (backed up to {BACKUPS})", typed=name, undo=BACKED)


def _skill_items(ctx, agent, base) -> list[Item]:
    out = []
    for r in ctx.get("rows") or ():
        menu = menus.menu(
            menus.item("edit", "Edit…", "edit", href=_edit_href(base, "skill", r["name"])) if r.get("edit") else None,
            menus.item("copy", "Copy path", "copy", copy=r["path"]) if r.get("path") else None,
            menus.SEP,
            menus.item("rm", "Remove…", "trash", danger=True, event="a:plan") if r.get("edit") else None)
        plans = {"rm": _delete("skill", r["name"])} if r.get("edit") else {}
        sw = json.dumps(plan([("action", "skill_toggle"), ("agent", agent.name), ("skill", r["name"])], r["name"], "",
                             f"{{}} {r['name']} for {agent.label or agent.name}", quiet=True))
        out.append(Item(id=r["name"], name=r["name"], desc=r.get("desc") or "", meta=r.get("scope") or "",
                        q=f"{r['name']} {r.get('desc') or ''}", on=bool(r.get("on")), why=r.get("why") or "",
                        sw=sw, cells=(r.get("scope") or "",), menu=menu, attrs=plans_attr(plans)))
    return out


def _rule_items(ctx, base) -> list[Item]:
    can_edit, can_del = ctx.get("can_edit") or set(), ctx.get("can_delete") or set()
    out = []
    for r in ctx.get("rows") or ():
        base_name = r.path.rsplit("/", 1)[-1]
        state, word = RULE_DOT.get(r.status, ("unknown", r.status))
        edit = r.path in can_edit
        menu = menus.menu(
            menus.item("edit", "Edit…", "edit", href=_edit_href(base, "rule", base_name)) if edit else None,
            menus.item("sync", "Sync…", "swap", event="a:sync") if r.status == "drift" else None,
            menus.item("copy", "Copy path", "copy", copy=r.path),
            menus.SEP,
            menus.item("rm", "Remove…", "trash", danger=True, event="a:plan") if r.path in can_del else None)
        plans = {"rm": _delete("rule", base_name)} if r.path in can_del else {}
        path = r.path + (f" → {r.target}" if r.target else "")
        out.append(Item(id=r.path, name=r.name, desc=path, meta=f"{r.mode} · {r.lines} lines · {word}",
                        q=f"{r.name} {r.path}", state=state, word=word, cells=(r.mode, r.lines), menu=menu,
                        attrs=plans_attr(plans)))
    return out


def _mcp_items(ctx, inv) -> list[Item]:
    out = []
    for m in ctx.get("rows") or ():
        state, word = (("running", "enabled") if m.enabled else ("unknown", "unknown") if m.enabled is None
                       else ("idle", "disabled"))
        fixes = agents_library.key_fixes(inv, m.name, m.agent)
        warn = "key in args" if fixes or agents_library.key_in_args(inv, m.name, m.agent) else ""
        menu = menus.menu(
            menus.item("env", "Move the key to env…", "shield", event="a:plan") if fixes else None,
            menus.item("copy", "Copy name", "copy", copy=m.name))
        plans = {"env": fixes[0]} if fixes else {}
        cmd = m.command or m.host
        out.append(Item(id=m.name, name=m.name, desc=cmd, meta=f"{m.transport} · {cmd}", q=f"{m.name} {cmd}",
                        state=state, word=word, cells=(m.transport, ", ".join(m.env_names) or "–"), warn=warn,
                        menu=menu, attrs=plans_attr(plans)))
    return out


def _script_items(ctx, base) -> list[Item]:
    can_edit = ctx.get("can_edit") or set()
    out = []
    for s in ctx.get("rows") or ():
        edit = s.path in can_edit
        menu = menus.menu(
            menus.item("edit", "Edit…", "edit", href=_edit_href(base, "script", s.name)) if edit else None,
            menus.item("copy", "Copy path", "copy", copy=s.path),
            menus.SEP,
            menus.item("rm", "Remove…", "trash", danger=True, event="a:plan") if edit else None)
        out.append(Item(id=s.path, name=s.name, desc=s.purpose or "", meta=f"{s.kind} · {s.path}",
                        q=f"{s.name} {s.purpose} {s.path}", cells=(s.kind, s.path), menu=menu,
                        attrs=plans_attr({"rm": _delete("script", s.name)} if edit else {})))
    return out


ACTS = col("", sr="Actions")
DSPEC = {
    "skills": (col("", sr="Enabled"), col("c-nm", "name", "Name"), col("c-ds", "desc", "Description"),
               col("c-sc", "scope", "Scope", "hide-n"), ACTS),
    "rules": (col("c-dot", "status", sr="Status"), col("c-nm", "name", "Name"), col("c-pa", "path", "Path"),
              col("c-mo", "mode", "Mode", "hide-n"), col("c-ln", "lines", "Lines", "hide-n", True), ACTS),
    "mcp": (col("c-dot", "state", sr="State"), col("c-nm", "name", "Name"), col("c-cm", "command", "Command / host"),
            col("c-tr", "transport", "Transport", "hide-n"), col("c-ev", "env", "Env names", "hide-n"), ACTS),
    "scripts": (col("c-nm", "name", "Name"), col("c-ds", "purpose", "Purpose"), col("c-ty", "kind", "Kind", "hide-n"),
                col("c-pa", "path", "Path", "hide-n"), ACTS),
}


def _skill_segs(ctx, base) -> list[dict]:
    """All · Enabled · Disabled (the core's ?show=), keeping ?q= and ?sort=."""
    show = ctx.get("show") or "all"
    keep = {k: v for k, v in (("q", ctx.get("q")), ("sort", dict(ctx.get("hidden") or ()).get("sort"))) if v}
    return [{"key": k, "label": lab, "cur": k == show,
             "href": base + "?" + urlencode({"s": "skills", **keep, **({"show": k} if k != "all" else {})})}
            for k, lab in ctx.get("shows") or ()]


def detail(ctx: Mapping) -> Detail:
    inv, agent = ctx.get("inv"), ctx["agent"]
    base = ctx.get("base") or f"{PATH}/{quote(agent.name, safe='')}"
    tab = ctx.get("tab") or "skills"
    mode = ctx.get("mode") or ""
    counts = ctx.get("counts") or {}
    tabs = [{"key": k, "label": lab, "href": f"{base}?s={k}", "cur": k == tab, "count": counts.get(k)}
            for k, lab in DTABS]
    kv = [("Version", agent.version or "–", "")] + [
        (k, v or none, "mono" if v else "muted") for k, v, none in (
            ("Binary", agent.binary, "not found"), ("Config dir", agent.config_dir, "–"),
            ("Rules file", agent.rules_file, "–"), ("Skills dir", agent.skills_dir, "none of its own"),
            ("MCP config", agent.mcp_config, "none found"))]
    items: list[Item] = []
    if not mode:
        items = {"skills": lambda: _skill_items(ctx, agent, base), "rules": lambda: _rule_items(ctx, base),
                 "mcp": lambda: _mcp_items(ctx, inv), "scripts": lambda: _script_items(ctx, base)}[tab]()
    cols, order = gh(ctx.get("cols") if not mode else None, DSPEC[tab])
    note = ""
    if tab == "mcp":
        note = (f"Written to {agent.mcp_config}. " if agent.mcp_config else "No MCP config found. ") + \
            "Per-agent on/off is not supported yet; env shows names only."
    back = ctx.get("back") or f"{base}?s={tab}"
    rm = ""
    if mode == "edit" and ctx.get("can_delete") and ctx.get("kind") and ctx.get("obj"):
        rm = json.dumps({**_delete(ctx["kind"], ctx["obj"]), "after": back})
    return Detail(
        name=agent.name, label=agent.label or agent.name, kind=kind(agent.name), installed=agent.installed,
        base=base, kv=kv, tabs=tabs, tab=tab, mode=mode, cols=cols, sort=order, items=items,
        total=int(ctx.get("total") or len(items)), new=NEW.get(tab, ""), note=note,
        segs=_skill_segs(ctx, base) if tab == "skills" and not mode else [], q=ctx.get("q") or "",
        self_url=ctx.get("self_url") or f"{base}?s={tab}",
        lib_href=f"{PATH}?" + urlencode({"t": "library", "a": agent.name}),
        sync_sources=list(ctx.get("sync_sources") or ()), sync_targets=list(ctx.get("sync_targets") or ()),
        validation=None if ctx.get("validation") is None else [
            (VAL_DOT.get(lvl, "unknown"), lvl, subj, msg) for lvl, subj, msg in ctx["validation"]],
        new_note=NEW_NOTE.get(NEW.get(tab, ""), ("", ""))[0], new_hint=NEW_NOTE.get(NEW.get(tab, ""), ("", ""))[1],
        validate_href=f"{base}?s=rules&validate=1", back=back, rm=rm)


# ------------------------------------------------------------------ Jinja global
_logged: set[str] = set()


def _parts() -> dict:
    return {"frame": frame, "installed": installed, "detail": detail, "library": agents_library.library,
            "mcp": agents_library.mcp, "usage": agents_usage.usage, "findings": agents_usage.findings}


@pass_context
def dt_agents(ctx, part: str):
    """``dt_agents("frame" | "installed" | "detail" | "library" | "mcp" | "usage" | "findings")``; None on an error
    (logged once per error type), so a broken view model never breaks the page."""
    try:
        return _parts()[part](ctx)
    except Exception as e:  # noqa: BLE001 - a broken view model must not break the page
        name = f"{part}:{type(e).__name__}"
        if name not in _logged:
            _logged.add(name)
            log.exception("dt agents view model failed (%s)", part)
        return None
