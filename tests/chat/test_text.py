"""chat.text.parse_peers: tmux-agents request / reply / notice envelopes become peer messages."""
from pm.chat.text import parse_peers

TAIL = ("When you have finished, send your result back to the requester by running this\n"
        "shell command (it is the only way they will see it; keep the summary concise,\n"
        "and put long output in a file and mention its path):\n"
        '  tmux-agents reply 045102-7925 "<what you did and the result>"\n'
        "If you fail or are blocked, reply the same way with --status failed|blocked and say why.")
REQ = "[tmux-agents request id=045102-7925 from pane %1588 (pm id=claude-a84751)]\naccounts is merged.\nRebase.\n\n---\n" + TAIL + "\n"
NOTICE = "agent finished c21 '#267 import'; rebase before merging\n\n[tmux-agents id=052238-8afb from pane %1593 (pm id=claude-21edb1), no reply expected]"


def one(text):
    got = parse_peers(text)
    assert got and len(got) == 1
    return got[0]


def test_request_shows_sender_id_and_body_only():
    p = one(REQ)
    assert (p.role, p.sender, p.kind, p.body) == ("request", "claude-a84751", "claude", "accounts is merged.\nRebase.")


def test_request_inside_pasted_content_wrapper():
    assert one('<pasted_content id="x">\n' + REQ + "</pasted_content id=\"x\">").sender == "claude-a84751"


def test_reply_and_queue_note():
    p = one("[tmux-agents reply id=1-2 from devin-44fd82 status=done]\nShipped.")
    assert (p.role, p.sender, p.kind, p.status, p.body) == ("reply", "devin-44fd82", "devin", "done", "Shipped.")
    long = one("[tmux-agents reply id=1-2 from codex-37cdc3 status=failed] Result too long to inline (9 chars)")
    assert long.kind == "codex" and long.status == "failed"


def test_notice_with_trailing_inbox_note():
    p = one("heads up\n\n[tmux-agents id=1-2 from pane %3 (name=ops, pm id=cursor-agent-a42089), no reply expected]"
            "\n[2 older message(s) for you are in your inbox: run `tmux-agents inbox`]")
    assert (p.role, p.sender, p.kind, p.body) == ("notice", "cursor-agent-a42089", "cursor-agent", "heads up")


def test_several_queued_messages_in_one_user_message():
    got = parse_peers(NOTICE + "\n\n" + REQ + "\n[1 older message(s) for you are in your inbox: run `tmux-agents inbox`]\n"
                      "[tmux-agents reply id=9-9 from devin-44fd82 status=done]\nShipped.")
    assert [(p.role, p.sender) for p in got] == [("notice", "claude-21edb1"), ("request", "claude-a84751"),
                                                 ("reply", "devin-44fd82")]
    assert got[0].body == "agent finished c21 '#267 import'; rebase before merging"
    assert got[1].body == "accounts is merged.\nRebase." and got[2].body == "Shipped."


def test_notice_of_an_older_tool_version_has_no_sender():
    p = one("do this\n\n[tmux-agents id=220608-1f58, no reply expected]")
    assert (p.sender, p.kind, p.body) == ("agent", "other", "do this")


def test_sender_without_id_falls_back_to_name_then_pane():
    assert one("x\n\n[tmux-agents id=1-2 from pane %3 (name=ops), no reply expected]").sender == "ops"
    assert one("x\n\n[tmux-agents id=1-2 from pane %3, no reply expected]").kind == "other"


def test_ordinary_or_mixed_messages_are_not_peer_messages():
    assert parse_peers("fix the bug") is None
    assert parse_peers("use [tmux-agents request ...] later") is None
    assert parse_peers("") is None
    assert parse_peers(REQ + "\nand then some text of mine") is None  # not all of it is an envelope


def test_long_unrelated_text_with_the_marker_parses_fast():
    import time

    t0 = time.perf_counter()
    assert parse_peers("[tmux-agents x] " + "\n" * 60000 + "tail") is None
    assert parse_peers("note\n" + "\n" * 60000 + "[tmux-agents id=1-2 from pane %1, no reply expected]") is not None
    assert time.perf_counter() - t0 < 1.0
