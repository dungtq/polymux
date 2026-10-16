"""Git tab JSON API (theme-neutral): status view model, "what will happen" plans and the git actions.

GET  /p/{name}/git/status  -> ``ops.view``: status (files, counts, ahead/behind), branches, stashes, commits.
GET  /p/{name}/git/plan?action=..&paths=..&path=..&message=..&untracked=..&index=..&branch=..  -> ``Plan.view()``.
POST /p/{name}/git/{stage,unstage,discard,commit,stash,stash-pop,stash-drop,checkout,merge,branch-delete,
pull-stash} -> {ok, text, clash} or {ok: false, error[, files]} with the domain status (git failures 409).

Every POST: ``mutation_guard`` (SAFE; MESSAGE for discard, branch-delete, stash-drop and a merge with a merge
commit) + a typed ``confirm`` equal to the object's name for those (the path, the branch, ``stash@{n}``) + an audit
line. Error texts are masked and never carry argv. Push stays POST /p/{name}/git/push (control.py, pm.toml
``push_remote`` only). The status, plan and pull-stash calls get ``push_remote`` from the settings.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from pm.core import executors
from pm.core.errors import PmError
from pm.core.models import Tier
from pm.git import ops, plans
from pm.git.ctl import GitDenied, GitError
from pm.security.auth import require_session
from pm.security.guard import mutation_guard, require_tier
from pm.security.mask import mask
from pm.web import controlctl
from pm.web.deps import project_of

router = APIRouter()
_AUTH = [Depends(require_session)]
_NOSTORE = {"Cache-Control": "no-store"}
_PLAN_KEYS = ("path", "message", "untracked", "index", "branch")


def _remote(request: Request) -> str:
    return request.app.state.settings.push_remote


def _why(e: Exception) -> tuple[int, str]:
    if isinstance(e, PmError):
        return e.status, str(e)
    if isinstance(e, GitDenied):
        return 403, "git refused"
    return 409, ops.short(str(e)) or "git failed"


def _err(status: int, why: str, files: tuple[str, ...] = ()) -> JSONResponse:
    body: dict = {"ok": False, "error": mask(why)[:200]}
    if files:
        body["files"] = list(files[:200])
    return JSONResponse(body, status_code=status, headers=_NOSTORE)


async def _field(request: Request, key: str) -> str:
    return (await controlctl.form(request, key)).strip()


async def _flag(request: Request, key: str) -> bool:
    return (await _field(request, key)) in ("1", "true", "on")


async def _index(request: Request) -> int | None:
    v = await _field(request, "index")
    return int(v) if v.isdigit() and len(v) < 6 else None


def _bad_index(request: Request, action: str, name: str) -> JSONResponse:
    controlctl.done(request, action, name, "denied", {"reason": "bad stash index"})
    return _err(400, "bad stash index")


async def _act(request: Request, action: str, name: str, fn, *args, target: str = "", detail: dict | None = None,
               **kw) -> JSONResponse:
    """Run one ``ops`` action in the collector pool; audit ok / denied / error; JSON result."""
    try:
        p = project_of(request, name)
    except HTTPException:
        raise controlctl.refuse(request, action, name, 404, "unknown project") from None
    tgt = f"{name}:{target[:120]}" if target else name
    detail = dict(detail or {})
    try:
        out = await executors.run_collectors(fn, p.path, *args, **kw)
    except (PmError, GitError) as e:
        status, why = _why(e)
        controlctl.done(request, action, tgt, "denied" if status < 500 else "error",
                        {**detail, "reason": mask(why)[:80]})
        return _err(status, why, getattr(e, "files", ()))
    extra = {**({"sha": out.sha} if out.sha else {}), **({"clash": len(out.clash)} if out.clash else {})}
    controlctl.done(request, action, tgt, "ok", {**detail, **extra})
    return JSONResponse({"ok": True, "text": mask(out.text), "clash": list(out.clash)}, headers=_NOSTORE)


async def _typed(request: Request, action: str, name: str, want: str) -> JSONResponse | None:
    """None when the form's ``confirm`` equals ``want``; else an audited 400."""
    if want.strip() and await _field(request, "confirm") == want.strip():
        return None
    controlctl.done(request, action, f"{name}:{want[:120]}", "denied", {"reason": "confirm"})
    return _err(400, "type the name to confirm")


# ------------------------------------------------------------------ reads
@router.get("/p/{name}/git/status", dependencies=_AUTH)
async def git_status(request: Request, name: str):
    p = project_of(request, name)
    try:
        data = await executors.run_collectors(ops.view, p.path, remote=_remote(request))
    except (PmError, GitError) as e:
        return _err(*_why(e))
    return JSONResponse(data, headers=_NOSTORE)


@router.get("/p/{name}/git/plan", dependencies=_AUTH)
async def git_plan(request: Request, name: str, action: str = ""):
    p = project_of(request, name)
    q = request.query_params
    args = {"paths": q.getlist("paths"), **{k: q[k] for k in _PLAN_KEYS if k in q}}
    try:
        got = await executors.run_collectors(plans.plan, p.path, action, args, remote=_remote(request))
    except (PmError, GitError) as e:
        return _err(*_why(e))
    return JSONResponse(got.view(), headers=_NOSTORE)


# ------------------------------------------------------------------ files and commit
async def _paths(request: Request) -> list[str]:
    return [v for v in (await request.form()).getlist("paths") if isinstance(v, str)]


@router.post("/p/{name}/git/stage")
async def git_stage(request: Request, name: str, _=Depends(mutation_guard("git_stage", Tier.SAFE))):
    paths = await _paths(request)
    return await _act(request, "git_stage", name, ops.stage, paths, detail={"files": len(paths)})


@router.post("/p/{name}/git/unstage")
async def git_unstage(request: Request, name: str, _=Depends(mutation_guard("git_unstage", Tier.SAFE))):
    paths = await _paths(request)
    return await _act(request, "git_unstage", name, ops.unstage, paths, detail={"files": len(paths)})


@router.post("/p/{name}/git/discard")
async def git_discard(request: Request, name: str, _=Depends(mutation_guard("git_discard", Tier.MESSAGE))):
    rel = await controlctl.form(request, "path")
    if bad := await _typed(request, "git_discard", name, rel):
        return bad
    untracked = await _flag(request, "untracked")
    return await _act(request, "git_discard", name, ops.discard, rel, untracked=untracked, target=rel,
                      detail={"untracked": int(untracked)})


@router.post("/p/{name}/git/commit")
async def git_commit(request: Request, name: str, _=Depends(mutation_guard("git_commit", Tier.SAFE))):
    message = await controlctl.form(request, "message")
    return await _act(request, "git_commit", name, ops.commit, message)


# ------------------------------------------------------------------ stash
@router.post("/p/{name}/git/stash")
async def git_stash(request: Request, name: str, _=Depends(mutation_guard("git_stash", Tier.SAFE))):
    message, untracked = await controlctl.form(request, "message"), await _flag(request, "untracked")
    return await _act(request, "git_stash", name, ops.stash_push, message, untracked=untracked,
                      detail={"untracked": int(untracked)})


@router.post("/p/{name}/git/stash-pop")
async def git_stash_pop(request: Request, name: str, _=Depends(mutation_guard("git_stash_pop", Tier.SAFE))):
    index = await _index(request)
    if index is None:
        return _bad_index(request, "git_stash_pop", name)
    return await _act(request, "git_stash_pop", name, ops.stash_pop, index, target=f"stash@{{{index}}}")


@router.post("/p/{name}/git/stash-drop")
async def git_stash_drop(request: Request, name: str, _=Depends(mutation_guard("git_stash_drop", Tier.MESSAGE))):
    index = await _index(request)
    if index is None:
        return _bad_index(request, "git_stash_drop", name)
    if bad := await _typed(request, "git_stash_drop", name, f"stash@{{{index}}}"):
        return bad
    return await _act(request, "git_stash_drop", name, ops.stash_drop, index, target=f"stash@{{{index}}}")


@router.post("/p/{name}/git/pull-stash")
async def git_pull_stash(request: Request, name: str, _=Depends(mutation_guard("git_pull_stash", Tier.SAFE))):
    return await _act(request, "git_pull_stash", name, ops.pull_stash, remote=_remote(request))


# ------------------------------------------------------------------ branches
@router.post("/p/{name}/git/checkout")
async def git_checkout(request: Request, name: str, _=Depends(mutation_guard("git_checkout", Tier.SAFE))):
    branch, stash = await _field(request, "branch"), await _flag(request, "stash")
    return await _act(request, "git_checkout", name, ops.checkout, branch, stash=stash, target=branch,
                      detail={"stash": int(stash)})


@router.post("/p/{name}/git/merge")
async def git_merge(request: Request, name: str, _=Depends(mutation_guard("git_merge", Tier.SAFE))):
    """Fast-forward by default; ``ff_only=0`` (a merge commit) needs the MESSAGE tier and the typed branch."""
    branch = await _field(request, "branch")
    ff_only = await _field(request, "ff_only") != "0"
    if not ff_only:
        require_tier(request, "git_merge", Tier.MESSAGE, name)
        if bad := await _typed(request, "git_merge", name, branch):
            return bad
    return await _act(request, "git_merge", name, ops.merge, branch, ff_only=ff_only, target=branch,
                      detail={"ff_only": int(ff_only)})


@router.post("/p/{name}/git/branch-delete")
async def git_branch_delete(request: Request, name: str,
                            _=Depends(mutation_guard("git_branch_delete", Tier.MESSAGE))):
    branch = await _field(request, "branch")
    if bad := await _typed(request, "git_branch_delete", name, branch):
        return bad
    unmerged = await _flag(request, "unmerged")
    return await _act(request, "git_branch_delete", name, ops.delete_branch, branch, unmerged_ok=unmerged,
                      target=branch, detail={"unmerged": int(unmerged)})
