"""Quick chat popup scales with large screens (MBP 14", 27") instead of a phone-sized 420x500 box."""
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "src/pm/static/css/nav.css"


def test_qchat_popup_scales_with_viewport():
    rules = [l for l in CSS.read_text().splitlines() if l.startswith(".qchat{")]
    assert rules
    for r in rules:
        assert "width:min(420px," not in r and "height:min(500px," not in r
        assert "vw" in r.split("width:", 1)[1].split(";", 1)[0]
        assert "dvh" in r.split("height:", 1)[1].split(";", 1)[0]
