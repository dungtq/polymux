"""Agents tables 'Last message' cell: the newer of the user's last message and the latest ntfy item."""

import json
import os

import pytest

from pm.chat import live as chat
from pm.core.models import Agent, AgentKind, AgentState, NtfyItem
from pm.web.render import env

UUID = "11111111-2222-3333-4444-555555555555"
CWD = "/home/me/dev/my.proj_x"


def _agent():
    return Agent(id="claude-1", kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=1,
                 cwd=CWD, state=AgentState.RUNNING, session_uuid=UUID)


def _user(text, ts):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}}


def _result(i):
    return {"type": "user", "timestamp": "2026-01-02T04:00:00.000Z",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "x" * 600}]}}


def _asst(i):
    return {"type": "assistant", "timestamp": "2026-01-02T04:00:00.000Z",
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {}}]}}


@pytest.fixture(autouse=True)
def _fresh_cache():
    chat._TASK_CACHE.clear()


@pytest.fixture
def tdir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    d = tmp_path / ".claude" / "projects" / chat.encode_cwd(CWD)
    d.mkdir(parents=True)
    return d


def _long_turn(path, extra=0):  # extra: bytes beyond the tail window
    """An old user prompt followed by far more than TAIL_BYTES of tool_use / tool_result pairs."""
    n = (chat.TAIL_BYTES + extra) // 900 + 50
    lines = [json.dumps(_user("fix the list", "2026-01-02T03:00:00.000Z"))]
    for i in range(n):
        lines += [json.dumps(_asst(i)), json.dumps(_result(i))]
    path.write_text("\n".join(lines) + "\n")


def test_last_user_found_beyond_tail(tdir):
    p = tdir / f"{UUID}.jsonl"
    _long_turn(p)
    assert p.stat().st_size > chat.TAIL_BYTES
    assert chat.read_transcript_info(p).task == ""  # the tail alone has nothing: the reported bug
    assert chat.last_user_task(_agent()) == "fix the list"
    assert chat.last_user_task_ts(_agent()) > 0


def test_last_user_found_beyond_first_deep_window(tdir):
    p = tdir / f"{UUID}.jsonl"
    _long_turn(p, extra=chat._DEEP_WINDOWS[0] * 2)
    assert p.stat().st_size > chat._DEEP_WINDOWS[0]
    assert chat.last_user_task(_agent()) == "fix the list"


def test_last_user_kept_when_tail_has_none_after_write(tdir):
    p = tdir / f"{UUID}.jsonl"
    _long_turn(p)
    assert chat.last_user_task(_agent()) == "fix the list"
    with open(p, "a") as f:  # transcript grows: mtime changes, still no user text in the tail
        f.write(json.dumps(_asst(9999)) + "\n")
    os.utime(p, (1, p.stat().st_mtime + 5))
    assert chat.last_user_task(_agent()) == "fix the list"
    assert chat.last_user_task_ts(_agent()) > 0


def test_last_user_newer_in_tail_wins(tdir):
    p = tdir / f"{UUID}.jsonl"
    _long_turn(p)
    assert chat.last_user_task(_agent()) == "fix the list"
    with open(p, "a") as f:
        f.write(json.dumps(_user("now the footer", "2026-01-02T05:00:00.000Z")) + "\n")
    os.utime(p, (1, p.stat().st_mtime + 5))
    assert chat.last_user_task(_agent()) == "now the footer"


def test_last_user_none_in_file(tdir):
    p = tdir / f"{UUID}.jsonl"
    p.write_text("\n".join(json.dumps(_asst(i)) for i in range(5)) + "\n")
    assert chat.last_user_task(_agent()) == ""
    assert chat.last_user_task_ts(_agent()) == 0.0


def _cell(nt, user, uts):
    t = env.from_string('{% from "_macros.html" import msg_cell, msg_title %}{{ msg_cell(nt, u, ts) }}|{{ msg_title(nt, u, ts) }}')
    return t.render(nt=nt, u=user, ts=uts).split("|")


def _nt(t):
    return NtfyItem(id="n", time=t, title="claude-1 - task", message="all done")


def test_cell_user_newer_wins():
    cell, title = _cell(_nt(100.0), "fix the list", 200.0)
    assert ">user</span> fix the list" in cell and "ntfy" not in cell
    assert title == "fix the list"


def test_cell_ntfy_newer_wins():
    cell, title = _cell(_nt(300.0), "fix the list", 200.0)
    assert ">ntfy</span> all done" in cell and "user</span>" not in cell
    assert title == "all done"


def test_cell_unknown_user_ts_keeps_ntfy():
    cell, title = _cell(_nt(300.0), "fix the list", 0.0)
    assert ">ntfy</span> all done" in cell and title == "all done"


def test_cell_user_only_and_ntfy_only_and_empty():
    assert ">user</span> fix the list" in _cell(None, "fix the list", 5.0)[0]
    assert ">user</span> fix the list" in _cell(None, "fix the list", 0.0)[0]
    assert _cell(None, "", 0.0) == ["", ""]


def test_ended_rows_unchanged():
    t = env.from_string('{% from "_macros.html" import msg_cell, msg_title %}{{ msg_cell(nt, none) }}|{{ msg_title(nt, none) }}')
    cell, title = t.render(nt=_nt(1.0)).split("|")
    assert ">ntfy</span> all done" in cell and title == "all done"
    assert t.render(nt=None) == "|"


def _live_cell(nt, user, uts, a):
    t = env.from_string('{% from "_macros.html" import msg_cell, msg_title %}{{ msg_cell(nt, u, ts, a) }}|{{ msg_title(nt, u, ts, a) }}')
    return t.render(nt=nt, u=user, ts=uts, a=a).split("|")


def test_new_session_label_without_user_message_or_ntfy(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"model": "claude-sonnet-5-5", "effortLevel": "high"}))
    cell, title = _live_cell(None, "", 0.0, _agent())
    assert "New sonnet-5-5 session with effort high" in title
    assert 'class="muted"' in cell and 'title="New sonnet-5-5 session with effort high"' in cell
    assert "kpill" not in cell


def test_new_session_label_hidden_when_a_message_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _live_cell(_nt(1.0), "", 0.0, _agent())[1] == "all done"
    cell, title = _live_cell(None, "fix the list", 5.0, _agent())
    assert ">user</span> fix the list" in cell and title == "fix the list"
    assert "New" not in cell + title


def test_new_session_label_unknown_model_and_other_kinds(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _live_cell(None, "", 0.0, _agent())[1] == "New session"
    codex = Agent(id="codex-1", kind=AgentKind.CODEX, pane_id="%2", session_id="$1", pid=2, cwd=CWD,
                  state=AgentState.RUNNING)
    assert _live_cell(None, "", 0.0, codex) == ["", ""]


def test_ended_rows_get_no_new_session_label():
    t = env.from_string('{% from "_macros.html" import msg_cell, msg_title %}{{ msg_cell(nt, none) }}|{{ msg_title(nt, none) }}')
    assert t.render(nt=None) == "|"
