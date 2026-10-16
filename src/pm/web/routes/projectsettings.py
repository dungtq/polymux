"""Project page > Settings: hidden components, default branch, login accounts, linked projects (``project_settings.json``).

Both POSTs: POST + CSRF + Origin + session + tier 1 + audit (``project_settings``, ``project_links``).
The audit detail carries counts and names only, never paths. The panel is the fragment
``_project_settings.html``; saves return it again with a flash text.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from pm.core import accounts, executors
from pm.core.errors import Invalid
from pm.core.models import Tier
from pm.infra import sshkeys
from pm.projects import projects, workspace
from pm.projects import settings as pset
from pm.security.auth import require_session
from pm.security.guard import mutation_guard
from pm.web import controlctl
from pm.web.deps import project_of
from pm.web.render import get_template, git_remotes_html, page_ctx

router = APIRouter()
_AUTH = [Depends(require_session)]
_NOSTORE = {"Cache-Control": "no-store"}


def _known(request: Request) -> set[str]:
    return {p.name for p in projects.discover(request.app.state.settings)}


def _build(request: Request, name: str, path, flash: str) -> str:
    st = request.app.state.settings
    ps = pset.load(st, name)
    known = _known(request)
    key = sshkeys.managed_key(st)
    return get_template("_project_settings.html").render(
        **page_ctx(request), project=project_of(request, name),
        base=f"/p/{quote(name)}", ps=ps, components=pset.COMPONENTS,
        account_kinds=[(k, ns, ps.account(k), accounts.global_default(st, k))
                       for k in pset.ACCOUNT_KINDS if (ns := accounts.names(st, k))],
        choices=sorted(known - {name}), linked=[n for n in ps.links if n in known],
        pubkey=key.line if key else "", git_html=git_remotes_html(request, name, path), flash=flash,
        workspaces=[(w.id, w.label) for w in _workspaces(st)])


async def _render(request: Request, name: str, flash: str = "") -> HTMLResponse:
    p = project_of(request, name)
    html = await executors.run_collectors(_build, request, name, p.path, flash)
    return HTMLResponse(html, headers=_NOSTORE)


@router.get("/p/{name}/settings/panel", response_class=HTMLResponse, dependencies=_AUTH)
async def settings_panel(request: Request, name: str):
    return await _render(request, name)


async def _save(request: Request, action: str, name: str, fn, detail: dict):
    """Run the store call; audit ok/denied/error. Returns the flash text."""
    try:
        await executors.run_collectors(fn)
    except Invalid as e:
        controlctl.done(request, action, name, "denied", {"reason": str(e)[:80]})
        return f"Refused: {str(e)[:100]}"
    except OSError:
        controlctl.done(request, action, name, "error", {"reason": "save"})
        return "could not save settings"
    controlctl.done(request, action, name, "ok", detail)
    return "Saved."


@router.post("/p/{name}/settings", response_class=HTMLResponse)
async def save_settings(request: Request, name: str,
                        _=Depends(mutation_guard("project_settings", Tier.SAFE))):
    project_of(request, name)
    st = request.app.state.settings
    form = await request.form()
    show = {v for v in form.getlist("show") if isinstance(v, str)}
    hidden = [c for c in pset.COMPONENTS if c not in show]
    branch = await controlctl.form(request, "default_branch")
    vms = await controlctl.form(request, "vms")
    # only kinds present in the form (the panel shows a select per configured kind); '' = default
    accounts = {k: await controlctl.form(request, f"account_{k}") for k in pset.ACCOUNT_KINDS
                if f"account_{k}" in form}

    def fn():
        pset.save_hidden(st, name, hidden)
        pset.save_default_branch(st, name, branch)
        pset.save_vms(st, name, vms)
        if accounts:
            pset.save_accounts(st, name, accounts)

    flash = await _save(request, "project_settings", name, fn,
                        {"hidden": len(hidden), "branch_set": int(bool(branch.strip())),
                         "vms": len([x for x in vms.replace(",", "\n").splitlines() if x.strip()]),
                         **({"accounts": ",".join(f"{k}={v or 'global'}" for k, v in accounts.items())} if accounts else {})})
    return await _render(request, name, flash)


@router.post("/p/{name}/settings/links", response_class=HTMLResponse)
async def save_links(request: Request, name: str,
                     _=Depends(mutation_guard("project_settings", Tier.SAFE))):
    project_of(request, name)
    st = request.app.state.settings
    links = [v for v in (await request.form()).getlist("link") if isinstance(v, str)]
    known = _known(request)
    flash = await _save(request, "project_links", name,
                        lambda: pset.save_links(st, name, links, known), {"links": len(links)})
    return await _render(request, name, flash)


def _workspaces(st) -> list[workspace.WorkspaceFile]:
    """``*.code-workspace`` files in every project dir and its parent (the ids the import accepts)."""
    roots = [q for p in projects.discover(st) for q in (Path(p.path), Path(p.path).parent)]
    return workspace.discover(roots, limit=20)


@router.post("/p/{name}/settings/links/import", response_class=HTMLResponse)
async def import_links(request: Request, name: str,
                       _=Depends(mutation_guard("project_settings", Tier.SAFE))):
    project_of(request, name)
    st = request.app.state.settings
    wid = (await controlctl.form(request, "workspace")).strip()
    got: dict = {"linked": 0, "skipped": 0}

    def fn():
        found = {w.id: w for w in _workspaces(st)}
        if wid not in found:
            raise Invalid("workspace: not found")
        known = {p.name: Path(p.path) for p in projects.discover(st)}
        names, got["skipped"] = workspace.match(workspace.parse_folders(found[wid].path), known, name)
        old = [n for n in pset.load(st, name).links if n in known]
        got["linked"] = len([n for n in names if n not in old])
        pset.save_links(st, name, old + names, set(known))

    flash = await _save(request, "project_links", name, fn, got)
    if flash == "Saved.":
        flash = f"Linked {got['linked']} project(s). {got['skipped']} folder(s) are not pm projects."
    return await _render(request, name, flash)
