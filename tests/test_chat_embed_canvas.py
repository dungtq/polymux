"""Canvas button on a reply bubble (chat items): live in the full page, absent in the embed drawer."""
from helpers import chat_env as env  # noqa: F401  (fixture)
from helpers import login

from pm.core.models import ChatItem


def _reply(monkeypatch):
    it = ChatItem(seq=1, rev=1, uid="u:0", conv="c", kind="text", turn="u", ts=1.0, text="hello", name="opus-5-5")
    monkeypatch.setattr("pm.chat.items.list_items", lambda *a, **k: [it])


def test_full_page_has_live_canvas_button(env, monkeypatch):
    s, c = env
    login(s, c)
    _reply(monkeypatch)
    t = c.get("/a/claude-12/items").text
    assert "data-canvas-open" in t and "cv-off" not in t


def test_embed_has_no_canvas_button(env, monkeypatch):
    s, c = env
    login(s, c)
    _reply(monkeypatch)
    t = c.get("/a/claude-12/items?embed=1").text
    assert "hello" in t
    assert "data-canvas-open" not in t and "cv-open" not in t and "cv-off" not in t


def test_strip_keeps_embed_flag(env):
    s, c = env
    login(s, c)
    t = c.get("/a/claude-12/chat", headers={"HX-Current-URL": "https://pm.example.test/a/claude-12?embed=1#x"}).text
    assert " data-embed" in t
