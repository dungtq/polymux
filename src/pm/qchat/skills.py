"""Quick chat skills: plain SKILL.md folders (user, project, plugins); hooks and settings are never loaded.

Enabled maps are passed in by ``cfg`` (which imports this module, never the other way round).
"""

from __future__ import annotations

import re
from pathlib import Path

from pm.agents.inventory import read as inv
from pm.core.config import Settings
from pm.projects.projects import discover as discover_projects
from pm.qchat.mcpcfg import _home

SKILL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
MAX_SKILL_BODY = 20000


def _folder_path(settings: Settings, folder: str) -> Path | None:
    if not folder:
        return None
    for p in discover_projects(settings):
        if p.name == folder:
            return Path(p.path)
    return None


def _skill_dirs(settings: Settings, folder: str) -> list[tuple[str, Path]]:
    """(source, SKILL dir) in priority order: user, project, plugins."""
    home = _home()
    found: list[tuple[str, Path]] = []
    roots: list[tuple[str, Path]] = [("user", home / ".claude/skills")]
    proj = _folder_path(settings, folder)
    if proj is not None:
        roots.append(("project", proj / ".claude/skills"))
    for source, root in roots:
        for e in inv._list_dir(root, home):
            if e.name.startswith(".") or not inv._is_dir(e):
                continue
            if e.is_symlink() and inv.check_path(e, home) is None:
                continue
            if inv._is_file(e / "SKILL.md"):
                found.append((source, e))
    for sd in inv._plugin_skill_dirs(home / ".claude/plugins/cache", home):
        for e in inv._list_dir(sd, home):
            if not e.name.startswith(".") and inv._is_dir(e) and inv._is_file(e / "SKILL.md"):
                found.append(("plugin", e))
    return found


def _skills(settings: Settings, folder: str) -> dict[str, tuple[str, Path]]:
    out: dict[str, tuple[str, Path]] = {}
    for source, d in _skill_dirs(settings, folder):
        if SKILL_RE.fullmatch(d.name) and d.name not in out:
            out[d.name] = (source, d)
    return out


def _body(path: Path) -> str | None:
    text = inv._read(path / "SKILL.md", _home(), MAX_SKILL_BODY * 2)
    if text is None:
        return None
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            lines = lines[end + 1:]
    return "\n".join(lines).strip()[:MAX_SKILL_BODY]


def discover_skills(settings: Settings, folder: str, enabled: dict[str, bool]) -> list[dict]:
    out = []
    for name, (source, d) in sorted(_skills(settings, folder).items()):
        desc = inv._summary(inv._read(d / "SKILL.md", _home()))
        out.append({"name": name, "source": source, "description": desc,
                    "enabled": enabled.get(name, True)})
    return out


def skill_text(settings: Settings, name: str, folder: str = "") -> str | None:
    hit = _skills(settings, folder).get(name) if isinstance(name, str) else None
    return _body(hit[1]) if hit else None


def skill_prompt(settings: Settings, names: list[str], folder: str, typed: str,
                 enabled: dict[str, bool]) -> tuple[str, str]:
    """(system-prompt addendum listing ticked skills, user text with a leading /name expanded)."""
    skills = _skills(settings, folder)
    user = typed
    m = re.match(r"^\s*/(" + SKILL_RE.pattern[1:-1] + r")(?=\s|$)", typed or "")
    if m and m.group(1) in skills and enabled.get(m.group(1), True):
        body = _body(skills[m.group(1)][1])
        if body:
            rest = typed[m.end():].strip()
            user = f"Follow this skill ({m.group(1)}):\n\n{body}" + (f"\n\n{rest}" if rest else "")
    lines = []
    seen: set[str] = set()
    for n in names or []:
        if n in seen or n not in skills or not enabled.get(n, True):
            continue
        seen.add(n)
        desc = inv._summary(inv._read(skills[n][1] / "SKILL.md", _home()))
        lines.append(f"- {n}: {desc} (file: {skills[n][1] / 'SKILL.md'})")
    addendum = ""
    if lines:
        addendum = ("Skills available for this chat (use one when the request matches; "
                    "read the SKILL.md file if you can):\n" + "\n".join(lines))
    return addendum, user
