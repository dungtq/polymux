"""Scroll chaining: independently scrolling regions (aside, embedded chat page, md viewer, sidebar, panes, dialogs)
must carry overscroll-behavior: contain so the page behind does not scroll at their top/bottom."""
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "src/pm/static/css/shell.css").read_text()
JS = (Path(__file__).resolve().parent.parent / "src/pm/static/js/drawer.js").read_text()


def _block():
    i = CSS.index("scroll chaining (2026-10-11)")
    return CSS[i:]


def test_regions_contain_overscroll():
    b = _block()
    assert "overscroll-behavior:contain" in b
    for sel in ("html.v5-embed", ".v5-chat", ".v5-side", ".v5-md", ".ts-body", "dialog.pmdlg", ".moresheet", ".slash-list"):
        assert sel in b.split("{")[0], sel


def test_drawer_blocks_wheel_on_its_chrome():
    assert "addEventListener('wheel'" in JS and "passive: false" in JS
    assert "innerHTML" not in JS
