#!/usr/bin/env python3
"""Architecture gate for pm (docs/ARCHITECTURE.md "Layer rules", "Size limits").

Scans src/pm (Python, plus static JS/CSS outside vendor dirs) and reports offenders as keys
``kind:relpath[:name]`` (relpath relative to src/pm). Keys listed in the baseline are known,
existing offenders; the baseline may only shrink. Route handlers under web/ (``@<name>.get/post/...``)
longer than ROUTE_MAX lines are ``route-lines:`` offenders (baselinable, like ``func-lines:``). Any offender not in the baseline is NEW and
fails the check. Process spawning is counted per file (``subprocess:relpath`` -> number of call
sites, baseline ``counts``): a file whose count grows, or a new file, fails (SPAWN_HELPERS exempt). ``shell=`` with any
value other than the constant ``False`` is never allowed, baselined or not. Package import edges
outside ALLOWED (``pkg-edge:``, ``pkg-unknown:``) and module/package import cycles (``cycle:``,
``pkg-cycle:``) are never baselinable either: always NEW, never written, an error if listed.
Frontend (static/css, static/js, vendor skipped): ``css-root-token:`` (custom properties on a ``:root``
rule outside css/tokens.css) is never baselinable; ``css-color:`` (colour literals outside tokens.css),
``js-csrf:`` (CSRF lookups outside js/core.js) and ``js-fetch:`` (raw ``fetch(`` outside core.js/sw.js)
are per-file counts like ``subprocess:``. These rules live in tools/archcheck_front.py.

``--static-dir DIR`` (repeatable) also scans a theme static tree (DIR/css, DIR/js, DIR/*.css|*.js, vendor
skipped) with zero tolerance and no baseline: ``css-color:`` and ``css-root-token:`` (DIR/css/tokens.css is
the only tokens file), ``js-fetch:`` and ``js-csrf:`` (DIR/js/core.js is the only allowed file),
``asset-lines:``, plus two rules for these trees only: ``line-len:`` (a .js/.css line over 120 chars) and
``z-index:`` (a CSS ``z-index`` that is a raw value instead of ``var(--z-...)`` or a calc of one). Keys carry
DIR as given. Without ``--root``, ``--static-dir`` scans only those trees (not src/pm), so another repo can
run ``python <polymux>/tools/archcheck.py --static-dir src/<plugin>/themes/<name>/static``. Any finding
exits non-zero.

Usage: uv run python tools/archcheck.py [--root DIR] [--baseline FILE] [--strict] [--write-baseline]
       [--static-dir DIR ...]
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:  # sibling module, also when loaded by file path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from archcheck_front import ASSET_MAX, FRONT_LABELS, assets, check_frontend, check_static_dir  # noqa: E402

MODULE_MAX = 600
FUNC_MAX = 80
ROUTE_MAX = 40
# Route handlers (``@router.get(...)``, ``@app.post(...)``, ...) under web/: kept thin (``route-lines:``).
ROUTE_DIR = "web/"
ROUTE_ATTRS = {"get", "post", "put", "delete", "patch", "websocket", "api_route"}

BASELINE_COMMENT = ("Existing offenders. This list may only shrink; never add entries or raise counts "
                    "(docs/ARCHITECTURE.md).")

WEB_IMPORTS = ("fastapi", "starlette", "jinja2")
# Web-side modules (relative to src/pm) that may import the web stack: the web/ and security/ packages,
# plus the package root (``pm.main`` builds the app).
WEB_SIDE_DIRS = ("web/", "security/")
WEB_SIDE_FILES = {"__init__.py"}
# Process-spawning callables, by resolved dotted name; os.exec*/spawn*/posix_spawn* by prefix.
SPAWN = {
    *(f"subprocess.{n}" for n in ("run", "Popen", "call", "check_call", "check_output", "getoutput",
                                  "getstatusoutput")),
    "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell",
    "os.system", "os.popen", "pty.spawn", "pty.fork",
}
SPAWN_PREFIXES = ("os.exec", "os.spawn", "os.posix_spawn")
# Attribute names that spawn whatever the receiver is (event loop methods, unresolved asyncio aliases).
SPAWN_ATTRS = {"subprocess_exec", "subprocess_shell", "create_subprocess_exec", "create_subprocess_shell"}
DYN_IMPORTS = {"importlib.import_module", "__import__"}
# Offender kinds stored as a per-file count in the baseline (may only shrink), with the report label.
COUNTED_LABELS = {
    "subprocess:": "process-spawning call sites",
    **FRONT_LABELS,
}
COUNTED = tuple(COUNTED_LABELS)
# The shared spawn helper(s) (relpath under src/pm): the one place meant to spawn, never counted.
SPAWN_HELPERS = {"core/proc.py"}

# Package import rules (docs/ARCHITECTURE.md "Packages", "Layer rules" 1 and 4): package -> packages it
# may import. A package is the first path component under src/pm; the root ``pm/__init__.py`` (and any
# other root module) counts as ``web``. "*" = anything. Edits here are architecture decisions.
ALLOWED: dict[str, set[str]] = {
    "core": set(),
    "security": {"core"},
    "tmux": {"core"},
    "git": {"core"},
    "chat": {"core", "security", "tmux"},
    "projects": {"core", "security", "tmux", "git"},
    "agents": {"core", "security", "tmux", "git", "chat", "projects"},
    "qchat": {"core", "security", "chat", "projects", "agents"},
    "infra": {"core", "security", "tmux", "git", "projects", "agents"},
    "notify": {"core", "security", "tmux", "projects", "agents", "infra"},
    "web": {"*"},
}
# Structural kinds: always NEW, never written to the baseline; a baseline listing one is an error.
NEVER_BASELINE = ("pkg-edge:", "pkg-unknown:", "cycle:", "pkg-cycle:", "css-root-token:")


def _lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def _module_name(rel: str) -> str:
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(["pm", *parts])


def _is_package(rel: str) -> bool:
    return rel.endswith("/__init__.py") or rel == "__init__.py"


def _resolve_from(rel: str, node: ast.ImportFrom) -> str:
    """Absolute module name of a ``from X import ...`` statement in the module at ``rel``."""
    if not node.level:
        return node.module or ""
    base = _module_name(rel).split(".")
    if not _is_package(rel):
        base = base[:-1]
    if node.level > 1:
        base = base[: len(base) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def _web_target(name: str) -> str | None:
    """The forbidden root a module name belongs to, or None."""
    top = name.split(".")[0]
    if top in WEB_IMPORTS:
        return top
    if name == "pm.web" or name.startswith("pm.web."):
        return "pm.web"
    return None


def _is_web_side(rel: str) -> bool:
    return rel in WEB_SIDE_FILES or rel.startswith(WEB_SIDE_DIRS)


def _is_route_decorator(d: ast.AST) -> bool:
    """``@router.get(...)`` / ``@app.post(...)``: a call of ``<Name>.<route attr>``."""
    return isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in ROUTE_ATTRS \
        and isinstance(d.func.value, ast.Name)


class _FuncLines(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.found: list[tuple[str, int]] = []
        self.routes: list[tuple[str, int]] = []  # route handlers longer than ROUTE_MAX

    def _func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        n = (node.end_lineno or node.lineno) - node.lineno + 1
        if n > FUNC_MAX:
            self.found.append((".".join(self.stack), n))
        if n > ROUTE_MAX and any(_is_route_decorator(d) for d in node.decorator_list):
            self.routes.append((".".join(self.stack), n))
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _func
    visit_AsyncFunctionDef = _func

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()


def _callee(f: ast.AST) -> str:
    """Last name of a call target: ``f`` for ``f()``, ``g`` for ``x.g()``."""
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def _extras_key(node: ast.AST) -> str | None:
    """String key of ``X.extras.get("k")``, ``X.extras["k"]`` or ``_extras(...).get("k")``."""
    if isinstance(node, ast.Call):
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr == "get" and node.args):
            return None
        obj = f.value
        hit = (isinstance(obj, ast.Attribute) and obj.attr == "extras") or (
            isinstance(obj, ast.Call) and _callee(obj.func) == "_extras"
        )
        arg = node.args[0]
        if hit and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    elif isinstance(node, ast.Subscript):
        obj = node.value
        if isinstance(obj, ast.Attribute) and obj.attr == "extras":
            s = node.slice
            if isinstance(s, ast.Constant) and isinstance(s.value, str):
                return s.value
    return None


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Local name -> dotted origin for every import in the module (``sp`` -> ``subprocess``,
    ``r`` -> ``subprocess.run``). Module wide, function-level imports included."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    out[a.asname] = a.name
                else:
                    top = a.name.split(".")[0]
                    out[top] = top
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            for a in node.names:
                out[a.asname or a.name] = f"{node.module}.{a.name}"
    return out


def _dotted(node: ast.AST, aliases: dict[str, str]) -> str:
    """Resolved dotted name of ``a.b.c`` / ``name`` through import aliases, or ""."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    base = aliases.get(node.id, node.id)
    return ".".join([base, *reversed(parts)])


def _is_spawn(name: str) -> bool:
    return name in SPAWN or name.startswith(SPAWN_PREFIXES)


def _annotation_ids(tree: ast.AST) -> set[int]:
    """ids of nodes inside type annotations (``-> subprocess.Popen`` is not a call site)."""
    roots: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation:
            roots.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
            roots.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
    return {id(n) for r in roots for n in ast.walk(r)}


def spawn_sites(tree: ast.AST) -> int:
    """Number of process-spawning references: calls of, or references to (``run_in_executor(None,
    subprocess.run, ...)``), a SPAWN callable through any alias; ``x.subprocess_exec``-style loop
    methods; and ``importlib.import_module("subprocess")`` / ``__import__("subprocess")``."""
    aliases = _aliases(tree)
    skip = _annotation_ids(tree)
    n = 0
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Attribute) and (node.attr in SPAWN_ATTRS or _is_spawn(_dotted(node, aliases))):
            n += 1
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in aliases \
                and _is_spawn(aliases[node.id]):
            n += 1
        elif isinstance(node, ast.Call) and node.args and _dotted(node.func, aliases) in DYN_IMPORTS:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.split(".")[0] == "subprocess":
                n += 1
    return n


def check_python(path: Path, rel: str) -> tuple[dict[str, str | int], list[tuple[str, str]]]:
    """Offenders (key -> detail, or call-site count for ``subprocess:``) and hard errors for one module."""
    out: dict[str, str | int] = {}
    errors: list[tuple[str, str]] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    n = len(text.splitlines())
    if n > MODULE_MAX:
        out[f"module-lines:{rel}"] = f"{n} lines > {MODULE_MAX}"
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as e:
        errors.append((f"parse:{rel}", f"syntax error line {e.lineno}"))
        return out, errors

    fl = _FuncLines()
    fl.visit(tree)
    for qual, ln in fl.found:
        out[f"func-lines:{rel}:{qual}"] = f"{ln} lines > {FUNC_MAX}"
    if rel.startswith(ROUTE_DIR):
        for qual, ln in fl.routes:
            out[f"route-lines:{rel}:{qual}"] = f"{ln} lines > {ROUTE_MAX}"

    web_side = _is_web_side(rel)
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = _resolve_from(rel, node)
            names = [mod, *(f"{mod}.{a.name}" for a in node.names)]
        for name in names:
            target = _web_target(name)
            if target and not web_side:
                out[f"layer:{rel}:{target}"] = f"domain module imports {target}"
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
                    errors.append((f"shell-true:{rel}:{node.lineno}", "shell= other than False is never allowed"))
        key = _extras_key(node)
        if key is not None:
            out[f"extras:{rel}:{key}"] = "settings.extras key; use a typed Settings field"
    sites = 0 if rel in SPAWN_HELPERS else spawn_sites(tree)
    if sites:
        out[f"subprocess:{rel}"] = sites
    return out, errors


def _src_package(rel: str) -> str:
    """Package of the module at ``rel``: first path component; root modules are ``web``."""
    return rel.split("/")[0] if "/" in rel else "web"


def _module_of(name: str, modules: set[str]) -> str:
    """Longest prefix of dotted ``name`` that is a scanned module (``name`` itself when none is)."""
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        cand = ".".join(parts[:i])
        if cand in modules:
            return cand
    return name


def _target_package(mod: str, pkg_dirs: set[str]) -> str:
    """Package of pm module ``mod``: ``pm`` and root modules (``pm.x``) are ``web``; ``pm.x.y`` is ``x``."""
    parts = mod.split(".")
    if len(parts) == 1 or (len(parts) == 2 and parts[1] not in pkg_dirs):
        return "web"
    return parts[1]


def _pm_imports(rel: str, tree: ast.AST, modules: set[str]) -> list[tuple[int, str]]:
    """(lineno, target module) for every pm import in the module, function-local and
    ``if TYPE_CHECKING:`` ones included. ``from pm.a import b`` targets ``pm.a.b`` if that is a
    module, else ``pm.a``."""
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(node.lineno, _module_of(a.name, modules)) for a in node.names
                    if a.name == "pm" or a.name.startswith("pm.")]
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from(rel, node)
            if base != "pm" and not base.startswith("pm."):
                continue
            for a in node.names:
                full = f"{base}.{a.name}"
                out.append((node.lineno, full if full in modules else _module_of(base, modules)))
    return out


def _sccs(graph: dict[str, set[str]]) -> list[list[str]]:
    """Strongly connected components with more than one node (Tarjan, iterative)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = 0
    for start in sorted(graph):
        if start in index:
            continue
        work = [(start, iter(sorted(graph[start])))]
        index[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            v, it = work[-1]
            w = next(it, None)
            if w is not None:
                if w not in graph:
                    continue
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(sorted(graph[w]))))
                elif w in on_stack:
                    low[v] = min(low[v], index[w])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    out.append(sorted(comp))
    return out


def check_structure(trees: dict[str, ast.AST]) -> list[tuple[str, str]]:
    """Package edges against ALLOWED, unknown packages, module and package import cycles.
    Never baselinable: every finding is NEW."""
    modules = {_module_name(rel) for rel in trees}
    pkg_dirs = {rel.split("/")[0] for rel in trees if "/" in rel}
    found: dict[str, str] = {}
    mod_graph: dict[str, set[str]] = {m: set() for m in modules}
    pkg_graph: dict[str, set[str]] = {}
    for rel, tree in sorted(trees.items()):
        src_pkg = _src_package(rel)
        known = src_pkg in ALLOWED
        if not known:
            found[f"pkg-unknown:{rel}"] = f"package {src_pkg!r} is not in ALLOWED"
        src_mod = _module_name(rel)
        for lineno, target in _pm_imports(rel, tree, modules):
            if target != src_mod and target in modules:
                mod_graph[src_mod].add(target)
            to_pkg = _target_package(target, pkg_dirs)
            if to_pkg == src_pkg:
                continue
            pkg_graph.setdefault(src_pkg, set()).add(to_pkg)
            pkg_graph.setdefault(to_pkg, set())
            allowed = ALLOWED.get(src_pkg, set())
            if known and "*" not in allowed and to_pkg not in allowed:
                found[f"pkg-edge:{src_pkg}->{to_pkg}:{rel}:{lineno}"] = f"{src_pkg} may not import {to_pkg} ({target})"
    for comp in _sccs(mod_graph):
        found[f"cycle:{' -> '.join([*comp, comp[0]])}"] = f"module import cycle ({len(comp)} modules)"
    for comp in _sccs(pkg_graph):
        found[f"pkg-cycle:{' -> '.join([*comp, comp[0]])}"] = f"package import cycle ({len(comp)} packages)"
    return sorted(found.items())


def scan(root: Path) -> tuple[dict[str, str | int], list[tuple[str, str]], list[tuple[str, str]]]:
    """(offenders, errors, structural findings)."""
    pkg = root / "src" / "pm"
    offenders: dict[str, str | int] = {}
    errors: list[tuple[str, str]] = []
    trees: dict[str, ast.AST] = {}
    for path in sorted(pkg.rglob("*.py")):
        rel = path.relative_to(pkg).as_posix()
        if "__pycache__" in rel or rel.startswith(("static/", "templates/")):
            continue
        o, e = check_python(path, rel)
        offenders.update(o)
        errors.extend(e)
        try:
            trees[rel] = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        except SyntaxError:
            pass  # reported by check_python
    structural = check_structure(trees)
    for path in assets(pkg / "static"):
        n = _lines(path)
        if n > ASSET_MAX:
            offenders[f"asset-lines:{path.relative_to(pkg).as_posix()}"] = f"{n} lines > {ASSET_MAX}"
    fe, fe_found = check_frontend(pkg / "static", pkg)
    offenders.update(fe)
    structural += fe_found
    return offenders, errors, structural


def load_baseline(path: Path) -> tuple[set[str], dict[str, int]]:
    """(entries, counts): offender keys, and ``subprocess:`` key -> allowed call-site count."""
    if not path.exists():
        return set(), {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data.get("entries", [])), {k: int(v) for k, v in data.get("counts", {}).items()}


def write_baseline(path: Path, offenders: dict[str, str | int]) -> None:
    counts = {k: v for k, v in sorted(offenders.items()) if k.startswith(COUNTED)}
    entries = sorted(k for k in offenders if not k.startswith(("shell-true:", *COUNTED)))
    data = {"_comment": BASELINE_COMMENT, "entries": entries, "counts": counts}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def compare(offenders: dict[str, str | int], entries: set[str],
            counts: dict[str, int]) -> tuple[list[str], list[str], list[str]]:
    """Report lines (new, fixed, lower) of the scan against the baseline."""
    new, fixed, lower = [], [], []
    for key, val in sorted(offenders.items()):
        if key.startswith(COUNTED):
            base = counts.get(key, 0)
            label = next(v for k, v in COUNTED_LABELS.items() if key.startswith(k))
            if val > base:
                new.append(f"NEW {key} ({val} {label} > baseline {base})")
            elif val < base:
                lower.append(f"LOWER (update baseline count): {key} ({val} < {base})")
        elif key not in entries:
            new.append(f"NEW {key} ({val})")
    fixed += [f"FIXED (remove from baseline): {k}" for k in sorted(entries)
              if k not in offenders and not k.startswith("shell-true:")]
    fixed += [f"FIXED (remove from baseline): {k}" for k in sorted(counts) if k not in offenders]
    return new, fixed, lower


def _static_only(lines: list[str], n_dirs: int) -> int:
    """Report of a ``--static-dir`` run without ``--root``: src/pm is not scanned, no baseline."""
    for line in lines:
        print(line)
    if lines:
        print(f"archcheck: FAIL ({len(lines)} new)")
        return 1
    print(f"archcheck: OK ({n_dirs} static dir{'s' if n_dirs != 1 else ''})")
    return 0


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=None,
                    help="repo root (default: parent of tools/; not scanned when only --static-dir is given)")
    ap.add_argument("--baseline", type=Path, default=None, help="baseline JSON (default: tools/archcheck_baseline.json)")
    ap.add_argument("--strict", action="store_true", help="stale or lowered baseline entries also fail")
    ap.add_argument("--write-baseline", action="store_true", help="regenerate the baseline (maintainers only)")
    ap.add_argument("--static-dir", type=Path, action="append", default=[], metavar="DIR",
                    help="also scan a theme static tree, zero tolerance, no baseline (repeatable)")
    args = ap.parse_args(argv)
    static_new = [f"NEW {key} ({detail})" for d in args.static_dir for key, detail in check_static_dir(d)]
    if args.static_dir and args.root is None:
        return _static_only(static_new, len(args.static_dir))
    root = (args.root or here.parent).resolve()
    baseline_path = args.baseline or (root / "tools" / "archcheck_baseline.json")

    offenders, errors, structural = scan(root)

    for key, detail in errors:
        print(f"ERROR {key} ({detail})")
    structural_new = [f"NEW {key} ({detail})" for key, detail in structural] + static_new

    if args.write_baseline:
        write_baseline(baseline_path, offenders)  # structural findings are never written
        print(f"archcheck: wrote {len(offenders)} entries to {baseline_path}")
        for line in structural_new:
            print(line)
        return 1 if errors or structural_new else 0

    entries, counts = load_baseline(baseline_path)
    for key in sorted(k for k in entries | counts.keys() if k.startswith(NEVER_BASELINE)):
        errors.append((f"baseline:{key}", "structural findings are never baselinable"))
        print(f"ERROR baseline:{key} (structural findings are never baselinable)")
    entries = {k for k in entries if not k.startswith(NEVER_BASELINE)}
    counts = {k: v for k, v in counts.items() if not k.startswith(NEVER_BASELINE)}
    new, fixed, lower = compare(offenders, entries, counts)
    new += structural_new
    for line in (*new, *fixed, *lower):
        print(line)

    n_new = len(new) + len(errors)
    if n_new:
        print(f"archcheck: FAIL ({n_new} new)")
        return 1
    if (fixed or lower) and args.strict:
        print(f"archcheck: FAIL ({len(fixed) + len(lower)} stale baseline entries, --strict)")
        return 1
    print(f"archcheck: OK ({len((entries | counts.keys()) & offenders.keys())} baselined)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
