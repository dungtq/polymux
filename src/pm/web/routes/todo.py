"""Project-page TODO box: toggle ``- [ ]`` / ``- [x]`` lines in ``<project>/TODO.md``.

Same rules as web.routes.backlog: the project NAME is validated against the registry, the path is
derived server-side, a symlinked / escaping TODO.md is refused, writes are atomic, capped at
256 KB, and done under the cross-process lock (filelock.py). The item is identified by line
index AND the text the page showed and applied to the CURRENT file, so unrelated edits do not
matter; if the item is gone or changed the toggle is refused (409). Audit details
carry the project name and sizes only, never the text.

A tracker project (docs/TRACKER-PLAN.md) keeps TODO.md in pm.db: ``line`` is the item number and the
toggle goes to the store, which re-renders the file. ``POST /p/{name}/todo/add`` (tracker projects only; 409
otherwise) adds an open item, optionally under a section heading.
"""

from __future__ import annotations

import sqlite3
from typing import NoReturn
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from pm.core import executors
from pm.core.errors import PmError
from pm.core.models import Tier
from pm.projects import backlog as rb
from pm.projects.backlog import Bad
from pm.projects.todo import FILE, resolve_path, toggle_line
from pm.projects.tracker import store, views
from pm.security.audit import AuditError
from pm.security.guard import audit_result, mutation_guard
from pm.web.issuectx import audit_ok, num_of

router = APIRouter()

MAX_TEXT = 2000
MAX_SECTION = 200


def _deny(request: Request, name: str, status: int, reason: str, msg: str) -> NoReturn:
    """Audit a denied toggle (project name and reason only), then raise ``status``."""
    try:
        audit_result(request, "todo_toggle", "todo", "denied",
                     {"project": name[:60], "reason": reason[:40]})
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None
    raise HTTPException(status, msg)


@router.post("/p/{name}/todo/toggle", response_class=HTMLResponse)
async def todo_toggle(request: Request, name: str,
                      _=Depends(mutation_guard("todo_toggle", Tier.SAFE))) -> Response:
    form = await request.form()
    settings = request.app.state.settings
    try:
        known = await executors.run_collectors(rb.registry, settings)
    except OSError:
        raise HTTPException(502, "registry unavailable") from None
    if name not in known:
        _deny(request, name, 404, "unknown project", "unknown project")
    text = form.get("text")
    try:
        idx = int(str(form.get("line", "")))
    except ValueError:
        _deny(request, name, 400, "bad line", "bad line")
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        _deny(request, name, 400, "bad form", "bad form")
    if await executors.run_collectors(views.is_tracker, settings, name, "todo"):
        return await _tracker_toggle(request, name, idx)
    try:
        path = resolve_path(known[name])
        old, new = await executors.run_collectors(
            lambda: rb.mutate(settings, path, lambda t: toggle_line(t, idx, text), name=FILE))
    except Bad as e:
        _deny(request, name, e.status, e.msg, e.msg)
    except OSError:
        try:
            audit_result(request, "todo_toggle", "todo", "error", {"project": name, "reason": "io"})
        except AuditError:
            pass
        raise HTTPException(502, "could not write TODO.md") from None
    try:
        audit_result(request, "todo_toggle", "todo", "ok",
                     {"project": name, "bytes_before": old, "bytes_after": new})
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None
    return _back(name)


@router.post("/p/{name}/todo/add", response_class=HTMLResponse)
async def todo_add(request: Request, name: str,
                   _=Depends(mutation_guard("todo_add", Tier.SAFE))) -> Response:
    """Add an open TODO item to a tracker project (``text``, optional ``section``); 409 for a file-mode project.
    The store writes the "ok" audit line (item number only, never text) inside its transaction."""
    form = await request.form()
    settings = request.app.state.settings
    try:
        known = await executors.run_collectors(rb.registry, settings)
    except OSError:
        raise HTTPException(502, "registry unavailable") from None
    if name not in known:
        _deny(request, name, 404, "unknown project", "unknown project")
    text, section = form.get("text"), form.get("section") or ""
    if (not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT
            or not isinstance(section, str) or len(section) > MAX_SECTION or "\n" in text + section):
        _deny(request, name, 400, "bad form", "bad form")
    if not await executors.run_collectors(views.is_tracker, settings, name, "todo"):
        _deny(request, name, 409, "not a tracker", "TODO add needs a tracker project")
    audit = audit_ok(request, "todo_add", "todo", {"project": name}, keep=("num",))
    try:
        await executors.run_collectors(
            lambda s: store.add_item(s, name, "todo", text.strip(), section=section.strip() or None, audit=audit),
            settings)
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None
    except PmError as e:
        _deny(request, name, e.status, str(e), str(e))
    except sqlite3.Error:
        _deny(request, name, 503, "tracker busy", "tracker database busy")
    return _back(name)


def _back(name: str) -> Response:
    return RedirectResponse(f"/p/{quote(name)}#todo", status_code=303,
                            headers={"Cache-Control": "no-store"})


async def _tracker_toggle(request: Request, name: str, idx: int) -> Response:
    """Tracker project: the posted ``line`` is the TODO item number; done <-> open in pm.db. The store writes
    the "ok" audit line inside its transaction (a failed write rolls the toggle back)."""
    audit = audit_ok(request, "todo_toggle", "todo", {"project": name}, keep=("num", "done"))
    try:
        await executors.run_collectors(lambda s: store.toggle(s, name, "todo", num_of(idx), audit=audit),
                                       request.app.state.settings)
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None
    except PmError as e:
        _deny(request, name, e.status, str(e), str(e))
    except sqlite3.Error:
        _deny(request, name, 503, "tracker busy", "tracker database busy")
    return _back(name)
