"""POST /a/{id}/ask: free-text answer / note into an open AskUserQuestion form (keys worked out from the pane)."""
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from helpers import audit_text, fake, mk_client, post, snap_with  # noqa: F401  (fake is a fixture)

from pm.agents import message as msg
from pm.chat.ask import find_ask
from pm.core.models import AgentKind, Tier
from pm.tmux import ctl as tmuxctl
from pm.web.routes import ask as routes_ask

RULE = "─" * 80
FORM = ["❯ prompt", RULE, "←  ☐ A  ☐ B  ✔ Submit  →", "Which option?", "❯ 1. Option 1", "     First.",
        "  2. Option 2", "     Second.", "  3. Type something.", RULE, "  4. Chat about this",
        "Enter to select · Tab/Arrow keys to navigate · Esc to cancel"]


def client(fake, monkeypatch, pane, ceiling=Tier.MESSAGE):
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])
    monkeypatch.setattr(tmuxctl, "capture_pane", lambda s, p, lines=200: "\n".join(pane))
    monkeypatch.setattr(msg.time, "sleep", lambda s: None)
    app, c, sess, tok, st = mk_client(st, ceiling=ceiling)
    app.include_router(routes_ask.router)
    return c, tok, st, calls


def keys_sent(calls):
    return [a[-1] for a in calls if a[:1] == ["send-keys"] and "-l" not in a]


def typed(calls):
    return [a[-1] for a in calls if a[:2] == ["send-keys", "-l"]]


@pytest.mark.parametrize("fixture,index,keys", [
    ("codex_folder_trust.txt", 0, ["1", "Enter"]),
    ("codex_folder_trust.txt", 1, ["2"]),
    ("codex_hooks_review.txt", 0, ["1"]),
    ("codex_hooks_review.txt", 1, ["2", "Enter"]),
    ("codex_hooks_review.txt", 2, ["3"]),
])
def test_codex_startup_choice_delivery(fake, monkeypatch, fixture, index, keys):
    st, calls, agent = fake
    agent = replace(agent, id="codex-1", kind=AgentKind.CODEX)
    pane = (Path(__file__).resolve().parent.parent / "fixtures" / "chat" / fixture).read_text().splitlines()
    c, tok, st, calls = client((st, calls, agent), monkeypatch, pane)
    ask, _ = find_ask(pane)
    r = post(c, tok, "/a/codex-1/key", key=ask.options[index].key)
    assert r.status_code == 200
    assert keys_sent(calls) == keys and not typed(calls)
    log = audit_text(st)
    assert "agent_key" in log and "Trust and continue" not in log and "Hooks need review" not in log


def test_other_answer_presses_type_something_then_text(fake, monkeypatch):
    c, tok, st, calls = client(fake, monkeypatch, FORM)
    secret = "my-free-text-answer-42"
    r = post(c, tok, "/a/claude-1/ask", text=secret)
    assert r.status_code == 200 and "Answer sent" in r.text
    assert keys_sent(calls)[:1] == ["3"] and secret in typed(calls) and keys_sent(calls)[-1] == "Enter"
    log = audit_text(st)
    assert "agent_ask" in log
    assert secret not in log and "Option 1" not in log and hashlib.sha256(secret.encode()).hexdigest() in log


def test_note_moves_cursor_then_n(fake, monkeypatch):
    c, tok, st, calls = client(fake, monkeypatch, FORM)
    assert post(c, tok, "/a/claude-1/ask", text="a note", note="2").status_code == 200
    assert keys_sent(calls)[:2] == ["Down", "n"] and "a note" in typed(calls)


def test_refused_without_a_form_in_the_pane(fake, monkeypatch):
    c, tok, st, calls = client(fake, monkeypatch, ["$ ls", "file", "❯ "])
    r = post(c, tok, "/a/claude-1/ask", text="hello")
    assert r.status_code == 409 and not typed(calls) and not keys_sent(calls)
    assert post(c, tok, "/a/claude-1/ask", text="x", note="9").status_code == 409  # no option 9


def test_bad_input_and_guard(fake, monkeypatch):
    c, tok, st, calls = client(fake, monkeypatch, FORM)
    assert post(c, tok, "/a/claude-1/ask", text="x", note="12").status_code == 400
    assert post(c, tok, "/a/claude-1/ask", text="x", note="-1").status_code == 400
    assert post(c, tok, "/a/claude-1/ask", text="two\nlines").status_code == 400
    assert post(c, tok, "/a/claude-1/ask", text="  ").status_code == 400
    assert post(c, tok, "/a/claude-1/ask", csrf=False, text="x").status_code == 403
    assert post(c, tok, "/a/claude-1/ask", origin="https://evil.example", text="x").status_code == 403
    assert not typed(calls)
    c2, tok2, _, calls2 = client(fake, monkeypatch, FORM, ceiling=Tier.SAFE)
    assert post(c2, tok2, "/a/claude-1/ask", text="x").status_code == 403 and not calls2


REAL = Path(__file__).resolve().parent.parent / "fixtures" / "askq"  # real Claude Code captures


def test_real_captures_note_and_other(fake, monkeypatch):
    pv = (REAL / "cap_q1_preview.txt").read_text().splitlines()
    c, tok, st, calls = client(fake, monkeypatch, pv)
    assert post(c, tok, "/a/claude-1/ask", text="note on list", note="2").status_code == 200
    assert keys_sent(calls)[:2] == ["2", "n"] and "note on list" in typed(calls)
    calls.clear()
    assert post(c, tok, "/a/claude-1/ask", text="free").status_code == 409  # no "Type something" here
    assert not typed(calls)
    c, tok, st, calls = client(fake, monkeypatch, (REAL / "cap_q2.txt").read_text().splitlines())
    assert post(c, tok, "/a/claude-1/ask", text="Solarized").status_code == 200
    assert keys_sent(calls)[:1] == ["3"] and "Solarized" in typed(calls)
