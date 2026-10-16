"""pm's own systemd user unit: may it be restarted from the UI, and restart it."""

from __future__ import annotations

import re

from pm.core.proc import spawn_detached

SYSTEMCTL = "/usr/bin/systemctl"
_UNIT = re.compile(r"[A-Za-z0-9_.@-]+\.service")


def reload_allowed(settings) -> bool:
    """Never on a dev instance; only a plain ``*.service`` unit name."""
    return not settings.dev_banner and bool(_UNIT.fullmatch(settings.reload_unit or ""))


def restart_unit(unit: str) -> None:
    """Ask the user manager to restart ``unit`` (non-blocking; it stops this process)."""
    if not _UNIT.fullmatch(unit or ""):
        raise ValueError("bad unit name")
    spawn_detached([SYSTEMCTL, "--user", "restart", "--no-block", "--", unit])
