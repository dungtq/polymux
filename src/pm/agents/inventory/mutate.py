"""Filesystem mutations for the Agents page (no HTTP, no audit, no subprocess).

Every action is planned first (read-only, returns the exact file operations plus a token
over the plan and the current state of every target) and applied only with that token.
Allowed roots: ~/.agents/skills, ~/.agents/hooks, ~/agents, the agent skills dirs and
rules files reported by the inventory. Never ~/.agents-git or ~/.cfg, also not via symlink.
"""

from __future__ import annotations

import hmac
import json
import os
import stat
import threading
from datetime import UTC, datetime
from pathlib import Path

from pm.core.fsutil import atomic_write

from .mutate_base import (
    _OP_KIND,
    _backup,
    _Built,
    _disp,
    _guard,
    _home,
    _lstat,
    _make_link,
    _sha,
    _state,
    _Step,
)
from .mutate_base import AGENT_RE as AGENT_RE
from .mutate_base import BACKUP_DISP as BACKUP_DISP
from .mutate_base import BACKUP_REL as BACKUP_REL
from .mutate_base import MAX_BODY as MAX_BODY
from .mutate_base import MAX_DELETE_DEPTH as MAX_DELETE_DEPTH
from .mutate_base import MAX_DELETE_FILES as MAX_DELETE_FILES
from .mutate_base import MAX_DESC as MAX_DESC
from .mutate_base import MAX_READ as MAX_READ
from .mutate_base import MAX_TARGETS as MAX_TARGETS
from .mutate_base import PROTECTED as PROTECTED
from .mutate_base import RULE_RE as RULE_RE
from .mutate_base import SCRIPT_RE as SCRIPT_RE
from .mutate_base import SKILL_RE as SKILL_RE
from .mutate_base import STALE as STALE
from .mutate_base import TOP_RULES as TOP_RULES
from .mutate_base import MutError as MutError
from .mutate_base import Op as Op
from .mutate_base import Plan as Plan
from .mutate_mcp import _mcp_env
from .mutate_rules import _index_prune, _rule, _rules_sync, _script
from .mutate_skills import MAX_LINK as MAX_LINK
from .mutate_skills import _skill_create, _skill_delete, _skill_edit, _skill_toggle, _skills_link
from .validate import validate_rules as validate_rules

_LOCK = threading.Lock()


# ---------------------------------------------------------------- plan / apply


def _build(action: str, params: dict, home: Path) -> _Built:
    if not isinstance(params, dict):
        raise MutError("Parameters must be an object.")
    if action == "skill_toggle":
        return _skill_toggle(params, home)
    if action == "skills_link":
        return _skills_link(params, home)
    if action == "index_prune":
        return _index_prune(params, home)
    if action == "mcp_env":
        return _mcp_env(params, home)
    if action == "rules_sync":
        return _rules_sync(params, home)
    if action == "skill_create":
        return _skill_create(params, home)
    if action == "skill_edit":
        return _skill_edit(params, home)
    if action == "skill_delete":
        return _skill_delete(params, home)
    for prefix, fn in (("rule_", _rule), ("script_", _script)):
        if action.startswith(prefix) and action[len(prefix) :] in (
            "create",
            "edit",
            "delete",
        ):
            return fn(action[len(prefix) :], params, home)
    raise MutError("Unknown action.")


def _ops(built: _Built, home: Path) -> tuple[Op, ...]:
    return tuple(
        Op(_OP_KIND[s.kind], _disp(s.path, home), s.detail) for s in built.steps
    )


def _states(built: _Built, home: Path) -> dict[Path, str]:
    """State of every path the plan touches or depends on, read once per plan/apply."""
    return {p: _state(p, home) for p in {s.path for s in built.steps} | set(built.extra)}


def _token(action: str, params: dict, built: _Built, home: Path, states: dict[Path, str]) -> str:
    payload = {
        "action": action,
        "params": params,
        "ops": [(o.kind, o.path, o.detail) for o in _ops(built, home)],
        "targets": [(s.kind, str(s.target), _sha(s.data), s.mode) for s in built.steps],
        "state": sorted((_disp(p, home), st) for p, st in states.items()),
    }
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=True)
    return _sha(raw.encode())


def plan(action: str, params: dict, *, home: Path | None = None) -> Plan:
    """Read-only: the exact operations plus the token that apply() needs."""
    h = _home(home)
    built = _build(action, params, h)
    return Plan(action, _ops(built, h), _token(action, params, built, h, _states(built, h)))


def apply(
    action: str, params: dict, token: str, *, home: Path | None = None
) -> list[str]:
    """Recompute the plan, refuse when the token differs, then execute."""
    h = _home(home)
    if not isinstance(token, str):
        raise MutError(STALE)
    with _LOCK:
        built = _build(action, params, h)
        states = _states(built, h)
        if not hmac.compare_digest(_token(action, params, built, h, states), token):
            raise MutError(STALE)
        return _execute(built, h, states)


def _execute(built: _Built, home: Path, states: dict[Path, str]) -> list[str]:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S-%fZ")
    # state of every file a step rewrites, exactly as the token hashed it (not a second read, which would miss a
    # write in between): re-checked right before the replace
    reviewed = {s.path: states.get(s.path, "") for s in built.steps if s.kind == "write"}
    out: list[str] = []
    for s in built.steps:
        d = _disp(s.path, home)
        if s.kind == "skip":
            out.append(f"skipped {d} ({s.detail})")
            continue
        _guard(s.path, s.root, home)
        try:
            out.extend(_run_step(s, d, home, stamp, reviewed.get(s.path, "")))
        except OSError as e:
            raise MutError(
                f"Could not change {d} ({e.strerror or 'I/O error'})."
            ) from e
    return out


def _unchanged(path: Path, home: Path, reviewed: str) -> None:
    """Refuse when a file changed between the review check and the replace (another program wrote it)."""
    if reviewed and _state(path, home) != reviewed:
        raise MutError(STALE)


def _run_step(s: _Step, d: str, home: Path, stamp: str, reviewed: str = "") -> list[str]:
    st = _lstat(s.path)
    out: list[str] = []
    if s.kind == "mkdir":
        if st is not None:
            raise MutError(f"{d} appeared since the review.")
        os.mkdir(s.path, 0o755)
        return [f"created directory {d}"]
    if s.kind == "link":
        if st is not None:
            raise MutError(f"{d} appeared since the review.")
        _make_link(s.path, s.target, False)
        return [f"linked {d}"]
    if s.kind == "unlink":
        if st is None or not stat.S_ISLNK(st.st_mode):
            raise MutError(f"{d} is no longer a symlink.")
        os.unlink(s.path)
        return [f"unlinked {d}"]
    if s.kind == "relink":
        if st is None or stat.S_ISDIR(st.st_mode):
            raise MutError(f"{d} changed since the review.")
        out.append(f"backed up {d} to {_backup(s.path, home, stamp)}")
        _make_link(s.path, s.target, True)
        return [*out, f"linked {d} (replaced)"]
    if s.kind == "write":
        if st is not None and not stat.S_ISREG(st.st_mode):
            raise MutError(f"{d} is not a regular file.")
        if st is not None and s.backup:
            out.append(f"backed up {d} to {_backup(s.path, home, stamp)}")
        atomic_write(s.path, s.data, mode=s.mode, fsync=True,
                     before_replace=lambda: _unchanged(s.path, home, reviewed))
        return [*out, f"{'wrote' if st is not None else 'created'} {d}"]
    if s.kind == "rmfile":
        if st is None or stat.S_ISDIR(st.st_mode):
            raise MutError(f"{d} changed since the review.")
        if s.backup:
            out.append(f"backed up {d} to {_backup(s.path, home, stamp)}")
        os.unlink(s.path)
        return [*out, f"deleted {d}"]
    if s.kind == "rmdir":
        if st is None or not stat.S_ISDIR(st.st_mode):
            raise MutError(f"{d} changed since the review.")
        os.rmdir(s.path)
        return [f"deleted {d}"]
    raise MutError("Unknown step.")


__all__ = ["MutError", "Op", "Plan", "apply", "plan", "validate_rules"]
