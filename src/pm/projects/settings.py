"""Per-project settings (hidden components, linked projects, default branch) in ``project_settings.json``."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from pm.core import accounts as accts
from pm.core import filelock
from pm.core.config import ACCOUNT_KINDS, Settings
from pm.core.errors import Invalid
from pm.core.fsutil import JsonStore

COMPONENTS: tuple[str, ...] = (
    "browse", "git", "skills", "plans", "todo", "backlog", "readme", "features", "changelog",
    "claude", "agents", "stack", "live",
)
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
_VM_PAT = re.compile(r"^[A-Za-z0-9._*?\[\]-]{1,64}$")
MAX_VMS = 20


@dataclass(frozen=True)
class ProjectSettings:
    hidden: frozenset[str] = frozenset()
    links: tuple[str, ...] = ()
    default_branch: str = ""
    vms: tuple[str, ...] = ()
    accounts: tuple[tuple[str, str], ...] = ()  # (kind, name or '-'); absent kind = the global default

    def account(self, kind: str) -> str:
        """Chosen account name for ``kind`` ('claude' / 'codex'); '' = the global default, '-' = the CLI's own folder."""
        return next((n for k, n in self.accounts if k == kind), "")


def _file(settings: Settings) -> Path:
    return Path(settings.state_dir) / "project_settings.json"


def _store(settings: Settings) -> JsonStore:
    return JsonStore(_file(settings), dict, validate=lambda d: isinstance(d, dict), mode=0o600)


def _branch_ok(b: str) -> bool:
    return bool(_BRANCH.fullmatch(b)) and not (
        b.startswith(("-", "/", ".")) or b.endswith(("/", ".", ".lock")) or ".." in b or "//" in b)


def _parse(raw) -> ProjectSettings:
    if not isinstance(raw, dict):
        return ProjectSettings()
    hid = raw.get("hidden")
    hidden = frozenset(x for x in hid if x in COMPONENTS) if isinstance(hid, list) else frozenset()
    lk = raw.get("links")
    links = tuple(dict.fromkeys(x for x in lk if isinstance(x, str) and x)) if isinstance(lk, list) else ()
    br = raw.get("default_branch")
    branch = br if isinstance(br, str) and _branch_ok(br) else ""
    vm = raw.get("vms")
    vms = tuple(dict.fromkeys(x for x in vm if isinstance(x, str) and _VM_PAT.fullmatch(x))) if isinstance(vm, list) else ()
    ac = raw.get("accounts")
    accounts = tuple((k, ac[k]) for k in ACCOUNT_KINDS if isinstance(ac.get(k), str) and ac[k]) \
        if isinstance(ac, dict) else ()
    return ProjectSettings(hidden, links, branch, vms, accounts)


def load(settings: Settings, name: str) -> ProjectSettings:
    return _parse(_store(settings).load().get(name))


def _update(settings: Settings, name: str, fn: Callable[[dict], None]) -> ProjectSettings:
    f = _file(settings)
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        with filelock.locked(f.parent / "project_settings.lock"):
            def apply(data: dict) -> dict:
                entry = data.get(name)
                entry = dict(entry) if isinstance(entry, dict) else {}
                fn(entry)
                data[name] = entry
                return data
            data = _store(settings).update(apply)
    except filelock.LockTimeout as e:
        raise OSError("project settings are busy") from e
    return _parse(data[name])


def save_hidden(settings: Settings, name: str, hidden: Iterable[str]) -> ProjectSettings:
    items = list(hidden)
    bad = [x for x in items if x not in COMPONENTS]
    if bad:
        raise Invalid(f"unknown component: {bad[0]!r}"[:80])
    return _update(settings, name, lambda e: e.__setitem__(
        "hidden", [c for c in COMPONENTS if c in set(items)]))


def save_default_branch(settings: Settings, name: str, branch: str) -> ProjectSettings:
    branch = (branch or "").strip()
    if branch and not _branch_ok(branch):
        raise Invalid("branch: letters, digits, . _ - / only (max 100), no leading '-' or '..'")
    return _update(settings, name, lambda e: e.__setitem__("default_branch", branch))


def save_links(settings: Settings, name: str, links: Iterable[str], known: set[str]) -> ProjectSettings:
    out: list[str] = []
    for x in links:
        if not isinstance(x, str) or x == name or x not in known:
            raise Invalid("links: must be other known projects")
        if x not in out:
            out.append(x)
    return _update(settings, name, lambda e: e.__setitem__("links", out))


def save_vms(settings: Settings, name: str, patterns: str | Iterable[str]) -> ProjectSettings:
    """VM name patterns (fnmatch) for the project's VM tab; split on newlines/commas, de-duplicated."""
    items = re.split(r"[\n,]", patterns) if isinstance(patterns, str) else list(patterns)
    out = list(dict.fromkeys(x.strip() for x in items if isinstance(x, str) and x.strip()))
    if len(out) > MAX_VMS:
        raise Invalid(f"vms: at most {MAX_VMS} patterns")
    if any(not _VM_PAT.fullmatch(x) for x in out):
        raise Invalid("vms: letters, digits, . _ - * ? [ ] only (max 64 each)")
    return _update(settings, name, lambda e: e.__setitem__("vms", out))


def save_accounts(settings: Settings, name: str, mapping: dict[str, str]) -> ProjectSettings:
    """Login account per agent kind ('' = global default, '-' = the CLI's own folder). Kinds not in ``mapping`` keep their value."""
    for kind, acc in mapping.items():
        if kind not in ACCOUNT_KINDS:
            raise Invalid(f"unknown account kind: {kind!r}"[:80])
        if acc and acc != accts.CLI_DEFAULT and acc not in accts.names(settings, kind):
            raise Invalid(f"unknown {kind} account: {acc!r}"[:80])

    def apply(e: dict) -> None:
        cur = e.get("accounts")
        cur = {k: v for k, v in cur.items() if k in ACCOUNT_KINDS and isinstance(v, str)} if isinstance(cur, dict) else {}
        cur.update(mapping)
        e["accounts"] = {k: cur[k] for k in ACCOUNT_KINDS if cur.get(k)}
    return _update(settings, name, apply)
