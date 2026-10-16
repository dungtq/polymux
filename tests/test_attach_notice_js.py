"""The composer's "File attached." notice ([data-cmp-flash], data-image-path) is cleared once the message is sent or the
last attachment is removed (static check of chat-send.js / composer-attach.js)."""

from pm.web.render import STATIC_DIR

JS = STATIC_DIR / "js"


def test_send_clears_attach_notice():
    js = (JS / "chat-send.js").read_text()
    before = js[js.index("htmx:beforeRequest"):js.index("htmx:beforeSwap")]
    assert "root.querySelector('[data-cmp-flash]')" in before
    assert "flash.querySelector('[data-image-path]')) flash.textContent = ''" in before


def test_removing_last_chip_clears_attach_notice():
    js = (JS / "composer-attach.js").read_text()
    remove = js[js.index("x.addEventListener('click'"):js.index("chip.appendChild(thumb)")]
    assert "!chipList(root).length" in remove and "s.querySelector('[data-image-path]')) s.textContent = ''" in remove
