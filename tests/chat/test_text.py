"""chat.text.parse_peer: tmux-agents request / reply / notice envelopes become a peer message."""
from pm.chat.text import parse_peer

REQ = ("[tmux-agents request id=045102-7925 from pane %1588 (pm id=claude-a84751)]\naccounts is merged.\nRebase.\n\n---\n"
       "When you have finished, send your result back to the requester by running this\n"
       "shell command (it is the only way they will see it; keep the summary concise,\n"
       "and put long output in a file and mention its path):\n"
       '  tmux-agents reply 045102-7925 "<what you did and the result>"\n'
       "If you fail or are blocked, reply the same way with --status failed|blocked and say why.\n")


def test_request_shows_sender_id_and_body_only():
    p = parse_peer(REQ)
    assert (p.role, p.sender, p.kind, p.body) == ("request", "claude-a84751", "claude", "accounts is merged.\nRebase.")


def test_request_inside_pasted_content_wrapper():
    p = parse_peer('<pasted_content id="x">\n' + REQ + "</pasted_content id=\"x\">")
    assert p and p.sender == "claude-a84751"


def test_reply_and_queue_note():
    p = parse_peer("[tmux-agents reply id=1-2 from devin-44fd82 status=done]\nShipped.")
    assert (p.role, p.sender, p.kind, p.status, p.body) == ("reply", "devin-44fd82", "devin", "done", "Shipped.")
    long = parse_peer("[tmux-agents reply id=1-2 from codex-37cdc3 status=failed] Result too long to inline (9 chars)")
    assert long.kind == "codex" and long.status == "failed"


def test_notice_with_trailing_inbox_note():
    p = parse_peer("heads up\n\n[tmux-agents id=1-2 from pane %3 (name=ops, pm id=cursor-agent-a42089), no reply expected]"
                   "\n[2 older message(s) for you are in your inbox: run `tmux-agents inbox`]")
    assert (p.role, p.sender, p.kind, p.body) == ("notice", "cursor-agent-a42089", "cursor-agent", "heads up")


def test_sender_without_id_falls_back_to_name_then_pane():
    assert parse_peer("x\n\n[tmux-agents id=1-2 from pane %3 (name=ops), no reply expected]").sender == "ops"
    assert parse_peer("x\n\n[tmux-agents id=1-2 from pane %3, no reply expected]").kind == "other"


def test_ordinary_messages_are_not_peer_messages():
    assert parse_peer("fix the bug") is None
    assert parse_peer("use [tmux-agents request ...] later") is None
    assert parse_peer("") is None
