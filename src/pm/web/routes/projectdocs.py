"""Read-only right-hand pane of the project page: project files, file tree, skills (tier 0).

Mounted inside ``pages.router`` (see ``web/routes/__init__.py``), not listed in ``ROUTERS`` on its own.
"""

from __future__ import annotations

import os
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from pm.core import executors
from pm.web.deps import AUTH, is_navigation, snapshot
from pm.web.docpane import VIEW_TITLES, doc_fragment, doc_html, project_file_html
from pm.web.render import get_template

router = APIRouter()


def _to_page(name: str, tab: str, arg: str = "") -> RedirectResponse:
    """These routes are pane fragments; a direct browser navigation lands on the project page with that tab
    open (``#browse[=path]``, ``#view=key``, ``#skill=scope/src/name``; projview.js reads the hash)."""
    frag = tab + ("=" + quote(arg, safe="/") if arg else "")
    return RedirectResponse("/p/" + quote(name) + "#" + frag, status_code=303)


@router.get("/p/{name}/view/{key}", response_class=HTMLResponse, dependencies=AUTH)
async def project_view(request: Request, name: str, key: str) -> Response:
    """Read-only right-hand tab body of the project page: CHANGELOG, README, CLAUDE.md, AGENTS.md, git dirty."""
    from pm.git import ctl as gitctl

    if is_navigation(request):
        return _to_page(name, "view", key)
    snap = await snapshot(request)
    project = snap.project(name)
    if project is None:
        raise HTTPException(404, "unknown project")
    if key == "dirty":
        files, total = await executors.run_collectors(gitctl.dirty_files, project.path)
        return doc_fragment(doc_html("Git status", dirty=True, files=files, total=total))
    if key == "skills":
        from pm.projects import projfiles

        skills = await executors.run_collectors(projfiles.list_skills, project.path, os.path.realpath(os.path.expanduser("~")))
        return doc_fragment(get_template("_project_skills.html").render(
            skills=skills, base="/p/" + quote(project.name)))
    if key not in VIEW_TITLES:
        raise HTTPException(404, "unknown file")
    html, _found = await executors.run_collectors(project_file_html, project.path, key)
    return doc_fragment(html)


@router.get("/p/{name}/files", response_class=HTMLResponse, dependencies=AUTH)
async def project_files(request: Request, name: str, path: str = "", raw: int = 0, diff: int = 0) -> Response:
    """Read-only file/dir tree of the project root for the right-hand pane (no symlinks, nothing outside the root)."""
    from pm.projects import projfiles

    if is_navigation(request):
        return _to_page(name, "browse", path)
    snap = await snapshot(request)
    project = snap.project(name)
    if project is None:
        raise HTTPException(404, "unknown project")
    try:
        tree = await executors.run_collectors(projfiles.file_view, project.path, path, bool(raw), bool(diff))
    except projfiles.FileRefused as e:
        return doc_fragment(doc_html("Browse", error=e.msg), 404 if e.status == 404 else 403)
    html = get_template("_project_files.html").render(
        tree=tree, base="/p/" + quote(project.name), project_name=project.name)
    return doc_fragment(html)


@router.get("/p/{name}/skill/{scope}/{src}/{skill}", response_class=HTMLResponse, dependencies=AUTH)
async def project_skill(request: Request, name: str, scope: str, src: str, skill: str) -> Response:
    """Read-only SKILL.md of a local (project) or global (home) skill."""
    from pm.projects import projfiles

    if is_navigation(request):
        return _to_page(name, "skill", f"{scope}/{src}/{skill}")
    snap = await snapshot(request)
    project = snap.project(name)
    if project is None:
        raise HTTPException(404, "unknown project")
    try:
        doc = await executors.run_collectors(projfiles.read_skill, project.path, os.path.realpath(os.path.expanduser("~")), scope, src, skill)
    except projfiles.FileRefused as e:
        return doc_fragment(doc_html(skill, error=e.msg), 404)
    return doc_fragment(doc_html(f"{skill} ({scope}, {src})", doc))
