"""Chat request helpers shared by the route modules (chat, chatitems, builder, canvas, recap, subagents, usage,
next), and the display grouping of chat items (``blocks``)."""

from __future__ import annotations

import hashlib
from collections import Counter

from fastapi import HTTPException, Request

from pm.agents import registry, subagents
from pm.web import deps
from pm.web.deps import agent_or_404


def live_or_ended(request: Request, snap, agent_id: str):
    """(agent, ended): the running agent, or the stand-in of an ended one (read-only views only)."""
    try:
        return agent_or_404(snap, agent_id), False
    except HTTPException:
        a = ended_standin(deps.settings(request), snap, agent_id)
        if a is None:
            raise
        return a, True


def ended_standin(settings, snap, agent_id: str):
    """The read-only stand-in of an agent the registry knows but ``snap`` no longer runs, else None.
    Also the chat feed's fallback (``web/app.py``), so an open ended page gets its items ingested."""
    res = registry.resolve(settings, snap, agent_id)
    return registry.ended_agent(res.ended) if res is not None and res.ended is not None else None


def get_chatlog():
    """The stored chat (``pm.chat.log``); None while it is not importable. Tests monkeypatch this."""
    try:
        from pm.chat import log as chatlog
    except ImportError:
        return None
    return chatlog


async def composer_ctx(request: Request, agent) -> dict:
    """Context keys the shared composer (``_composer_macros.html``) needs for a live agent (agent page and builder)."""
    from pm.agents import catalog, launch, slash  # noqa: PLC0415
    from pm.agents import head as agenthead  # noqa: PLC0415
    from pm.core import executors, prefs  # noqa: PLC0415

    settings = deps.settings(request)
    try:
        models = (await executors.run_tmux(catalog.kind_models, settings, agent.kind)
                  if agent.kind in catalog.SWITCHABLE else [])
    except Exception:  # noqa: BLE001 - the picker is optional
        models = []
    cp = prefs.composer_prefs(settings)
    cur = agenthead.live_model(agent) if models else ""
    efforts = list(launch.EFFORTS.get(agent.kind, ())) if agent.kind in launch.EFFORT_FLAG else []
    cur_effort = await executors.run_tmux(agenthead.live_effort, agent) if efforts else ""
    # no reply, --model or --effort yet: show what Claude's settings default to (labelled "(default)")
    dmodel, deffort = (await executors.run_tmux(agenthead.settings_defaults, agent)
                       if (models or efforts) else ("", ""))
    unknown = cur == agent.kind.value
    head_pill = await executors.run_tmux(agenthead.live_pill, agent)  # the heading pill (_agentname.html agent_head)
    subs_running = await executors.run_collectors(subagents.running_count, agent)
    return {
        "subagents_running": subs_running,
        "head_pill": head_pill,
        "models": models,
        "cur_model": dmodel if unknown else cur,
        "model_is_default": unknown and bool(dmodel),
        "efforts": efforts,
        "cur_effort": cur_effort or deffort,
        "effort_is_default": not cur_effort and bool(deffort),
        "slash_catalog": slash.catalog_for(agent.kind.value),
        "clear_cmd": slash.clear_cmd(agent.kind.value),
        "composer_paste_lines": cp["paste_lines"],
        "composer_accept": ",".join("." + e for e in cp["exts"]),
    }


def _group_id(tool) -> str:
    """DOM id of a tool group: ``g-<parent seq>``; tools before any text: one group per turn."""
    if tool.parent:
        return f"g-{tool.parent}"
    return "g-0-" + hashlib.sha256((tool.turn or "").encode()).hexdigest()[:12]


def blocks(items) -> list[dict]:
    """Items (oldest first) -> render blocks: ``{"it": item}`` or a tool group
    ``{"group": id, "seq": first tool seq, "parent", "tools", "counts": [(name, n)], "running"}``.
    A group sits where its first tool is (right after its parent reply: tool seqs follow it). A ``think`` item
    (never a tool parent) does not end the open group: the tools after it share their parent's group id, and one
    id must not appear twice."""
    out: list[dict] = []
    cur: dict | None = None
    for it in items:
        if it.kind != "tool":
            if it.kind != "think":
                cur = None
            out.append({"it": it})
            continue
        gid = _group_id(it)
        if cur is None or cur["group"] != gid:
            cur = {"group": gid, "seq": it.seq, "parent": it.parent, "tools": []}
            out.append(cur)
        cur["tools"].append(it)
    for b in out:
        if "group" in b:
            b["counts"] = list(Counter(t.name or "tool" for t in b["tools"]).items())
            b["running"] = sum(1 for t in b["tools"] if t.status == "run")
    return out
