"""Named login accounts per agent kind (Claude, Codex): who exists, the global default, where each one lives.

Names come from pm.toml ``[accounts]`` (read-only) and from ``state_dir/accounts.json`` (added and removed in the UI,
read per call, no restart). An account is a login folder ``state_dir/accounts/<kind>/<name>`` that the CLI is pointed
at with ``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME``; the CLI's own folder (``~/.claude``, ``~/.codex``) is the account ``""``.
A project's choice (``ProjectSettings.accounts``) is ``""`` = use the global default, ``"-"`` = the CLI's own folder,
or a name; ``resolve`` turns it into the account to launch with.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pm.core import filelock
from pm.core.cache import LRU
from pm.core.config import ACCOUNT_KINDS, ACCOUNT_NAME_RE, Settings
from pm.core.errors import Invalid
from pm.core.fsutil import JsonStore

CLI_DEFAULT = "-"  # a project's explicit choice of the CLI's own folder (never a valid account name)
_CACHE = LRU(max_bytes=1 << 20, max_entries=16)


def _file(settings: Settings) -> Path:
    return Path(settings.state_dir) / "accounts.json"


def _store(settings: Settings) -> JsonStore:
    return JsonStore(_file(settings), dict, validate=lambda d: isinstance(d, dict), mode=0o600)


def _clean(raw) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for kind in ACCOUNT_KINDS:
        e = raw.get(kind) if isinstance(raw, dict) else None
        e = e if isinstance(e, dict) else {}
        ns = e.get("names")
        names = [n for n in ns if isinstance(n, str) and ACCOUNT_NAME_RE.fullmatch(n)] if isinstance(ns, list) else []
        d = e.get("default")
        out[kind] = {"names": list(dict.fromkeys(names)), "default": d if isinstance(d, str) else ""}
    return out


def _state(settings: Settings) -> dict[str, dict]:
    f = _file(settings)
    try:
        st = f.stat()
    except OSError:
        return _clean({})
    key = (str(f), st.st_mtime_ns, st.st_size)
    hit = _CACHE.get(key)
    if hit is None:
        hit = _clean(_store(settings).load())
        _CACHE.put(key, hit)
    return hit


def _kind(kind: str) -> str:
    if kind not in ACCOUNT_KINDS:
        raise Invalid(f"unknown account kind: {kind!r}"[:80])
    return kind


def names(settings: Settings, kind: str) -> tuple[str, ...]:
    """Account names of a kind: pm.toml first, then the ones added in the UI."""
    _kind(kind)
    return tuple(dict.fromkeys((*settings.accounts.get(kind, ()), *_state(settings)[kind]["names"])))


def is_readonly(settings: Settings, kind: str, name: str) -> bool:
    """A name defined in pm.toml: shown, not removable in the UI."""
    return name in settings.accounts.get(_kind(kind), ())


def global_default(settings: Settings, kind: str) -> str:
    """The account new agents use when a project has no choice ('' = the CLI's own folder)."""
    d = _state(settings)[_kind(kind)]["default"]
    return d if d in names(settings, kind) else ""


def resolve(settings: Settings, kind: str, project_choice: str = "") -> str:
    """Account to launch with: the project's choice, else the global default, else ''. Invalid: a name that is gone."""
    if project_choice == CLI_DEFAULT:
        return ""
    if not project_choice:
        return global_default(settings, kind)
    if project_choice not in names(settings, kind):
        raise Invalid(f"unknown {kind} account {project_choice!r}"[:80])
    return project_choice


def account_dir(settings: Settings, kind: str, name: str) -> Path | None:
    """Login folder of a named account, ``None`` for the CLI's own (``""``). Invalid: unknown kind or name."""
    if not name:
        return None
    if name not in names(settings, kind):
        raise Invalid(f"unknown {kind} account {name!r}"[:80])
    return Path(settings.state_dir) / "accounts" / kind / name


def account_env(settings: Settings, kind: str, name: str) -> dict[str, str]:
    """Env that points a CLI at an account's login folder; ``{}`` for the CLI's own."""
    d = account_dir(settings, kind, name)
    return {} if d is None else {ACCOUNT_KINDS[kind]: str(d)}


def cli_dir(settings: Settings, kind: str) -> Path:
    """The CLI's own config folder (the account ``""``)."""
    return Path(settings.claude_dir) if _kind(kind) == "claude" else Path.home() / ".codex"


def config_dir(settings: Settings, kind: str, account: str = "") -> Path:
    """Config folder of an account, the CLI's own one for ``""``."""
    return account_dir(settings, kind, account) or cli_dir(settings, kind)


def config_dirs(settings: Settings, kind: str) -> list[Path]:
    """Every config folder sessions can live in: the CLI's own first, then each account's."""
    return [cli_dir(settings, kind), *(account_dir(settings, kind, n) for n in names(settings, kind))]


def _update(settings: Settings, fn: Callable[[dict], None]) -> None:
    f = _file(settings)
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        with filelock.locked(f.parent / "accounts.lock"):
            def apply(data: dict) -> dict:
                data = _clean(data)
                fn(data)
                return data
            _store(settings).update(apply)
    except filelock.LockTimeout as e:
        raise OSError("accounts are busy") from e


def add(settings: Settings, kind: str, name: str) -> None:
    """Add an account in the UI. Invalid: bad name, or already defined (also by pm.toml)."""
    _kind(kind)
    if not ACCOUNT_NAME_RE.fullmatch(name or ""):
        raise Invalid("account name: lowercase letters, digits, - and _ (max 32), starting with a letter or digit")
    if name in names(settings, kind):
        raise Invalid(f"{kind} account {name!r} already exists")
    _update(settings, lambda d: d[kind]["names"].append(name))


def remove(settings: Settings, kind: str, name: str) -> None:
    """Unlist a UI-added account (its login folder stays on disk). Invalid: unknown, or defined in pm.toml."""
    _kind(kind)
    if is_readonly(settings, kind, name):
        raise Invalid(f"{kind} account {name!r} is defined in pm.toml")
    if name not in _state(settings)[kind]["names"]:
        raise Invalid(f"unknown {kind} account {name!r}"[:80])

    def apply(d: dict) -> None:
        d[kind]["names"] = [n for n in d[kind]["names"] if n != name]
        if d[kind]["default"] == name:
            d[kind]["default"] = ""
    _update(settings, apply)


def set_default(settings: Settings, kind: str, name: str) -> None:
    """Global default account of a kind; ``""`` = the CLI's own folder."""
    _kind(kind)
    if name and name not in names(settings, kind):
        raise Invalid(f"unknown {kind} account {name!r}"[:80])
    _update(settings, lambda d: d[kind].__setitem__("default", name))
