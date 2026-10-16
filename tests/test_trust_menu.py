"""Unnumbered cursor menus (Claude Code's "trust this folder") are offered as pickable options."""
from pm.agents import message as msg
from pm.chat import live as chat

PANE = """\
 Accessing workspace:
 /home/me/dev/zz-flowtest
 Quick safety check: Is this a project you created or one you trust? (Like your own code, a
 well-known open source project, or work from your team). If not, take a moment to review what's
 in this folder first.
 Claude Code'll be able to read, edit, and execute files here.
 Security guide
 ❯ No, exit
   Yes, I trust this folder
 Enter to confirm · Esc to cancel
""".splitlines()


def test_trust_menu_options():
    ask, _ = chat.find_ask(PANE)
    assert ask and ask.kind == "permission"
    assert [(o.label, o.key) for o in ask.options] == [
        ("No, exit", "Enter"), ("Yes, I trust this folder", "Down Enter")]


def test_plain_enter_prompt_unchanged():
    ask, _ = chat.find_ask(["Setup done", "Press Enter to continue"])
    assert ask and [o.key for o in ask.options] == ["Enter"]


def test_key_seq():
    assert msg.key_seq("Down Enter") == ["Down", "Enter"]
    assert msg.key_seq("Down rm") is None
    assert msg.key_seq("") is None
