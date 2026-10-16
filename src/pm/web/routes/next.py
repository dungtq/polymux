"""Continue an agent's last (or a stored, by ``seq``) assistant message in a new agent, and clear
finished tmux windows.

POST + CSRF + Origin + audit via ``mutation_guard`` (Tier.MESSAGE). The prompt is read server
side from the agent's own chat view (never from the form); audit lines carry lengths/hashes only
(of the user's text). The new agent's first prompt is that text behind a short preamble naming the
source agent (``lineage.continue_prompt``: read it, then ask what to do next), and the new pane is
recorded in ``pm.agents.lineage`` so its page can link back ("Continued from"). Form ``mode=custom``
sends the user's ``text`` as-is (no preamble). The new agent always gets its own tmux window
(``where=window``, the only value; anything else is refused).
"""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from pm.agents import head as agenthead
from pm.agents import launch as launch_mod
from pm.agents import lineage
from pm.chat.live import chat_view
from pm.core import executors
from pm.core.models import Launch, Tier
from pm.security.audit import message_detail
from pm.security.guard import mutation_guard
from pm.tmux import ctl as tmuxctl
from pm.tmux import kill as tmuxkill
from pm.web import agentctl, chatctx

router = APIRouter()
MODES = ("predefined", "custom")
WHERES = ("window",)


def _stored_msg(settings, agent_id: str, seq: int):
    """Stored chat message ``seq`` of this agent (``pm.chat.log``), or None."""
    cl = chatctx.get_chatlog()
    if cl is None:
        return None
    try:
        return cl.get(settings, agent_id, seq)
    except Exception:  # noqa: BLE001 - treated as "no assistant message"
        return None


async def _prompt_text(request: Request, action: str, settings, a) -> str:
    """The new agent's prompt: form ``text``, else stored message ``seq``, else the last assistant
    message of the chat view ("" when there is none). A bad ``seq`` stops with 400 (audited)."""
    form = await request.form()
    raw_seq = str(form.get("seq") or "").strip()
    given = str(form.get("text") or "").strip()
    if given:
        # the message as the user adjusted it in the composer (Continue… -> edit -> Start)
        return given
    if raw_seq:
        # one stored assistant message (chat log), read server side by its seq
        if not raw_seq.isascii() or not raw_seq.isdigit() or len(raw_seq) > 18:
            raise agentctl.stop(request, action, a.id, 400, "invalid message", reason="bad seq")
        m = await executors.run_tmux(_stored_msg, settings, a.id, int(raw_seq))
        return (m.text or "").strip() if m is not None and m.role == "assistant" else ""
    view = await executors.run_tmux(chat_view, settings, a)
    return (view.last_text or "").strip() if view.last_role == "assistant" else ""


def _publish_inbox(request: Request, agent_id: str, action: str) -> None:
    """Best-effort SSE "inbox" nudge so other open pages refresh."""
    hub = getattr(request.app.state, "hub", None)
    if hub is not None:
        try:
            hub.publish("inbox", {"agent": agent_id, "action": action})
        except Exception:  # noqa: BLE001
            pass


async def _options(request: Request, action: str, a) -> tuple[str, str]:
    """Form ``mode`` (predefined | custom) and ``where`` (window, the default); anything else stops with 400."""
    form = await request.form()
    mode = str(form.get("mode") or "predefined").strip()
    where = str(form.get("where") or "window").strip()
    if mode not in MODES or where not in WHERES:
        raise agentctl.stop(request, action, a.id, 400, "invalid option",
                            reason="bad mode" if mode not in MODES else "bad where")
    return mode, where


async def _build_prompt(request: Request, action: str, settings, a, mode: str, where: str) -> tuple[str, dict]:
    """(final prompt, audit detail). predefined: the message behind the lineage preamble; custom: the
    user's own text as-is. The detail carries kind, mode, where and lengths/hashes, never text."""
    if mode == "custom":
        form = await request.form()
        text = str(form.get("text") or "").strip()
        why = "empty prompt"
    else:
        text = await _prompt_text(request, action, settings, a)
        why = "no assistant message"
    base = {"kind": a.kind.value, "mode": mode, "where": where}
    if not text:
        raise agentctl.stop(request, action, a.id, 400 if mode == "custom" else 409, why, detail=base, reason=why)
    detail = {**base, **{f"prompt_{k}": v for k, v in message_detail(text).items()}}
    prompt = text if mode == "custom" else lineage.continue_prompt(a.id, text)
    try:
        launch_mod.check_prompt(prompt)  # the final text, preamble included
    except launch_mod.LaunchError:
        raise agentctl.stop(request, action, a.id, 400, "message too long", detail=detail,
                            reason="prompt too long") from None
    return prompt, detail


async def _start(request: Request, action: str, settings, a, project, prompt: str, detail: dict):
    """Launch the new agent (same kind and model as ``a``) in a new tmux window."""
    model = await executors.run_collectors(
        agenthead.live_model, a, config_dirs=agenthead.kind_dirs(settings, a.kind))  # the new chat keeps this agent's model
    launch = Launch(project=project.name, kind=a.kind, model=None if model == a.kind.value else model, prompt=prompt)
    try:
        return await executors.run_tmux(launch_mod.launch_agent, settings, launch, project)
    except launch_mod.LaunchError as e:
        raise agentctl.stop(request, action, a.id, 400, "launch refused", detail=detail,
                            reason=f"launch refused: {str(e)[:80]}") from None
    except (tmuxctl.TmuxError, OSError):
        raise agentctl.stop(request, action, a.id, 502, "launch failed", "error", detail) from None


@router.post("/a/{agent_id}/continue", response_class=HTMLResponse)
async def agent_continue(request: Request, agent_id: str,
                         _=Depends(mutation_guard("agent_continue", Tier.MESSAGE))):
    """Start a new agent of the same kind in a new tmux window (``where``: window only). ``mode``: predefined
    (default; form ``text``, else stored message ``seq``, else the last assistant message, behind the lineage
    preamble) or custom (``text`` is the whole prompt, no preamble)."""
    action = "agent_continue"
    a = await agentctl.agent(request, action, agent_id)
    settings = request.app.state.settings
    if not a.project:
        raise agentctl.stop(request, action, a.id, 409, "no project", reason="no project")
    project = agentctl.project(request, action, a.project)
    mode, where = await _options(request, action, a)
    prompt, detail = await _build_prompt(request, action, settings, a, mode, where)
    res = await _start(request, action, settings, a, project, prompt, detail)
    await executors.run_collectors(lineage.record, settings, a.id, "continue", res.pane_id, a.kind.value)
    agentctl.done(request, action, a.id, "ok", {**detail, "window": res.window_id})
    _publish_inbox(request, a.id, action)
    return agentctl.frag(f"Started a new {a.kind.value} in {res.window_name} with this message.")


@router.post("/tmux/session/{sid}/clear", response_class=HTMLResponse)
async def clear_session(request: Request, sid: str,
                        _=Depends(mutation_guard("tmux_session_clear", Tier.MESSAGE))):
    """Kill the finished (all panes dead) windows of one session; live windows stay."""
    action = "tmux_session_clear"
    try:
        tid = agentctl.tmux_id(sid, "$")
    except tmuxctl.TmuxError:
        raise agentctl.stop(request, action, "", 400, "invalid id") from None
    try:
        res = await executors.run_tmux(tmuxkill.clear_ended_windows, request.app.state.settings, tid)
    except tmuxkill.KillRefused:
        raise agentctl.stop(request, action, tid, 403, "clear refused") from None
    except (tmuxctl.TmuxError, ValueError):
        raise agentctl.stop(request, action, tid, 502, "tmux error", "error") from None
    agentctl.done(request, action, tid, "ok", {"killed": len(res.killed), "kept": res.kept})
    if not res.killed:
        return agentctl.frag("No finished windows to clear.")
    return agentctl.frag(f"Cleared {len(res.killed)} finished window(s) from {res.session}: "
                 + ", ".join(escape(n) for n in res.killed[:8]) + ("..." if len(res.killed) > 8 else "") + ".")
