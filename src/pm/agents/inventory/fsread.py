"""Guarded file access for the inventory: home-only, never inside ~/.agents-git or ~/.cfg.

Plus the text helpers that turn what was read into one-line summaries.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .specs import BLOCKED, MAX_DIR_ENTRIES, MAX_READ, NO_SUMMARY, YAML_MARKERS


def display_path(p: Path, home: Path) -> str:
    """Return p in ~/... form when it lives under home."""
    p = Path(p)
    for base in {home, _resolve(home) or home}:
        try:
            rel = p.relative_to(base)
        except ValueError:
            continue
        return "~" if not rel.parts else "~/" + rel.as_posix()
    return str(p)


# ---------------------------------------------------------------- safe file access


def _resolve(p: Path) -> Path | None:
    try:
        return p.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _under(p: Path, base: Path) -> bool:
    try:
        p.relative_to(base)
        return True
    except ValueError:
        return False


def is_blocked(p: Path, home: Path) -> bool:
    """True when p (lexically or after resolving symlinks) is inside ~/.agents-git or ~/.cfg."""
    rp = _resolve(Path(p))
    if rp is None:
        return True
    homes = {home, _resolve(home) or home}
    for h in homes:
        for b in BLOCKED:
            blk = h / b
            if _under(Path(p), blk) or _under(rp, blk):
                return True
    return False


def check_path(p: Path, home: Path) -> Path | None:
    """Resolved path when it may be read (under home, not blocked), else None."""
    if is_blocked(p, home):
        return None
    rp = _resolve(Path(p))
    h = _resolve(home) or home
    if rp is None or not (_under(rp, h) or _under(rp, home)):
        return None
    return rp


def _read(p: Path, home: Path, limit: int = MAX_READ) -> str | None:
    """Read up to limit bytes as text; None on any problem or when the guard refuses."""
    rp = check_path(p, home)
    if rp is None:
        return None
    try:
        if not rp.is_file():
            return None
        with rp.open("rb") as f:
            data = f.read(limit)
    except (OSError, ValueError):
        return None
    if b"\x00" in data[:4096]:
        return None
    return data.decode("utf-8", "replace")


def _list_dir(p: Path, home: Path) -> list[Path]:
    """Sorted entries of a directory (capped); empty when blocked or unreadable."""
    rp = check_path(p, home)
    if rp is None:
        return []
    names: list[str] = []
    try:
        with os.scandir(rp) as it:
            for e in it:
                names.append(e.name)
                if len(names) >= MAX_DIR_ENTRIES:
                    break
    except OSError:
        return []
    return [p / n for n in sorted(names)]


def _lexists(p: Path) -> bool:
    return os.path.lexists(p)


def _is_dir(p: Path) -> bool:
    try:
        return p.is_dir()
    except OSError:
        return False


def _is_file(p: Path) -> bool:
    try:
        return p.is_file()
    except OSError:
        return False


# ---------------------------------------------------------------- text helpers


def _clip(s: str, n: int = 160) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _summary(text: str | None) -> str:
    """First meaningful line: frontmatter description, else first heading or paragraph."""
    if not text:
        return NO_SUMMARY
    lines = text.splitlines()
    body_start = 0
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            body_start = end + 1
            for i in range(1, end):
                m = re.match(r"description\s*:\s*(.*)$", lines[i])
                if not m:
                    continue
                val = m.group(1).strip()
                if val in YAML_MARKERS or val == "":
                    parts = []
                    for ln in lines[i + 1 : end]:
                        if ln[:1] in (" ", "\t") and ln.strip():
                            parts.append(ln.strip())
                        else:
                            break
                    val = " ".join(parts)
                val = _unquote(val)
                if val and val not in YAML_MARKERS:
                    return _clip(val)
                break
    for ln in lines[body_start:]:
        s = ln.strip()
        if not s or s in ("---", "```") or s.startswith("<!--"):
            continue
        s = s.lstrip("#").strip()
        if s and s not in YAML_MARKERS:
            return _clip(s)
    return NO_SUMMARY


def _script_purpose(text: str | None) -> str:
    if not text or not text.startswith("#!"):
        return ""
    for ln in text.splitlines()[1:12]:
        s = ln.strip()
        if s.startswith("#") and not s.startswith("#!"):
            s = s.lstrip("#").strip()
            if s and not s.startswith(("shellcheck", "-*-", "vim:")):
                return _clip(s)
        elif s:
            break
    return ""
