"""Guarded file access for the inventory: home-only, never inside ~/.agents-git or ~/.cfg.

Plus the text helpers that turn what was read into one-line summaries, and ``read_listed``: the read-only viewer of
one file the library lists (a skill's SKILL.md, a rule, a script), for the Agents page's View.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from pm.core.errors import NotFound
from pm.security import render_pane

from .specs import BLOCKED, MAX_DIR_ENTRIES, MAX_READ, NO_SUMMARY, YAML_MARKERS

VIEW_MAX = 256 * 1024


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


# ---------------------------------------------------------------- the library's viewer


@dataclass(frozen=True)
class Viewed:
    """One listed file as text: ``path`` = the listed path, ``file`` = the real file read (symlinks resolved; a skill: its
    SKILL.md), both in ~/... form; ``text`` ANSI-stripped and masked; ``cut`` = longer than VIEW_MAX."""
    path: str
    file: str
    text: str
    cut: bool


def listed_paths(inv) -> set[str]:
    """The paths the library lists (skills, rules, scripts), display form; globs never."""
    return {x.path for x in (*inv.skills, *inv.rules, *inv.scripts) if x.path and "*" not in x.path}


def _hidden_ok(rp: Path, home: Path, bases: list[Path]) -> bool:
    """A resolved path may sit in a hidden folder only inside one of the listed paths, taken as written (no symlink
    escape to ~/.ssh and the like); outside them, no part below home may start with a dot."""
    if any(rp == b or _under(rp, b) for b in bases):
        return True
    for h in {home, _resolve(home) or home}:
        if _under(rp, h):
            return not any(part.startswith(".") for part in rp.relative_to(h).parts)
    return False


def read_listed(path: str, listed: set[str], home: Path, limit: int = VIEW_MAX) -> Viewed:
    """Read one file the library lists, read-only. Raises ``NotFound`` (one message for every refusal) unless
    ``path`` is in ``listed``, resolves (symlinks followed, then re-checked) to a regular file under home outside
    ~/.agents-git and ~/.cfg, and any hidden folder it ends up in is one the library lists. A skill folder shows its
    SKILL.md. Binary files are refused; text is ANSI-stripped and masked."""
    gone = NotFound("not a file the library lists")
    if path not in listed or not path.startswith("~/"):
        raise gone
    real_home = _resolve(home) or home
    # lexical, never resolved: a listed symlink must not make its own target an allowed place
    bases = [real_home / x[2:].rstrip("/") for x in listed if x.startswith("~/") and ".." not in x.split("/")]
    p = home / path[2:]
    if _is_dir(p):
        p = p / "SKILL.md"
    rp = check_path(p, home)
    if rp is None or not _hidden_ok(rp, real_home, bases):
        raise gone
    try:
        fd = os.open(rp, os.O_RDONLY | os.O_NOFOLLOW)  # rp is resolved: a swap to a link since then is refused
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise gone
            data = f.read(limit + 1)
    except OSError:
        raise gone from None
    if b"\x00" in data[:4096]:
        raise gone
    cut = len(data) > limit
    text = render_pane(data[:limit].decode("utf-8", "replace"))
    return Viewed(path=path, file=display_path(rp, home), text=text, cut=cut)


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
