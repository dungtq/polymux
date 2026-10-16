"""dt isolation (docs/ARCHITECTURE.md, CONDUCT.md V7-ISO-3): core never imports pm.web.dt, and pm.web.dt never imports
pm.web.routes.* or a private ``_name`` from another package."""

import ast
from pathlib import Path

import pm

SRC = Path(pm.__file__).parent
DT = SRC / "web" / "dt"


def _imports(path: Path):
    """(module, names) for every absolute import in ``path``."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, []
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module, [a.name for a in node.names]


def test_core_never_imports_dt():
    bad = [f"{p.relative_to(SRC)}: {mod}" for p in SRC.rglob("*.py") if DT not in p.parents
           for mod, names in _imports(p)
           if mod == "pm.web.dt" or mod.startswith("pm.web.dt.") or (mod == "pm.web" and "dt" in names)]
    assert bad == []


def test_dt_never_imports_routes_or_private_names():
    bad = []
    for p in DT.rglob("*.py"):
        for mod, names in _imports(p):
            if mod == "pm.web.routes" or mod.startswith("pm.web.routes."):
                bad.append(f"{p.name}: {mod}")
            if mod.startswith("pm.") and not mod.startswith("pm.web.dt"):
                bad += [f"{p.name}: {mod}.{n}" for n in names if n.startswith("_")]
    assert bad == []
