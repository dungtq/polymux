"""The dt Agents page: Library (one row per skill, rule or script across agents) and MCP servers (one row per server,
the coverage grid). View models for ``dt_agents("library")`` / ``dt_agents("mcp")`` (``pm.web.dt.agents``).

Library rows are grouped here from the inventory (``inv``): the core's ``rows`` are one per file (``AGENTS.md`` once
per agent). A rule that is an agent's own rules file joins the canonical ``AGENTS.md`` row; other rules group by name
without the extension (``cfg.md`` and ``cfg.mdc`` are one row); skills and scripts by name. "same / N differ" counts
the rules copies whose status is ``drift``; skills are symlinks and never differ. Filters and the page size come from
the URL (the core's ``q``, ``type``, ``a``, ``n``, ``sort``), so the filter box, the type segments, the agent select
and the sort headers load ``#a-lib`` (the core answers htmx with ``_agents_lib_list.html``).

Edit / Remove are offered where the core's editor works: skills in ``~/.agents/skills``, rules in ``~/agents`` (the
top files only edited, never removed), scripts in ``~/.agents/hooks``; the core re-checks every path. Remove backs
the files up (no pm trash). MCP: names only; per-agent on/off and "add to an agent" are not in the core, so the
coverage dots are read-only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

from pm.agents.inventory import mutate as agentsmut
from pm.web.dt import menus
from pm.web.dt.agents_base import BACKED, BACKUPS, PATH, col, gh, kind, label_of, plan, plans_attr

TYPES = (("", "All"), ("skill", "Skills"), ("rule", "Rules"), ("script", "Scripts"))
LIMITS = ("20", "50", "all")
SORTS = ("type", "name", "agents", "copies")
TOP_RULE = "AGENTS.md"
UNUSED = {"skill": "not linked", "rule": "canonical", "script": "unused"}  # Copies / Agents of an item no agent uses
KEY_ARGS = "API key passed as a CLI argument for MCP server '{}'"


@dataclass
class Group:
    """One library item before it becomes a row: every file of it, across agents."""
    type: str
    name: str
    desc: str = ""
    scope: str = ""
    agents: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    drift: list[str] = field(default_factory=list)  # rules: agents whose copy differs from the canonical
    canonical: bool = False

    def add(self, agents, path: str, desc: str = "", scope: str = "") -> None:
        self.agents.extend(a for a in agents if a and a not in self.agents)
        if path and path not in self.paths:
            self.paths.append(path)
        self.desc = self.desc or desc
        self.scope = self.scope or scope


def _stem(name: str) -> str:
    for ext in (".mdc", ".md"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


def items(inv) -> list[Group]:
    """Every library item of the inventory, grouped (order: type, then name)."""
    if inv is None:
        return []
    top = {a.name: a.rules_file for a in inv.agents if a.rules_file}
    out: dict[tuple[str, str], Group] = {}

    def group(t: str, name: str) -> Group:
        return out.setdefault((t, name.lower()), Group(t, name))

    for s in inv.skills:
        group("skill", s.name).add(list(s.enabled_for) + ([s.owner] if s.owner else []), s.path, s.description,
                                   "shared" if s.scope == "shared" else "agent")
    for r in inv.rules:
        own = r.agent and top.get(r.agent) == r.path
        g = group("rule", TOP_RULE if own or (not r.agent and r.name == TOP_RULE) else _stem(r.name))
        g.add([r.agent], r.path, r.summary, "canonical" if not r.agent else "agent")
        if not r.agent:
            g.canonical, g.scope = True, "canonical"
        if r.status == "drift" and r.agent and r.agent not in g.drift:
            g.drift.append(r.agent)
    for sc in inv.scripts:
        group("script", sc.name).add(list(sc.used_by), sc.path, sc.purpose, sc.kind)
    return sorted(out.values(), key=lambda g: (g.type, g.name.lower()))


# ------------------------------------------------------------------ what a row may offer
def _editable(g: Group) -> str:
    """The base name the core's editor takes ("" = not editable here): the core's own rule (``agentspage``
    _skill_editable / _rule_editable / _script_editable, private to a route module) with the domain's name patterns."""
    for p in g.paths:
        base = p.rsplit("/", 1)[-1]
        if "*" in p:
            continue
        if g.type == "skill" and g.scope == "shared" and agentsmut.SKILL_RE.fullmatch(g.name) \
                and p == f"~/.agents/skills/{g.name}":
            return g.name
        top = p == f"~/agents/{base}" and base in agentsmut.TOP_RULES
        if g.type == "rule" and agentsmut.RULE_RE.fullmatch(base) and (top or p == f"~/agents/rules/{base}"):
            return base
        if g.type == "script" and agentsmut.SCRIPT_RE.fullmatch(base) and p == f"~/.agents/hooks/{base}":
            return base
    return ""


def _host(g: Group, inv) -> str:
    """An agent whose detail page hosts the editor (any agent works; its own first)."""
    names = [a.name for a in inv.agents]
    for a in g.agents:
        if a in names:
            return a
    inst = [a.name for a in inv.agents if a.installed]
    return (inst or names or [""])[0]


def _link_targets(g: Group, inv) -> list:
    return [a for a in inv.agents if a.installed and a.skills_dir.startswith("~/") and a.name not in g.agents]


def _menu(g: Group, inv) -> tuple[list[dict], dict]:
    """(⋯ items, planned changes by item id) of one library row."""
    plans: dict[str, dict] = {}
    base = _editable(g)
    host = _host(g, inv)
    edit = None
    if base and host:
        edit = menus.item("edit", "Edit…", "edit", href=f"{PATH}/{quote(host, safe='')}/edit?"
                          + urlencode({"kind": g.type, "name": base}))
    else:
        edit = menus.item("edit", "Edit…", "edit", disabled=True,
                          why="plugin skill" if g.scope == "plugin" else "lives in an agent's own folder")
    sync = menus.item("sync", "Sync copies…", "swap", disabled=True,
                      why="only rules files sync" if g.type != "rule" else "all copies are the same")
    if g.type == "rule" and g.drift and g.canonical and g.name == TOP_RULE:
        sync = menus.item("sync", "Sync copies…", "swap", note=f"{len(g.drift)} differ", event="a:plan")
        plans["sync"] = plan([("action", "rules_sync"), ("source", "canonical"), *(("targets", a) for a in g.drift)],
                             f"Sync {len(g.drift)} copies of {g.name}", "Sync", f"Synced {g.name}")
    links = []
    if g.type == "skill":
        for a in _link_targets(g, inv):
            iid = f"link:{a.name}"
            links.append(menus.item(iid, f"Link into {a.label or a.name}", "link", event="a:plan"))
            plans[iid] = plan([("action", "skill_toggle"), ("agent", a.name), ("skill", g.name), ("enable", "1")],
                              f"Link {g.name} into {a.label or a.name}", "Link", f"Linked {g.name}")
        if not links:
            links = [menus.item("link", "Link into…", "link", disabled=True, why="linked everywhere it can be")]
    rm = menus.item("rm", "Remove…", "trash", danger=True, disabled=True,
                    why="a top rules file" if base in agentsmut.TOP_RULES else "not in a folder pm edits")
    if base and base not in agentsmut.TOP_RULES:
        rm = menus.item("rm", "Remove…", "trash", danger=True, event="a:plan")
        plans["rm"] = plan([("action", f"{g.type}_delete"), ("name", base), ("confirm", base)],
                           f"Remove {g.type} {base}", "Remove", f"Removed {base} (backed up to {BACKUPS})", typed=base,
                           undo=BACKED)
    view = menus.item("view", "View", "file", disabled=True, why="no reader for files outside a session yet")
    return menus.menu(view, edit, sync, links, menus.SEP,
                      menus.item("copy", "Copy path", "copy", copy=g.paths[0]) if g.paths else None, menus.SEP,
                      rm), plans


# ------------------------------------------------------------------ Library
@dataclass(frozen=True)
class Chip:
    name: str
    label: str
    kind: str


@dataclass(frozen=True)
class LRow:
    id: str
    type: str
    name: str
    desc: str
    chips: list[Chip]
    copies: str  # "same" | "N differ" | "one" | UNUSED[type]
    unused: str  # the Agents cell when no agent has it
    differ: int
    path: str
    meta: str
    menu: list
    attrs: dict | None


@dataclass(frozen=True)
class Library:
    q: str
    segs: list[dict]
    agent_opts: list
    a: str
    a_url: str  # the agent select's hx-get (every other parameter kept)
    cols: list[dict]
    sort: str | None
    rows: list[LRow]
    shown: int
    total: int
    total_all: int
    more_url: str | None
    empty: str
    clear_url: str | None
    self_url: str
    backups: str


def lib_url(params: Mapping[str, str], **kw) -> str:
    p = {"t": "library", **{k: v for k, v in {**params, **kw}.items() if v not in (None, "") and k != "t"}}
    return f"{PATH}?" + urlencode(p)


def _sort(rows: list[Group], sort: str) -> list[Group]:
    key = sort.lstrip("-") if sort.lstrip("-") in SORTS else "type"
    fn = {"type": lambda g: (g.type, g.name.lower()), "name": lambda g: g.name.lower(),
          "agents": lambda g: (len(g.agents), g.name.lower()), "copies": lambda g: (len(g.drift), g.name.lower())}[key]
    out = sorted(rows, key=fn)
    return out[::-1] if sort.startswith("-") and key == sort.lstrip("-") else out


def _row(g: Group, inv) -> LRow:
    menu, plans = _menu(g, inv)
    n = len(g.agents)
    copies = f"{len(g.drift)} differ" if g.drift else "same" if n > 1 else "one" if n else UNUSED[g.type]
    chips = [Chip(a, label_of(inv, a).split(" ")[0], kind(a)) for a in g.agents]
    meta = " · ".join(x for x in (g.type, f"{n} agent{'' if n == 1 else 's'}" if n else UNUSED[g.type],
                                  f"{len(g.drift)} differ" if g.drift else "") if x)
    return LRow(id=f"{g.type}:{g.name}", type=g.type, name=g.name, desc=g.desc, chips=chips, copies=copies,
                unused=UNUSED[g.type] if g.type == "skill" else "–",
                differ=len(g.drift), path=", ".join(g.paths), meta=meta, menu=menu, attrs=plans_attr(plans))


def library(ctx: Mapping) -> Library:
    inv = ctx.get("inv")
    q, typ, a = (ctx.get("q") or "").strip(), ctx.get("typ") or "", ctx.get("a") or ""
    n = ctx.get("n") if ctx.get("n") in LIMITS else "20"
    sort = ctx.get("sort") or ""
    params = {"q": q, "type": typ, "a": a, "sort": sort, "n": n if n != "20" else ""}
    every = items(inv)
    words = q.lower().split()
    base = [g for g in every if (not a or a in g.agents)
            and all(w in f"{g.name} {g.desc} {' '.join(g.paths)}".lower() for w in words)]
    counts = {t: sum(1 for g in base if not t or g.type == t) for t, _ in TYPES}
    rows = _sort([g for g in base if not typ or g.type == typ], sort)
    shown = rows if n == "all" else rows[: int(n)]
    segs = [{"key": t or "all", "label": lab, "count": counts[t], "cur": t == typ,
             "href": lib_url(params, type=t or None, n=None)} for t, lab in TYPES]

    def sort_url(key: str) -> str:
        first = "name" if key in ("type", "name") else "-" + key  # counts: the most first
        nxt = (first[1:] if first.startswith("-") else "-" + first) if sort == first else first
        return lib_url(params, sort=nxt)
    cols = [{"label": "Type", "key": "type", "href": sort_url("type"), "cls": "hide-n"},
            {"label": "Name", "key": "name", "href": sort_url("name")}, {"label": "Description", "cls": "hide-n"},
            {"label": "Agents", "key": "agents", "href": sort_url("agents")},
            {"label": "Copies", "key": "copies", "href": sort_url("copies"), "cls": "hide-s"}, {"sr": "Actions"}]
    filtered = bool(q or typ or a)
    empty = "" if shown else ("Nothing matches." if filtered else "No skills, rules or scripts found.")
    return Library(
        q=q, segs=segs, agent_opts=[("", "All agents")] + [(x.name, x.label or x.name) for x in inv.agents],
        a=a, a_url=lib_url(params, a=None), cols=cols, sort=sort if sort.lstrip("-") in SORTS else None,
        rows=[_row(g, inv) for g in shown], shown=len(shown), total=len(rows), total_all=len(every),
        more_url=lib_url(params, n="all") if len(shown) < len(rows) else None, empty=empty,
        clear_url=f"{PATH}?t=library" if filtered and not shown else None, self_url=lib_url(params), backups=BACKUPS)


# ------------------------------------------------------------------ MCP servers
def key_fixes(inv, server: str, agent: str = "") -> list[dict]:
    """Planned "move the key to env" changes for a server (one per config), from the findings' fix fields."""
    out = []
    for f in getattr(inv, "findings", ()) or ():
        fx = dict(f.fix) if f.fix else {}
        if fx.get("action") == "mcp_env" and fx.get("server") == server and (not agent or f.agent == agent):
            out.append(plan(f.fix, f"Move the key of {server} to env", "Apply", f"Moved the key of {server} to env"))
    return out


def key_in_args(inv, server: str, agent: str = "") -> bool:
    text = KEY_ARGS.format(server)
    return any(text in f.text and (not agent or f.agent == agent) for f in getattr(inv, "findings", ()) or ())


@dataclass(frozen=True)
class MRow:
    id: str
    name: str
    transport: str
    command: str
    env: str
    chips: list[Chip]
    warn: bool
    configs: str
    meta: str
    q: str
    menu: list
    attrs: dict | None


@dataclass(frozen=True)
class Mcp:
    rows: list[MRow]
    cols: list[dict]
    sort: str | None
    no_mcp: list[str]
    cov_agents: list[Chip]
    cov_rows: list[tuple[str, list[bool]]]
    cov_cols: list[dict]  # tb.gh columns of the coverage table


MCP_SPEC = (col("c-nm", "name", "Server"), col("c-tr", "transport", "Type", "hide-n"),
            col("c-cm", "command", "Command / host"), col("c-ev", "env", "Env names", "hide-n"),
            col("c-ci", "agent", "Configured in", "hide-s"), col("", sr="Actions"))


def mcp(ctx: Mapping) -> Mcp:
    inv = ctx.get("inv")
    by: dict[str, list] = {}
    for m in ctx.get("servers") or ():
        by.setdefault(m.name, []).append(m)
    rows = []
    for name, ms in by.items():
        fixes = key_fixes(inv, name)
        agents = list(dict.fromkeys(m.agent for m in ms))
        env = sorted({e for m in ms for e in m.env_names})
        cmd = next((m.command or m.host for m in ms if m.command or m.host), "")
        menu = menus.menu(*(menus.item(f"env:{i}", "Move the key to env…" + (f" ({i + 1})" if len(fixes) > 1 else ""),
                                       "shield", event="a:plan") for i in range(len(fixes))),
                          menus.item("copy", "Copy name", "copy", copy=name))
        rows.append(MRow(id=name, name=name, transport=ms[0].transport, command=cmd, env=", ".join(env) or "–",
                         chips=[Chip(a, label_of(inv, a).split(" ")[0], kind(a)) for a in agents],
                         warn=bool(fixes) or key_in_args(inv, name), configs=", ".join(m.config_path for m in ms),
                         meta=f"{ms[0].transport} · {cmd}", q=f"{name} {cmd}", menu=menu,
                         attrs=plans_attr({f"env:{i}": p for i, p in enumerate(fixes)})))
    cols, order = gh(ctx.get("cols"), MCP_SPEC)
    labels = ctx.get("labels") or {}
    return Mcp(rows=rows, cols=cols, sort=order, no_mcp=[a.label or a.name for a in ctx.get("no_mcp") or ()],
               cov_agents=[Chip(a.name, labels.get(a.name, a.name).split(" ")[0], kind(a.name))
                           for a in ctx.get("cov_agents") or ()],
               cov_rows=[(r["name"], list(r["has"])) for r in ctx.get("cov_rows") or ()],
               cov_cols=[{"label": "Server"}] + [{"label": labels.get(a.name, a.name).split(" ")[0], "cls": "a-cc",
                                                  "title": a.label or a.name} for a in ctx.get("cov_agents") or ()])
