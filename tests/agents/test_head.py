"""pm.agents.head: tmux_win (the composer placeholder's ``<session>:<window>``)."""

import dataclasses
import json
import os
from types import SimpleNamespace

from pm.agents.head import tmux_win


def _agent(kind="claude", label="", window_name=""):
    return SimpleNamespace(kind=SimpleNamespace(value=kind), label=label, window_name=window_name)


def test_tmux_win():
    assert tmux_win(_agent(label="dev-pm-1-claude")) == "dev-pm:1"
    assert tmux_win(_agent(label="work-3-claude-2")) == "work:3"  # pane position suffix
    assert tmux_win(_agent(label="claude-12", window_name="a1-hub")) == "a1-hub"
    assert tmux_win(_agent()) == ""
    assert tmux_win(SimpleNamespace()) == ""


# live_model: transcript model, else the process's own --model arg (New Chat), else the kind


def _proc(root, pid, argv):
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    return root


def _live(tmp_path, kind="claude", pid=4242, transcript="", aid="claude-abc123"):
    from pm.core.models import Agent, AgentKind
    return Agent(id=aid, kind=AgentKind(kind), pane_id="%1", session_id="$1", pid=pid, cwd=str(tmp_path),
                 transcript=transcript)


def test_live_model_falls_back_to_model_arg_then_kind(tmp_path):
    from pm.agents.head import arg_model, live_model
    proc = _proc(tmp_path / "proc", 4242, ["node", "/x/claude", "--model", "opus[1m]", "--", "--model x"])
    a = _live(tmp_path)
    assert arg_model(a, proc) == "opus[1m]"
    assert live_model(a, tmp_path, proc) == "claude-opus-5-5[1m]"  # a claude alias shows its full id
    _proc(tmp_path / "proc", 4243, ["codex", "--model=gpt-6.1-sol"])
    assert live_model(_live(tmp_path, "codex", 4243, aid="codex-1"), tmp_path, proc) == "gpt-6.1-sol"
    _proc(tmp_path / "proc", 4244, ["claude", "--", "--model", "sonnet"])  # after -- is the prompt
    assert live_model(_live(tmp_path, pid=4244, aid="claude-2"), tmp_path, proc) == "claude"
    assert live_model(_live(tmp_path, pid=0, aid="claude-3"), tmp_path, proc) == "claude"  # ended stand-in
    assert live_model(_live(tmp_path, pid=999, aid="claude-4"), tmp_path, proc) == "claude"  # no /proc entry


def test_live_model_prefers_the_transcript(tmp_path):
    from pm.agents.head import live_model
    t = tmp_path / ".claude/projects/-x/s.jsonl"
    t.parent.mkdir(parents=True)
    t.write_text('{"type":"assistant","message":{"model":"claude-sonnet-5-5"}}\n')
    proc = _proc(tmp_path / "proc", 4242, ["claude", "--model", "opus"])
    assert live_model(_live(tmp_path, transcript=str(t), aid="claude-5"), tmp_path, proc) == "claude-sonnet-5-5"
    # a New chat's empty transcript (no reply yet): the --model arg
    t.write_text('{"type":"user","message":{"content":"hi"}}\n')
    assert live_model(_live(tmp_path, transcript=str(t), aid="claude-6"), tmp_path, proc) == "claude-opus-5-5"


def test_live_pill_is_no_jinja_filter():
    """The heading pill is computed in Python (chatctx.composer_ctx head_pill), never by a filter that reads files."""
    from pm.web.render import env
    assert "live_pill" not in env.filters


def test_full_model_id_resolves_claude_aliases_only():
    from pm.agents.head import full_model_id, model_short
    from pm.core.models import AgentKind
    assert full_model_id(AgentKind.CLAUDE, "opus") == "claude-opus-5-5"
    assert model_short(full_model_id(AgentKind.CLAUDE, "opus")) == "opus-5-5"
    assert full_model_id(AgentKind.CLAUDE, "sonnet[1m]") == "claude-sonnet-5-5[1m]"
    assert full_model_id(AgentKind.CLAUDE, "claude-opus-5-5") == "claude-opus-5-5"
    assert full_model_id(AgentKind.CLAUDE, "") == ""
    assert full_model_id(AgentKind.DEVIN, "fable") == "fable"  # devin has its own alias meaning


def test_live_effort_transcript_then_arg_then_none(tmp_path):
    from pm.agents.head import arg_model, live_effort
    t = tmp_path / ".claude/projects/-x/e.jsonl"
    t.parent.mkdir(parents=True)
    t.write_text('{"type":"assistant","effort":"low","message":{"model":"claude-opus-5-5"}}\n'
                 '{"type":"assistant","effort":"xhigh","message":{"model":"claude-opus-5-5"}}\n'
                 '{"type":"user","message":{"content":"hi"}}\n')
    proc = _proc(tmp_path / "proc", 4300, ["claude", "--model", "opus", "--effort", "medium", "--", "--effort max"])
    assert live_effort(_live(tmp_path, pid=4300, transcript=str(t), aid="claude-e1"), tmp_path, proc) == "xhigh"
    a = _live(tmp_path, pid=4300, aid="claude-e2")
    assert live_effort(a, tmp_path, proc) == "medium"
    assert arg_model(a, proc) == "opus"  # model and effort are cached apart (flag in the key)
    _proc(tmp_path / "proc", 4301, ["claude"])
    assert live_effort(_live(tmp_path, pid=4301, aid="claude-e3"), tmp_path, proc) == ""
    _proc(tmp_path / "proc", 4302, ["codex", "--effort", "high"])
    assert live_effort(_live(tmp_path, "codex", 4302, aid="codex-e"), tmp_path, proc) == ""


def test_noted_effort_wins_until_a_reply_shows_it(tmp_path):
    from pm.agents.head import live_effort, note_effort, note_effort_cmd
    t = tmp_path / ".claude/projects/-x/n.jsonl"
    t.parent.mkdir(parents=True)
    t.write_text('{"type":"assistant","effort":"medium","message":{"model":"claude-opus-5-5"}}\n')
    proc = _proc(tmp_path / "proc", 4400, ["claude"])
    a = _live(tmp_path, pid=4400, transcript=str(t), aid="claude-n1")
    assert live_effort(a, tmp_path, proc) == "medium"
    note_effort(a, "high", now=1000.0)
    assert live_effort(a, tmp_path, proc, now=1001.0) == "high"  # set, no reply carries it yet
    assert live_effort(a, tmp_path, proc, now=1000.0 + 3600) == "medium"  # expired
    note_effort(a, "xhigh", now=2000.0)
    with t.open("a") as f:
        f.write('{"type":"assistant","effort":"xhigh","message":{"model":"claude-opus-5-5"}}\n')
    os.utime(t, (5000, 5000))  # the transcript cache is keyed by mtime
    assert live_effort(a, tmp_path, proc, now=2001.0) == "xhigh"  # confirmed: the note is dropped
    with t.open("a") as f:
        f.write('{"type":"assistant","effort":"low","message":{"model":"claude-opus-5-5"}}\n')
    os.utime(t, (6000, 6000))
    assert live_effort(a, tmp_path, proc, now=2002.0) == "low"  # changed in the pane later: transcript rules
    b = _live(tmp_path, pid=4400, transcript=str(t), aid="claude-n2")
    note_effort_cmd(b, "/effort max")
    assert live_effort(b, tmp_path, proc) == "max"
    c = _live(tmp_path, pid=4400, transcript=str(t), aid="claude-n3")
    for bad in ("/effort ultra", "/effort", "/effort high now", "/model high"):
        note_effort_cmd(c, bad)
    assert live_effort(c, tmp_path, proc) == "low"


# claude settings defaults, note_model, placeholder (home is always a tmp_path, never the real ~/.claude)


_MT = [10_000]


def _settings(path, **kw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kw))
    _MT[0] += 10  # the settings cache is keyed by mtime: every write gets its own
    os.utime(path, (_MT[0], _MT[0]))


def test_claude_defaults_precedence(tmp_path):
    from pm.agents import claude_defaults as cd
    home, proj = tmp_path / "home", tmp_path / "proj"
    assert cd.read(str(proj), home) == ("", "")
    _settings(home / ".claude/settings.json", model="claude-sonnet-5-5", effortLevel="medium", env={"x": 1})
    assert cd.read(str(proj), home) == ("claude-sonnet-5-5", "medium")
    _settings(proj / ".claude/settings.json", model="opus")
    assert cd.read(str(proj), home) == ("opus", "medium")  # per key: the effort still comes from the user file
    _settings(proj / ".claude/settings.local.json", model="haiku", effortLevel="low")
    assert cd.read(str(proj), home) == ("haiku", "low")
    assert cd.read("", home) == ("claude-sonnet-5-5", "medium")  # no cwd: the user file only
    assert cd.read("relative/dir", home) == ("claude-sonnet-5-5", "medium")


def test_claude_defaults_bad_files(tmp_path):
    from pm.agents import claude_defaults as cd
    home = tmp_path / "home"
    p = home / ".claude/settings.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    assert cd.read("", home) == ("", "")
    p.write_text("[1, 2]")  # not an object
    assert cd.read("", home) == ("", "")
    _settings(p, model=["x"], effortLevel=3)
    assert cd.read("", home) == ("", "")
    _settings(p, model="m" * 500 + "\x07", effortLevel="high")
    assert cd.read("", home) == ("m" * 80, "high")  # capped, control chars dropped
    p.unlink()
    p.mkdir()  # a directory is not a settings file
    assert cd.read("", home) == ("", "")


def test_claude_defaults_cached_by_mtime(tmp_path):
    from pm.agents import claude_defaults as cd
    home = tmp_path / "home"
    p = home / ".claude/settings.json"
    _settings(p, model="opus")
    os.utime(p, (1000, 1000))
    assert cd.read("", home) == ("opus", "")
    _settings(p, model="sonnet")
    os.utime(p, (1000, 1000))
    assert cd.read("", home) == ("opus", "")  # same (path, mtime): the cached read
    os.utime(p, (2000, 2000))
    assert cd.read("", home) == ("sonnet", "")


def test_settings_defaults_full_id_and_claude_only(tmp_path):
    from pm.agents.head import settings_defaults
    _settings(tmp_path / ".claude/settings.json", model="opus", effortLevel="high")
    assert settings_defaults(_live(tmp_path, aid="claude-d1"), tmp_path) == ("claude-opus-5-5", "high")
    _settings(tmp_path / ".claude/settings.json", model="opusplan")
    assert settings_defaults(_live(tmp_path, aid="claude-d2"), tmp_path)[0] == "opusplan"  # left as is
    assert settings_defaults(_live(tmp_path, "codex", aid="codex-d"), tmp_path) == ("", "")


def test_live_model_does_not_fall_back_to_the_settings_default(tmp_path):
    from pm.agents.head import live_model
    _settings(tmp_path / ".claude/settings.json", model="claude-sonnet-5-5")
    proc = _proc(tmp_path / "proc", 4500, ["claude"])
    assert live_model(_live(tmp_path, pid=4500, aid="claude-u1"), tmp_path, proc) == "claude"  # the "unknown" sentinel


def test_noted_model_wins_until_a_reply_shows_it(tmp_path):
    from pm.agents.head import live_model, note_model
    t = tmp_path / ".claude/projects/-x/m.jsonl"
    t.parent.mkdir(parents=True)
    t.write_text('{"type":"assistant","message":{"model":"claude-sonnet-5-5"}}\n')
    proc = _proc(tmp_path / "proc", 4600, ["claude"])
    a = _live(tmp_path, pid=4600, transcript=str(t), aid="claude-m1")
    assert live_model(a, tmp_path, proc) == "claude-sonnet-5-5"
    note_model(a, "opus", now=1000.0)
    assert live_model(a, tmp_path, proc, now=1001.0) == "claude-opus-5-5"  # alias as its full id, no reply yet
    assert live_model(a, tmp_path, proc, now=1000.0 + 3600) == "claude-sonnet-5-5"  # expired
    note_model(a, "opus", now=2000.0)
    with t.open("a") as f:
        f.write('{"type":"assistant","message":{"model":"claude-opus-5-5[1m]"}}\n')
    os.utime(t, (5000, 5000))
    assert live_model(a, tmp_path, proc, now=2001.0) == "claude-opus-5-5[1m]"  # confirmed (same short name)
    with t.open("a") as f:
        f.write('{"type":"assistant","message":{"model":"claude-haiku-4-5-20251001"}}\n')
    os.utime(t, (6000, 6000))
    assert live_model(a, tmp_path, proc, now=2002.0) == "claude-haiku-4-5-20251001"  # changed in the pane later
    b = _live(tmp_path, pid=4600, aid="claude-m2")  # no transcript at all
    note_model(b, "claude-opus-5-5", now=3000.0)
    assert live_model(b, tmp_path, proc, now=3001.0) == "claude-opus-5-5"


def test_placeholder_uses_live_then_settings_default(tmp_path):
    from pm.agents.head import note_effort, note_model, placeholder
    _settings(tmp_path / ".claude/settings.json", model="claude-sonnet-5-5")
    a = dataclasses.replace(_live(tmp_path, aid="claude-p1"), label="dev-pm-19-claude")
    assert placeholder(a, tmp_path) == ("Message to sonnet-5-5 in dev-pm:19", "Message sonnet-5-5")
    _settings(tmp_path / ".claude/settings.json", model="claude-sonnet-5-5", effortLevel="medium")
    assert placeholder(a, tmp_path)[1] == "Message sonnet-5-5 · medium"
    note_model(a, "opus")
    note_effort(a, "high")
    assert placeholder(a, tmp_path) == ("Message to opus-5-5 · high in dev-pm:19", "Message opus-5-5 · high")
    assert placeholder(_live(tmp_path, "codex", aid="codex-p"), tmp_path)[1] == "Message codex"  # no settings guess
    from pm.web.render import env
    assert env.filters["placeholder"] is placeholder


def test_placeholder_output_unchanged_by_the_shared_resolver(tmp_path):
    from pm.agents.head import _resolved, placeholder
    _settings(tmp_path / ".claude/settings.json", model="claude-sonnet-5-5", effortLevel="low")
    a = dataclasses.replace(_live(tmp_path, aid="claude-r1"), label="dev-pm-3-claude")
    assert _resolved(a, tmp_path) == ("claude-sonnet-5-5", "low")
    assert placeholder(a, tmp_path) == ("Message to sonnet-5-5 · low in dev-pm:3", "Message sonnet-5-5 · low")


def test_new_session_label(tmp_path):
    from pm.agents.head import new_session_label, note_effort, note_model
    _settings(tmp_path / ".claude/settings.json", model="claude-sonnet-5-5", effortLevel="medium")
    a = _live(tmp_path, aid="claude-n1")
    assert new_session_label(a, tmp_path) == "New sonnet-5-5 session with effort medium"  # settings fallback
    note_model(a, "opus")
    note_effort(a, "high")
    assert new_session_label(a, tmp_path) == "New opus-5-5 session with effort high"  # live wins
    _settings(tmp_path / ".claude/settings.json", model="claude-sonnet-5-5")
    b = _live(tmp_path, aid="claude-n2")
    assert new_session_label(b, tmp_path) == "New sonnet-5-5 session"  # model, no effort


def test_new_session_label_unknown_model_and_other_kinds(tmp_path):
    from pm.agents.head import new_session_label
    assert new_session_label(_live(tmp_path, aid="claude-n3"), tmp_path) == "New session"  # no settings at all
    assert new_session_label(_live(tmp_path, "codex", aid="codex-n"), tmp_path) == ""
    from pm.web.render import env
    assert env.filters["new_session"] is new_session_label


def test_live_pill_falls_back_to_the_settings_default_then_the_kind(tmp_path):
    """#155: the heading pill shows the same model as the composer placeholder: live, else Claude's settings default,
    else the kind name (``live_model`` itself keeps the kind as its "unknown")."""
    from pm.agents.head import live_model, live_pill, placeholder
    a = _live(tmp_path, pid=0, aid="claude-p1")
    assert live_pill(a, tmp_path) == "claude"  # nothing known
    _settings(tmp_path / ".claude/settings.json", model="opus")
    assert live_pill(a, tmp_path) == "opus-5-5" and live_model(a, tmp_path) == "claude"
    assert placeholder(a, tmp_path)[1] == "Message opus-5-5"  # the composer says the same
    t = tmp_path / ".claude/projects/-x/p.jsonl"
    t.parent.mkdir(parents=True)
    t.write_text('{"type":"assistant","message":{"model":"claude-sonnet-5-5"}}\n')
    assert live_pill(_live(tmp_path, pid=0, transcript=str(t), aid="claude-p2"), tmp_path) == "sonnet-5-5"  # live wins
    assert live_pill(_live(tmp_path, "codex", pid=0, aid="codex-p3"), tmp_path) == "codex"  # no settings for other kinds
