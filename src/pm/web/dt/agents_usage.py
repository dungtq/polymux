"""The dt Agents page: Usage (totals, by-day chart, by-model share, Claude plan limits, top sessions) and Findings
(grouped by kind, each with its planned fix). View models for ``dt_agents("usage")`` / ``dt_agents("findings")``
(``pm.web.dt.agents``).

Usage reads the core's ``agg`` (``pm.agents.usage.Aggregate``: days newest first, models, top + top_ids, total,
sessions), ``r`` / ``ranges`` and ``limits`` (``pm.agents.limits.Window``). A top session links to its pm record
``/a/<registry.mint_id("claude", session id)>`` (the ended agent page; pm knows the sessions it ran). The day chart's
bars are classes ``h0``…``h20`` (5 % steps of the busiest day), so the server draws the first state and no inline
style is needed.

Findings read ``finding_groups`` / ``fixable`` (``pm.web.agentslib.findings_ctx``): ``Finding.fix`` is the form of
``POST /agents/review`` (action first), turned into a plan for js/agents.js (review → the exact file operations in a
dialog → apply with the token; a 409 says the files changed and reviews again). A group with two or more fixes gets a
band button "Fix all N" (one dialog with every operation, then the applies in order).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from pm.agents import registry
from pm.chat.stats import human
from pm.web.dt.agents_base import PATH, kind, label_of, plan
from pm.web.dt.audit_errors import num

FIX_LABEL = {"mcp_env": "Move to env", "rules_sync": "Sync", "index_prune": "Remove line", "skill_toggle": "Link"}
FIX_DONE = {"mcp_env": "Moved the key to env", "rules_sync": "Synced the rules file",
            "index_prune": "Removed the stale line", "skills_link": "Linked the skills", "skill_toggle": "Linked"}
DAYS = 30  # bars in the chart (the newest days of the range)


# ------------------------------------------------------------------ Usage
@dataclass(frozen=True)
class Bar:
    day: str
    label: str  # "10-07"
    pct: int  # share of the range's new tokens
    h: int  # 0..20: height class (5 % steps of the busiest day)
    title: str


@dataclass(frozen=True)
class Model:
    name: str
    pct: int
    share: str  # "77%", "<1%"
    replies: str
    out: str


@dataclass(frozen=True)
class Top:
    id: str
    label: str
    sid: str
    href: str | None
    share: str
    replies: str
    span: str
    cwd: str


@dataclass(frozen=True)
class Usage:
    segs: list[dict]
    empty: bool
    totals: list[dict]  # meter dicts (parts/_meter.html)
    bars: list[Bar]
    models: list[Model]
    limits: list[dict]  # meter dicts
    top: list[Top]
    note: str


def _share(part: int, whole: int) -> tuple[int, str]:
    if not whole or not part:
        return 0, "0%"
    p = 100 * part / whole
    return round(p), "<1%" if p < 1 else f"{round(p)}%"


def _bars(agg) -> list[Bar]:
    days = list(reversed(agg.days[:DAYS]))
    tot = max(agg.total.total, 1)
    peak = max((c.total for _, c in days), default=0) or 1
    out = []
    for d, c in days:
        pct, share = _share(c.total, tot)
        out.append(Bar(day=d, label=d[5:] if len(d) >= 10 else d, pct=pct, h=round(20 * c.total / peak),
                       title=f"{d}: {share} of new tokens · {num(c.replies)} replies"))
    return out


def _limits(windows) -> list[dict]:
    out = []
    for w in windows or ():
        lv = {"red": "crit"}.get(w.level, w.level)
        sub = "reset" if w.reset == "reset" else (f"resets {w.reset}" if w.reset else "")
        out.append({"key": w.label, "label": w.name or w.label, "value": f"{w.pct}%", "pct": w.pct, "level": lv,
                    "sub": sub, "title": f"Resets {w.abs}" if w.abs else ""})
    return out


def usage(ctx: Mapping) -> Usage:
    from pm.agents import usage as usagemod

    agg, r = ctx.get("agg"), ctx.get("r") or "30"
    segs = [{"key": k, "label": lab, "cur": k == r, "href": f"{PATH}?t=usage&r={k}"}
            for k, lab in ctx.get("ranges") or ()]
    limits = _limits(ctx.get("limits"))
    if agg is None or not agg.sessions:
        return Usage(segs=segs, empty=True, totals=[], bars=[], models=[], limits=limits, top=[], note="")
    t = agg.total
    totals = [{"key": "sessions", "label": "Sessions", "value": num(agg.sessions)},
              {"key": "replies", "label": "Replies", "value": num(t.replies)},
              {"key": "tools", "label": "Tool calls", "value": num(t.tools)},
              {"key": "new", "label": "New tokens", "value": human(t.total), "sub": "in + out + cache written"},
              {"key": "cache", "label": "Cache read", "value": human(t.cache_read),
               "sub": f"{t.cache_hit}% cache hit"}]
    models = []
    for m, c in agg.models:
        pct, share = _share(c.total, t.total)
        models.append(Model(name=m, pct=max(pct, 1) if c.total else 0, share=share, replies=num(c.replies),
                            out=human(c.output)))
    top = []
    ids = list(getattr(agg, "top_ids", None) or ())
    for i, (label, cwd, c, dur) in enumerate(agg.top):
        sid = ids[i] if i < len(ids) else ""
        _, share = _share(c.total, t.total)
        top.append(Top(id=sid or f"{label}:{i}", label=label, sid=sid[:8], cwd=cwd, share=share,
                        href=f"/a/{registry.mint_id('claude', sid)}" if sid else None, replies=num(c.replies),
                        span=usagemod.fmt_duration(dur)))
    return Usage(segs=segs, empty=False, totals=totals, bars=_bars(agg), models=models, limits=limits, top=top,
                 note="Claude Code transcripts, sub-agents included · share is of new tokens (in + out + cache "
                      "written)")


# ------------------------------------------------------------------ Findings
@dataclass(frozen=True)
class FRow:
    id: str
    text: str
    warn: bool
    agent: str
    agent_label: str
    kind: str
    fix: str  # the plan for js/agents.js as JSON ("" = no fix)
    fix_label: str


@dataclass(frozen=True)
class FGroup:
    key: str
    label: str
    rows: list[FRow]
    fixable: int
    tone: str  # "warn" when a row is a warning
    actions: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class Findings:
    groups: list[FGroup]
    total: int
    fixable: int


def _fix(f) -> tuple[dict | None, str]:
    if not f.fix:
        return None, ""
    action = dict(f.fix).get("action", "")
    if action == "skills_link":
        n = sum(1 for k, _ in f.fix if k == "skills")
        lab = f"Link {n}"
    else:
        lab = FIX_LABEL.get(action, "Fix")
    title = f"{lab}: {f.text}"
    return plan(f.fix, title if len(title) <= 90 else title[:89] + "…", "Apply", FIX_DONE.get(action, "Fixed")), lab


def frow(f, inv) -> FRow:
    fx, lab = _fix(f)
    rid = hashlib.sha1(f"{f.kind}\0{f.agent}\0{f.text}".encode()).hexdigest()[:12]
    return FRow(id=rid, text=f.text, warn=f.severity == "warn", agent=f.agent,
                agent_label=label_of(inv, f.agent).split(" ")[0] if f.agent else "", kind=kind(f.agent),
                fix=json.dumps(fx) if fx else "", fix_label=lab)


def findings(ctx: Mapping) -> Findings:
    inv = ctx.get("inv")
    groups = []
    for key, label, fs in ctx.get("finding_groups") or ():
        rows = [frow(f, inv) for f in fs]
        n = sum(1 for r in rows if r.fix)
        acts = [{"label": f"Fix all {n}", "icon": "check", "title": f"Review every fix of {label} in one list",
                 "attrs": {"data-a-fixall": key}}] if n > 1 else []
        groups.append(FGroup(key=key, label=label, rows=rows, fixable=n,
                             tone="warn" if any(r.warn for r in rows) and key != "notes" else "", actions=acts))
    return Findings(groups=groups, total=sum(len(g.rows) for g in groups if g.key != "notes"),
                    fixable=int(ctx.get("fixable") or 0))
