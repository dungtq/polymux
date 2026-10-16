"""Rules and hook-script mutations: sync rules files, create/edit/delete rules and scripts, prune stale
lines of ~/agents/agents-index.md."""

from __future__ import annotations

import stat
from pathlib import Path

from pm.core.models import AgentInfo, Inventory

from .mutate_base import (
    BACKUP_DISP,
    MAX_READ,
    MAX_TARGETS,
    PROTECTED,
    RULE_RE,
    SCRIPT_RE,
    TOP_RULES,
    MutError,
    _agent,
    _body,
    _Built,
    _confirm,
    _disp,
    _guard,
    _inventory,
    _lines,
    _lstat,
    _named,
    _read_file,
    _resolve,
    _sha,
    _Step,
    _str,
    _under,
)
from .read import check_path, is_blocked
from .scripts import index_paths, stale_index_entries

ENTRY_MAX = 300  # one agents-index.md entry (a path)

# ---------------------------------------------------------------- rules sync


def _rule_files(inv: Inventory, home: Path) -> dict[str, tuple[Path, AgentInfo | None]]:
    out: dict[str, tuple[Path, AgentInfo | None]] = {}
    if any(r.agent == "" and r.name == "AGENTS.md" for r in inv.rules):
        out["canonical"] = (home / "agents/AGENTS.md", None)
    for a in inv.agents:
        if not a.rules_file or "*" in a.rules_file or not a.rules_file.startswith("~/"):
            continue
        if any(r.agent == a.name and r.path == a.rules_file for r in inv.rules):
            out[a.name] = (home / a.rules_file[2:], a)
    return out


def _rules_sync(params: dict, home: Path) -> _Built:
    source = _str(params, "source")
    targets = params.get("targets")
    if not isinstance(targets, list) or not targets or len(targets) > MAX_TARGETS:
        raise MutError("Choose at least one target agent.")
    if not all(isinstance(t, str) for t in targets):
        raise MutError("Targets must be agent names.")
    names = sorted(set(targets))
    inv = _inventory(home)
    files = _rule_files(inv, home)
    if source != "canonical":
        _agent(inv, source)
    if source not in files:
        raise MutError("That source has no rules file in the inventory.")
    src_path = files[source][0]
    sres = check_path(src_path, home)
    if sres is None:
        raise MutError(PROTECTED)
    hres = _resolve(home)
    if not _under(sres, hres) or not sres.is_file():
        raise MutError(
            "The source rules file is not a regular file inside the home directory."
        )
    src_data = _read_file(sres, home)
    src_sha = _sha(src_data)
    built = _Built(extra=[sres])
    for name in names:
        a = _agent(inv, name)
        if not a.installed:
            raise MutError(f"{a.label} is not installed.")
        if name not in files or files[name][1] is None:
            raise MutError(f"{a.label} has no single rules file.")
        if name == source:
            raise MutError("The source cannot also be a target.")
        path = files[name][0]
        _guard(path, path.parent, home)
        root = path.parent
        if not root.is_dir():
            raise MutError(f"The directory of {_disp(path, home)} does not exist.")
        st = _lstat(path)
        link_detail = f"-> {_disp(sres, home)}"
        if st is None:
            built.steps.append(_Step("link", path, root, link_detail, str(sres)))
        elif stat.S_ISLNK(st.st_mode):
            if _resolve(path) == sres:
                built.steps.append(_Step("skip", path, root, "already linked"))
            else:
                built.steps.append(_relink(path, root, sres, home))
        elif stat.S_ISREG(st.st_mode):
            if _resolve(path) == sres or _sha(_read_file(path, home)) == src_sha:
                built.steps.append(_Step("skip", path, root, "already identical"))
            else:
                built.steps.append(_relink(path, root, sres, home))
        else:
            raise MutError(f"{_disp(path, home)} is not a file or symlink.")
    return built


def _relink(path: Path, root: Path, target: Path, home: Path) -> _Step:
    detail = f"replace-with-backup: symlink -> {_disp(target, home)}; backup -> {BACKUP_DISP}"
    return _Step("relink", path, root, detail, str(target), backup=True)


# ---------------------------------------------------------------- rules / scripts CRUD


def _file_crud(
    verb: str,
    params: dict,
    home: Path,
    *,
    path: Path,
    root: Path,
    data: bytes | None,
    mode: int,
    mkdir: Path | None = None,
) -> _Built:
    _guard(path, root, home)
    st = _lstat(path)
    if verb == "create":
        assert data is not None
        if st is not None:
            raise MutError(f"{_disp(path, home)} already exists.")
        steps: list[_Step] = []
        if mkdir is not None and _lstat(mkdir) is None:
            steps.append(_Step("mkdir", mkdir, root))
        steps.append(
            _Step("write", path, root, f"{_lines(data)} lines", data=data, mode=mode)
        )
        return _Built(steps=steps)
    if st is None:
        raise MutError(f"{_disp(path, home)} does not exist.")
    if not stat.S_ISREG(st.st_mode):
        raise MutError(f"{_disp(path, home)} is not a regular file.")
    old = _read_file(path, home, 8 * MAX_READ)
    if verb == "edit":
        assert data is not None
        if old == data and stat.S_IMODE(st.st_mode) == mode:
            return _Built(steps=[_Step("skip", path, root, "unchanged")])
        detail = f"{_lines(data)} lines, backup -> {BACKUP_DISP}"
        return _Built(
            steps=[
                _Step("write", path, root, detail, data=data, mode=mode, backup=True)
            ]
        )
    return _Built(
        steps=[_Step("rmfile", path, root, f"backup -> {BACKUP_DISP}", backup=True)]
    )


def _rule(verb: str, params: dict, home: Path) -> _Built:
    name = _named(params, "name", RULE_RE, "rule")
    root = home / "agents"
    if is_blocked(root, home):
        raise MutError(PROTECTED)
    if not _resolve(root).is_dir():
        raise MutError("~/agents does not exist.")
    top = name in TOP_RULES
    if verb == "create" and (top or name.lower() in {t.lower() for t in TOP_RULES}):
        raise MutError("That name is reserved.")
    if verb == "delete":
        if top:
            raise MutError("That file cannot be deleted.")
        _confirm(params, name)
    rules_dir = root / "rules"
    path = root / name if top else rules_dir / name
    data = _body(params) if verb != "delete" else None
    mk = rules_dir if verb == "create" else None
    return _file_crud(
        verb, params, home, path=path, root=root, data=data, mode=0o640, mkdir=mk
    )


def _script(verb: str, params: dict, home: Path) -> _Built:
    name = _named(params, "name", SCRIPT_RE, "script")
    root = home / ".agents/hooks"
    if is_blocked(root, home):
        raise MutError(PROTECTED)
    if not _resolve(root).is_dir():
        raise MutError("~/.agents/hooks does not exist.")
    if verb == "delete":
        _confirm(params, name)
    data = _body(params) if verb != "delete" else None
    mode = 0o750 if data is not None and data.startswith(b"#!") else 0o640
    return _file_crud(
        verb, params, home, path=root / name, root=root, data=data, mode=mode
    )



# ---------------------------------------------------------------- agents-index.md


def _index_prune(params: dict, home: Path) -> _Built:
    """Drop the lines of ~/agents/agents-index.md that list the given stale entries (paths that do not exist)."""
    entries = params.get("lines")
    if not isinstance(entries, list) or not entries or len(entries) > MAX_TARGETS:
        raise MutError(f"Choose 1 to {MAX_TARGETS} stale entries.")
    if not all(isinstance(e, str) and 0 < len(e) <= ENTRY_MAX and "\n" not in e and "\x00" not in e for e in entries):
        raise MutError("Invalid entry.")
    root = home / "agents"
    path = root / "agents-index.md"
    _guard(path, root, home)
    stale = set(stale_index_entries(home))
    if not set(entries) <= stale:
        raise MutError("An entry is no longer stale; reload the findings.")
    st = _lstat(path)
    try:
        text = _read_file(path, home).decode("utf-8")
    except UnicodeDecodeError:
        raise MutError("~/agents/agents-index.md is not UTF-8 text.") from None
    want = set(entries)
    keep: list[str] = []
    dropped: list[int] = []
    for n, line in enumerate(text.splitlines(keepends=True), 1):
        if index_paths(line) & want:
            dropped.append(n)
        else:
            keep.append(line)
    if not dropped:
        raise MutError("No line lists those entries.")
    nums = ", ".join(map(str, dropped[:10])) + (" ..." if len(dropped) > 10 else "")
    detail = f"drop {len(dropped)} line{'s' if len(dropped) > 1 else ''} ({nums}), backup -> {BACKUP_DISP}"
    data = "".join(keep).encode("utf-8")
    mode = stat.S_IMODE(st.st_mode) if st is not None else 0o640
    return _Built(steps=[_Step("write", path, root, detail, data=data, mode=mode, backup=True)])
