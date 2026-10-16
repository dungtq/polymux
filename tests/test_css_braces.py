"""Every stylesheet has balanced braces: an unclosed @media silently scopes the rest of the file
(qchat.css had one from 2026-10-11 to 2026-10-12, so the quick-chat menus were phone-only)."""
import re
from pathlib import Path

import pytest

CSS = sorted(p for p in (Path(__file__).resolve().parents[1] / "src/pm/static").rglob("*.css") if "vendor" not in p.parts)


def _strip(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', text)


@pytest.mark.parametrize("path", CSS, ids=lambda p: p.name)
def test_braces_balanced(path):
    depth = 0
    for n, line in enumerate(_strip(path.read_text()).splitlines(), 1):
        for ch in line:
            depth += ch == "{"
            depth -= ch == "}"
            assert depth >= 0, f"{path.name}:{n}: '}}' without '{{'"
    assert depth == 0, f"{path.name}: {depth} unclosed '{{' at end of file"
