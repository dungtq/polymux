"""Agents pages (read-only): overview at /agents, one agent at /agents/{name}.

Review/apply, the editor and Re-scan are in agentsedit.py; login state (incl. /agents/auth.json) in agentsauth.py."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request

from pm.agents.inventory import mutate as agentsmut
from pm.core import accounts, executors
from pm.core.models import AgentInfo, Inventory, Sidebar, SideRow
from pm.web import agentslib as lib
from pm.web.agentslib import INST_COLS, SECTIONS, TABS, TITLES
from pm.web.agentslib import agent_rows as _agent_rows
from pm.web.agentslib import clean as _clean
from pm.web.agentslib import cols as _cols
from pm.web.agentslib import installed_sorted as _installed_sorted
from pm.web.agentslib import library_ctx as _library_ctx
from pm.web.agentslib import low as _low
from pm.web.agentslib import skills_tab as _skills_tab
from pm.web.agentslib import sort_rows as _sorted
from pm.web.agentslib import split_ctx as _split_ctx
from pm.web.agentslib import stats as _stats
from pm.web.deps import AUTH
from pm.web.listing import wants_fragment
from pm.web.render import fragment, get_template, html, page_ctx

router = APIRouter()


def _sidebar(inv: Inventory, current: str) -> Sidebar:
    rows = tuple(
        SideRow(a.name, "/agents/" + quote(a.name), a.label or a.name, "running" if a.installed else "idle",
                str(a.skills_enabled + a.mcp_count))
        for a in _agent_rows(inv)
    )
    return Sidebar("Agents", rows, current, "/agents", "All agents", "Filter agents")


MCP_COLS = [("c-nm", "Name", "name"), ("c-tr", "Transport", "transport"), ("c-cm", "Command / host", "command"),
            ("c-ev", "Env names", "env"), ("c-ci", "Configured in", "agent")]
MCP_SORT = {"name": lambda m: _low(m.name), "transport": lambda m: m.transport,
            "command": lambda m: _low(m.command or m.host), "env": lambda m: len(m.env_names),
            "agent": lambda m: m.agent}


def _coverage(inv: Inventory) -> tuple[list[AgentInfo], list[dict]]:
    ags = [a for a in _agent_rows(inv) if a.installed]
    names: dict[str, set[str]] = {}
    for m in inv.mcp:
        names.setdefault(m.name, set()).add(m.agent)
    rows = [{"name": n, "has": [a.name in have for a in ags]} for n, have in sorted(names.items(), key=lambda kv: kv[0].lower())]
    return ags, rows


def _no_mcp(inv: Inventory) -> list[AgentInfo]:
    have = {m.agent for m in inv.mcp}
    return [a for a in inv.agents if a.installed and a.mcp_count == 0 and a.name not in have]


async def _usage_ctx(request: Request) -> dict:
    """The Usage tab of /agents: token/time totals over ?r= days, plus the Claude plan limits (``limits``)."""
    from pm.agents import limits, usage
    from pm.chat.stats import human

    qp = request.query_params
    r = qp.get("r") if qp.get("r") in ("7", "30", "all") else "30"
    agg = await executors.run_collectors(usage.aggregate, lib.home(), 0 if r == "all" else int(r),
                                        config_dirs=accounts.config_dirs(request.app.state.settings, "claude"))
    lim = await executors.run_collectors(limits.read_limits, request.app.state.settings.state_dir)
    tot = max(agg.total.total, 1)
    return dict(agg=agg, r=r, ranges=(("7", "7 days"), ("30", "30 days"), ("all", "All")), human=human,
                n=lambda v: f"{v:,}", share=lambda c: round(100 * c.total / tot), fmt_dur=usage.fmt_duration,
                limits=lim)


def _mcp_ctx(inv: Inventory, query: dict, sort: str) -> dict:
    """The MCP servers tab of /agents: servers table, agents without MCP, coverage grid."""
    sort = sort or "name"
    ctx: dict = {"servers": _sorted(list(inv.mcp), MCP_SORT, sort, "name"),
                 "cols": _cols("/agents", query, MCP_COLS, sort, "name"), "no_mcp": _no_mcp(inv),
                 "installed_count": sum(1 for x in inv.agents if x.installed)}
    ctx["cov_agents"], ctx["cov_rows"] = _coverage(inv)
    ctx["labels"] = {x.name: x.label or x.name for x in inv.agents}
    return ctx


@router.get("/agents", dependencies=AUTH)
async def agents_overview(request: Request):
    """?t= section. ``view`` is the asked one; ?t=findings (not in the classic tab row) keeps ``section``
    "installed" with its keys, so classic renders it as before, and adds the findings_ctx keys."""
    inv = await lib.inventory()
    qp = request.query_params
    view = qp.get("t") if qp.get("t") in (*SECTIONS, "findings") else "installed"
    sec = view if view in SECTIONS else "installed"
    sort = _clean(qp.get("sort"), 20)
    query = {k: v for k, v in qp.items() if k in ("t", "q", "type", "a", "n", "sort")}
    query["t"] = sec
    ctx: dict = {"inv": inv, "section": sec, "view": view, "titles": TITLES, "tab_items": lib.tab_items(), "stats": _stats(inv)}
    if sec == "installed":
        ctx["agents"] = _installed_sorted(inv, sort)
        ctx["cols"] = _cols("/agents", query, INST_COLS, sort, "")
        ctx["findings"] = inv.findings
        if view == "findings":
            ctx.update(lib.findings_ctx(inv))
    elif sec == "library":
        ctx.update(_library_ctx(inv, qp, query, sort))
        if wants_fragment(request):  # the live filter box: only the list
            return fragment(request, get_template("_agents_lib_list.html").render(**page_ctx(request), **ctx))
    elif sec == "usage":
        ctx.update(await _usage_ctx(request))
    else:
        ctx.update(_mcp_ctx(inv, query, sort))
    return html(request, "agents.html", nav="agents", **ctx)


RU_COLS = [("c-dot", "", "status"), ("c-nm", "Name", "name"), ("c-mo", "Mode", "mode"), ("c-ln", "Lines", "lines"),
           ("c-pa", "Path", "path"), ("c-sm", "Summary", "summary")]
RU_SORT = {"status": lambda r: r.status, "name": lambda r: _low(r.name), "mode": lambda r: r.mode,
           "lines": lambda r: r.lines, "path": lambda r: _low(r.path), "summary": lambda r: _low(r.summary)}
SC_COLS = [("c-nm", "Name", "name"), ("c-ty", "Kind", "kind"), ("c-ds", "Purpose", "purpose"),
           ("c-ud", "Used by", "used"), ("c-pa", "Path", "path")]
SC_SORT = {"name": lambda s: _low(s.name), "kind": lambda s: s.kind, "purpose": lambda s: _low(s.purpose),
           "used": lambda s: len(s.used_by), "path": lambda s: _low(s.path)}
DMCP_COLS = [("c-dot", "", "state"), ("c-nm", "Name", "name"), ("c-tr", "Transport", "transport"),
             ("c-cm", "Command / host", "command"), ("c-ev", "Env names", "env")]
DMCP_SORT = {"state": lambda m: str(m.enabled), **{k: v for k, v in MCP_SORT.items() if k != "agent"}}


def _skill_rows(inv: Inventory, agent: AgentInfo) -> list[dict]:
    """Skills shown for one agent: shared ones plus its own built-ins, with the switch state."""
    why = _toggle_block(agent, lib.home())
    return [
        {"name": s.name, "desc": s.description, "scope": s.scope, "path": s.path, "on": agent.name in s.enabled_for,
         "why": why or ("built-in skill of another agent" if s.scope != "shared" else ""),
         "edit": _skill_editable(s)}
        for s in inv.skills if s.scope != "agent" or s.owner == agent.name or agent.name in s.enabled_for
    ]


async def _rules_tab(qp, inv: Inventory, agent: AgentInfo, rules: list, sort: str, path: str, query: dict) -> dict:
    """The Rules tab of /agents/{name}: table, edit/delete rights, sync form, optional ?validate=1."""
    return dict(rows=_sorted(rules, RU_SORT, sort or "name", "name"), cols=_cols(path, query, RU_COLS, sort or "name", "name"),
                can_edit={r.path for r in rules if _rule_editable(r.path)},
                can_delete={r.path for r in rules if _rule_editable(r.path) and r.path.rsplit("/", 1)[-1] not in agentsmut.TOP_RULES},
                sync_sources=_sync_sources(inv), sync_targets=_sync_targets(inv, agent.name),
                validation=await _validation() if qp.get("validate") == "1" else None)


@router.get("/agents/{name}", dependencies=AUTH)
async def agent_detail(request: Request, name: str):
    if name in ("review", "apply", "rescan"):
        raise HTTPException(405, "POST only", headers={"Allow": "POST"})
    inv = await lib.inventory()
    agent = lib.find(inv, name)
    if agent is None:
        raise HTTPException(404, "unknown agent")
    qp = request.query_params
    tab = qp.get("s") if qp.get("s") in TABS else "skills"
    sort = _clean(qp.get("sort"), 20)
    path = "/agents/" + quote(agent.name)
    query = {k: v for k, v in qp.items() if k in ("s", "q", "show", "sort")}
    query["s"] = tab
    skills = _skill_rows(inv, agent)
    rules = [r for r in inv.rules if r.agent == agent.name]
    servers = [m for m in inv.mcp if m.agent == agent.name]
    scripts = [s for s in inv.scripts if agent.name in s.used_by]
    query["isort"] = _clean(qp.get("isort"), 20)
    query = {k: v for k, v in query.items() if v}
    ctx: dict = {
        **_split_ctx(inv, qp, path, query),
        "agent": agent, "tab": tab, "base": path, "agent_rows": _agent_rows(inv),
        "counts": {"skills": sum(1 for s in skills if s["on"]), "rules": len(rules),
                   "mcp": len(servers), "scripts": len(scripts)},
    }
    if tab == "skills":
        ctx.update(_skills_tab(qp, skills, sort, path, query))
        if wants_fragment(request):  # the live filter box: only the list
            return fragment(request, get_template("_agents_skills_list.html").render(**page_ctx(request), **ctx))
    elif tab == "rules":
        ctx.update(await _rules_tab(qp, inv, agent, rules, sort, path, query))
    elif tab == "mcp":
        ctx.update(rows=_sorted(servers, DMCP_SORT, sort or "name", "name"), cols=_cols(path, query, DMCP_COLS, sort or "name", "name"))
    else:
        ctx.update(rows=_sorted(scripts, SC_SORT, sort or "name", "name"), cols=_cols(path, query, SC_COLS, sort or "name", "name"),
                   can_edit={x.path for x in scripts if _script_editable(x.path)})
    return html(request, "agents_detail.html", nav="agents", **ctx)


# ------------------------------------------------------------------ what the detail tabs may offer


def _skill_editable(s) -> bool:
    return s.scope == "shared" and bool(agentsmut.SKILL_RE.fullmatch(s.name)) and s.path == "~/.agents/skills/" + s.name


def _rule_editable(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    if "*" in path or not agentsmut.RULE_RE.fullmatch(base):
        return False
    return path == "~/agents/" + base and base in agentsmut.TOP_RULES or path == "~/agents/rules/" + base


def _script_editable(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return path == "~/.agents/hooks/" + base and bool(agentsmut.SCRIPT_RE.fullmatch(base))


def _toggle_block(agent: AgentInfo, home: Path) -> str:
    """Why the skill switch is unavailable for this agent ("" when it is allowed)."""
    if not agent.installed:
        return "not installed"
    if not agent.skills_dir or not agent.skills_dir.startswith("~/"):
        return "no skills directory of its own"
    try:
        shared = (home / ".agents/skills").resolve()
        sdir = (home / agent.skills_dir[2:]).resolve()
    except (OSError, RuntimeError, ValueError):
        return "skills directory not usable"
    if sdir == shared or shared in sdir.parents:
        return "reads ~/.agents/skills directly"
    return ""


def _rule_files(inv: Inventory) -> dict[str, AgentInfo | None]:
    out: dict[str, AgentInfo | None] = {}
    if any(r.agent == "" and r.name == "AGENTS.md" for r in inv.rules):
        out["canonical"] = None
    for a in inv.agents:
        if a.rules_file and "*" not in a.rules_file and a.rules_file.startswith("~/") \
                and any(r.agent == a.name and r.path == a.rules_file for r in inv.rules):
            out[a.name] = a
    return out


def _sync_sources(inv: Inventory) -> list[tuple[str, str]]:
    return [(k, "Canonical ~/agents/AGENTS.md" if a is None else a.label or a.name) for k, a in _rule_files(inv).items()]


def _sync_targets(inv: Inventory, current: str) -> list[tuple[str, str, bool]]:
    return [(k, a.label or a.name, k == current) for k, a in _rule_files(inv).items() if a is not None and a.installed]


async def _validation() -> list[tuple[str, str, str]]:
    return await executors.run_collectors(agentsmut.validate_rules, home=lib.home())
