"""Static contract of the canvas frontend (strict CSP: no inline script; Playwright covers behaviour)."""
from pathlib import Path

from pm.web.render import TEMPLATE_DIR

ROOT = Path(__file__).resolve().parents[1] / "src/pm"


def test_canvas_wiring():
    js = (ROOT / "static/js/canvas.js").read_text()
    for needle in ("/canvas", "/save", "/restore", "/delete", "/preview", "csrf = 'both'", "409", "data-canvas-open"):
        assert needle in js
    assert "eval(" not in js
    chat = (TEMPLATE_DIR / "_chatmsg.html").read_text()  # shared buttons, used by the chat items
    assert "data-canvas-open" in chat and "data-raw" in chat
    assert "canvas_btn" in (TEMPLATE_DIR / "_ci_macros.html").read_text()  # every reply bubble
    pane = (TEMPLATE_DIR / "_mdpane.html").read_text()
    for sel in ("cv-edw", "cv-pv", "data-cv-save", "data-cv-send", "data-cv-reload"):
        assert sel in pane
    assert "<script" not in pane
    assert "canvas.js" in (TEMPLATE_DIR / "agent.html").read_text()
