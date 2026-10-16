"""Cursor / VS Code ``*.code-workspace`` files: find them, read their folders, match them to pm projects.

Leaf module (stdlib + ``pm.core.errors`` only): the caller passes the known projects as ``{name: path}``.
Workspace files are JSONC (comments, trailing commas); they are read at most ``MAX_BYTES`` and never executed.
A file is offered by an opaque id (hash of its resolved path), so a route can accept only ids it discovered
itself. Labels never carry an absolute path.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SUFFIX = ".code-workspace"
MAX_BYTES = 256 * 1024
MAX_FOLDERS = 200


@dataclass(frozen=True)
class WorkspaceFile:
    id: str
    label: str
    path: Path


def _wid(path: Path) -> str:
    return hashlib.sha1(str(path).encode("utf-8", "replace")).hexdigest()[:12]


def discover(roots: Iterable[Path], limit: int = 20) -> list[WorkspaceFile]:
    """Non-hidden ``*.code-workspace`` files directly inside each root (depth 1), at most ``limit``."""
    out: dict[Path, WorkspaceFile] = {}
    seen: set[Path] = set()
    for r in roots:
        try:
            root = Path(r).resolve()
            if root in seen or not root.is_dir():
                continue
            seen.add(root)
            names = sorted(x.name for x in root.iterdir())
        except (OSError, RuntimeError):
            continue
        for n in names:
            if len(out) >= limit:
                return list(out.values())
            if n.startswith(".") or not n.endswith(SUFFIX) or n == SUFFIX:
                continue
            try:
                rp = (root / n).resolve()
                if rp.parent != root or not rp.is_file() or rp in out:
                    continue  # a symlink that leaves the root, a directory, a duplicate
            except (OSError, RuntimeError):
                continue
            out[rp] = WorkspaceFile(_wid(rp), f"{n} ({root.name})" if root.name else n, rp)
    return list(out.values())


def _strip(text: str) -> str:
    """JSONC to JSON: drop ``//`` and ``/* */`` comments and trailing commas, leave strings alone."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            while i < n and text[i] not in "\r\n":
                i += 1
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        else:
            out.append(c)
            i += 1
    return _drop_commas("".join(out))


def _drop_commas(text: str) -> str:
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
            continue
        if c == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "]}":
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def parse_folders(path: Path) -> list[Path]:
    """Resolved local folders of the workspace file (``folders[].path``; relative ones to the file's directory)."""
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_BYTES + 1)
    except OSError:
        return []
    if len(raw) > MAX_BYTES:
        return []
    try:
        data = json.loads(_strip(raw.decode("utf-8", "replace")))
    except (ValueError, RecursionError):
        return []
    items = data.get("folders") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    base = Path(path).resolve().parent
    out: list[Path] = []
    for it in items[:MAX_FOLDERS]:
        p = it.get("path") if isinstance(it, dict) else None
        if not isinstance(p, str) or not p.strip() or "\0" in p:
            continue  # uri-only (remote) entries and garbage
        try:
            out.append((base / p).resolve())
        except (OSError, RuntimeError, ValueError):
            continue
    return out


def match(folders: Iterable[Path], known: dict[str, Path], own: str) -> tuple[list[str], int]:
    """(names of the known projects the folders are, own project excluded; folders that are no project)."""
    by_path: dict[Path, str] = {}
    for name, p in known.items():
        try:
            by_path.setdefault(Path(p).resolve(), name)
        except (OSError, RuntimeError):
            continue
    names: list[str] = []
    skipped = 0
    for f in folders:
        name = by_path.get(f)
        if name is None:
            skipped += 1
        elif name != own and name not in names:
            names.append(name)
    return names, skipped
