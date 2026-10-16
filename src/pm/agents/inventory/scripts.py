"""Scripts: agent shims in ~/.local/bin, shared and per-agent hooks, stale index entries."""

from __future__ import annotations

import os
import re
from pathlib import Path

from pm.core.models import Finding, ScriptInfo

from .fsread import (
    _is_file,
    _lexists,
    _list_dir,
    _read,
    _resolve,
    _script_purpose,
    _under,
    display_path,
    is_blocked,
)
from .specs import ALL_BIN_NAMES, MAX_SCRIPTS, _Spec


def _collect_scripts(
    home: Path, specs: list[_Spec], hook_texts: dict[str, str]
) -> list[ScriptInfo]:
    out: list[ScriptInfo] = []
    agents_roots = [home / ".agents", home / "agents"]
    agents_res = [_resolve(r) or r for r in agents_roots]
    for e in _list_dir(home / ".local/bin", home):
        rp = _resolve(e)
        into_agents = rp is not None and any(_under(rp, r) for r in agents_res)
        by_name = e.name in ALL_BIN_NAMES
        if not (by_name or into_agents or e.name.startswith(("tmux-agent", "cmux"))):
            continue
        text = None
        if _is_file(e) and not e.is_symlink() or into_agents:
            head = _read(e, home, 4096)
            text = head if head and head.startswith("#!") else None
        if e.is_symlink() and rp is not None and not rp.exists():
            purpose = "Broken symlink"
        elif e.is_symlink():
            purpose = f"Symlink to {display_path(Path(os.readlink(e)), home)}"
        else:
            purpose = _script_purpose(text)
        kind = "shim" if (by_name or e.is_symlink()) else "script"
        used = (ALL_BIN_NAMES[e.name],) if by_name else ()
        out.append(ScriptInfo(e.name, display_path(e, home), kind, purpose, used))
    for e in _list_dir(home / ".agents/hooks", home):
        if not (_is_file(e) or e.is_symlink()):
            continue
        used = tuple(sorted(a for a, t in hook_texts.items() if e.name in t))
        out.append(
            ScriptInfo(
                e.name,
                display_path(e, home),
                "hook",
                _script_purpose(_read(e, home, 4096)),
                used,
            )
        )
    for spec in specs:
        for rel in spec.hook_dirs:
            for e in _list_dir(home / rel, home):
                if e.name.startswith(".") or not (_is_file(e) or e.is_symlink()):
                    continue
                purpose = (
                    f"Symlink to {display_path(Path(os.readlink(e)), home)}"
                    if e.is_symlink()
                    else ""
                )
                if not purpose:
                    purpose = _script_purpose(_read(e, home, 4096))
                out.append(
                    ScriptInfo(
                        e.name, display_path(e, home), "hook", purpose, (spec.name,)
                    )
                )
    out.sort(key=lambda s: (s.kind, s.name, s.path))
    return out[:MAX_SCRIPTS]


def _hook_texts(home: Path, specs: list[_Spec]) -> dict[str, str]:
    texts: dict[str, str] = {}
    for spec in specs:
        chunks = []
        for rel in spec.hook_cfgs:
            t = _read(home / rel, home)
            if t:
                chunks.append(t)
        if spec.name == "copilot":
            for e in _list_dir(home / ".copilot/hooks", home):
                if e.suffix == ".json":
                    t = _read(e, home)
                    if t:
                        chunks.append(t)
        if chunks:
            texts[spec.name] = "\n".join(chunks)
    return texts


_INDEX_PATH = re.compile(r"`(~/[^`\s<>*{}$]+|/[^`\s<>*{}$]+)`")


def index_paths(line: str) -> set[str]:
    """The backticked paths of one agents-index.md line, as the stale check reads them."""
    return {m.group(1).rstrip(".,;:)") for m in _INDEX_PATH.finditer(line)}


def stale_index_entries(home: Path) -> list[str]:
    """Paths listed in ~/agents/agents-index.md that do not exist (in file order, no duplicates)."""
    text = _read(home / "agents/agents-index.md", home)
    if not text:
        return []
    stale: list[str] = []
    seen: set[str] = set()
    hs = str(home)
    for m in _INDEX_PATH.finditer(text):
        raw = m.group(1).rstrip(".,;:)")
        if raw in seen or "YYYY" in raw:
            continue
        seen.add(raw)
        if raw.startswith("~/"):
            p = home / raw[2:]
        elif raw == hs or raw.startswith(hs + "/"):
            p = Path(raw)
        else:
            continue
        if is_blocked(p, home):
            continue
        if not _lexists(p):
            stale.append(raw)
    return stale


def _stale_index_findings(home: Path) -> list[Finding]:
    stale = stale_index_entries(home)
    out = [
        Finding(
            "warn",
            f"Stale entry in ~/agents/agents-index.md: {s} does not exist",
            "index",
            fix=(("action", "index_prune"), ("lines", s)),
        )
        for s in stale[:10]
    ]
    if len(stale) > 10:
        out.append(
            Finding(
                "warn",
                f"{len(stale) - 10} more stale entries in ~/agents/agents-index.md",
                "index",
            )
        )
    return out
