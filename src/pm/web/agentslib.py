"""Context of the /agents tables: column-header sort helpers, the Library tab (/agents?t=library), an agent's Skills
tab (/agents/{name}?s=skills), the Findings view (/agents?t=findings) and the Installed split shared by the read
routes (routes/agentspage.py) and the mutation routes (routes/agentsedit.py). Pure functions over the Inventory,
plus ``home()`` / ``inventory()`` (the one place the routes get them; tests patch ``home``)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from pm.core import executors
from pm.core.models import AgentInfo, Inventory

TYPES = ("skill", "rule", "script")
LIMITS = ("20", "50", "all")
SHOWS = (("all", "All"), ("enabled", "Enabled"), ("disabled", "Disabled"))
ASC, DESC = "▲", "▼"


SECTIONS = ("installed", "library", "mcp", "usage")
TITLES = {"installed": "Installed", "library": "Skills, rules & scripts", "mcp": "MCP servers", "usage": "Usage"}
TABS = ("skills", "rules", "mcp", "scripts")
# Findings view (?t=findings): an extra section, not in the classic tab row. Kinds in display order.
FINDING_KINDS = (("security", "Security"), ("rules", "Rules"), ("skills", "Skills not linked"),
                 ("index", "Stale index"), ("shims", "Broken shims"), ("notes", "Notes"))


def home() -> Path:
    return Path.home()


async def inventory() -> Inventory:
    from pm.agents.inventory import read as agentsinv

    return await executors.run_collectors(lambda: agentsinv.get_inventory(background=True))


def find(inv: Inventory, name: str) -> AgentInfo | None:
    from pm.agents.inventory import read as agentsinv

    return agentsinv.find_agent(inv, name)


def clean(value: str | None, size: int = 80) -> str:
    return (value or "").strip()[:size]


def sort_rows(rows: list, spec: dict, sort: str, default: str) -> list:
    """Sort by a key from ``spec`` ("-key" reverses); ties by name; unknown key -> default."""
    key = sort.lstrip("-")
    if key not in spec:
        key, sort = default, default
    out = sorted(rows, key=lambda r: (spec[key](r), str(row_name(r)).lower()))
    return out[::-1] if sort.startswith("-") else out


def row_name(row) -> str:
    return row["name"] if isinstance(row, dict) else row.name


def cols(path: str, query: dict, columns: list[tuple[str, str, str]], sort: str, default: str,
         param: str = "sort") -> dict:
    """Header cells by column id: plain ?sort= links, the active one links to its reverse."""
    base = {k: v for k, v in query.items() if k != param}
    key = sort.lstrip("-")
    if key not in {k for _, _, k in columns}:
        key, sort = default, default
    out = {}
    for cid, label, k in columns:
        on = k == key
        desc = sort.startswith("-")
        nxt = k if on and desc else "-" + k if on else k
        out[cid] = {
            "label": label, "active": on, "href": path + "?" + urlencode({**base, param: nxt}),
            "arrow": (DESC if desc else ASC) if on else "", "aria": ("descending" if desc else "ascending") if on else "none",
        }
    return out


def low(s: str) -> str:
    return s.lower()


def library_rows(inv: Inventory) -> list[dict]:
    rows = []
    for s in inv.skills:
        ags = tuple(s.enabled_for)
        rows.append({"type": "skill", "name": s.name, "desc": s.description, "scope": s.scope,
                     "agents": ags, "path": s.path, "all": set(ags) | ({s.owner} if s.owner else set())})
    for r in inv.rules:
        ags = (r.agent,) if r.agent else ()
        rows.append({"type": "rule", "name": r.name, "desc": r.summary, "scope": "agent" if r.agent else "canonical",
                     "agents": ags, "path": r.path, "all": set(ags)})
    for sc in inv.scripts:
        ags = tuple(sc.used_by)
        rows.append({"type": "script", "name": sc.name, "desc": sc.purpose, "scope": sc.kind,
                     "agents": ags, "path": sc.path, "all": set(ags)})
    return rows


LIB_COLS = [("c-ty", "Type", "type"), ("c-nm", "Name", "name"), ("c-ds", "Description", "desc"),
            ("c-sc", "Scope", "scope"), ("c-ags", "Agents", "agents"), ("c-pa", "Path", "path")]
LIB_SORT = {"type": lambda r: r["type"], "name": lambda r: low(r["name"]), "desc": lambda r: low(r["desc"]),
            "scope": lambda r: r["scope"], "agents": lambda r: len(r["agents"]), "path": lambda r: low(r["path"])}


def library_ctx(inv: Inventory, qp, query: dict, sort: str) -> dict:
    """The Library tab of /agents: filters (?q, ?type, ?a), sort, the ?n= page size."""
    q, typ, a = clean(qp.get("q")).lower(), qp.get("type") if qp.get("type") in TYPES else "", clean(qp.get("a"), 40)
    n = qp.get("n") if qp.get("n") in LIMITS else "20"
    rows = library_rows(inv)
    total_all = len(rows)
    if typ:
        rows = [r for r in rows if r["type"] == typ]
    if a:
        rows = [r for r in rows if a in r["all"]]
    if q:
        rows = [r for r in rows if q in " ".join((r["name"], r["desc"], r["path"], r["scope"], r["type"])).lower()]
    rows = sort_rows(rows, LIB_SORT, sort, "type")
    total = len(rows)
    shown_rows = rows if n == "all" else rows[: int(n)]
    base = {k: v for k, v in query.items() if k != "n"}
    return dict(
        rows=shown_rows, shown=len(shown_rows), total=total, total_all=total_all, q=clean(qp.get("q")),
        typ=typ, a=a, n=n, types=TYPES, agent_opts=[(x.name, x.label or x.name) for x in inv.agents],
        cols=cols("/agents", query, LIB_COLS, sort, "type"), sort=sort,
        limit_links=[{"key": k, "label": "All" if k == "all" else k, "active": k == n,
                      "href": "/agents?" + urlencode({**base, "n": k})} for k in LIMITS],
        hidden=[(k, v) for k, v in query.items() if k in ("t", "q", "type", "a", "sort")],
        self_url="/agents?" + urlencode(query),
    )


SK_COLS = [("c-st", "State", "state"), ("c-nm", "Name", "name"), ("c-ds", "Description", "desc"),
           ("c-sc", "Scope", "scope"), ("c-pa", "Path", "path")]
SK_SORT = {"state": lambda r: r["on"], "name": lambda r: low(r["name"]), "desc": lambda r: low(r["desc"]),
           "scope": lambda r: r["scope"], "path": lambda r: low(r["path"])}


def skills_tab(qp, skills: list[dict], sort: str, path: str, query: dict) -> dict:
    """The Skills tab of /agents/{name}: ?show= (the Filter select) / ?q= filters and the sorted table."""
    show = qp.get("show") if qp.get("show") in {k for k, _ in SHOWS} else "all"
    q = clean(qp.get("q"))
    rows = skills
    if show != "all":
        rows = [r for r in rows if r["on"] == (show == "enabled")]
    if q:
        rows = [r for r in rows if q.lower() in " ".join((r["name"], r["desc"], r["path"])).lower()]
    return dict(rows=sort_rows(rows, SK_SORT, sort or "name", "name"), total=len(skills), shows=SHOWS, show=show, q=q,
                cols=cols(path, query, SK_COLS, sort or "name", "name"),
                hidden=[(k, v) for k, v in query.items() if k in ("s", "sort")],
                self_url=path + "?" + urlencode({k: v for k, v in query.items() if k in ("s", "q", "show", "sort")}))


def agent_rows(inv: Inventory) -> list[AgentInfo]:
    return sorted(inv.agents, key=lambda a: not a.installed)


def stats(inv: Inventory) -> str:
    n = sum(1 for a in inv.agents if a.installed)
    return (f"{n} installed · {len(inv.skills)} skills · {len(inv.rules)} rules · "
            f"{len(inv.scripts)} scripts · {len(inv.mcp)} MCP servers")


INST_COLS = [("c-ag", "Agent", "agent"), ("c-ver", "Version", "version"), ("c-bin", "Binary", "binary"),
             ("c-cfg", "Config dir", "config"), ("c-rul", "Rules file", "rules"),
             ("c-sk", "Skills", "skills"), ("c-mc", "MCP", "mcp")]
INST_SORT = {"agent": lambda a: low(a.label or a.name), "version": lambda a: a.version, "binary": lambda a: a.binary,
             "config": lambda a: a.config_dir, "rules": lambda a: a.rules_file,
             "skills": lambda a: a.skills_enabled, "mcp": lambda a: a.mcp_count}


def installed_sorted(inv: Inventory, sort: str) -> list[AgentInfo]:
    if sort.lstrip("-") not in INST_SORT:
        return agent_rows(inv)
    return sort_rows(list(inv.agents), INST_SORT, sort, "agent")


def tab_items() -> list[tuple[str, str, str]]:
    return [(k, TITLES[k], "/agents?t=" + k) for k in SECTIONS]


def split_ctx(inv: Inventory, qp, path: str, query: dict) -> dict:
    """The Installed table + Findings kept on the left of /agents/{name}: same title, tabs and table as /agents.
    Its own sort param (isort) so it does not clash with the detail tables' ?sort=."""
    isort = clean(qp.get("isort"), 20)
    return {"inv": inv, "section": "installed", "titles": TITLES, "stats": stats(inv), "tab_items": tab_items(),
            "agents": installed_sorted(inv, isort), "findings": inv.findings,
            "icols": cols(path, query, INST_COLS, isort, "", param="isort")}


def findings_ctx(inv: Inventory) -> dict:
    """The Findings view: findings grouped by kind (display order, empty kinds left out), counts per kind and the
    fixable count. A finding without a known kind counts as "notes"."""
    known = {k for k, _ in FINDING_KINDS}
    groups: dict[str, list] = {k: [] for k, _ in FINDING_KINDS}
    for f in inv.findings:
        groups[f.kind if f.kind in known else "notes"].append(f)
    return {"finding_groups": [(k, label, groups[k]) for k, label in FINDING_KINDS if groups[k]],
            "finding_kinds": [(k, label, len(groups[k])) for k, label in FINDING_KINDS if groups[k]],
            "finding_counts": {k: len(v) for k, v in groups.items()},
            "fixable": sum(1 for f in inv.findings if f.fix)}
