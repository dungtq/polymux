"""Route modules must not import each other (shared code lives in domain modules or pm.web.*)."""

import ast
from pathlib import Path

ROUTES = Path(__file__).resolve().parent.parent / "src" / "pm" / "web" / "routes"


def _routes_imported(path: Path) -> list[str]:
    bad = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""] + [f"{node.module or ''}.{a.name}" for a in node.names]
        else:
            continue
        bad += [n for n in names if n.startswith("pm.web.routes.") and n != "pm.web.routes.slash_output"]
    return bad


def test_route_modules_do_not_import_each_other():
    files = [f for f in ROUTES.glob("*.py") if f.name != "__init__.py"]
    found = {f.name: _routes_imported(f) for f in files}
    assert not {k: v for k, v in found.items() if v}
