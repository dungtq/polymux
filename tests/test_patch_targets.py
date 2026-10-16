"""String patch targets in tests ("pm.x.y" for monkeypatch.setattr / mock.patch / sys.modules) must
resolve, so a module move cannot leave a test patching a name that no longer exists."""
import ast
import importlib
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
PATCHERS = {"setattr", "delattr", "patch"}


def _targets():
    """(file:line, target, may_be_missing) for every "pm.*" patch string under tests/."""
    for path in sorted(TESTS.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            arg = None
            if name in PATCHERS:
                arg = node.args[0]
            elif name in ("setitem", "delitem") and len(node.args) > 1 and ast.unparse(node.args[0]) == "sys.modules":
                arg = node.args[1]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("pm."):
                lenient = any(k.arg == "raising" and isinstance(k.value, ast.Constant) and k.value.value is False
                              for k in node.keywords)
                yield f"{path.relative_to(TESTS)}:{node.lineno}", arg.value, lenient


def _resolve(target: str, lenient: bool) -> None:
    parts = target.split(".")
    obj, k = None, len(parts)
    while k:
        try:
            obj = importlib.import_module(".".join(parts[:k]))
            break
        except ModuleNotFoundError:
            k -= 1
    assert obj is not None, f"no importable prefix in {target}"
    for i, attr in enumerate(parts[k:], start=k):
        if not hasattr(obj, attr) and lenient and i == len(parts) - 1:
            return
        obj = getattr(obj, attr)


TARGETS = list(_targets())


def test_there_are_targets():
    assert len(TARGETS) >= 10


@pytest.mark.parametrize(("where", "target", "lenient"), TARGETS, ids=[t[0] for t in TARGETS])
def test_patch_target_resolves(where, target, lenient):
    _resolve(target, lenient)
