from pathlib import Path

import pytest

from pm.chat import live as chat
from pm.chat.queued import find_queued
from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, AgentState
from pm.security.mask import render_pane

HINT = "  ctrl+x ctrl+s to send now"


def test_single_line():
    assert find_queued(["noise", "❯ fix the tests", HINT, "", "* Working… (3s)"]) == "fix the tests"


def test_multi_line_with_quotes_and_pipes():
    lines = ['❯ "Do you want it? /ntfy/panel?scope=inbox|project and ... have no',
             '  caller left." -- build a page, put it before Audit',
             "  chat aside", HINT, "", "* Canoodling… (3m 35s)"]
    assert find_queued(lines) == ('"Do you want it? /ntfy/panel?scope=inbox|project and ... have no '
                                  'caller left." -- build a page, put it before Audit chat aside')


def test_no_hint():
    assert find_queued(["❯ typed text", "", "* Working…"]) == ""


def test_plain_input_box():
    assert find_queued(["╭──╮", "│ ❯ │", "╰──╯", "  ? for shortcuts"]) == ""
    assert find_queued(["❯ ", ""]) == ""


def test_hint_without_prompt_block():
    assert find_queued(["", HINT]) == ""
    assert find_queued(["❯ old", "", "some text", HINT]) == ""


def test_hint_case_insensitive_and_old_hint_ignored():
    assert find_queued(["❯ hi", "CTRL+X CTRL+S to send now"]) == "hi"
    assert find_queued(["❯ hi", HINT] + ["x"] * 45) == ""


def test_secret_masked_via_render_pane():
    tok = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    plain = render_pane(f"❯ use {tok} now\n{HINT}\n")
    out = find_queued(plain.splitlines())
    assert out.startswith("use ") and tok not in out


def _agent(kind):
    return Agent(id="a-1", kind=kind, pane_id="%1", session_id="$1", pid=1,
                 cwd="/nonexistent/x", state=AgentState.RUNNING, session_uuid=None)


@pytest.mark.parametrize("kind,want", [(AgentKind.CLAUDE, "later please"), (AgentKind.CODEX, "")])
def test_chat_view_only_for_claude(kind, want, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    pane = f"❯ later please\n{HINT}\n\n* Working…\n"
    view = chat.chat_view(Settings(), _agent(kind), pane)
    assert view.queued == want
