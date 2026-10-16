"""pm.chat.log steps: per-block lines of an assistant turn (text / thinking / tool target), masked and capped."""

import json
import sqlite3

from test_chatlog import (
    TOKEN,
    _pb,
    agent,
    all_msgs,
    asst,
    atif,
    jsonl,  # noqa: F401 - fixture
    step,
    text,
    think,
    tool,
    tool_result,
    ts,
    user,
)

from pm.chat import log as chatlog
from pm.chat.sources import common
from pm.core.models import AgentKind, ChatStep


def kinds(m):
    return [(s.kind, s.name, s.line) for s in m.steps]


def test_claude_steps_in_order(settings, jsonl):  # noqa: F811
    p = jsonl(
        user("u1", "fix it", 1),
        asst("a1", think("Let me look\nsecond line"), think(""), n=2),
        asst("a2", text("\n\n  Looking   now.\nmore detail"), tool("Bash", command="ls -la\necho two",
                                                                    description="list"), n=3),
        tool_result("r1", 4),
        asst("a3", tool("Read", file_path="/a/b.py", limit=5), tool("Grep", pattern="def x", path="/a"),
             tool("Glob", pattern="**/*.py"), tool("WebFetch", url="https://x.dev/p", prompt="sum"),
             tool("WebSearch", query="pm docs"), tool("Agent", description="Explore code", prompt="long"),
             tool("Edit", file_path="/a/c.py", old_string="x", new_string="y"), tool("ExitPlanMode", plan="p"), n=5),
        asst("a4", text("Done."), n=6),
    )
    chatlog.sync(settings, agent(p))
    ms = all_msgs(settings)
    assert ms[0].steps == ()
    assert kinds(ms[1]) == [
        ("thinking", "", "Let me look"),
        ("text", "", "Looking now."),
        ("tool", "Bash", "ls -la"),
        ("tool", "Read", "/a/b.py"),
        ("tool", "Grep", "def x"),
        ("tool", "Glob", "**/*.py"),
        ("tool", "WebFetch", "https://x.dev/p"),
        ("tool", "WebSearch", "pm docs"),
        ("tool", "Agent", "Explore code"),
        ("tool", "Edit", "/a/c.py"),
        ("tool", "ExitPlanMode", ""),
        ("text", "", "Done."),
    ]
    assert all(isinstance(s, ChatStep) for s in ms[1].steps)
    raw = sqlite3.connect(settings.db_file).execute("SELECT group_concat(steps) FROM chat_messages").fetchone()[0]
    assert "SECRET OUTPUT" not in raw and "echo two" not in raw and "second line" not in raw


def test_growing_turn_appends_steps(settings, jsonl):  # noqa: F811
    p = jsonl(user("u1", "go", 1), asst("a1", text("one"), tool("Bash", command="make"), n=2))
    a = agent(p)
    chatlog.sync(settings, a)
    m1 = all_msgs(settings)[1]
    jsonl(tool_result("r1", 3), asst("a2", tool("Read", file_path="/x"), text("two"), n=4))
    chatlog.sync(settings, a)
    m2 = all_msgs(settings)[1]
    assert m2.seq == m1.seq and m2.updated >= m1.updated
    assert kinds(m2) == [("text", "", "one"), ("tool", "Bash", "make"), ("tool", "Read", "/x"),
                         ("text", "", "two")]
    assert chatlog.get(settings, a.id, m2.seq).steps == m2.steps


def test_step_line_masked(settings, jsonl):  # noqa: F811
    p = jsonl(user("u1", "go", 1),
              asst("a1", tool("Bash", command=f"curl -H 'Authorization: token {TOKEN}' https://api"),
                   text(f"token is {TOKEN}"), think(f"use {TOKEN}"), n=2))
    chatlog.sync(settings, agent(p))
    m = all_msgs(settings)[1]
    assert len(m.steps) == 3
    for s in m.steps:
        assert TOKEN not in s.line and TOKEN[:20] not in s.line
    assert m.steps[0].line.startswith("curl")
    raw = sqlite3.connect(settings.db_file).execute("SELECT group_concat(steps) FROM chat_messages").fetchone()[0]
    assert TOKEN[:20] not in raw


def test_step_line_cap_160(settings, jsonl):  # noqa: F811
    long = "x" * 1000
    p = jsonl(user("u1", "go", 1), asst("a1", text("a " * 300), tool("Bash", command="echo " + long),
                                        text("b" * 160), n=2))
    chatlog.sync(settings, agent(p))
    s = all_msgs(settings)[1].steps
    assert all(len(x.line) <= 160 for x in s)
    assert len(s[0].line) == 160 and s[0].line.endswith("…") and "  " not in s[0].line
    assert s[1].line.startswith("echo x") and s[1].line.endswith("…") and len(s[1].line) == 160
    assert s[2].line == "b" * 160  # exactly 160: kept whole


def test_step_cap_200_newest_kept(settings, jsonl):  # noqa: F811
    blocks = [tool("Bash", command=f"cmd{i}") for i in range(150)]
    p = jsonl(user("u1", "go", 1), asst("a1", *blocks, n=2))
    a = agent(p)
    chatlog.sync(settings, a)
    jsonl(asst("a2", *[tool("Bash", command=f"cmd{i}") for i in range(150, 260)], n=3))
    chatlog.sync(settings, a)
    s = all_msgs(settings)[1].steps
    assert len(s) == chatlog.MAX_STEPS == 200
    assert s[0].line == "cmd60" and s[-1].line == "cmd259"
    assert all_msgs(settings)[1].tool_total == 260


def test_migrates_old_table(settings, jsonl):  # noqa: F811
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(settings.db_file)
    con.execute("CREATE TABLE chat_messages (seq INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL,"
                " conv TEXT NOT NULL, uid TEXT NOT NULL, role TEXT NOT NULL, ts REAL NOT NULL DEFAULT 0,"
                " text TEXT NOT NULL DEFAULT '', thinking TEXT NOT NULL DEFAULT '',"
                " tools TEXT NOT NULL DEFAULT '[]', updated REAL NOT NULL DEFAULT 0, UNIQUE (agent_id, conv, uid))")
    con.execute("INSERT INTO chat_messages (agent_id, conv, uid, role, ts, text, tools) "
                "VALUES ('claude-abc123', 'old', 'o1:a', 'assistant', 1, 'old answer', '[[\"Bash\", 2]]')")
    con.commit()
    con.close()
    chatlog._INIT.discard(str(settings.db_file))
    p = jsonl(user("u1", "go", 1), asst("a1", text("new"), tool("Bash", command="ls"), n=2))
    chatlog.sync(settings, agent(p))
    ms = all_msgs(settings)
    assert ms[0].text == "old answer" and ms[0].steps == () and ms[0].tool_total == 2
    assert kinds(ms[2]) == [("text", "", "new"), ("tool", "Bash", "ls")]
    cols = [r[1] for r in sqlite3.connect(settings.db_file).execute("PRAGMA table_info(chat_messages)")]
    assert cols.count("steps") == 1
    chatlog._INIT.discard(str(settings.db_file))
    chatlog._migrate(sqlite3.connect(settings.db_file))  # idempotent


def test_steps_load_rejects_garbage():
    assert chatlog._steps_load("nope") == []
    assert chatlog._steps_load(json.dumps([["tool", "Bash", "ls"], ["evil", "", "x"], [1, 2], "s"])) == [
        ("tool", "Bash", "ls")]


def test_codex_steps(settings, tmp_path):
    rp = tmp_path / "rollout-s.jsonl"
    items = [
        {"type": "response_item", "timestamp": ts(1), "payload": {"type": "message", "role": "user", "id": "m1",
                                                                  "content": [{"type": "input_text", "text": "go"}]}},
        {"type": "response_item", "timestamp": ts(2), "payload": {"type": "reasoning", "id": "r1", "summary": [
            {"type": "summary_text", "text": "**Plan**\nmore"}, {"type": "summary_text", "text": "next"}]}},
        {"type": "response_item", "timestamp": ts(3), "payload": {
            "type": "function_call", "id": "f1", "name": "shell",
            "arguments": json.dumps({"command": ["bash", "-lc", "rg foo\nls"], "workdir": "/x"})}},
        {"type": "response_item", "timestamp": ts(4), "payload": {
            "type": "custom_tool_call", "id": "c1", "name": "apply_patch",
            "input": "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n*** End Patch"}},
        {"type": "response_item", "timestamp": ts(5), "payload": {"type": "custom_tool_call", "id": "c2",
                                                                  "name": "exec", "input": "\nrun(cmd)\nmore"}},
        {"type": "response_item", "timestamp": ts(6), "payload": {"type": "local_shell_call", "id": "l1",
                                                                  "action": {"type": "exec", "command": ["git", "status"]}}},
        {"type": "response_item", "timestamp": ts(7), "payload": {"type": "web_search_call", "id": "w1",
                                                                  "action": {"type": "search", "query": "uv docs"}}},
        {"type": "response_item", "timestamp": ts(8), "payload": {"type": "function_call_output", "id": "o",
                                                                  "output": "SECRET"}},
        {"type": "response_item", "timestamp": ts(9), "payload": {"type": "message", "role": "assistant", "id": "m2",
                                                                  "content": [{"type": "output_text", "text": "ok\nmore"}]}},
    ]
    rp.write_text("".join(json.dumps(e) + "\n" for e in items))
    a = agent(rp, "codex-steps1", AgentKind.CODEX)
    chatlog.sync(settings, a)
    m = list(reversed(chatlog.messages(settings, a.id)))[1]
    assert kinds(m) == [("thinking", "", "**Plan**"), ("thinking", "", "next"), ("tool", "shell", "rg foo"),
                        ("tool", "apply_patch", "src/a.py"), ("tool", "exec", "run(cmd)"),
                        ("tool", "local_shell", "git status"), ("tool", "web_search", "uv docs"),
                        ("text", "", "ok")]


def test_devin_steps(settings, tmp_path):
    p = tmp_path / "stepsy.json"
    s2 = step(2, "agent", "Checking.\nmore")
    s2["reasoning_content"] = "hmm\nmore"
    s2["tool_calls"] = [{"tool_call_id": "t1", "function_name": "exec", "arguments": {"command": "ls /x"}},
                        {"tool_call_id": "t2", "function_name": "read", "arguments": {"file_path": "/y.py"}},
                        {"tool_call_id": "t3", "function_name": "get_output", "arguments": {"shell_id": "s"}}]
    p.write_text(atif([step(1, "user", "do it"), s2]))
    a = agent(p, "devin-steps1", AgentKind.DEVIN)
    chatlog.sync(settings, a)
    m = list(reversed(chatlog.messages(settings, a.id)))[1]
    assert kinds(m) == [("thinking", "", "hmm"), ("text", "", "Checking."), ("tool", "exec", "ls /x"),
                        ("tool", "read", "/y.py"), ("tool", "get_output", "")]


def test_cursor_steps(settings, tmp_path):
    db = tmp_path / "chat-steps" / "store.db"
    db.parent.mkdir()
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB)")
    rows = [
        {"role": "user", "content": [{"type": "text", "text": "<user_query>\nexplain\n</user_query>"}]},
        {"role": "assistant", "content": [
            {"type": "reasoning", "text": "ponder\nmore"}, {"type": "reasoning", "text": ""},
            {"type": "text", "text": "Sure."},
            {"type": "tool-call", "toolName": "Read", "args": {"path": "/etc/x"}},
            {"type": "tool-call", "toolName": "Grep", "args": {"pattern": "foo", "path": "/a"}},
            {"type": "tool-call", "toolName": "Glob", "args": {"glob_pattern": "*.md", "target_directory": "/a"}},
            {"type": "tool-call", "toolName": "Shell", "args": {"command": "make test"}}]},
        {"role": "tool", "content": [{"type": "tool-result", "result": "SECRET"}]},
    ]
    for i, r in enumerate(rows):
        con.execute("INSERT INTO blobs VALUES (?, ?)", (f"id{i}", json.dumps(r).encode()))
    con.commit()
    con.close()
    a = agent(db, "cursor-steps1", AgentKind.CURSOR)
    chatlog.sync(settings, a)
    m = list(reversed(chatlog.messages(settings, a.id)))[1]
    assert kinds(m) == [("thinking", "", "ponder"), ("text", "", "Sure."), ("tool", "Read", "/etc/x"),
                        ("tool", "Grep", "foo"), ("tool", "Glob", "*.md"), ("tool", "Shell", "make test")]


def test_antigravity_steps(settings, tmp_path):
    db = tmp_path / "0c20fc1b-41cd-4c6c-b0b8-39058923ccb9.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE steps (idx integer PRIMARY KEY, step_type integer, step_payload blob)")
    tsb = _pb(5, _pb(6, _pb(1, 1791463454)))
    user_p = _pb(1, 14) + tsb + _pb(19, _pb(2, b"help me"))
    args = json.dumps({"AbsolutePath": "/x/y.py", "toolAction": "view", "toolSummary": "s"}).encode()
    cmd = json.dumps({"CommandLine": "git log -1", "Cwd": "/x"}).encode()
    reply = _pb(1, 15) + tsb + _pb(20, _pb(1, b"Here.\nmore") + _pb(3, b"thinking it over")
                                   + _pb(7, _pb(1, b"c1") + _pb(2, b"view_file") + _pb(3, args))
                                   + _pb(7, _pb(1, b"c2") + _pb(2, b"run_command") + _pb(3, cmd))
                                   + _pb(7, _pb(2, b"list_dir")))
    con.executemany("INSERT INTO steps VALUES (?,?,?)", [(0, 14, user_p), (1, 15, reply)])
    con.commit()
    con.close()
    a = agent(db, "antigravity-steps1", AgentKind.ANTIGRAVITY)
    chatlog.sync(settings, a)
    m = list(reversed(chatlog.messages(settings, a.id)))[1]
    assert kinds(m) == [("thinking", "", "thinking it over"), ("text", "", "Here."),
                        ("tool", "view_file", "/x/y.py"), ("tool", "run_command", "git log -1"),
                        ("tool", "list_dir", "")]


def test_tool_target_helper():
    t = common.tool_target
    assert t({"command": ["git", "status"]}) == "git status"
    assert t({"Command_Line": "  a \t b  \n c"}) == "a b"
    assert t('{"query": "x"}') == "x"
    assert t("*** Begin Patch\n*** Add File: n.txt\n+x") == "n.txt"
    assert t({"action": {"query": "q"}}) == "q"
    assert t({"shell_id": "s"}) == "" and t(None) == "" and t(42) == ""
    assert len(common.first_line("z" * 5000)) == common.STEP_RAW
