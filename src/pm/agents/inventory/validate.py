"""Read-only validation of the canonical rules file and every agent copy."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .mutate_base import (
    MutError,
    _disp,
    _home,
    _inventory,
    _lines,
    _lstat,
    _read_file,
    _resolve,
    _sha,
)
from .read import is_blocked


def validate_rules(*, home: Path | None = None) -> list[tuple[str, str, str]]:
    """Deterministic read-only checks of the canonical rules file and every agent copy."""
    h = _home(home)
    inv = _inventory(h)
    res: list[tuple[str, str, str]] = []
    canon = h / "agents/AGENTS.md"
    canon_sha = ""
    canon_lines = 0
    canon_res = _resolve(canon)
    try:
        cdata = _read_file(canon, h)
        canon_sha, canon_lines = _sha(cdata), _lines(cdata)
        res.append(
            ("ok", "~/agents/AGENTS.md", f"canonical rules file, {canon_lines} lines")
        )
    except MutError:
        if _lstat(canon) is None:
            res.append(
                ("error", "~/agents/AGENTS.md", "canonical rules file is missing")
            )
        else:
            res.append(
                (
                    "error",
                    "~/agents/AGENTS.md",
                    "canonical rules file is not a readable regular file",
                )
            )
    for a in inv.agents:
        if not a.rules_file or "*" in a.rules_file or not a.rules_file.startswith("~/"):
            continue
        path = h / a.rules_file[2:]
        subj = a.rules_file
        st = _lstat(path)
        if st is None:
            if a.installed:
                res.append(("warn", subj, f"rules file for {a.label} is missing"))
            continue
        if stat.S_ISLNK(st.st_mode):
            res.append(_check_link(path, subj, h, canon_res, canon_lines))
            continue
        try:
            data = _read_file(path, h)
        except MutError as e:
            res.append(("error", subj, str(e)))
            continue
        n = _lines(data)
        if not canon_sha:
            res.append(("warn", subj, f"{n} lines; nothing to compare with"))
        elif _sha(data) == canon_sha:
            res.append(("ok", subj, f"identical copy of the canonical file, {n} lines"))
        else:
            res.append(("warn", subj, f"drift: {n} lines vs {canon_lines} canonical"))
    for r in inv.rules:
        if r.agent and r.status == "broken" and not any(x[1] == r.path for x in res):
            res.append(("error", r.path, "broken or unreadable rules file"))
    return res


def _check_link(
    path: Path, subj: str, home: Path, canon_res: Path, canon_lines: int
) -> tuple[str, str, str]:
    if is_blocked(path, home):
        return ("warn", subj, "symlink target is in a protected directory; not read")
    rp = _resolve(path)
    if not rp.exists():
        return ("error", subj, f"broken symlink -> {os.readlink(path)}")
    if rp == canon_res:
        return ("ok", subj, f"symlink to the canonical file, {canon_lines} lines")
    return ("warn", subj, f"symlink to {_disp(rp, home)}, not the canonical file")

