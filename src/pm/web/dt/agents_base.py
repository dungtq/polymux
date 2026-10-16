"""Shared pieces of the dt Agents page view models (``pm.web.dt.agents``, ``agents_library``, ``agents_usage``):
the page path, kind colours, the planned-change dicts js/agents.js runs, and the sort-header adapter for the core's
column dicts. No IO."""

from __future__ import annotations

import json
from collections.abc import Mapping

from pm.agents.inventory import mutate as agentsmut

PATH = "/agents"
BACKUPS = "~/" + agentsmut.BACKUP_REL  # where the core's apply keeps every changed or removed file
BACKED = f"The files are backed up to {BACKUPS} first."  # a Remove's typed confirm says this, not "cannot be undone"
KINDS = {"claude": "claude", "codex": "codex", "devin": "devin", "cursor": "cursor", "antigravity": "antigravity"}
# the core's finding kinds in display order (pm.web.agentslib.FINDING_KINDS; a test keeps them equal)
FINDING_KINDS = (("security", "Security"), ("rules", "Rules"), ("skills", "Skills not linked"),
                 ("index", "Stale index"), ("shims", "Broken shims"), ("notes", "Notes"))


def kind(name: str) -> str:
    """data-kind of an agent name (kind colour); unknown agents use "other"."""
    return KINDS.get(name, "other")


def label_of(inv, name: str) -> str:
    for a in getattr(inv, "agents", ()) or ():
        if a.name == name:
            return a.label or a.name
    return name


def plan(fields, title: str, ok: str, done: str, typed: str = "", quiet: bool = False, undo: str = "") -> dict:
    """One planned change for js/agents.js: ``fields`` = (name, value) pairs for /agents/review (action first; a list
    field repeats its name), ``title`` / ``ok`` = the review dialog, ``done`` = the toast after the apply, ``typed`` =
    a name to type, ``undo`` = the typed dialog's last sentence instead of "This cannot be undone." (``BACKED``),
    ``quiet`` = apply without the dialog (a switch)."""
    out = {"fields": [[k, str(v)] for k, v in fields], "title": title, "ok": ok, "done": done}
    if typed:
        out["typed"] = typed
    if undo:
        out["undo"] = undo
    if quiet:
        out["quiet"] = True
    return out


def plans_attr(plans: Mapping[str, dict]) -> dict | None:
    """``tb.row`` attrs carrying the row's planned changes (``None`` when it has none)."""
    return {"data-a-plans": json.dumps(plans, separators=(",", ":"))} if plans else None


def gh(cols: Mapping | None, spec) -> tuple[list[dict], str | None]:
    """tb.gh columns from the core's sort-header dicts (``agentslib.cols``: label, href, active, aria) and the current
    order. ``spec`` = (core column id | None, key, {extra tb.gh keys}); a None id is a plain column."""
    out, order = [], None
    for cid, key, extra in spec:
        c = (cols or {}).get(cid) if cid else None
        col = {**extra, "label": c["label"] if c else extra.get("label", "")}
        if c:
            col.update(key=key, href=c["href"])
            if c.get("active"):
                order = ("-" if c.get("aria") == "descending" else "") + key
        out.append(col)
    return out, order


def col(cid: str | None, key: str = "", label: str = "", cls: str = "", num: bool = False, sr: str = ""):
    """One ``gh`` spec entry: the core's column id (None = a plain column), the sort key, the tb.gh extras."""
    return cid, key, {k: v for k, v in (("label", label), ("cls", cls), ("num", num), ("sr", sr)) if v}
