from pathlib import Path

from helpers import search_client

ROOT = Path(__file__).resolve().parents[1] / "src/pm"


def test_palette_markup_and_assets(fake_tmux, tmp_path):
    c, s = search_client(fake_tmux, tmp_path)
    c.post("/login", data={"token": s.token_file.read_text().strip()}, follow_redirects=False)
    html = c.get("/audit").text
    assert 'id="palette"' in html and "/static/js/palette.js" in html and "/static/css/palette.css" in html
    assert 'id="gsearch"' in html  # navbar search untouched
    assert c.get("/static/js/palette.js").status_code == 200


def test_palette_contract():
    js = (ROOT / "static/js/palette.js").read_text()
    assert "e.shiftKey" in js and "e.preventDefault()" in js and "e.metaKey || e.ctrlKey" in js
    assert "fetch(" not in js and "XMLHttpRequest" not in js and "method" not in js  # never POSTs itself
    for needle in ('.tb-launch', '.tb-stop', '[data-qchat-open]', '[data-theme-toggle]', "'/settings'", "'/audit'"):
        assert needle in js
    srch = (ROOT / "static/js/search.js").read_text()
    assert 'e.key !== "/"' in srch and 'k !== "k" && k !== "p"' in srch  # / and Cmd/Ctrl+K unchanged
