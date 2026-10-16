from pm.chat.text import clean_text as _clean


def test_clean_drops_banner_block_glyphs():
    out = _clean(" ▐▛███▜▌  Claude Code v2.1\n ▝▜█████▛▘  Sonnet 5.5\n  ▘▘ ▝▝  ~/dev/pm", 500)
    assert not any("▀" <= c <= "▟" for c in out)
    assert "Claude Code v2.1" in out and "~/dev/pm" in out


def test_drawer_remarks_open_row_after_list_swap():
    from pathlib import Path
    js = (Path(__file__).parent.parent / "src/pm/static/js/drawer.js").read_text()
    assert "MutationObserver" in js and "remark" in js and "attributeFilter" in js
