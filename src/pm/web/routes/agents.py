"""Tier-2 agent routes: message, quick key, interrupt, stop, snippet, launch, dispatch,
panic, and typed-confirm tmux kills.

Wiring: ``app.include_router(web.routes.agents.router)``. Requires app.state: settings,
sessions; optional ``hub`` (events.Hub) receives an ``inbox`` event after delivery.
Responses are tiny autoescaped HTML fragments (``<p class="flash ok|err">``) or 303.
Audit details carry only lengths, hashes and ids, never message or prompt text.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from pm.agents import catalog, deliver, registry, slash
from pm.agents import head as agenthead
from pm.agents import launch as launch_mod
from pm.agents import message as msg
from pm.agents import stop as stop_mod
from pm.chat import outbox
from pm.core import executors, prefs
from pm.core.errors import PmError
from pm.core.models import Agent, AgentKind, Launch, Project, Tier
from pm.infra import snapshot as services
from pm.projects.tracker import issues as tracker_issues
from pm.security.audit import message_detail
from pm.security.guard import mutation_guard
from pm.tmux import ctl as tmuxctl
from pm.tmux import kill as tmuxkill
from pm.web import agentctl, issuectx
from pm.web.routes import slash_output as slash_out

router = APIRouter()

_NOSTORE = {"Cache-Control": "no-store"}
PANIC_WORD = "PANIC"
NEWCHAT_TRIES = 16  # x NEWCHAT_POLL_S: how long New Chat waits for the pane's new agent id
NEWCHAT_POLL_S = 0.5


def _msg_error(request: Request, action: str, target: str, exc: Exception,
               detail: dict | None = None) -> HTTPException:
    if isinstance(exc, msg.InvalidMessage):
        return agentctl.stop(request, action, target, 400, "invalid input", detail=detail)
    if isinstance(exc, msg.NotAnAgent):
        return agentctl.stop(request, action, target, 409, "not an agent",
                     detail={**(detail or {}), "why": msg.describe(exc)})
    why = msg.describe(exc)
    return agentctl.stop(request, action, target, 409, f"send failed: {why}", "error",
                 {**(detail or {}), "error": why}, reason="send failed")


async def _deliver(request: Request, action: str, agent_id: str, fn, detail: dict,
                   okmsg: str, notconfirmed: str | None = None, **fkw) -> HTMLResponse:
    a = await agentctl.agent(request, action, agent_id)
    settings = request.app.state.settings
    try:
        res = await executors.run_tmux(fn, settings, a, **fkw)
    except msg.MessageError as e:
        raise _msg_error(request, action, a.id, e, detail) from None
    submitted = getattr(res, "submitted", True)
    if notconfirmed is not None:
        detail = {**detail, "submitted": int(submitted)}
    agentctl.done(request, action, a.id, "ok", detail)
    agentctl.publish(request, agent=a.id, action=action)
    return agentctl.frag(okmsg if submitted or notconfirmed is None else notconfirmed)


# ------------------------------------------------------------------ agent actions
@router.post("/a/{agent_id}/model", response_class=HTMLResponse)
async def agent_model(request: Request, agent_id: str,
                      _=Depends(mutation_guard("agent_model", Tier.MESSAGE))):
    """Switch the running agent's model: types ``/model <id>`` (only ids from its own program's list)."""
    model = (await agentctl.form(request, "model")).strip()
    a = await agentctl.agent(request, "agent_model", agent_id)
    settings = request.app.state.settings
    detail = {"model": model[:64]}
    ids = {r[0] for r in await executors.run_tmux(catalog.kind_models, settings, a.kind)} \
        if a.kind in catalog.SWITCHABLE else set()
    if model not in ids:
        raise agentctl.stop(request, "agent_model", a.id, 400, "unknown model for this program", detail=detail,
                    reason="model not in list")
    resp = await _deliver(request, "agent_model", agent_id,
                          lambda s, ag: msg.send_message(s, ag, f"/model {model}"), detail,
                          f"Model set to {model}.", "Sent (not confirmed)")
    agenthead.note_model(a, model)  # the picker and placeholder show it until the agent's next reply carries it
    return await agentctl.composer_event(resp, a)


@router.post("/a/{agent_id}/effort", response_class=HTMLResponse)
async def agent_effort(request: Request, agent_id: str,
                       _=Depends(mutation_guard("agent_effort", Tier.MESSAGE))):
    """Switch the running agent's reasoning effort: types ``/effort <level>`` (claude, listed levels only)."""
    level = (await agentctl.form(request, "effort")).strip()
    a = await agentctl.agent(request, "agent_effort", agent_id)
    detail = {"effort": level[:16]}
    if not launch_mod.effort_switchable(a.kind, level):
        raise agentctl.stop(request, "agent_effort", a.id, 400, "unknown effort for this program", detail=detail,
                    reason="effort not in list")
    resp = await _deliver(request, "agent_effort", agent_id,
                          lambda s, ag: msg.send_message(s, ag, f"/effort {level}"), detail,
                          f"Effort set to {level}.", "Sent (not confirmed)")
    agenthead.note_effort(a, level)  # the picker shows it until the agent's next reply carries it
    return await agentctl.composer_event(resp, a)


async def _slash_message(request: Request, agent_id: str, text: str) -> HTMLResponse:
    """A ``/command`` (or any text starting with "/") keeps the direct path: typed in at once."""
    detail = message_detail(text)
    confirm = (await agentctl.form(request, "confirm")) == "1"
    a = await agentctl.agent(request, "agent_message", agent_id)
    cmd = slash.command_of(text)
    known = cmd if cmd and slash.lookup(a.kind.value, cmd) else None
    risk = slash.risk_of(a.kind.value, text)
    detail = {**detail, "slash": risk or "no", **({"cmd": known[:32]} if known else {})}
    try:
        slash.check_message(a.kind.value, text, confirm)
    except slash.SlashError as e:
        raise agentctl.stop(request, "agent_message", a.id, 400, str(e), detail=detail,
                    reason="slash needs confirm") from None
    cmdinfo = slash.lookup(a.kind.value, cmd) if cmd else None
    if cmdinfo and cmdinfo.risk == "info" and cmdinfo.output == "panel":
        token = await slash_out.snapshot_before(request, a)
        extra = slash_out.card_trigger(a.id, token) if token else ""
        resp = await _deliver(request, "agent_message", agent_id,
                              lambda s, a: msg.send_message(s, a, text), detail, "Sent.",
                              "Sent (not confirmed)")
        if extra:
            resp.body = resp.body + extra.encode()
            resp.headers["content-length"] = str(len(resp.body))
        return resp
    resp = await _deliver(request, "agent_message", agent_id,
                          lambda s, a: msg.send_message(s, a, text), detail, "Sent.",
                          "Sent (not confirmed)")
    agenthead.note_effort_cmd(a, text)
    return resp


@router.post("/a/{agent_id}/message", response_class=HTMLResponse)
async def agent_message(request: Request, agent_id: str,
                        _=Depends(mutation_guard("agent_message", Tier.MESSAGE))):
    """Store the message in the outbox; the deliverer types it in when the agent is idle (no tmux here).
    Answers with the composer bubble. Audit: outbox id + length only."""
    action, text = "agent_message", await agentctl.form(request, "text")
    if text.startswith("/"):
        return await _slash_message(request, agent_id, text)
    a = await agentctl.agent(request, action, agent_id)
    size = {"len": len(text.encode("utf-8", "replace"))}
    st = request.app.state.settings
    if (await agentctl.form(request, "mode")) == "builder" and st.builder_hint and a.cwd:
        # before outbox.add: the stored row = the text pasted into the pane, so it still matches the transcript
        text = msg.with_builder_hint(text, a.cwd, (st.builder_frame_hosts or ("",))[0])
    try:
        text = msg.validate_text(text)
        m = await executors.run_collectors(outbox.add, request.app.state.settings, a.id, text)
    except msg.InvalidMessage:
        raise agentctl.stop(request, action, a.id, 400, "invalid input", detail=size) from None
    except PmError as e:
        raise agentctl.stop(request, action, a.id, e.status, str(e), detail=size) from None
    try:
        agentctl.done(request, action, a.id, "ok", {"outbox": m.id, **size})
    except BaseException:  # audit failed: fail closed, the stored message is never delivered
        await executors.run_collectors(outbox.set_state, request.app.state.settings, m.id, "cancelled")
        raise
    deliver.kick(a.id)
    agentctl.publish_outbox(request, m)
    return agentctl.outbox_bubble(request, m, a)


async def _new_chat_target(request: Request, old: Agent) -> str | None:
    """Where the page goes after New Chat: a new conversation is a new agent (Claude: new session id;
    other kinds: the relaunched process) and so a new permanent id in the same pane. Wait for the
    registry to show it, else fall back to the pane based id (the agent page redirects once that
    resolves)."""
    settings = request.app.state.settings
    for _ in range(NEWCHAT_TRIES):
        try:
            snap = await services.snapshot_async(settings, max_age_s=0.2)
            new = next((x for x in snap.agents if x.pane_id == old.pane_id and x.id != old.id), None)
        except Exception:  # noqa: BLE001 - best effort
            break
        if new is not None:
            return f"/a/{new.id}"
        await asyncio.sleep(NEWCHAT_POLL_S)
    return f"/a/{old.kind.value}-{old.pane_id[1:]}" if old.pane_id[1:].isdigit() else None


@router.post("/a/{agent_id}/newchat", response_class=HTMLResponse)
async def agent_newchat(request: Request, agent_id: str,
                        _=Depends(mutation_guard("agent_newchat", Tier.MESSAGE))):
    """Send the agent's /clear and clear the pane's tmux scrollback (confirm in the UI)."""
    action = "agent_newchat"
    a = await agentctl.agent(request, action, agent_id)
    # same process after /clear: quit and relaunch instead (Claude too, when an update waits for a restart)
    restart = a.kind is not AgentKind.CLAUDE or await executors.run_tmux(
        msg.update_pending, request.app.state.settings, a)
    cmd = slash.exit_cmd(a.kind.value) if restart else slash.clear_cmd(a.kind.value)
    if cmd is None:
        raise agentctl.stop(request, action, a.id, 409, "no /clear for this agent", reason="no clear cmd")
    settings = request.app.state.settings
    detail = {"kind": a.kind.value, "cmd": cmd, **({"restart": 1} if restart else {})}
    try:
        await executors.run_tmux(msg.restart_chat if restart else msg.new_chat, settings, a, cmd)
    except msg.Refused as e:
        raise agentctl.stop(request, action, a.id, 409, str(e), reason=str(e), detail=detail) from None
    except msg.MessageError as e:
        raise _msg_error(request, action, a.id, e, detail) from None
    agentctl.done(request, action, a.id, "ok", detail)
    agentctl.publish(request, agent=a.id, action=action)
    target = await _new_chat_target(request, a)
    if target is None:
        return agentctl.frag("New chat started (history cleared).")
    if "embed=1" in request.headers.get("HX-Current-URL", ""):
        target += "?embed=1"  # inside the drawer iframe: stay framable; agentmode.js tells the drawer
    return HTMLResponse("", headers={**_NOSTORE, "HX-Redirect": target})


@router.post("/a/{agent_id}/key", response_class=HTMLResponse)
async def agent_key(request: Request, agent_id: str,
                    _=Depends(mutation_guard("agent_key", Tier.MESSAGE))):
    key = await agentctl.form(request, "key")
    if msg.key_seq(key) is None:
        raise agentctl.stop(request, "agent_key", agent_id, 400, "invalid input")
    return await _deliver(request, "agent_key", agent_id,
                          lambda s, a: msg.quick_key(s, a, key), {"key": key},
                          f"Sent {key}.")


@router.post("/a/{agent_id}/interrupt", response_class=HTMLResponse)
async def agent_interrupt(request: Request, agent_id: str,
                          _=Depends(mutation_guard("agent_interrupt", Tier.MESSAGE))):
    return await _deliver(request, "agent_interrupt", agent_id,
                          lambda s, a: msg.interrupt(s, a), {}, "Interrupt sent.")


@router.post("/a/{agent_id}/snippet", response_class=HTMLResponse)
async def agent_snippet(request: Request, agent_id: str,
                        _=Depends(mutation_guard("agent_snippet", Tier.MESSAGE))):
    raw = await agentctl.form(request, "index")
    snippets = msg.load_snippets(request.app.state.settings)
    try:
        idx = int(raw)
        if not 0 <= idx < len(snippets):
            raise ValueError
    except ValueError:
        raise agentctl.stop(request, "agent_snippet", agent_id, 400, "invalid input") from None
    text = snippets[idx][1]
    detail = {"index": idx, **message_detail(text)}
    return await _deliver(request, "agent_snippet", agent_id,
                          lambda s, a: msg.send_message(s, a, text), detail,
                          "Sent.", "Sent (not confirmed)")


@router.post("/a/{agent_id}/stop", response_class=HTMLResponse)
async def agent_stop(request: Request, agent_id: str,
                     _=Depends(mutation_guard("agent_stop", Tier.MESSAGE))):
    action = "agent_stop"
    a = await agentctl.agent(request, action, agent_id)
    try:
        res = await executors.run_tmux(stop_mod.stop_agent, request.app.state.settings, a)
    except Exception:  # noqa: BLE001
        raise agentctl.stop(request, action, a.id, 502, "stop failed", "error") from None
    if res.status == "error":
        raise agentctl.stop(request, action, a.id, 502, "stop failed", "error")
    agentctl.done(request, action, a.id, "ok", {"status": res.status})
    if res.status != "still_running":
        agentctl.publish(request, agent=a.id, action=action)
    if res.status == "still_running":
        return agentctl.frag(f"Escape and Ctrl-C sent to {a.id}, but the agent is still running.", ok=False)
    name = a.id
    return agentctl.frag(f"{name} stopped." if res.status == "stopped" else f"{name}: pane is gone.")


# ------------------------------------------------------------------ launch / dispatch
async def _model(request: Request) -> str | None:
    """Model id: the Variant select, else the model picker, else the custom text field."""
    variant = (await agentctl.form(request, "variant")).strip()
    if variant:
        return variant
    choice = (await agentctl.form(request, "model_choice")).strip()
    if choice and choice != "custom":
        return choice
    return _opt(await agentctl.form(request, "model"))


async def _options(request: Request, action: str, name: str, kind: AgentKind,
                   detail: dict) -> dict:
    """Model / effort / permission fields of a launch or dispatch form.

    Adds them to the audit ``detail``; a bypass mode needs the explicit confirm field."""
    mode = _opt(await agentctl.form(request, "permission_mode"))
    extra = _opt(await agentctl.form(request, "permission_extra"))
    model = await _model(request)
    effort = _opt(await agentctl.form(request, "effort"))
    detail.update({k: v[:64] for k, v in (("model", model), ("effort", effort),
                                          ("permission_mode", mode),
                                          ("permission_extra", extra)) if v})
    if launch_mod.is_bypass(kind, mode):
        detail["bypass"] = 1
        if await agentctl.form(request, "confirm_bypass") != "1":
            raise agentctl.stop(request, action, name, 400, "launch refused", detail=detail,
                        reason="launch refused: bypass not confirmed")
    return {"model": model, "permission_mode": mode, "permission_extra": extra, "effort": effort}


def _opt(v: str) -> str | None:
    v = v.strip()
    return v or None


async def _permanent_target(request: Request, legacy: str) -> str:
    """``/a/<permanent id>`` of a just launched pane from ONE fresh snapshot (no wait, no poll), else the
    pane based id. The build is published, so the agent page redirects that id once it resolves."""
    settings = request.app.state.settings
    try:
        res = registry.resolve(settings, await services.snapshot_async(settings, max_age_s=0.0), legacy)
    except Exception:  # noqa: BLE001 - best effort, fall back to the legacy id
        res = None
    perm = res and (res.redirect or (res.agent.id if res.agent else ""))
    return f"/a/{perm or legacy}"


async def _run_launch(request: Request, action: str, name: str, launch: Launch,
                      project: Project, detail: dict, remember: dict | None = None,
                      open_after: bool = False, item_key: str = "", item_title: str = "") -> Response:
    settings = request.app.state.settings
    try:
        res = await executors.run_tmux(launch_mod.launch_agent, settings, launch, project)
    except launch_mod.LaunchError as e:
        raise agentctl.stop(request, action, name, 400, "launch refused", detail=detail,
                    reason=f"launch refused: {str(e)[:120]}") from None
    except (tmuxctl.TmuxError, OSError, sqlite3.Error):
        raise agentctl.stop(request, action, name, 502, "launch failed", "error", detail) from None
    agentctl.done(request, action, name, "ok", {**detail, "window": res.window_id})
    agentctl.publish(request, project=name, action=action)
    if remember is not None:
        try:
            prefs.remember(settings.prefs_file, name, remember)
        except OSError:
            pass  # remembering the form is a convenience only
    target = f"/a/{launch.kind.value}-{res.pane_id[1:]}" if res.pane_id[1:].isdigit() else ""
    if item_key and target:
        try:
            prefs.mark_dispatched(settings.prefs_file, name, item_key, target[3:], res.pane_id, time.time(), title=item_title)
        except OSError:
            pass  # the mark is a convenience; the audit line is the record
    if open_after and target:
        target = await _permanent_target(request, target[3:])
    hx = request.headers.get("HX-Request") == "true"
    if open_after and target:
        if hx:
            # launch popup (framed ?embed=1): a framable target, so the iframe is never refused
            if "embed=1" in request.headers.get("HX-Current-URL", ""):
                target += "?embed=1"
            return HTMLResponse("", headers={**_NOSTORE, "HX-Redirect": target})
        return RedirectResponse(target, status_code=303)
    if not hx:
        return RedirectResponse(f"/p/{name}", status_code=303)
    # HTMX would follow a 303 and swap the whole project page into #flash: answer with a result.
    kind = launch.kind.value
    links = [f'<a href="/p/{quote(name)}">Project</a>']
    if res.pane_id[1:].isdigit():
        links.insert(0, f'<a href="/a/{kind}-{res.pane_id[1:]}">Open {escape(kind)} agent</a>')
    note = ("The agent can take a few seconds to show up; if the link says 'gone', "
            "reload the project page.")
    return agentctl.frag(f"Launched {kind} in window {res.window_name} ({res.window_id}).",
                 extra=f'<p class="launched">{" &middot; ".join(links)}</p>'
                       f'<p class="muted small">{escape(note)}</p>')


@router.post("/p/{name}/launch", response_class=HTMLResponse)
async def project_launch(request: Request, name: str,
                         _=Depends(mutation_guard("agent_launch", Tier.MESSAGE))):
    action = "agent_launch"
    project = agentctl.project(request, action, name)
    kind_s = await agentctl.form(request, "kind")
    prompt = await agentctl.form(request, "prompt")
    branch = _opt(await agentctl.form(request, "branch"))
    detail = {"kind": kind_s[:32], **{f"prompt_{k}": v for k, v in message_detail(prompt).items()}}
    if branch:
        detail["branch"] = branch[:64]
    try:
        kind = AgentKind(kind_s)
    except ValueError:
        raise agentctl.stop(request, action, name, 400, "invalid kind", detail=detail) from None
    opts = await _options(request, action, name, kind, detail)
    form_mode = opts["permission_mode"]  # what the form chose (remembered); a /plan prompt overrides the launch
    prompt, opts["permission_mode"] = launch_mod.apply_prompt_commands(kind, prompt, form_mode)
    if opts["permission_mode"] != form_mode:
        detail["permission_mode"] = opts["permission_mode"]
    launch = Launch(project=name, kind=kind, worktree_branch=branch, prompt=prompt, **opts)
    remember = {
        "kind": kind.value, "model_choice": await agentctl.form(request, "model_choice"),
        "variant": await agentctl.form(request, "variant"), "model": await agentctl.form(request, "model"),
        "effort": opts["effort"], "extra": opts["permission_extra"], "branch": branch,
        "mode": "" if launch_mod.is_bypass(kind, form_mode) else (form_mode or ""),
    }
    return await _run_launch(request, action, name, launch, project, detail, remember,
                             open_after=await agentctl.form(request, "open") == "1")


@router.post("/p/{name}/dispatch", response_class=HTMLResponse)
async def project_dispatch(request: Request, name: str,
                           _=Depends(mutation_guard("agent_dispatch", Tier.MESSAGE))):
    action = "agent_dispatch"
    project = agentctl.project(request, action, name)
    raw, kind_s = await agentctl.form(request, "index"), await agentctl.form(request, "kind")
    try:
        idx, kind = int(raw), AgentKind(kind_s or "claude")
        item = await executors.run_tmux(launch_mod.todo_item_text, project.path, idx)
    except (ValueError, launch_mod.LaunchError):
        raise agentctl.stop(request, action, name, 400, "invalid input") from None
    key = launch_mod.item_key(item)
    settings = request.app.state.settings
    prior = await launch_mod.in_progress(settings, name, key,
                                         get_snap=lambda: services.snapshot_async(settings, max_age_s=10.0))
    if prior:
        detail = {"index": idx, "kind": kind.value, "dup": 1}
        raise agentctl.stop(request, action, name, 409,
                    f"already dispatched to {prior}; stop that agent first", detail=detail,
                    reason="dispatch refused: item already in progress")
    prompt = launch_mod.dispatch_prompt(item)
    detail = {"index": idx, "kind": kind.value,
              **{f"prompt_{k}": v for k, v in message_detail(prompt).items()}}
    opts = await _options(request, action, name, kind, detail)
    launch = Launch(project=name, kind=kind, prompt=prompt, **opts)
    return await _run_launch(request, action, name, launch, project, detail, item_key=key, item_title=item,
                             open_after=await agentctl.form(request, "open") == "1")


@router.post("/p/{name}/issues/{num}/dispatch", response_class=HTMLResponse)
async def issue_dispatch(request: Request, name: str, num: int,
                         _=Depends(mutation_guard("agent_dispatch_issue", Tier.MESSAGE))):
    """Launch an agent on tracker issue ``num`` (title + body as the prompt); refused while the issue's last
    agent is still alive. Same form fields and answers as the TODO dispatch."""
    action, settings = "agent_dispatch_issue", request.app.state.settings
    project = agentctl.project(request, action, name)
    try:
        kind = AgentKind(await agentctl.form(request, "kind") or "claude")
        it, prompt = await executors.run_collectors(tracker_issues.dispatch_prompt, settings, name, num)
    except ValueError:
        raise agentctl.stop(request, action, name, 400, "invalid input", detail={"num": num}) from None
    except (PmError, sqlite3.Error) as e:
        status, why = (e.status, str(e)) if isinstance(e, PmError) else (503, "tracker database busy")
        raise agentctl.stop(request, action, name, status, why, detail={"num": num}) from None
    key = issuectx.issue_key(it.num)
    prior = await launch_mod.in_progress(settings, name, key,
                                         get_snap=lambda: services.snapshot_async(settings, max_age_s=10.0))
    if prior:
        raise agentctl.stop(request, action, name, 409, f"already dispatched to {prior}; stop that agent first",
                            detail={"num": num, "kind": kind.value, "dup": 1},
                            reason="dispatch refused: issue already in progress")
    detail = {"num": num, "kind": kind.value, **{f"prompt_{k}": v for k, v in message_detail(prompt).items()}}
    opts = await _options(request, action, name, kind, detail)
    launch = Launch(project=name, kind=kind, prompt=prompt, **opts)
    return await _run_launch(request, action, name, launch, project, detail, item_key=key,
                             item_title=f"#{it.num} {it.title_short}",
                             open_after=await agentctl.form(request, "open") == "1")


# ------------------------------------------------------------------ panic
@router.post("/panic", response_class=HTMLResponse)
async def panic(request: Request, _=Depends(mutation_guard("panic", Tier.MESSAGE))):
    action = "panic"
    scope, confirm = await agentctl.form(request, "scope"), await agentctl.form(request, "confirm")
    if confirm != PANIC_WORD:
        raise agentctl.stop(request, action, scope[:80], 403, "confirm mismatch")
    snap = await agentctl.snapshot(request)
    try:
        results = await executors.run_tmux(stop_mod.panic, request.app.state.settings,
                                          list(snap.agents), scope)
    except ValueError:
        raise agentctl.stop(request, action, scope[:80], 400, "invalid scope") from None
    counts: dict[str, int] = {}
    for r in results.values():
        counts[r.status] = counts.get(r.status, 0) + 1
    agentctl.done(request, action, scope, "ok", {"agents": len(results), **counts})
    if results:
        agentctl.publish(request, action=action)
    names = {a.id: a.id for a in snap.agents}
    items = "".join(f"<li>{escape(names.get(r.agent_id, r.agent_id))}: {escape(r.status)}</li>"
                    for r in results.values())
    bad = counts.get("still_running", 0) + counts.get("error", 0)
    return agentctl.frag(f"Panic sent to {len(results)} agent(s)." if results else "No agents in scope.",
                 ok=not bad, extra=f"<ul>{items}</ul>" if items else "")


# ------------------------------------------------------------------ tmux kills
async def _kill(request: Request, action: str, raw_id: str, kind: str, fn) -> HTMLResponse:
    settings = request.app.state.settings
    confirm = await agentctl.form(request, "confirm")
    force = (await agentctl.form(request, "force_attached")) == "1"
    try:
        tid = agentctl.tmux_id(raw_id, kind)
    except tmuxctl.TmuxError:
        raise agentctl.stop(request, action, "", 400, "invalid id") from None
    try:
        res = await executors.run_tmux(fn, settings, tid, confirm, force)
    except tmuxkill.KillRefused as e:
        why = "confirm mismatch" if isinstance(e, tmuxkill.ConfirmMismatch) else "kill refused"
        raise agentctl.stop(request, action, tid, 403, why, detail={"force": int(force)}) from None
    except (tmuxctl.TmuxError, ValueError):
        raise agentctl.stop(request, action, tid, 502, "tmux error", "error") from None
    agentctl.done(request, action, tid, "ok", {"name": res.name, "force": int(force)})
    await agentctl.tmux_changed(request, action)
    return agentctl.frag(f"Killed {res.name} ({tid}).")


@router.post("/tmux/session/{sid}/kill", response_class=HTMLResponse)
async def kill_session(request: Request, sid: str,
                       _=Depends(mutation_guard("tmux_session_kill", Tier.MESSAGE))):
    return await _kill(request, "tmux_session_kill", sid, "$", tmuxkill.kill_session)


@router.post("/tmux/window/{wid}/kill", response_class=HTMLResponse)
async def kill_window(request: Request, wid: str,
                      _=Depends(mutation_guard("tmux_window_kill", Tier.MESSAGE))):
    return await _kill(request, "tmux_window_kill", wid, "@", tmuxkill.kill_window)


@router.post("/tmux/pane/{pid}/kill", response_class=HTMLResponse)
async def kill_pane(request: Request, pid: str,
                    _=Depends(mutation_guard("tmux_pane_kill", Tier.MESSAGE))):
    return await _kill(request, "tmux_pane_kill", pid, "%", tmuxkill.kill_pane)


@router.post("/p/{name}/tmux/close-all", response_class=HTMLResponse)
async def project_close_all(request: Request, name: str,
                            _=Depends(mutation_guard("tmux_project_close_all", Tier.MESSAGE))):
    """Close every non-protected tmux session of one project (same guards as a single kill)."""
    action, settings = "tmux_project_close_all", request.app.state.settings
    snap = await agentctl.snapshot(request)
    project = snap.project(name)
    if project is None:
        raise agentctl.stop(request, action, "", 404, "unknown project")
    live = set(project.pane_ids)
    ids = []
    for sid in project.sessions:
        s = snap.session(sid)
        if s is not None and any(p.id in live for w in s.windows for p in w.panes):
            ids.append(s.id)
    try:
        res = await executors.run_tmux(tmuxkill.close_sessions, settings, ids)
    except tmuxkill.KillRefused:
        raise agentctl.stop(request, action, name, 403, "kill refused") from None
    except (tmuxctl.TmuxError, ValueError):
        raise agentctl.stop(request, action, name, 502, "tmux error", "error") from None
    agentctl.done(request, action, name, "ok", {"closed": len(res.closed), "skipped": len(res.skipped)})
    if res.closed:
        await agentctl.tmux_changed(request, action)
    msg_ = f"Closed {len(res.closed)} session(s)."
    if res.skipped:
        msg_ += " Skipped: " + ", ".join(f"{n} ({why})" for n, why in res.skipped) + "."
    return agentctl.frag(msg_, ok=not res.skipped or bool(res.closed))
