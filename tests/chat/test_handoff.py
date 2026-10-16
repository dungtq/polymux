"""pm.chat.handoff: the Switch agent prompt (user and assistant text only, clipped oldest first)."""

from pm.chat import handoff
from pm.core.models import ChatItem


def _it(kind, text, seq=0):
    return ChatItem(seq=seq, rev=seq, uid=f"u{seq}", conv="c", kind=kind, turn="", ts=float(seq), text=text)


def _chat(*pairs):
    return [_it(k, t, i) for i, (k, t) in enumerate(pairs, 1)]


def test_keeps_user_and_text_only_in_order():
    items = _chat(("user", "fix the bug"), ("think", "SECRETTHOUGHT"), ("tool", "TOOLLINE"), ("text", "looking"), ("end", ""),
                  ("user", "and the docs"), ("text", "done"))
    out = handoff.build(items)
    assert out.startswith(handoff.PREAMBLE)
    assert out[len(handoff.PREAMBLE):].split("\n\n")[1:] == [
        "User: fix the bug", "Assistant: looking", "User: and the docs", "Assistant: done"]
    assert "SECRETTHOUGHT" not in out and "TOOLLINE" not in out


def test_empty_and_no_turns():
    assert handoff.build([]) == ""
    assert handoff.build(_chat(("tool", "ls"), ("think", "x"), ("end", ""), ("user", "  "), ("text", ""))) == ""


def test_nul_is_dropped():
    assert "\x00" not in handoff.build(_chat(("user", "a\x00b")))


def test_first_user_and_last_assistant_kept_whole_oldest_middle_dropped():
    first, last = "T" * 500, "L" * 500
    mid = [("user" if i % 2 else "text", f"m{i:03d} " + "x" * 200) for i in range(40)]
    items = _chat(("user", first), *mid, ("text", last))
    out = handoff.build(items, limit=3000)
    assert len(out) <= 3000
    assert "User: " + first in out and "Assistant: " + last in out
    assert handoff.MARKER in out and out.count(handoff.MARKER) == 1
    assert "m000" not in out and "m039" in out  # oldest go first, newest stay
    assert out.index("User: " + first) < out.index(handoff.MARKER) < out.index("m039") < out.index("Assistant: " + last)


def test_oldest_kept_item_is_clipped_not_dropped():  # newer items stay whole, the one that does not fit is cut
    items = _chat(("user", "task"), ("text", "a" * 900), ("user", "b" * 900), ("text", "end"))
    out = handoff.build(items, limit=1500)
    assert len(out) <= 1500 and "b" * 900 in out and "a" * 900 not in out
    assert "a" * 100 in out and "…" in out
    assert out.endswith("Assistant: end")


def test_huge_protected_items_are_capped_and_the_limit_holds():
    items = _chat(("user", "U" * 20000), ("text", "mid"), ("text", "A" * 20000))
    for limit in (8000, 1000, 300, 120, 50, 0):
        out = handoff.build(items, limit=limit)
        assert len(out) <= limit
    out = handoff.build(items)
    assert 0 < len(out) <= handoff.HANDOFF_MAX and "U" * 100 in out and "A" * 100 in out


def test_under_the_limit_nothing_is_dropped():
    out = handoff.build(_chat(("user", "a"), ("text", "b")), limit=handoff.HANDOFF_MAX)
    assert handoff.MARKER not in out and "…" not in out
