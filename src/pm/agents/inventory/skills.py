"""Skills: the shared ~/.agents/skills tree, per-agent skill dirs and plugin caches."""

from __future__ import annotations

from pathlib import Path

from pm.core.models import SkillInfo

from .fsread import (
    _is_dir,
    _is_file,
    _list_dir,
    _read,
    _resolve,
    _summary,
    display_path,
    is_blocked,
)
from .specs import MAX_DIR_ENTRIES, MAX_SKILLS, _Spec


def _skill_from_dir(d: Path, home: Path) -> tuple[str, str] | None:
    md = d / "SKILL.md"
    if not _is_file(md):
        return None
    return d.name, _summary(_read(md, home))


def _plugin_skill_dirs(root: Path, home: Path) -> list[Path]:
    """Directories named 'skills' under a plugin cache (bounded walk)."""
    found: list[Path] = []
    budget = [MAX_DIR_ENTRIES]

    def walk(d: Path, depth: int) -> None:
        if depth > 6 or budget[0] <= 0:
            return
        for e in _list_dir(d, home):
            budget[0] -= 1
            if (
                budget[0] <= 0
                or e.name in ("node_modules", ".git")
                or e.is_symlink()
                or not _is_dir(e)
            ):
                continue
            if e.name == "skills":
                found.append(e)
            else:
                walk(e, depth + 1)

    walk(root, 1)
    return found


def _collect_skills(
    home: Path, specs: list[_Spec], installed: set[str]
) -> tuple[list[SkillInfo], dict[str, set[str]]]:
    shared_root = home / ".agents/skills"
    shared_res = _resolve(shared_root) or shared_root
    shared: dict[str, tuple[str, Path]] = {}
    for d, name, desc in _scan_skill_tree_flat(shared_root, home):
        shared[name] = (desc, d)
    enabled: dict[str, set[str]] = {n: set() for n in shared}
    own: list[SkillInfo] = []
    for spec in specs:
        if spec.direct_shared and spec.name in installed:
            for n in shared:
                enabled[n].add(spec.name)
        dirs = [home / rel for rel in spec.skill_dirs]
        if spec.name == "claude":
            dirs += _plugin_skill_dirs(home / ".claude/plugins/cache", home)
        for sd in dirs:
            for e in _list_dir(sd, home):
                if e.name.startswith("."):
                    continue
                rp = _resolve(e)
                if (
                    rp is not None
                    and e.is_symlink()
                    and rp.parent == shared_res
                    and rp.name in shared
                ):
                    enabled[rp.name].add(spec.name)
                    continue
                if (
                    e.is_symlink()
                    and rp is not None
                    and (is_blocked(e, home) or not rp.exists())
                ):
                    continue
                if not _is_dir(e):
                    continue
                sk = _skill_from_dir(e, home)
                items = [(e, sk[0], sk[1])] if sk else _scan_skill_tree_entry(e, home)
                for d, name, desc in items:
                    own.append(
                        SkillInfo(
                            name=name,
                            description=desc,
                            path=display_path(d, home),
                            scope="agent",
                            owner=spec.name,
                            enabled_for=(spec.name,),
                        )
                    )
    skills = [
        SkillInfo(
            name=n,
            description=desc,
            path=display_path(d, home),
            scope="shared",
            enabled_for=tuple(sorted(enabled[n])),
        )
        for n, (desc, d) in shared.items()
    ]
    skills.sort(key=lambda s: s.name)
    own = sorted(
        {(s.owner, s.path): s for s in own}.values(),
        key=lambda s: (s.owner, s.name, s.path),
    )
    return (skills + own)[:MAX_SKILLS], enabled


def _scan_skill_tree_flat(root: Path, home: Path) -> list[tuple[Path, str, str]]:
    out = []
    for e in _list_dir(root, home):
        if e.name.startswith("."):
            continue
        sk = _skill_from_dir(e, home) if _is_dir(e) else None
        if sk:
            out.append((e, sk[0], sk[1]))
        if len(out) >= MAX_SKILLS + 50:
            break
    return out


def _scan_skill_tree_entry(e: Path, home: Path) -> list[tuple[Path, str, str]]:
    """Skills nested below a namespace directory (depth <= MAX_DEPTH counting the entry itself)."""
    if e.is_symlink():
        return []
    out: list[tuple[Path, str, str]] = []
    for sub in _list_dir(e, home):
        if sub.name.startswith(".") or not _is_dir(sub):
            continue
        sk = _skill_from_dir(sub, home)
        if sk:
            out.append((sub, sk[0], sk[1]))
        elif not sub.is_symlink():
            for sub2 in _list_dir(sub, home):
                if _is_dir(sub2) and not sub2.name.startswith("."):
                    sk2 = _skill_from_dir(sub2, home)
                    if sk2:
                        out.append((sub2, sk2[0], sk2[1]))
    return out
