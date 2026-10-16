"""Agents page mutations: the two-step review/apply flow, the skill/rule/script editor and Re-scan.

``POST /agents/review|apply`` answer the classic HTML pages for a browser navigation (a plain form post) and JSON
for a fetch (``is_navigation`` false): review ``{ok, ops: [{kind, path, detail}], token, destructive}``, apply
``{ok, lines}``, a refusal ``{ok: false, error, stale}`` (400, or 409 when the state changed since the review).
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from pm.agents.inventory import mutate as agentsmut
from pm.core import executors
from pm.core.models import Tier
from pm.security.audit import AuditError
from pm.security.guard import audit_result, mutation_guard
from pm.web import agentslib as lib
from pm.web.agentslib import TABS
from pm.web.agentslib import clean as _clean
from pm.web.deps import AUTH, is_navigation
from pm.web.render import html

router = APIRouter()

PARAMS: dict[str, dict[str, str]] = {
    "skill_toggle": {"agent": "str", "skill": "str", "enable": "bool"},
    "skills_link": {"agent": "str", "skills": "names"},
    "rules_sync": {"source": "str", "targets": "list"},
    "index_prune": {"lines": "entries"},
    "mcp_env": {"config": "str", "server": "str"},
    "skill_create": {"name": "str", "description": "str", "body": "str"},
    "skill_edit": {"name": "str", "description": "str", "body": "str"},
    "skill_delete": {"name": "str", "confirm": "str"},
    "rule_create": {"name": "str", "body": "str"},
    "rule_edit": {"name": "str", "body": "str"},
    "rule_delete": {"name": "str", "confirm": "str"},
    "script_create": {"name": "str", "body": "str"},
    "script_edit": {"name": "str", "body": "str"},
    "script_delete": {"name": "str", "confirm": "str"},
}
# list fields: (max items, max chars per item); one item over the cap is kept so the domain refuses the list
LISTS = {"names": (agentsmut.MAX_LINK, 64), "entries": (agentsmut.MAX_TARGETS, 300)}
KINDS = ("skill", "rule", "script")
EDIT_MAX = 256 * 1024
_FM_DESC = re.compile(r"description: (.*)")


def _nl(v: str) -> str:
    return v.replace("\r\n", "\n").replace("\r", "\n")


async def _params(request: Request) -> tuple[str, dict, str]:
    """(action, typed params, token) from the form; unknown action raises 400, unknown fields are dropped."""
    form = await request.form()
    action = form.get("action")
    spec = PARAMS.get(action) if isinstance(action, str) else None
    if spec is None:
        raise HTTPException(400, "unknown action")
    params: dict = {}
    for key, kind in spec.items():
        if kind == "list":
            params[key] = [_clean(v, 40) for v in form.getlist(key) if isinstance(v, str)][:agentsmut.MAX_TARGETS]
        elif kind in LISTS:
            cap, size = LISTS[kind]
            params[key] = [_clean(v, size) for v in form.getlist(key) if isinstance(v, str)][:cap + 1]
        elif kind == "bool":
            v = form.get(key)
            if v not in ("1", "0"):
                raise HTTPException(400, f"invalid {key}")
            params[key] = v == "1"
        else:
            v = form.get(key, "")
            params[key] = _nl(v) if isinstance(v, str) else ""
    token = form.get("token")
    return action, params, token if isinstance(token, str) else ""


def _target(action: str, params: dict) -> str:
    """Audit target: names and paths only (never a field value such as a body or a credential)."""
    if action == "skill_toggle":
        return f"{params['agent']}/{params['skill']}"[:120]
    if action == "skills_link":
        return f"{params['agent']} ({len(params['skills'])} skills)"[:120]
    if action == "rules_sync":
        return str(params["source"])[:60]
    if action == "index_prune":
        return "~/agents/agents-index.md"
    if action == "mcp_env":
        return f"{params['config']} {params['server']}"[:160]
    return str(params.get("name", ""))[:80]


def _hidden(params: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k, v in params.items():
        if isinstance(v, bool):
            out.append((k, "1" if v else "0"))
        elif isinstance(v, list):
            out.extend((k, x) for x in v)
        else:
            out.append((k, str(v)))
    return out


async def _page(request: Request, form_agent: str, tab: str, mode: str, status: int = 200, **extra):
    inv = await lib.inventory()
    agent = lib.find(inv, form_agent)
    if agent is None:
        raise HTTPException(404, "unknown agent")
    tab = tab if tab in TABS else "skills"
    path = "/agents/" + quote(agent.name)
    ctx = {**lib.split_ctx(inv, request.query_params, path, {"s": tab}),
           "agent": agent, "tab": tab, "base": path, "agent_rows": lib.agent_rows(inv), "mode": mode,
           "back": f"{path}?s={tab}", "counts": {"skills": 0, "rules": 0, "mcp": 0, "scripts": 0}}
    ctx["counts"] = {"skills": sum(1 for s in inv.skills if agent.name in s.enabled_for),
                     "rules": sum(1 for r in inv.rules if r.agent == agent.name),
                     "mcp": sum(1 for m in inv.mcp if m.agent == agent.name),
                     "scripts": sum(1 for s in inv.scripts if agent.name in s.used_by)}
    return html(request, "agents_detail.html", status=status, nav="agents", **ctx, **extra)


async def _ctx_of(request: Request) -> tuple[str, str]:
    form = await request.form()
    a, t = form.get("ctx_agent", ""), form.get("ctx_tab", "")
    return (_clean(a, 40) if isinstance(a, str) else ""), (t if isinstance(t, str) else "")


def _done(request: Request, action: str, target: str, result: str, detail: dict | None = None) -> None:
    try:
        audit_result(request, "agents_" + action, target, result, detail)
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None


def _refusal(msg: str, code: int) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg, "stale": code == 409}, status_code=code)


@router.post("/agents/review", dependencies=[Depends(mutation_guard("agents_review", Tier.SAFE))])
async def agents_review(request: Request):
    action, params, _tok = await _params(request)
    ag, tab = await _ctx_of(request)
    try:
        pl = await executors.run_collectors(agentsmut.plan, action, params, home=lib.home())
    except agentsmut.MutError as e:
        if not is_navigation(request):
            return _refusal(str(e), 400)
        return await _page(request, ag, tab, "notice", 400, notice=str(e), notice_kind="err")
    destructive = action.endswith("_delete")
    if not is_navigation(request):
        return JSONResponse({"ok": True, "ops": [{"kind": o.kind, "path": o.path, "detail": o.detail} for o in pl.ops],
                             "token": pl.token, "destructive": destructive})
    return await _page(request, ag, tab, "review", plan=pl, hidden=_hidden(params), action=action,
                       ctx_agent=ag, ctx_tab=tab, destructive=destructive, title="Review changes")


@router.post("/agents/apply", dependencies=[Depends(mutation_guard("agents_apply", Tier.SAFE))])
async def agents_apply(request: Request):
    action, params, token = await _params(request)
    ag, tab = await _ctx_of(request)
    target = _target(action, params)
    try:
        lines = await executors.run_collectors(agentsmut.apply, action, params, token, home=lib.home())
    except agentsmut.MutError as e:
        _done(request, action, target, "denied" if str(e) == agentsmut.STALE else "error")
        code = 409 if str(e) == agentsmut.STALE else 400
        msg = str(e) + (" Review the changes again." if code == 409 and "review" not in str(e).lower() else "")
        if not is_navigation(request):
            return _refusal(msg, code)
        return await _page(request, ag, tab, "notice", code, notice=msg, notice_kind="err")
    _done(request, action, target, "ok", {"ops": len(lines)})
    from pm.agents.inventory import read as agentsinv

    agentsinv.clear_cache()  # the next page load shows the change, not a cached inventory
    if not is_navigation(request):
        return JSONResponse({"ok": True, "lines": lines})
    return await _page(request, ag, tab, "result", lines=lines, title="Applied")


@router.post("/agents/rescan", dependencies=[Depends(mutation_guard("agents_rescan", Tier.SAFE))])
async def agents_rescan(request: Request):
    """Drop the inventory and usage caches (the head's Re-scan); the next page load reads everything again."""
    from pm.agents import usage
    from pm.agents.inventory import read as agentsinv

    _done(request, "rescan", "agents", "ok")  # first: fail closed before anything is dropped
    agentsinv.clear_cache()
    usage.clear_cache()
    if is_navigation(request):
        return RedirectResponse("/agents", status_code=303)
    return JSONResponse({"ok": True})


def _read_edit(kind: str, name: str, home: Path) -> bytes:
    """Read the editable file of one object: regular file, no symlinks, inside its root, <= 256 KB."""
    from pm.agents.inventory.read import is_blocked

    rx = {"skill": agentsmut.SKILL_RE, "rule": agentsmut.RULE_RE, "script": agentsmut.SCRIPT_RE}[kind]
    if not rx.fullmatch(name):
        raise agentsmut.MutError("Invalid name.")
    if kind == "skill":
        root, path = home / ".agents/skills", home / ".agents/skills" / name / "SKILL.md"
    elif kind == "rule":
        root = home / "agents"
        path = root / name if name in agentsmut.TOP_RULES else root / "rules" / name
    else:
        root, path = home / ".agents/hooks", home / ".agents/hooks" / name
    if is_blocked(path, home) or is_blocked(path.parent, home):
        raise agentsmut.MutError(agentsmut.PROTECTED)
    try:
        rroot, rparent = root.resolve(), path.parent.resolve()
        if not (rparent == rroot or rroot in rparent.parents) or home.resolve() not in rroot.parents:
            raise agentsmut.MutError("Outside the allowed directories.")
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise agentsmut.MutError("Not a regular file.")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as f:
            data = f.read(EDIT_MAX + 1)
    except OSError:
        raise agentsmut.MutError("The file could not be read.") from None
    if len(data) > EDIT_MAX:
        raise agentsmut.MutError("The file is larger than 256 KB; edit it directly.")
    return data


def _split_skill(text: str) -> tuple[str, str]:
    """(description, body) of a SKILL.md that has exactly name + description frontmatter."""
    m = re.fullmatch(r"---\nname: [^\n]*\n(description: [^\n]*)\n---\n\n?(.*)", text, re.DOTALL)
    if not m:
        raise agentsmut.MutError("This skill has other frontmatter fields; edit the file directly.")
    raw = _FM_DESC.fullmatch(m.group(1)).group(1)
    try:
        desc = json.loads(raw) if raw.startswith('"') else raw
    except ValueError:
        raise agentsmut.MutError("The description could not be parsed; edit the file directly.") from None
    if not isinstance(desc, str):
        raise agentsmut.MutError("The description could not be parsed; edit the file directly.")
    return desc, m.group(2).rstrip("\n")


@router.get("/agents/{name}/edit", dependencies=AUTH)
async def agent_edit(request: Request, name: str):
    kind, obj = request.query_params.get("kind", ""), _clean(request.query_params.get("name"), 80)
    if kind not in KINDS:
        raise HTTPException(400, "unknown kind")
    ctx_tab = {"skill": "skills", "rule": "rules", "script": "scripts"}[kind]
    try:
        data = await executors.run_collectors(_read_edit, kind, obj, lib.home())
        text = data.decode("utf-8")
        desc, body = _split_skill(text) if kind == "skill" else ("", text)
    except UnicodeDecodeError:
        return await _page(request, name, ctx_tab, "notice", 400, notice="The file is not UTF-8 text.", notice_kind="err")
    except agentsmut.MutError as e:
        return await _page(request, name, ctx_tab, "notice", 400, notice=str(e), notice_kind="err")
    can_delete = not (kind == "rule" and obj in agentsmut.TOP_RULES)
    return await _page(request, name, ctx_tab, "edit", kind=kind, obj=obj, desc=desc, body=body,
                       can_delete=can_delete, title="Edit " + obj)
