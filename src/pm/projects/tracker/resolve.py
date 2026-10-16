"""Which project a ``pmctl`` call is about, and who makes it.

A linked git worktree resolves to its main project (one list per project); ``actor`` is the agent id
(``$PM_ACTOR``, else the output of ``actor_cmd`` when set), ``agent`` when neither is known.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from pm.core.config import Settings
from pm.core.errors import NotFound
from pm.core.proc import ProcError, run_argv
from pm.projects import projects

_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,63}$")


def project_for(settings: Settings, cwd: Path | None = None, name: str | None = None) -> tuple[str, Path]:
    """(project name, main work tree path) for ``name`` or the directory ``cwd`` (default: the cwd)."""
    found = projects.discover(settings)
    if name:
        hit = next((p for p in found if p.name == name), None)
        if hit is None:
            raise NotFound(f"unknown project {name!r}")
        hit = projects.project_for_cwd(hit.path, found) or hit
    else:
        hit = projects.project_for_cwd(cwd or Path.cwd(), found)
        if hit is None:
            raise NotFound("this directory is not in a pm project (use --project NAME)")
    return hit.name, Path(hit.path)


def actor(settings: Settings | None = None) -> str:
    env = os.environ.get("PM_ACTOR", "").strip()
    if _ACTOR.match(env):
        return env
    cmd = list(settings.actor_cmd) if settings is not None else []
    if not cmd:
        return "agent"
    try:
        cp = run_argv(cmd, timeout=2, text=True)
    except (ProcError, TypeError):  # TypeError: actor_cmd holds a non-string
        return "agent"
    out = (cp.stdout or "").strip()
    return out if cp.returncode == 0 and _ACTOR.match(out) else "agent"
