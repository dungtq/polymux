"""Quick chat send: validate a request, store the user message, stream the answer, store it.

Kept out of ``run`` because ``db`` imports ``run`` (session_alive). Errors are ``SendInvalid`` (a
``pm.core.errors.Invalid`` with the failing form ``field``); the route maps them to its audited 400.
Message text is never logged or audited here; ``on_end`` gets lengths, ids and the profile only.

btw (``agent``): the conversation is about another agent's session. Its recent stored chat
(``pm.chat.log``, never the pane) goes to claude as system-prompt context; the agent's tmux pane is
never touched. The agent is fixed by the first turn and stored on the conversation. A btw conversation
always runs with profile "none" (no tools, MCP or skills): its context is untrusted text.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace

from pm.chat import log as chatlog
from pm.core.config import Settings
from pm.core.errors import Invalid, PmError
from pm.core.models import Agent
from pm.qchat import cfg as qchatcfg
from pm.qchat import db as qchatdb
from pm.qchat import run as qchat


class SendInvalid(Invalid):
    def __init__(self, field: str, msg: str) -> None:
        super().__init__(msg)
        self.field = field


@dataclass
class SendReq:
    text: str
    model: str = ""
    conv: str = ""
    profile: str = "none"
    folder: str = ""
    mcp: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    agent: str = ""  # agent id of an aside (first turn only; later turns use the stored one)


@dataclass
class Prepared:
    req: SendReq
    conv: qchatdb.Conv
    folder: str
    mcp: list[str]
    skills: list[str]
    context: str = ""  # the aside agent's recent conversation (masked), "" for a plain chat


MAX_AGENT_ID = 120


def _aside_agent(req: SendReq, conv, resolve_agent) -> Agent | None:
    """The agent a new aside is about (None: a plain chat). ``resolve_agent`` checks the id format."""
    if conv is not None or not req.agent:
        return None
    a = (resolve_agent(req.agent) if resolve_agent is not None and len(req.agent) <= MAX_AGENT_ID
         and req.agent.isprintable() else None)
    if a is None:
        raise SendInvalid("agent", "unknown agent")
    return a


def _context(settings: Settings, agent_id: str, resolve_agent) -> str:
    """Ingest the agent's newest entries (never raises), then read its recent conversation."""
    a = resolve_agent(agent_id) if resolve_agent is not None else None
    if a is not None:
        chatlog.sync(settings, a)
    return chatlog.context_text(settings, agent_id)


def _check(req: SendReq) -> None:
    if not req.text.strip() or "\0" in req.text or len(req.text) > qchat.MAX_TEXT:
        raise SendInvalid("text", "invalid message")
    if req.model not in qchat.MODELS:
        raise SendInvalid("model", "invalid model")
    if req.profile not in qchat.PROFILES:
        raise SendInvalid("profile", "invalid profile")


def _folder(settings: Settings, req: SendReq, conv, cfg: dict, folder_names) -> str:
    if req.profile == "none":
        return ""
    if conv is not None and conv["msgs"] > 0 and conv["folder"]:
        if req.folder and req.folder != conv["folder"]:
            raise SendInvalid("folder", "folder is fixed once the conversation has a message")
        return conv["folder"]
    fol = req.folder or cfg["default_folder"]
    if fol not in folder_names(settings):
        raise SendInvalid("folder", "choose a project folder")
    return fol


def prepare_send(settings: Settings, req: SendReq, *, folder_names: Callable[[Settings], list[str]],
                 resolve_agent: Callable[[str], Agent | None] | None = None) -> Prepared:
    """Validate against settings / registry, create or update the conversation, store the user message.

    ``resolve_agent(id)``: the live or ended agent for an aside, None when unknown (injected by the route)."""
    _check(req)
    cfg = qchatcfg.load(settings)
    conv = None
    if req.conv:
        if not req.conv.isdigit() or len(req.conv) > 9:
            raise SendInvalid("conv", "unknown conversation")
        conv = qchatdb.get_conv(settings, int(req.conv))
        if conv is None:
            raise SendInvalid("conv", "unknown conversation")
    aside = _aside_agent(req, conv, resolve_agent)
    if aside is not None or (conv is not None and conv["agent"]):
        # btw: the context is another agent's chat, untrusted text: no tools, MCP or skills it could steer
        req = replace(req, profile="none", folder="", mcp=[], skills=[])
    if req.profile != "none" and not cfg["profiles"].get(req.profile):
        raise SendInvalid("profile", "profile not enabled")
    fol = _folder(settings, req, conv, cfg, folder_names)
    sel_mcp: list[str] = []
    sel_skills: list[str] = []
    if req.profile != "none":
        known = {m["name"] for m in qchatcfg.discover_mcp(settings)
                 if m.get("enabled") and m.get("type") != "connector"}
        if any(n not in known for n in req.mcp):
            raise SendInvalid("mcp", "unknown MCP server")
        sel_mcp = list(dict.fromkeys(req.mcp))
    if req.profile != "none" or cfg["skills_in_none"]:
        known_sk = {k["name"] for k in qchatcfg.discover_skills(settings, fol) if k.get("enabled")}
        if any(n not in known_sk for n in req.skills):
            raise SendInvalid("skills", "unknown skill")
        sel_skills = list(dict.fromkeys(req.skills))
    if conv is None:
        cid = qchatdb.create_conv(settings, profile=req.profile, folder=fol, model=req.model,
                                  mcp=sel_mcp, skills=sel_skills, agent=aside.id if aside else "",
                                  title=f"btw: {aside.label or aside.id}" if aside else "")
    else:
        cid = conv["id"]
        qchatdb.set_conv_opts(settings, cid, profile=req.profile, model=req.model, mcp=sel_mcp, skills=sel_skills)
    qchatdb.add_message(settings, cid, "user", req.text)
    stored = qchatdb.get_conv(settings, cid)
    if stored is None:  # the row was just written; a miss means the db is broken
        raise PmError("quick chat: conversation vanished after save")
    ctx = _context(settings, stored["agent"], resolve_agent) if stored["agent"] else ""
    return Prepared(req=req, conv=stored, folder=fol, mcp=sel_mcp, skills=sel_skills, context=ctx)


def store_answer(s: Settings, cid: int, done: dict, text: str, tools: list[dict], model: str) -> None:
    """Save the assistant message + claude's session id, then prune. History problems never break the stream."""
    try:
        if done.get("session"):
            qchatdb.set_session(s, cid, str(done["session"]))
        qchatdb.add_message(s, cid, "assistant", text, html=str(done.get("html") or ""), tools=tools,
                            cost=done.get("cost"), model=model)
        cfg = qchatcfg.load(s)
        qchatdb.prune(s, int(cfg["keep_convs"]), int(cfg["keep_days"]))
    except Exception:  # noqa: BLE001 - the answer was already streamed
        pass


async def consume_stream(s: Settings, prep: Prepared, *, on_end: Callable[[str, dict], None],
                         offload: Callable[..., Awaitable]) -> AsyncIterator[str]:
    """SSE frames of one turn; stores the answer on ``done`` (via ``offload``), then ``on_end(outcome, detail)``."""
    req, conv = prep.req, prep.conv
    cid = conv["id"]
    outcome, n = "ok", 0
    acc: list[str] = []
    tools: dict[str, dict] = {}
    try:
        yield qchat.frame("conv", {"id": cid, "title": conv["title"]})
        async for ev, data in qchat.stream(s, req.text, model=req.model, session=conv["session"],
                                           profile=req.profile, folder=prep.folder, mcp=prep.mcp,
                                           skills=prep.skills, context=prep.context):
            if ev == "delta":
                n += len(data["t"])
                acc.append(data["t"])
            elif ev == "tool":
                tools[data["id"]] = {**data, "text": "", "err": False}
            elif ev == "toolres":
                t = tools.get(data["id"])
                if t is not None:
                    t["text"], t["err"] = data["text"], data["err"]
            elif ev == "error":
                outcome = "error"
            elif ev == "done":
                await offload(store_answer, s, cid, data, "".join(acc), list(tools.values()), req.model)
            yield qchat.frame(ev, data)
    finally:
        on_end(outcome, {"in": len(req.text), "out": n, "model": req.model or "default", "profile": req.profile,
                         "conv": cid, "tools": len(tools), **({"agent": conv["agent"], "ctx": len(prep.context)}
                                                              if conv["agent"] else {})})
