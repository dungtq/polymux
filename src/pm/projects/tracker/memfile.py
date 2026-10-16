"""Claude Code's memory files: parse and render one memory file (YAML frontmatter ``name``, ``description``,
``metadata: {type, ...}``, then the Markdown body), the ``MEMORY.md`` index (``- [Title](file.md) — hook``), and
bounded reads of a memory dir (regular files only, never through a symlink). Used by ``memsync``.

Dates in the frontmatter stay text, and every key pm does not model stays in ``Memory.meta``, so a file read and
written back says the same; ``same`` compares by meaning, so a file that already says it is never rewritten.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

import yaml

from pm.core.errors import Denied, Invalid, PmError
from pm.projects.tracker import memory, store
from pm.projects.tracker.memory import Memory

INDEX = "MEMORY.md"
MAX_FILE = 64 * 1024
MAX_FILES = 500
MAX_INDEX = 256 * 1024
_MAX_NODES = 2000
_MAX_DEPTH = 20
_CONFLICT = re.compile(r"-conflict-\d{8}T\d{6}(?:-\d+)?\.md$")
_INDEX_LINE = re.compile(r"^\s*[-*]\s+\[([^\]]*)\]\(([^)\s]+)\)\s*(?:[—–-]{1,2}\s*(.*?))?\s*$")
_TS = "tag:yaml.org,2002:timestamp"


class _Loader(yaml.SafeLoader):
    """Dates and times stay text (``modified: 2026-10-07T07:27:13Z`` round-trips as written)."""


class _Dumper(yaml.SafeDumper):
    pass


for _cls in (_Loader, _Dumper):
    _cls.yaml_implicit_resolvers = {k: [r for r in v if r[0] != _TS]
                                    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()}


@dataclass(frozen=True)
class Parsed:
    name: str
    type: str
    description: str
    body: str
    meta: dict


def _bounded(obj) -> None:
    stack, n = [(obj, 0)], 0
    while stack:
        o, depth = stack.pop()
        n += 1
        if depth > _MAX_DEPTH or n > _MAX_NODES:
            raise Invalid("frontmatter nested too deep or too large")
        if isinstance(o, dict):
            if not all(isinstance(k, str) for k in o):
                raise Invalid("frontmatter keys must be text")
            stack += [(v, depth + 1) for v in o.values()]
        elif isinstance(o, list):
            stack += [(v, depth + 1) for v in o]


def name_from_file(fname: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", Path(fname).stem.lower()).strip("-_")[:64]


def parse(data: bytes, fname: str) -> Parsed:
    """A memory file; Invalid when it has no frontmatter, no description or an unknown type. A missing ``name``
    comes from the file name, a missing type is ``project``."""
    try:
        lines = data.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        raise Invalid("not UTF-8") from None
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), 0) if lines else 0
    if not lines or lines[0].rstrip() != "---" or not end:
        raise Invalid("no frontmatter")
    try:
        fm = yaml.load("".join(lines[1:end]), Loader=_Loader) or {}
    except (yaml.YAMLError, RecursionError):
        raise Invalid("bad frontmatter YAML") from None
    if not isinstance(fm, dict):
        raise Invalid("frontmatter is not a mapping")
    _bounded(fm)
    meta = {k: v for k, v in fm.items() if k not in ("name", "description")}
    md = meta.get("metadata")
    t = md.get("type") if isinstance(md, dict) and "type" in md else meta.get("type", "project")
    if t not in memory.TYPES:
        raise Invalid(f"unknown type {str(t)[:40]!r}")
    name, desc = fm.get("name"), fm.get("description")
    name = name.strip() if isinstance(name, str) and memory.NAME.match(name.strip()) else name_from_file(fname)
    if not isinstance(desc, str):
        raise Invalid("no description")
    return Parsed(name=memory.clean_name(name), type=t, description=memory.clean_description(desc, collapse=False),
                  body=store.clean_body("".join(lines[end + 1:])), meta=memory.with_type(meta, t))


def render(m: Memory) -> str:
    fm = {"name": m.name, "description": m.description, **m.meta}
    head = yaml.dump(fm, Dumper=_Dumper, sort_keys=False, allow_unicode=True, default_flow_style=False, width=4096)
    return f"---\n{head}---\n" + (f"\n{m.body_md}\n" if m.body_md else "")


def same(p: Parsed, m: Memory) -> bool:
    return (p.name, p.type, p.description, p.body, p.meta) == (m.name, m.type, m.description, m.body_md, m.meta)


def render_index(mems: list[Memory]) -> str:
    return "".join(f"- [{(m.title or m.name).replace('[', '(').replace(']', ')')}]({m.file})"
                   f" — {' '.join((m.hook or m.description).split())}\n" for m in mems)


def sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def read_file(path: Path, cap: int) -> bytes | None:
    """Bytes of a regular file, None when missing; Denied for a symlink or anything else, Invalid over ``cap``."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError:
        raise Denied(f"{path.name}: a symlink or unreadable") from None
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise Denied(f"{path.name}: not a regular file")
        data = f.read(cap + 1)
    if len(data) > cap:
        raise Invalid(f"{path.name}: larger than {cap // 1024} KB")
    return data


def scan_dir(d: Path, notes: list[str]) -> list[tuple[str, bytes]]:
    """(file name, bytes) of the memory files in ``d`` (no index, conflict copies, hidden or oversized files),
    in name order, at most ``MAX_FILES``."""
    try:
        names = sorted(e.name for e in os.scandir(d))
    except FileNotFoundError:
        return []
    out: list[tuple[str, bytes]] = []
    for fname in names:
        if not fname.endswith(".md") or fname == INDEX or fname.startswith(".") or _CONFLICT.search(fname):
            continue
        if len(out) >= MAX_FILES:
            notes.append(f"more than {MAX_FILES} memory files: the rest skipped")
            break
        try:
            data = read_file(d / fname, MAX_FILE)
        except PmError as e:
            notes.append(f"{fname}: skipped ({e})")
            continue
        if data is not None:
            out.append((fname, data))
    return out


def read_index(d: Path) -> dict[str, tuple[str, str]]:
    """{file name: (title, hook)} from ``MEMORY.md``, in its order; over-long texts are cut to the caps of
    ``memory`` (an index line never stops a memory's import)."""
    try:
        data = read_file(d / INDEX, MAX_INDEX) or b""
    except PmError:
        return {}
    out: dict[str, tuple[str, str]] = {}
    for ln in data.decode("utf-8", "replace").splitlines():
        if m := _INDEX_LINE.match(ln):
            title, hook = " ".join(m.group(1).split()), " ".join((m.group(3) or "").split())
            out.setdefault(m.group(2), (title[:memory.MAX_TITLE], hook[:memory.MAX_DESC]))
    return out
