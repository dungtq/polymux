"""One shared row-hover rule (docs/UI-RULES.md "Row states"): every interactive list/table row gets --hover."""
import re
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "src/pm/static/css/rows.css"

ROWS = [
    "tbody tr:hover td",
    ".ntfy-row>summary:hover",
    ".bl-group>summary:hover",
    ".dt>.dt-row:hover",
    "a.lrow:hover",
    ".lrow.srow:hover",
    ".lrow.pl-row:hover",
    ".lrow.usg-row:hover",
    '.lrow[class*="arow-"]:hover',
    ".tx-row:hover",
    ".bl-item:hover",
]
NOT_ROWS = (".lhead", ".hrow", ".dt-head", ".nhead", ".srv-row", "thead")


def _rules():
    text = CSS.read_text()
    block = text[text.index("row hover: ONE rule"):]
    block = block[block.index("*/") + 2:]
    return re.findall(r"([^{}]+)\{([^}]*)\}", block)


def test_shared_rule_covers_row_classes():
    hover = {s.strip() for sel, body in _rules() if "background:var(--hover)" in body for s in sel.split(",")}
    for row in ROWS:
        assert row in hover, row


def test_selected_rows_keep_accent_soft():
    soft = " ".join(sel for sel, body in _rules() if "background:var(--accent-soft)" in body)
    for sel in ("a.lrow.v5-active:hover", ".lrow.ag-sel:hover", ".tx-row.v5-active:hover"):
        assert sel in soft


def test_header_and_static_rows_not_hovered():
    sels = " ".join(sel for sel, body in _rules() if "var(--hover)" in body and "background" in body)
    for bad in NOT_ROWS:
        assert bad not in sels, bad


def test_only_tokens():
    assert not re.findall(r"#[0-9a-fA-F]{3,6}", "".join(b for _, b in _rules()))
