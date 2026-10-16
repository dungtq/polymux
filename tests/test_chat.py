import builtins
import json
from pathlib import Path

import pytest

from pm.chat import live as chat
from pm.core.models import Agent, AgentKind, AgentState

FIX = Path(__file__).parent / "fixtures" / "chat"
UUID = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-2222-3333-4444-555555555555"
CWD = "/home/me/dev/my.proj_x"
TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def agent(kind=AgentKind.CLAUDE, uuid=UUID, state=AgentState.IDLE):
    return Agent(id="claude-1", kind=kind, pane_id="%1", session_id="$1", pid=1,
                 cwd=CWD, state=state, session_uuid=uuid)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    d = tmp_path / ".claude" / "projects" / chat.encode_cwd(CWD)
    d.mkdir(parents=True)
    return d


def asst(text, ts="2026-01-02T03:04:05.000Z", **kw):
    return {"type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}, **kw}


def user(content):
    return {"type": "user", "message": {"role": "user", "content": content}}


def write(path, entries, raw_extra=()):
    lines = [json.dumps(e) for e in entries] + list(raw_extra)
    path.write_text("\n".join(lines) + "\n")


def view(settings, ag=None, pane=""):
    return chat.chat_view(settings, ag or agent(), pane)


def test_encode_cwd():
    assert chat.encode_cwd("/home/me/dev/pm") == "-home-me-dev-pm"
    assert chat.encode_cwd("/a/b.c/d_e") == "-a-b-c-d-e"


def test_assistant_last(home, settings):
    write(home / f"{UUID}.jsonl", [user("hi"), asst("hello there")])
    v = view(settings)
    assert (v.source, v.last_role, v.last_text) == ("transcript", "assistant", "hello there")
    assert v.last_ts > 0


def test_user_last(home, settings):
    write(home / f"{UUID}.jsonl", [asst("old"), user("please do X")])
    v = view(settings)
    assert (v.last_role, v.last_text) == ("user", "please do X")


def test_last_user_task(home):
    write(home / f"{UUID}.jsonl", [user("first"), asst("ok"), user("fix the list\nmore"), asst("done")])
    assert chat.last_user_task(agent()) == "fix the list"
    assert chat.last_user_task(agent(kind=AgentKind.CODEX)) == ""
    assert chat.last_user_task(agent(uuid=OTHER)) == ""


def test_tool_blocks_ignored(home, settings):
    tool_res = user([{"type": "tool_result", "tool_use_id": "t", "content": "SECRET OUT"}])
    tool_use = {"type": "assistant", "message": {"content": [
        {"type": "thinking", "thinking": "hmm"}, {"type": "tool_use", "name": "Bash", "input": {}}]}}
    side = asst("sidechain text", isSidechain=True)
    meta = user("meta text")
    meta["isMeta"] = True
    write(home / f"{UUID}.jsonl", [asst("real answer"), tool_use, tool_res, side, meta])
    v = view(settings)
    assert (v.last_role, v.last_text) == ("assistant", "real answer")


def test_partial_first_line_and_malformed(home, settings):
    p = home / f"{UUID}.jsonl"
    filler = json.dumps(asst("x" * 2000))
    sep = json.dumps(user("u"))
    n = chat.TAIL_BYTES // len(filler) + 5
    lines = [filler, sep] * n + ["{not json", '["list"]', json.dumps(asst("final"))]
    p.write_text("\n".join(lines) + "\n")
    v = view(settings)
    assert v.last_text == "final"


def test_only_tail_is_read(home, settings):
    p = home / f"{UUID}.jsonl"
    sentinel = json.dumps(asst("SENTINEL_OLD"))
    pad = json.dumps({"type": "system", "pad": "p" * 1000})
    n = chat.TAIL_BYTES // len(pad) + 10
    p.write_text(sentinel + "\n" + "\n".join([pad] * n) + "\n")
    v = view(settings)
    assert v.source == "none" and "SENTINEL_OLD" not in v.last_text
    assert chat._read_tail(p)[0] != sentinel


def test_mask_in_transcript_and_pane(home, settings):
    write(home / f"{UUID}.jsonl", [asst(f"token {TOKEN} end")])
    pane = f"\x1b[31mDo you want to proceed?\x1b[0m {TOKEN}\n 1. Yes\n 2. No\n"
    v = view(settings, pane=pane)
    assert TOKEN not in repr(v)
    v2 = view(settings, agent(AgentKind.CODEX, None), pane=f"output {TOKEN}\n> ")
    assert TOKEN not in repr(v2) and "\x1b" not in repr(v2)


def test_size_bounds(home, settings):
    write(home / f"{UUID}.jsonl", [asst("w" * 30000)])
    v = view(settings)
    assert len(v.last_text) <= 20000 and v.last_text.endswith("…")
    pane = "Q " + "q" * 3000 + "\n" + "\n".join(f"{i}. opt{i}" for i in range(1, 10)) + "\n"
    a = view(settings, pane=pane).asking
    assert a and len(a.text) <= 1500 and len(a.options) <= 9


def test_nine_options_all_keys(settings):
    pane = "\n".join(["Pick a target"] + [f"  {i}. opt{i}" for i in range(1, 10)]) + "\n"
    a = view(settings, pane=pane).asking
    assert a and [o.key for o in a.options] == [str(i) for i in range(1, 10)]


def test_permission_three_options(settings):
    v = view(settings, ag=agent(state=AgentState.WAITING), pane=(FIX / "perm3.txt").read_text())
    a = v.asking
    assert a.kind == "permission" and "proceed" in a.text
    assert [o.key for o in a.options] == ["1", "2", "3"]
    assert a.options[0].label == "Yes"
    assert v.last_text.startswith("Running the migration")


def test_yn(settings):
    a = view(settings, pane=(FIX / "yn.txt").read_text()).asking
    assert a.kind == "question" and [o.key for o in a.options] == ["y", "n"]


def test_enter(settings):
    a = view(settings, pane=(FIX / "enter.txt").read_text()).asking
    assert [o.key for o in a.options] == ["Enter"]


def test_no_prompt(settings):
    assert view(settings, pane="all done\n1. a thing\n> \n").asking is None
    assert view(settings, pane="").asking is None


def test_ask_tool_enriches_choice(home, settings):
    entry = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "AskUserQuestion",
         "input": {"questions": [{"question": "Which DB should we use?"}]}}]}}
    write(home / f"{UUID}.jsonl", [asst("thinking"), entry])
    pane = "Pick one\n 1. SQLite\n 2. Postgres\n"
    a = view(settings, pane=pane).asking
    assert a.kind == "choice" and a.text == "Which DB should we use?"
    assert [o.label for o in a.options] == ["SQLite", "Postgres"]


def test_non_claude_pane_heuristic(settings):
    v = view(settings, ag=agent(AgentKind.CODEX, None), pane=(FIX / "plain_codex.txt").read_text())
    assert v.source == "pane"
    assert v.last_text == "I changed the parser and ran the tests.\nAll 12 pass."
    assert v.asking is None


def test_claude_without_transcript_falls_back_to_pane(home, settings):
    v = view(settings, pane="hello world\n> \n")
    assert v.source == "pane" and v.last_text == "hello world"


def test_nothing_useful(settings):
    v = view(settings, ag=agent(AgentKind.OTHER, None), pane="\n\n  \n")
    assert v.source == "none" and v.last_text == "" and v.asking is None


def test_only_exact_transcript_opened(home, settings, monkeypatch):
    (home / f"{OTHER}.jsonl").write_text(json.dumps(asst("other session")) + "\n")
    (home / "secret.key").write_text("k")
    write(home / f"{UUID}.jsonl", [asst("mine")])
    opened = []
    real = builtins.open

    def spy(file, *a, **k):
        opened.append(str(file))
        return real(file, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    v = view(settings)
    assert v.last_text == "mine"
    assert opened == [str(home / f"{UUID}.jsonl")]


def test_bad_uuid_never_opens(home, settings, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("opened")
    monkeypatch.setattr(builtins, "open", boom)
    for bad in ("../../etc/passwd", "x.key", ""):
        v = view(settings, ag=agent(uuid=bad), pane="")
        assert v.source == "none"


def test_errors_swallowed(home, settings, monkeypatch):
    write(home / f"{UUID}.jsonl", [asst("x")])
    monkeypatch.setattr(chat, "read_transcript_info", lambda p: 1 / 0)
    assert view(settings, pane="").source == "none"
    monkeypatch.setattr(chat, "_chat_view", lambda *a: 1 / 0)
    assert view(settings).source == "none"


def test_capture_when_pane_text_none(settings, monkeypatch):
    monkeypatch.setattr(chat.tmuxctl, "capture_pane", lambda s, p, n: "hi there\n> \n")
    assert chat.chat_view(settings, agent(AgentKind.CODEX, None)).last_text == "hi there"
    def bad(*a):
        raise RuntimeError
    monkeypatch.setattr(chat.tmuxctl, "capture_pane", bad)
    assert chat.chat_view(settings, agent(AgentKind.CODEX, None)).source == "none"


# ---------------------------------------------------------------- delayed-message fixes
def asst_blocks(blocks, ts="2026-01-02T03:04:05.000Z"):
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": blocks}}


def tool_result():
    return user([{"type": "tool_result", "tool_use_id": "t", "content": "out"}])


def test_split_assistant_entries_are_joined(home, settings):
    write(home / f"{UUID}.jsonl", [
        user("go"),
        asst("earlier progress note"),
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}},
        tool_result(),
        asst("part one"),
        {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "x"}]}},
        {"type": "attachment", "timestamp": "2026-01-02T03:04:07.000Z"},
        asst_blocks([{"type": "tool_use", "name": "Read", "input": {}}, {"type": "text", "text": "part two"}],
                    ts="2026-01-02T03:04:09.000Z"),
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}},
    ])
    v = view(settings)
    assert v.last_text == "part one\n\npart two"
    assert "earlier" not in v.last_text
    assert v.last_ts == chat._parse_ts("2026-01-02T03:04:09.000Z")


def test_tool_result_between_is_transparent_for_newest_text(home, settings):
    write(home / f"{UUID}.jsonl", [asst("answer"),
                                   {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "B", "input": {}}]}},
                                   tool_result()])
    assert view(settings).last_text == "answer"


def test_pane_ahead_of_transcript_while_running(home, settings):
    write(home / f"{UUID}.jsonl", [user("refactor"), asst("Here is the plan, starting now")])
    pane = (FIX / "claude_stream.txt").read_text()
    v = view(settings, ag=agent(state=AgentState.RUNNING), pane=pane)
    assert v.source == "pane" and v.last_role == "assistant"
    assert v.last_text.startswith("Now writing the new streaming parser")
    assert "Bash(" not in v.last_text and "Cogitating" not in v.last_text
    # same pane when the transcript already has that text (markdown stripped in the pane): transcript wins
    write(home / f"{UUID}.jsonl", [user("refactor"),
                                   asst("**Now** writing the new `streaming` parser, almost done\nwith the second half.")])
    v = view(settings, ag=agent(state=AgentState.RUNNING), pane=pane)
    assert v.source == "transcript" and "**Now**" in v.last_text


def test_pane_prompt_after_block_is_not_newer(home, settings):
    # the user already sent a new prompt: pane history shows it below the old block
    write(home / f"{UUID}.jsonl", [asst("old answer"), user("new question")])
    pane = "● brand new unseen text\n\n❯ new question\n\n╭────╮\n│ >  │\n╰────╯\n  ? for shortcuts\n"
    v = view(settings, ag=agent(state=AgentState.RUNNING), pane=pane)
    assert (v.source, v.last_role, v.last_text) == ("transcript", "user", "new question")


def test_live_session_id_beats_stale_agent_uuid(home, settings, monkeypatch):
    write(home / f"{UUID}.jsonl", [asst("stale session")])
    write(home / f"{OTHER}.jsonl", [asst("fresh session after /clear")])
    ag = Agent(id="claude-1", kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=4242, cwd=CWD,
               state=AgentState.IDLE, session_uuid=UUID)
    monkeypatch.setattr(chat, "live_session_id", lambda pid: OTHER)
    assert view(settings, ag=ag).last_text == "fresh session after /clear"
    monkeypatch.setattr(chat, "live_session_id", lambda pid: None)
    assert view(settings, ag=ag).last_text == "stale session"
    # missing transcript: pane fallback, no error
    monkeypatch.setattr(chat, "live_session_id", lambda pid: "00000000-0000-0000-0000-000000000000")
    assert view(settings, ag=ag, pane="plain words\n> \n").source == "pane"


def lines_of(name):
    return (FIX / name).read_text().splitlines()


def test_pane_devin_skips_tool_boxes():
    out = chat.pane_last_block(lines_of("devin_tool_last.txt"), AgentKind.DEVIN)
    assert out == "Fixed the lockfile and reran the build. Two warnings remain."


def test_pane_codex_skips_tool_summaries():
    out = chat.pane_last_block(lines_of("codex_tool_last.txt"), AgentKind.CODEX)
    assert out == "I updated the parser and the tests pass.\n\n  Next I will update the docs.".replace("\n  Next", "\nNext")


def test_pane_cursor_shell_and_diff_lines_skipped():
    pane = ("  nice prose line\n\n  $ run --flag 'x'\n    wrapped arg\n    {\"json\":1}\n\n"
            "    ▎+ added line\n    ▎  ctx\n\n  Final words for you.\n\n→ next\n\n  gpt 5% · 1 file\n  ~/proj\n")
    assert chat.pane_last_block(pane.splitlines(), AgentKind.CURSOR) == "Final words for you."


def test_pane_banner_only_is_nothing():
    assert chat.pane_last_block(["Cursor Agent", "v2026.10.01-abc", "", "→ hi"], AgentKind.CURSOR) == ""


def test_prose_ending_with_period_not_a_tool_header():
    out = chat.pane_last_block(["• Read the docs and they agree.", "› x"], AgentKind.CODEX)
    assert out == "Read the docs and they agree."


def test_ask_bound_raised_and_mask_everywhere(home, settings):
    write(home / f"{UUID}.jsonl", [asst(f"{TOKEN} " + "z" * 15000)])
    v = view(settings)
    assert TOKEN not in v.last_text and 10000 < len(v.last_text) <= 20000


def test_task_skips_system_injected(home):
    p = home / f"{UUID}.jsonl"
    write(p, [
        user("Fix the login bug\nmore detail"),
        asst("on it"),
        user("<task-notification>\n<task-id>x</task-id>\n</task-notification>"),
        user("<foo-bar>only tags</foo-bar>"),
        user("<system-reminder>hi</system-reminder>"),
    ])
    assert chat.read_transcript_info(p).task == "Fix the login bug"


def test_task_strips_tags_and_empty(home):
    p = home / f"{UUID}.jsonl"
    write(p, [user("<ide_opened_file>a.py</ide_opened_file>\nrefactor <b>this</b>")])
    assert chat.read_transcript_info(p).task == "refactor this"
    write(p, [user("<task-notification>x</task-notification>"), asst("ok")])
    assert chat.read_transcript_info(p).task == ""
