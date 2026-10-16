"""Skill mutations: link shared skills into an agent (one or a batch), create, edit and delete shared skills."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from pm.core.models import AgentInfo, Inventory

from .mutate_base import (
    BACKUP_DISP,
    MAX_DELETE_DEPTH,
    MAX_DELETE_FILES,
    MAX_DESC,
    PROTECTED,
    SKILL_RE,
    MutError,
    _agent,
    _body,
    _Built,
    _confirm,
    _disp,
    _from_display,
    _guard,
    _inventory,
    _lines,
    _link_points_to,
    _lstat,
    _named,
    _read_file,
    _resolve,
    _Step,
    _str,
    _under,
)
from .read import MAX_LINK, is_blocked


def _skill_toggle(params: dict, home: Path) -> _Built:
    agent = _str(params, "agent")
    skill = _named(params, "skill", SKILL_RE, "skill")
    enable = params.get("enable")
    if not isinstance(enable, bool):
        raise MutError("Parameter 'enable' must be true or false.")
    inv = _inventory(home)
    a = _link_agent(inv, agent)
    built = _Built()
    _toggle_one(built, inv, a, skill, enable, home)
    return built


def _skills_link(params: dict, home: Path) -> _Built:
    """Link several shared skills into one agent (the "N shared skills not linked" fix)."""
    agent = _str(params, "agent")
    skills = params.get("skills")
    if not isinstance(skills, list) or not skills or len(skills) > MAX_LINK:
        raise MutError(f"Choose 1 to {MAX_LINK} skills.")
    if not all(isinstance(k, str) and SKILL_RE.fullmatch(k) for k in skills):
        raise MutError("Invalid skill name.")
    inv = _inventory(home)
    a = _link_agent(inv, agent)
    built = _Built()
    for skill in sorted(set(skills)):
        _toggle_one(built, inv, a, skill, True, home)
    return built


def _link_agent(inv: Inventory, name: str) -> AgentInfo:
    a = _agent(inv, name)
    if not a.installed:
        raise MutError(f"{a.label} is not installed.")
    if not a.skills_dir:
        raise MutError(f"{a.label} has no skills directory of its own.")
    return a


def _toggle_one(built: _Built, inv: Inventory, a: AgentInfo, skill: str, enable: bool, home: Path) -> None:
    """Add the step that links (enable) or unlinks one shared skill in the agent's skills dir."""
    if not any(s.scope == "shared" and s.name == skill for s in inv.skills):
        raise MutError("Not a shared skill (agent-specific skills cannot be toggled).")
    shared = home / ".agents/skills"
    canon = shared / skill
    cst = _lstat(canon)
    if cst is None or not stat.S_ISDIR(cst.st_mode):
        raise MutError("The shared skill is not a real directory.")
    _guard(canon, shared, home)
    sdir = _from_display(a.skills_dir, home)
    if _under(_resolve(sdir), _resolve(shared)):
        raise MutError(f"{a.label} reads ~/.agents/skills directly.")
    link = sdir / skill
    _guard(link, sdir, home)
    root = sdir
    built.extra.extend([canon, canon / "SKILL.md"])
    st = _lstat(link)
    if enable:
        if st is None:
            built.steps.append(
                _Step("link", link, root, f"-> {_disp(canon, home)}", str(canon))
            )
        elif stat.S_ISLNK(st.st_mode) and _link_points_to(link, canon):
            built.steps.append(_Step("skip", link, root, "already linked"))
        else:
            raise MutError(
                f"{_disp(link, home)} exists and is not the expected symlink."
            )
    else:
        if st is None:
            built.steps.append(_Step("skip", link, root, "not linked"))
        elif stat.S_ISLNK(st.st_mode) and _points_into(link, shared):
            built.steps.append(_Step("unlink", link, root, "symlink"))
        else:
            raise MutError(
                f"{_disp(link, home)} is not a symlink into ~/.agents/skills."
            )


def _points_into(link: Path, base: Path) -> bool:
    try:
        raw = os.readlink(link)
    except OSError:
        return False
    lexical = Path(os.path.normpath(os.path.join(link.parent, raw)))
    rbase = _resolve(base)
    for cand, b in ((_resolve(link), rbase), (lexical, base), (lexical, rbase)):
        if cand != b and _under(cand, b):
            return True
    return False


# ---------------------------------------------------------------- shared skills CRUD


def _frontmatter_desc(desc: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9(][^:#'\"]*", desc) and desc == desc.strip():
        return desc
    return json.dumps(desc, ensure_ascii=False)


def _skill_md(params: dict) -> tuple[str, bytes]:
    name = _named(params, "name", SKILL_RE, "skill")
    desc = _str(params, "description").strip()
    if len(desc) > MAX_DESC:
        raise MutError(f"Description is longer than {MAX_DESC} characters.")
    if "\n" in desc or "\r" in desc:
        raise MutError("Description must be a single line.")
    body = _body(params).decode("utf-8").rstrip("\n")
    text = f"---\nname: {name}\ndescription: {_frontmatter_desc(desc)}\n---\n\n{body}\n"
    return name, text.encode("utf-8")


def _shared_root(home: Path) -> Path:
    root = home / ".agents/skills"
    if is_blocked(root, home):
        raise MutError(PROTECTED)
    st = _lstat(root)
    if st is None or not _resolve(root).is_dir():
        raise MutError("~/.agents/skills does not exist.")
    if not _under(_resolve(root), _resolve(home)):
        raise MutError("~/.agents/skills is outside the home directory.")
    return root


def _skill_dir_ok(d: Path, root: Path, home: Path) -> None:
    _guard(d, root, home)
    st = _lstat(d)
    if st is None or not stat.S_ISDIR(st.st_mode):
        raise MutError("That skill is not a real directory in ~/.agents/skills.")
    md = _lstat(d / "SKILL.md")
    if md is None or not stat.S_ISREG(md.st_mode):
        raise MutError("That skill has no regular SKILL.md.")


def _skill_create(params: dict, home: Path) -> _Built:
    name, data = _skill_md(params)
    root = _shared_root(home)
    d = root / name
    _guard(d, root, home)
    if _lstat(d) is not None:
        raise MutError(f"{_disp(d, home)} already exists.")
    md = d / "SKILL.md"
    return _Built(
        steps=[
            _Step("mkdir", d, root),
            _Step("write", md, root, f"{_lines(data)} lines", data=data, mode=0o640),
        ]
    )


def _skill_edit(params: dict, home: Path) -> _Built:
    name, data = _skill_md(params)
    root = _shared_root(home)
    d = root / name
    _skill_dir_ok(d, root, home)
    md = d / "SKILL.md"
    _guard(md, root, home)
    old = _read_file(md, home)
    if old == data:
        return _Built(steps=[_Step("skip", md, root, "unchanged")])
    detail = f"{_lines(data)} lines, backup -> {BACKUP_DISP}"
    return _Built(
        steps=[_Step("write", md, root, detail, data=data, mode=0o640, backup=True)]
    )


def _walk_skill(d: Path, home: Path) -> tuple[list[Path], list[Path], list[Path]]:
    """(files, inner symlinks, dirs deepest first) of a skill dir; refuses unsafe content."""
    rd = _resolve(d)
    files: list[Path] = []
    links: list[Path] = []
    dirs: list[Path] = []

    def walk(cur: Path, depth: int) -> None:
        try:
            with os.scandir(cur) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError as e:
            raise MutError("A skill directory could not be listed.") from e
        for e in entries:
            p = cur / e.name
            if is_blocked(p, home):
                raise MutError(PROTECTED)
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                if not _under(_resolve(p), rd):
                    raise MutError(
                        f"{_disp(p, home)} is a symlink pointing outside the skill."
                    )
                links.append(p)
            elif stat.S_ISREG(st.st_mode):
                files.append(p)
            elif stat.S_ISDIR(st.st_mode):
                if depth >= MAX_DELETE_DEPTH:
                    raise MutError("The skill has subdirectories deeper than 2 levels.")
                dirs.append(p)
                walk(p, depth + 1)
            else:
                raise MutError(f"{_disp(p, home)} is not a regular file.")
            if len(files) + len(links) > MAX_DELETE_FILES:
                raise MutError(f"The skill has more than {MAX_DELETE_FILES} files.")

    walk(d, 0)
    dirs.sort(key=lambda p: (-len(p.parts), str(p)))
    return files, links, dirs


def _skill_delete(params: dict, home: Path) -> _Built:
    name = _named(params, "name", SKILL_RE, "skill")
    _confirm(params, name)
    root = _shared_root(home)
    d = root / name
    _skill_dir_ok(d, root, home)
    files, links, dirs = _walk_skill(d, home)
    built = _Built(extra=[d])
    inv = _inventory(home)
    for a in inv.agents:
        if not a.skills_dir:
            continue
        sdir = _from_display(a.skills_dir, home)
        link = sdir / name
        st = _lstat(link)
        if st is None or not stat.S_ISLNK(st.st_mode):
            continue
        _guard(link, sdir, home)
        if _link_points_to(link, d):
            built.steps.append(_Step("unlink", link, sdir, f"{a.label} symlink"))
    built.steps.sort(key=lambda s: str(s.path))
    for f in sorted(files):
        built.steps.append(
            _Step("rmfile", f, root, f"backup -> {BACKUP_DISP}", backup=True)
        )
    for ln in sorted(links):
        built.steps.append(_Step("rmfile", ln, root, "inner symlink"))
    for sub in dirs:
        built.steps.append(_Step("rmdir", sub, root, "directory"))
    built.steps.append(_Step("rmdir", d, root, "skill directory"))
    return built

