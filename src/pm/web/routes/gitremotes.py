"""Project page > Git remotes: list, add, set-url, remove (design section 6).

All mutations: POST + CSRF + Origin + session + tier 1 + audit (``git_remote_add|set_url|remove``).
URLs are validated in ``gitctl`` (ssh shapes only, host allow-list ``git_allowed_hosts``) and passed
after ``--``. Pushing stays gitdev-only; other remotes are never fetched or pushed by pm. Audit detail
carries the remote name and URL host only, never the full URL.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from pm.core import executors
from pm.core.models import Tier
from pm.git import ctl as gitctl
from pm.infra import sshkeys
from pm.security.auth import require_session
from pm.security.guard import mutation_guard
from pm.web import controlctl
from pm.web.deps import project_of, snapshot
from pm.web.render import fragment, get_template, git_remotes_html, page_ctx

router = APIRouter()
_AUTH = [Depends(require_session)]
_NOSTORE = {"Cache-Control": "no-store"}


def _hosts(request: Request) -> tuple[str, ...]:
    return tuple(request.app.state.settings.git_allowed_hosts) or gitctl.DEFAULT_GIT_HOSTS


def _panel(request: Request, name: str, path, notice: str = "", ok: bool = True) -> str:
    return git_remotes_html(request, name, path, notice, ok)


async def _render(request: Request, name: str, notice: str = "", ok: bool = True) -> HTMLResponse:
    p = project_of(request, name)
    html = await executors.run_collectors(_panel, request, name, p.path, notice, ok)
    return HTMLResponse(html, headers=_NOSTORE)


@router.get("/p/{name}/git/remotes", response_class=HTMLResponse, dependencies=_AUTH)
async def remotes_panel(request: Request, name: str):
    p = project_of(request, name)
    html = await executors.run_collectors(_panel, request, name, p.path)
    return fragment(request, html)


@router.get("/p/{name}/git/panel", response_class=HTMLResponse, dependencies=_AUTH)
async def git_panel(request: Request, name: str):
    """Git tab of the project docs panel: History (recent commits) and Config (remotes + ssh key)."""
    snap = await snapshot(request)
    project = snap.project(name)
    if project is None:
        raise HTTPException(404, "unknown project")

    def build() -> str:
        return get_template("_project_git.html").render(
            **page_ctx(request), project=project, project_name=project.name,
            commits=gitctl.commit_log(project.path, 5), commit_base=gitctl.commit_url_base(project.path, _hosts(request)),
            remotes=gitctl.remotes(project.path), notice="", notice_ok=True, gitdev=gitctl.REMOTE,
            hosts=_hosts(request), pub=sshkeys.managed_key(request.app.state.settings))

    return HTMLResponse(await executors.run_collectors(build), headers=_NOSTORE)


async def _fields(request: Request, action: str, name: str, want_url: bool):
    rname = (await controlctl.form(request, "name")).strip()
    url = (await controlctl.form(request, "url")).strip() if want_url else ""
    try:
        gitctl.check_remote_name(rname)
        host = ""
        if want_url:
            gitctl.check_remote_url(url, _hosts(request))
            host = gitctl.remote_url_host(url)
    except gitctl.GitDenied as e:
        controlctl.done(request, action, f"{name}:{rname[:32]}", "denied", {"reason": str(e)[:80]})
        return None
    return rname, url, host


async def _mutate(request: Request, action: str, name: str, fn, label: str, want_url: bool):
    p = project_of(request, name)
    got = await _fields(request, action, name, want_url)
    if got is None:
        return await _render(request, name, "Refused: invalid remote name or url (ssh urls on an allowed git host only).", False)
    rname, url, host = got
    target = f"{name}:{rname}"
    detail = {"remote": rname, **({"url_host": host} if host else {})}
    try:
        if want_url:
            await executors.run_collectors(fn, p.path, rname, url, _hosts(request))
        else:
            await executors.run_collectors(fn, p.path, rname)
    except gitctl.GitDenied:
        raise controlctl.refuse(request, action, target, 403, "git refused") from None
    except gitctl.GitError as e:
        controlctl.done(request, action, target, "denied", {**detail, "reason": str(e)[:80]})
        return await _render(request, name, f"{label} failed: {str(e)[:120]}", False)
    controlctl.done(request, action, target, "ok", detail)
    return await _render(request, name, f"{label} ok.")


@router.post("/p/{name}/git/remote/add", response_class=HTMLResponse)
async def remote_add(request: Request, name: str,
                     _=Depends(mutation_guard("git_remote_add", Tier.SAFE))):
    return await _mutate(request, "git_remote_add", name, gitctl.remote_add, "Add remote", True)


@router.post("/p/{name}/git/remote/set-url", response_class=HTMLResponse)
async def remote_set_url(request: Request, name: str,
                         _=Depends(mutation_guard("git_remote_set_url", Tier.SAFE))):
    return await _mutate(request, "git_remote_set_url", name, gitctl.remote_set_url, "Set url", True)


@router.post("/p/{name}/git/remote/remove", response_class=HTMLResponse)
async def remote_remove(request: Request, name: str,
                        _=Depends(mutation_guard("git_remote_remove", Tier.SAFE))):
    action = "git_remote_remove"
    rname = (await controlctl.form(request, "name")).strip()
    if rname == gitctl.REMOTE and (await controlctl.form(request, "confirm")).strip() != gitctl.REMOTE:
        project_of(request, name)
        controlctl.done(request, action, f"{name}:{rname}", "denied", {"reason": "confirm"})
        return await _render(request, name, "Removing gitdev needs the typed confirmation.", False)
    return await _mutate(request, action, name, _remove_fn, "Remove remote", False)


def _remove_fn(path, rname):
    gitctl.remote_remove(path, rname)
