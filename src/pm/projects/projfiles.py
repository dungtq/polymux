"""Read-only file viewing for the project page (right-hand tabs, CLAUDE.md/AGENTS.md, skills).

Everything here is GET-only and confined: project files come from a fixed name list and are
refused when they are symlinks or not regular files; skill files are ``<root>/<name>/SKILL.md``
with ``name`` validated, and the resolved file must sit inside one of the known skill roots.
Reads are size-capped, opened ``O_NOFOLLOW|O_NONBLOCK`` (no FIFO hang), ANSI-stripped and masked
before ``markdown_render`` (raw HTML is escaped there).
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from pm.core.cache import LRU
from pm.core.proc import ProcError, run_argv
from pm.security import render_pane

MAX_BYTES = 256 * 1024  # read cap per file
MAX_CHARS = 120_000  # rendered cap
MAX_SKILLS = 200  # per root
DESC_CHARS = 180

# key -> candidate file names (first regular file wins)
FILES = {
    "changelog": ("CHANGELOG.md", "Changelog.md", "changelog.md"),
    "readme": ("README.md", "Readme.md", "readme.md"),
    "features": ("FEATURES.md", "Features.md", "features.md"),
    "claude": ("CLAUDE.md",),
    "agents": ("AGENTS.md",),
}
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
SRCS = ("claude", "agents")
SCOPES = ("local", "global")


class FileRefused(Exception):
    """The file is missing, a symlink, not a regular file or outside the allowed roots."""

    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status, self.msg = status, msg


@dataclass(frozen=True)
class Doc:
    name: str
    text: str  # masked, ANSI-free
    size: int
    mtime: float
    truncated: bool


def _read_capped(fd: int) -> tuple[bytes, int, float]:
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise FileRefused(404, "not a regular file")
    with os.fdopen(fd, "rb") as fh:
        return fh.read(MAX_BYTES + 1), st.st_size, st.st_mtime


def _open_nofollow(path: str) -> int:
    try:
        return os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        raise FileRefused(404, "no such file") from None


def _clean(data: bytes, size: int, name: str, mtime: float) -> Doc:
    trunc = len(data) > MAX_BYTES
    text = data[:MAX_BYTES].decode("utf-8", "replace")
    text = render_pane(text)
    return Doc(name, text, size, mtime, trunc)


def read_project_file(project_path: str, key: str) -> Doc:
    """One of the fixed project files; refuses symlinks (same stance as TODO.md/BACKLOG.md)."""
    names = FILES.get(key)
    if not names:
        raise FileRefused(404, "unknown file")
    root = Path(project_path)
    for n in names:
        p = root / n
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if stat.S_ISLNK(st.st_mode):
            raise FileRefused(403, f"{n} is a symlink; refused")
        if not stat.S_ISREG(st.st_mode):
            continue
        data, size, mtime = _read_capped(_open_nofollow(str(p)))
        return _clean(data, size, n, mtime)
    raise FileRefused(404, "not found")


def skill_roots(project_path: str, home: str) -> dict[tuple[str, str], Path]:
    """{(scope, src): <skills dir>} for the four known locations."""
    return {
        ("local", "claude"): Path(project_path) / ".claude" / "skills",
        ("local", "agents"): Path(project_path) / ".agents" / "skills",
        ("global", "claude"): Path(home) / ".claude" / "skills",
        ("global", "agents"): Path(home) / ".agents" / "skills",
    }


def _resolved(p: Path) -> Path | None:
    try:
        return p.resolve(strict=True)
    except (OSError, RuntimeError):
        return None


def _head(path: Path, limit: int = 8192) -> str:
    try:
        fd = _open_nofollow(str(path))
        with os.fdopen(fd, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                return ""
            return fh.read(limit).decode("utf-8", "replace")
    except (OSError, FileRefused):
        return ""


def _describe(text: str) -> str:
    """``description:`` from the front matter, else the first prose line."""
    lines = text.splitlines()
    body = lines
    if lines and lines[0].strip() == "---":
        end = next((i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---"), len(lines))
        for i, ln in enumerate(lines[1:end], 1):
            m = re.match(r"description:\s*(.*)$", ln)
            if m:
                d = m.group(1).strip().strip("\"'")
                if d in (">", "|", ">-", "|-", ""):
                    nxt = [x.strip() for x in lines[i + 1:end] if x.startswith((" ", "\t"))]
                    d = " ".join(nxt[:3])
                return d
        body = lines[end + 1:]
    for ln in body:
        s = ln.strip()
        if s and not s.startswith(("#", "---")):
            return s
    return ""


def list_skills(project_path: str, home: str) -> dict[str, list[dict]]:
    """{'local': [...], 'global': [...]}; each {name, src, desc}. Same skill dir (symlinks) once per scope."""
    out: dict[str, list[dict]] = {"local": [], "global": []}
    roots = skill_roots(project_path, home)
    for scope in SCOPES:
        seen: set[Path] = set()
        for src in SRCS:
            base = roots[(scope, src)]
            rb = _resolved(base)
            if rb is None or not rb.is_dir():
                continue
            try:
                with os.scandir(rb) as it:
                    entries = sorted((e.name for e in it), key=str.lower)
            except OSError:
                continue
            for n in entries[:MAX_SKILLS]:
                if not _NAME.match(n):
                    continue
                d = _resolved(base / n)
                if d is None or d in seen or not d.is_dir():
                    continue
                f = d / "SKILL.md"
                if not f.is_file() or not _inside(_resolved(f), _allowed(project_path, home)):
                    continue
                seen.add(d)
                desc = render_pane(_describe(_head(f))).strip()
                out[scope].append({"name": n, "src": src, "desc": desc[:DESC_CHARS]})
    return out


def _allowed(project_path: str, home: str) -> list[Path]:
    """Resolved skill roots; a project-local root that points outside the project (and the global roots) is dropped."""
    roots = skill_roots(project_path, home)
    glob = [r for k, p in roots.items() if k[0] == "global" and (r := _resolved(p)) is not None]
    proj = _resolved(Path(project_path))
    loc = [r for k, p in roots.items() if k[0] == "local" and (r := _resolved(p)) is not None
           and (_inside(r, glob) or (proj is not None and _inside(r, [proj])))]
    return glob + loc


def _inside(p: Path | None, roots: list[Path]) -> bool:
    if p is None:
        return False
    return any(p == r or r in p.parents for r in roots)


def read_skill(project_path: str, home: str, scope: str, src: str, name: str) -> Doc:
    if scope not in SCOPES or src not in SRCS or not _NAME.match(name or ""):
        raise FileRefused(404, "unknown skill")
    base = skill_roots(project_path, home)[(scope, src)]
    f = _resolved(base / name / "SKILL.md")
    if f is None or not _inside(f, _allowed(project_path, home)):
        raise FileRefused(404, "not found")
    data, size, mtime = _read_capped(_open_nofollow(str(f)))
    return _clean(data, size, f"{name}/SKILL.md", mtime)


# --- read-only file tree of the project (right-hand pane "Files") ---
MAX_ENTRIES = 500
_SECRET = re.compile(r"(^\.env($|\.)|\.(pem|key|p12|pfx)$|^id_[a-z0-9]+$|^\.netrc$|^\.git-credentials$)", re.I)
_SKIP_DIRS = {".git"}


def _rel_parts(rel: str) -> list[str]:
    if "\x00" in rel or len(rel) > 1024:
        raise FileRefused(404, "bad path")
    parts = [p for p in rel.split("/") if p]
    if any(p in (".", "..") or len(p) > 255 or "\\" in p for p in parts) or (parts and parts[0] in _SKIP_DIRS):
        raise FileRefused(404, "bad path")
    return parts


def _walk_fd(project_path: str, parts: list[str]) -> int:
    """fd of the final component, opened one component at a time with O_NOFOLLOW (no symlink anywhere, no escape)."""
    cloexec = getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(project_path, os.O_RDONLY | os.O_DIRECTORY | cloexec)
    except OSError:
        raise FileRefused(404, "no such project dir") from None
    try:
        for p in parts:
            try:
                nfd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | cloexec, dir_fd=fd)
            except OSError:
                raise FileRefused(404, "no such file, or symlink refused") from None
            os.close(fd)
            fd = nfd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _btime(project_path: str, parts: list[str]) -> float:
    """Birth time via ``stat -c %W`` (fixed binary, argv, timeout); 0.0 when the fs does not record it."""
    try:
        cp = run_argv(["stat", "-c", "%W", "--", os.path.join(project_path, *parts)], text=True, timeout=2)
        v = float(cp.stdout.strip()) if cp.returncode == 0 else 0.0
    except (ProcError, ValueError):
        return 0.0
    return v if v > 0 else 0.0


_btimes_cache = LRU(max_bytes=1024 * 1024, max_entries=128)


def _btimes(project_path: str, parts: list[str], names: list[str]) -> dict[str, float]:
    """Cached ``_btimes_raw`` per (dir, dir mtime_ns, names): a created/removed entry changes the dir mtime."""
    base = os.path.join(project_path, *parts)
    try:
        key = (base, os.stat(base).st_mtime_ns, tuple(names))
    except OSError:
        return _btimes_raw(project_path, parts, names)
    hit = _btimes_cache.get(key)
    if hit is None:
        hit = _btimes_raw(project_path, parts, names)
        if hit or not names:
            _btimes_cache.put(key, hit)
    return dict(hit)


def _btimes_raw(project_path: str, parts: list[str], names: list[str]) -> dict[str, float]:
    """Birth times for ``names`` below the dir in ONE ``stat`` call (fixed binary, argv, timeout); missing = absent."""
    if not names:
        return {}
    base = os.path.join(project_path, *parts)
    try:
        cp = run_argv(["stat", "--printf", "%W\\0%n\\0", "--", *[os.path.join(base, n) for n in names]],
                      text=True, timeout=5)
    except ProcError:
        return {}
    f = cp.stdout.split("\0")
    out: dict[str, float] = {}
    for i in range(0, len(f) - 1, 2):
        try:
            v = float(f[i])
        except ValueError:
            continue
        if v > 0:
            out[os.path.basename(f[i + 1])] = v
    return out


def read_tree(project_path: str, rel: str = "") -> dict:
    """List a directory or read a file below the project root: {'kind': 'dir'|'file', 'parts', ...}."""
    parts = _rel_parts(rel)
    if parts and _SECRET.search(parts[-1]):
        raise FileRefused(403, "hidden for safety")
    fd = _walk_fd(project_path, parts)
    try:
        st = os.fstat(fd)
        if stat.S_ISDIR(st.st_mode):
            entries = []
            with os.scandir(fd) as it:
                for e in it:
                    if e.name in _SKIP_DIRS and not parts:
                        continue
                    try:
                        s = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    kind = ("link" if stat.S_ISLNK(s.st_mode) else "dir" if stat.S_ISDIR(s.st_mode)
                            else "file" if stat.S_ISREG(s.st_mode) else "other")
                    entries.append({"name": e.name, "kind": kind, "size": s.st_size if kind == "file" else 0,
                                    "mtime": s.st_mtime})
            total = len(entries)
            entries.sort(key=lambda x: (x["kind"] != "dir", x["name"].lower()))
            entries = entries[:MAX_ENTRIES]
            bt = _btimes(project_path, parts, [e["name"] for e in entries])
            for e in entries:
                e["btime"] = bt.get(e["name"], 0.0)
            return {"kind": "dir", "parts": parts, "total": total, "entries": entries}
        if not stat.S_ISREG(st.st_mode):
            raise FileRefused(404, "not a regular file")
        data, size, mtime = _read_capped(fd)  # closes fd
        fd = -1
        out = {"kind": "file", "parts": parts, "size": size, "mtime": mtime, "btime": _btime(project_path, parts),
               "binary": False, "text": "", "truncated": False}
        if b"\x00" in data[:8192]:
            out["binary"] = True
        else:
            doc = _clean(data, size, parts[-1], mtime)
            out.update(text=doc.text, truncated=doc.truncated)
        return out
    finally:
        if fd >= 0:
            os.close(fd)


# --- left project tree + full file view (highlight, markdown, diff) ---
TREE_MAX_NODES = 300
TREE_MAX_DEPTH = 8
_MD_EXT = (".md", ".markdown")


def _tree_level(project_path: str, parts: list[str], cur: list[str], budget: list[int]) -> list[dict]:
    """Entries of one directory; only dirs on the path to ``cur`` get children. Same safety rules as read_tree."""
    try:
        fd = _walk_fd(project_path, parts)
    except (FileRefused, OSError):
        return []
    rows: list[tuple[str, str]] = []
    try:
        with os.scandir(fd) as it:
            for e in it:
                if (not parts and e.name in _SKIP_DIRS) or _SECRET.search(e.name):
                    continue
                try:
                    if e.is_symlink():
                        continue
                    kind = "dir" if e.is_dir(follow_symlinks=False) else "file" if e.is_file(follow_symlinks=False) else ""
                except OSError:
                    continue
                if kind:
                    rows.append((e.name, kind))
    except OSError:
        return []
    finally:
        os.close(fd)
    rows.sort(key=lambda r: (r[1] != "dir", r[0].lower()))
    out: list[dict] = []
    for name, kind in rows:
        if budget[0] <= 0:
            break
        budget[0] -= 1
        sub = [*parts, name]
        on_path = kind == "dir" and cur[:len(sub)] == sub and len(sub) <= TREE_MAX_DEPTH
        out.append({"name": name, "kind": kind, "rel": "/".join(sub), "cur": sub == cur, "open": on_path,
                    "children": _tree_level(project_path, sub, cur, budget) if on_path else []})
    return out


def build_tree(project_path: str, cur: list[str]) -> list[dict]:
    return _tree_level(project_path, [], cur, [TREE_MAX_NODES])


def _lines(text: str) -> int:
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


def file_view(project_path: str, rel: str, raw: bool = False, diff: bool = False) -> dict:
    """read_tree plus the left tree and, for files, the rendered body: ``body_kind`` is code|md|diff."""
    from pm.core import codehl
    from pm.core.markdown_render import render_markdown
    from pm.git import ctl as gitctl

    tree = read_tree(project_path, rel)
    tree["ftree"] = build_tree(project_path, tree["parts"])
    if tree["kind"] == "dir":
        gs, in_repo = gitctl.status_map(project_path)
        tree["in_repo"] = in_repo
        have = {e["name"] for e in tree["entries"]}
        pre = "/".join(tree["parts"]) + "/" if tree["parts"] else ""
        for k, v in sorted(gs.items()):  # tracked files deleted on disk: show them (red, no link)
            if v == "D" and k.startswith(pre) and "/" not in k[len(pre):] and k[len(pre):] not in have:
                tree["entries"].append({"name": k[len(pre):], "kind": "gone", "size": 0, "mtime": 0.0, "btime": 0.0})
        tree["gs"] = {e["name"]: gs.get("/".join([*tree["parts"], e["name"]]), "C") for e in tree["entries"]} if in_repo else {}
        return tree
    if tree["binary"]:
        return tree
    name = tree["parts"][-1]
    tree["lines"] = _lines(tree["text"])
    is_md = name.lower().endswith(_MD_EXT)
    tree["is_md"] = is_md
    tree["raw"] = raw
    d = render_pane(gitctl.file_diff(project_path, "/".join(tree["parts"])))
    tree["has_diff"] = bool(d.strip())
    tree["diff"] = diff and tree["has_diff"]
    tree["lang"] = "Text"
    fkey = _render_key(project_path, tree)
    if tree["diff"]:
        tree["body_kind"], tree["body"] = "diff", _cached(("diff", hash(d)), fkey, lambda: codehl.diff_html(d[:MAX_CHARS]))
        tree["diff_cut"] = len(d) > MAX_CHARS
    elif is_md and not raw:
        tree["body_kind"], tree["body"] = "md", _cached(("md",), fkey, lambda: render_markdown(tree["text"], MAX_CHARS))
        tree["lang"] = "Markdown"
    else:
        tree["body_kind"] = "code"
        tree["body"], tree["lang"] = _cached(("code",), fkey, lambda: codehl.highlight(tree["text"], name))
    return tree


# Rendered bodies (already masked: tree["text"] and the diff went through render_pane/_clean before this point).
_render_cache = LRU(max_bytes=32 * 1024 * 1024, max_entries=256)


def _render_key(project_path: str, tree: dict) -> tuple | None:
    try:
        p = os.path.join(project_path, *tree["parts"])
        st = os.stat(p)
        return (os.path.realpath(p), st.st_mtime_ns, st.st_size, len(tree["text"]))
    except OSError:
        return None


def _cached(kind: tuple, fkey: tuple | None, compute):
    if fkey is None:
        return compute()
    key = (*fkey, *kind)
    hit = _render_cache.get(key)
    if hit is None:
        hit = compute()
        _render_cache.put(key, hit)
    return hit


# --- bounded text grep over the project (navbar search) ---
GREP_MIN_Q = 3
GREP_MAX_FILES = 400  # files opened per search
GREP_MAX_BYTES = 200 * 1024  # larger files are skipped
GREP_MAX_DEPTH = 6
GREP_BUDGET_S = 1.5
GREP_SNIP = 100
_GREP_SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target"}


def _snippet(line: str, i: int, n: int) -> str:
    """~GREP_SNIP chars around the match, one line, masked (never raw file text)."""
    half = (GREP_SNIP - n) // 2
    a = max(0, i - half)
    out = " ".join(line[a:i + n + half].split())
    out = render_pane(out)
    return ("…" if a else "") + out + ("…" if i + n + half < len(line) else "")


def grep_project(project_path: str, q: str, limit: int = 5, budget: float = GREP_BUDGET_S) -> list[dict]:
    """[{'rel', 'line', 'snippet'}]: first match per file, case-insensitive literal, text files only.

    Bounded by file count, file size, depth, wall time and ``limit``; hidden entries, ``.git``,
    ``node_modules``, ``.venv`` and secret-looking names are skipped; every component is opened
    O_NOFOLLOW (no symlink followed). Never raises.
    """
    import time

    q = " ".join(q.split()).lower()
    if len(q) < GREP_MIN_Q:
        return []
    limit = max(1, min(int(limit), 20))
    deadline = time.monotonic() + budget
    out: list[dict] = []
    opened = 0
    stack: list[list[str]] = [[]]
    while stack and len(out) < limit and opened < GREP_MAX_FILES and time.monotonic() < deadline:
        parts = stack.pop()
        try:
            dfd = _walk_fd(project_path, parts)
        except (FileRefused, OSError):
            continue
        files: list[str] = []
        dirs: list[str] = []
        try:
            with os.scandir(dfd) as it:
                for e in it:
                    n = e.name
                    if n.startswith(".") or n in _GREP_SKIP or _SECRET.search(n):
                        continue
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            dirs.append(n)
                        elif e.is_file(follow_symlinks=False) and e.stat(follow_symlinks=False).st_size <= GREP_MAX_BYTES:
                            files.append(n)
                    except OSError:
                        continue
        except OSError:
            continue
        finally:
            os.close(dfd)
        if len(parts) < GREP_MAX_DEPTH:
            stack.extend([*parts, d] for d in sorted(dirs, reverse=True))
        for n in sorted(files):
            if len(out) >= limit or opened >= GREP_MAX_FILES or time.monotonic() >= deadline:
                break
            opened += 1
            try:
                fd = _walk_fd(project_path, [*parts, n])
            except (FileRefused, OSError):
                continue
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    continue
                data = os.read(fd, GREP_MAX_BYTES + 1)
            except OSError:
                continue
            finally:
                os.close(fd)
            if len(data) > GREP_MAX_BYTES or b"\x00" in data[:8192]:
                continue
            for ln, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                i = line.lower().find(q)
                if i >= 0:
                    out.append({"rel": "/".join([*parts, n]), "line": ln, "snippet": _snippet(line, i, len(q))})
                    break
    return out
