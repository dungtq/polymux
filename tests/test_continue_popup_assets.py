"""The Continue popup: assets are linked and use no native popups."""
import re
from pathlib import Path

from pm.web.render import TEMPLATE_DIR

ROOT = Path(__file__).resolve().parents[1] / "src/pm"


def test_continue_popup_assets_linked():
    assert "/static/css/chat-continue.css" in (TEMPLATE_DIR / "base.html").read_text()
    for t in ("agent.html", "builder.html"):
        assert "/static/js/continuepop.js" in (TEMPLATE_DIR / t).read_text()
    assert (ROOT / "static/css/chat-continue.css").exists()


def test_continue_popup_uses_the_shared_modal():
    js = (ROOT / "static/js/continuepop.js").read_text()
    assert "pmConfirm" in js and "collect" in js
    assert not re.search(r"(?<![\w.])(alert|confirm|prompt)\(|window\.open", js)
    assert "newchat.js" not in js and "form.cm-cont" not in (ROOT / "static/js/newchat.js").read_text()


def test_continue_popup_has_no_where_choice():
    """The new chat always opens in its own tmux window: no Where radios, no where=pane post."""
    js = (ROOT / "static/js/continuepop.js").read_text()
    assert "where" not in js.replace("where you left off", "")
    assert "split" not in js and "'pane'" not in js
    assert "where it opens" not in (TEMPLATE_DIR / "_chatmsg.html").read_text()
