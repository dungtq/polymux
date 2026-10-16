"""Top-navbar shortcuts that work from any page, and the launch form partial every theme can embed."""
from __future__ import annotations

from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from pm.web import launchctx
from pm.web.deps import AUTH, snapshot
from pm.web.render import fragment, get_template, page_ctx

router = APIRouter()


def _pick(snap, want: str) -> str:
    """``want`` when it is a project, else the project of the most recently active agent, else the first; "" = none."""
    known = {p.name for p in snap.projects}
    recent = sorted((a for a in snap.agents if a.project in known), key=lambda a: -(a.last_active or a.state_since))
    return want if want in known else recent[0].project if recent else (snap.projects[0].name if snap.projects else "")


@router.get("/launch", dependencies=AUTH)
async def launch_any(request: Request) -> RedirectResponse:
    """Launch form of the current project (?project=), else the most recently active one (the form has a project picker); /p when none."""
    snap = await snapshot(request)
    name = _pick(snap, request.query_params.get("project", ""))  # the page the button was clicked on
    if not name:
        return RedirectResponse("/p", status_code=303)
    q = {"from": "inbox"}
    if request.query_params.get("embed") == "1":
        q["embed"] = "1"
    return RedirectResponse(f"/p/{quote(name)}/launch?{urlencode(q)}", status_code=303)


@router.get("/launch/form", dependencies=AUTH)
async def launch_form(request: Request, project: str = "", todo: str = "") -> Response:
    """The launch form of ``project`` (else as ``/launch`` picks one) as the theme's ``_launch_form.html`` partial;
    ``todo=<n>`` preselects open TODO item n ("From TODO"). Data: ``launchctx.form_model``. 404 when no project."""
    snap = await snapshot(request)
    p = snap.project(_pick(snap, project))
    if p is None:
        raise HTTPException(404, "no project")
    idx = int(todo) if todo.isdigit() and len(todo) < 6 else None
    model = await launchctx.form_model(request.app.state.settings, snap, p, idx)
    return fragment(request, get_template("_launch_form.html").render(**page_ctx(request), **model))
