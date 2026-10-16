"""``@`` file picker for the composer: list entries of a directory (default: the agent's cwd).

``GET /a/{id}/atfiles?dir=<path>`` (agent composer) and ``GET /p/{name}/atfiles?dir=<path>`` (Launch prompt,
base = the project dir) return ``{"dir": <abs>, "base": "cwd"|"other"|"denied", "items": [...]}``.
``dir`` is relative to the agent's cwd, absolute, or ``~/...``. Names only, never file contents;
read-only; login required. Dotfiles are hidden unless ``q`` starts with ``.``.
Containment: after ``realpath`` the dir must be under ``$HOME`` or the agent's cwd, and not under
``DENY`` (secrets/config inside home); otherwise ``base`` is ``"denied"`` with no items. A cwd of
``/`` or of an ancestor of ``$HOME``'s parent (e.g. ``/home``) grants nothing beyond ``$HOME``.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from pm.core import executors
from pm.web.chatctx import live_or_ended
from pm.web.deps import AUTH, project_of, snapshot

router = APIRouter()
MAX_ITEMS = 60
SKIP = {"node_modules", "__pycache__", ".git"}
# Never listed, even though they are inside $HOME (relative to home; matched on realpath, per path component).
# Directories only: listing is per directory, so secret files (e.g. .netrc) are never opened, only named.
DENY = (".ssh", ".gnupg", ".config/pm", ".local/state/pm", ".cfg", ".agents-git", ".password-store",
        ".aws", ".kube", ".config/gh", ".docker", ".mozilla", ".local/share/keyrings", ".config/rclone",
        ".azure", ".config/gcloud")


def resolve_dir(cwd: Path, raw: str) -> Path:
    p = Path(raw).expanduser() if raw else Path()
    return Path(os.path.normpath(p if p.is_absolute() else cwd / p))


def allowed_dir(cwd: Path, d: Path) -> bool:
    """True when realpath(d) is under $HOME or realpath(cwd), and not under a DENY dir.

    The cwd allowance is ignored when realpath(cwd) is ``/`` or contains $HOME's parent (too broad)."""
    real = Path(os.path.realpath(d))
    home = Path(os.path.realpath(Path.home()))
    cwd_real = Path(os.path.realpath(cwd))
    cwd_ok = not home.parent.is_relative_to(cwd_real)  # also False for cwd == "/" or == home.parent
    if not (real.is_relative_to(home) or (cwd_ok and real.is_relative_to(cwd_real))):
        return False
    return not any(real.is_relative_to(os.path.realpath(home / x)) for x in DENY)


def list_entries(cwd: Path, dir: str, q: str) -> dict:
    """``{"dir", "base", "items"}`` for ``dir`` (relative to ``cwd``, absolute or ``~/``), names starting with ``q``."""
    d = resolve_dir(cwd, dir[:512])
    if not allowed_dir(cwd, d):
        return {"dir": str(d), "base": "denied", "items": []}
    items: list[dict] = []
    try:
        with os.scandir(d) as it:
            for e in it:
                n = e.name
                if n in SKIP or (n.startswith(".") and not q.startswith(".")):
                    continue
                if q and not n.lower().startswith(q.lower()):
                    continue
                try:
                    isdir = e.is_dir()
                except OSError:
                    isdir = False
                items.append({"name": n, "dir": isdir})
    except OSError:
        pass
    items.sort(key=lambda i: (not i["dir"], i["name"].lower()))
    other = d != cwd and cwd not in d.parents
    return {"dir": str(d), "base": "other" if other else "cwd", "items": items[:MAX_ITEMS]}


def _json(data: dict) -> Response:
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.get("/a/{agent_id}/atfiles", dependencies=AUTH)
async def atfiles(request: Request, agent_id: str, dir: str = "", q: str = "") -> Response:
    snap = await snapshot(request)
    agent, _ended = live_or_ended(request, snap, agent_id)
    return _json(await executors.run_collectors(list_entries, Path(agent.cwd or Path.home()), dir, q))


@router.get("/p/{name}/atfiles", dependencies=AUTH)
async def project_atfiles(request: Request, name: str, dir: str = "", q: str = "") -> Response:
    return _json(await executors.run_collectors(list_entries, Path(project_of(request, name).path), dir, q))
