import json
import time

from helpers import chat_env as env  # noqa: F401  (fixture)
from helpers import login

from pm.agents import recap, registry
from pm.core.models import Agent, AgentKind, AgentState


def _line(**kw):
    return json.dumps(kw)


def _transcript(tmp_path):
    p = tmp_path / "s.jsonl"
    lines = [
        _line(type="user", timestamp="2026-10-08T10:00:00+00:00", message={"role": "user", "content": "Fix the \x1b[31mbug\x1b[0m in app"}),
        _line(type="assistant", timestamp="2026-10-08T10:01:00+00:00", message={"role": "assistant", "content": [
            {"type": "text", "text": "working"},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/w/a.py"}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}),
        _line(type="user", timestamp="2026-10-08T10:01:05+00:00", message={"role": "user", "content": [{"type": "tool_result", "content": "x"}]}),
        _line(type="assistant", isSidechain=True, timestamp="2026-10-08T10:02:00+00:00", message={"role": "assistant", "content": [{"type": "tool_use", "name": "Write", "input": {"file_path": "/w/side.py"}}]}),
        "not json",
        _line(type="assistant", timestamp="2026-10-08T10:05:00+00:00", message={"role": "assistant", "content": [
            {"type": "text", "text": "Done. token sk-ant-api03-" + "A" * 40},
            {"type": "tool_use", "name": "Write", "input": {"file_path": "/w/b.py"}}]}),
    ]
    p.write_text("\n".join(lines) + "\n")
    return p


def test_parse_claude(tmp_path):
    rc = recap.parse_claude(_transcript(tmp_path), "/w")
    assert rc.prompt == "Fix the bug in app"
    assert rc.tools == 3 and rc.files == ("a.py", "b.py")  # sidechain ignored
    assert "sk-ant" not in rc.reply and rc.reply.startswith("Done.")
    assert rc.duration == 300
    assert recap.human_duration(300) == "5m" and recap.human_duration(5) == "5s"
    assert recap.human_duration(90000) == "1d 1h" and recap.human_duration(-5) == "0s"


def test_recap_empty_when_no_transcript():
    a = Agent(id="x", kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=0, cwd="/nope",
              state=AgentState.IDLE, state_since=1.0)
    assert recap.recap_for(a).empty


def test_recap_route_and_history(env, monkeypatch, tmp_path):
    s, c = env
    assert c.get("/a/claude-12/recap", follow_redirects=False).status_code in (303, 401)
    login(s, c)
    monkeypatch.setattr(recap, "recap_for", lambda a: recap.parse_claude(_transcript(tmp_path), a.cwd))
    now = time.time()
    agent = c.get("/a/claude-12/chat")  # warm snapshot
    assert agent.status_code == 200
    con = registry._connect(s)
    con.execute("INSERT INTO agent_records VALUES ('claude-aaaaaa','claude','u',11,?,?,'%9',NULL,?, 'old one','',?,'')",
                (now - 900, now - 300, str(s.dev_root / "alpha"), now - 300))
    con.execute("INSERT INTO agent_records VALUES ('claude-bbbbbb','claude','u2',12,?,?,'%8',NULL,'/elsewhere','other cwd','',?,'')",
                (now - 900, now - 300, now - 300))
    con.commit(); con.close()
    r = c.get("/a/claude-12/recap")
    assert r.status_code == 200
    assert "First prompt" in r.text and "Fix the bug in app" in r.text and "a.py" in r.text
    assert "\x1b" not in r.text and "sk-ant" not in r.text
    assert "Earlier agents here" in r.text and "/a/" in r.text and "claude-aaaaaa" in r.text
    assert "other cwd" not in r.text
    assert c.get("/a/nope-99/recap").status_code == 404


def test_parse_claude_away_summary(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join([
        _line(type="user", timestamp="2026-10-08T10:00:00+00:00", message={"role": "user", "content": "hi"}),
        _line(type="system", subtype="away_summary", timestamp="2026-10-08T10:02:00+00:00",
              content="Old recap. (disable recaps in /config)"),
        _line(type="system", subtype="away_summary", timestamp="2026-10-08T10:03:00+00:00",
              content="You fixed the bug. Next: ship it. (disable recaps in /config)"),
    ]) + "\n")
    rc = recap.parse_claude(p, "/w")
    assert rc.away == "You fixed the bug. Next: ship it." and not rc.empty


def test_recap_row_route(env, monkeypatch):
    s, c = env
    assert c.get("/a/claude-12/recap-row", follow_redirects=False).status_code in (303, 401)
    login(s, c)
    assert c.get("/a/claude-12/chat").status_code == 200
    monkeypatch.setattr(recap, "recap_for", lambda a: recap.Recap(reply="All done.", away="Recap **text**.\n\n- one"))
    r = c.get("/a/claude-12/recap-row")
    assert r.status_code == 200 and "<strong>text</strong>" in r.text and "<li>one</li>" in r.text and "All done." not in r.text
    monkeypatch.setattr(recap, "recap_for", lambda a: recap.Recap(reply="All done.", exact=False))
    r = c.get("/a/claude-12/recap-row")
    assert "Last reply" in r.text and "All done." in r.text
    monkeypatch.setattr(recap, "recap_for", lambda a: recap.Recap())
    assert "No recap available" in c.get("/a/claude-12/recap-row").text
    for n, word in ((1, "tool call<"), (2, "tool calls<")):
        monkeypatch.setattr(recap, "recap_for", lambda a, n=n: recap.Recap(reply="x", tools=n))
        assert f"<strong>{n}</strong> {word}" in c.get("/a/claude-12/recap-row").text
    assert c.get("/a/nope-99/recap-row").status_code == 404
