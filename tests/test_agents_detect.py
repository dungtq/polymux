import json
from pathlib import Path

from pm.agents import ResourceSampler, classify_pane_text, detect_agents, sample_once
from pm.core.models import AgentKind, AgentState, TmuxPane

TCK = 100


def mkproc(root: Path, pid, ppid, comm, cmdline, start=1000, ut=0, st=0, rss=256):
    d = root / str(pid)
    d.mkdir(parents=True)
    f = ["S", str(ppid)] + ["0"] * 9 + [str(ut), str(st), "0", "0", "20", "0", "1", "0", str(start), "0", str(rss)]
    (d / "stat").write_text(f"{pid} ({comm}) " + " ".join(f))
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in cmdline) + b"\0")


def pane(pid, n=12):
    return TmuxPane(id=f"%{n}", window_id="@1", session_id="$3", pid=pid, command="x", cwd="/pane")


def sess(dirp: Path, pid, proc_start, status="busy"):
    (dirp / f"{pid}.json").write_text(
        json.dumps({"pid": pid, "procStart": str(proc_start), "status": status, "sessionId": "uuid-1",
                    "cwd": "/proj", "statusUpdatedAt": 1700000000000})
    )


def test_claude_with_session(tmp_path):
    proc, sd = tmp_path / "proc", tmp_path / "s"
    sd.mkdir()
    proc.mkdir()
    (proc / "uptime").write_text("100.0 0\n")
    mkproc(proc, 10, 1, "fish", ["fish"])
    mkproc(proc, 11, 10, "node", ["node", "/x/bin/claude", "--foo"], start=1000, ut=300, st=100, rss=256)
    sess(sd, 11, 1000, "busy")
    (sd / "11.abcdef.key").write_text("SECRET")
    (sd / "11.abcdef.key").chmod(0)  # unreadable: any open would raise, but we must not even try
    a = detect_agents([pane(10)], sessions_dir=sd, proc_root=proc)
    assert len(a) == 1
    x = a[0]
    assert (x.id, x.kind, x.pid, x.state, x.session_uuid, x.cwd) == (
        "claude-12", AgentKind.CLAUDE, 11, AgentState.RUNNING, "uuid-1", "/proj")
    assert x.state_since == 1700000000.0 and x.confidence == 1.0
    assert x.age_s > 0 and x.rss_mb > 0


def test_only_pid_json_opened(tmp_path, monkeypatch):
    proc, sd = tmp_path / "proc", tmp_path / "s"
    sd.mkdir()
    proc.mkdir()
    mkproc(proc, 11, 1, "claude", ["claude"])
    sess(sd, 11, 1000)
    (sd / "11.k.key").write_text("SENTINEL")
    opened = []
    real = Path.read_text

    def spy(self, *a, **k):
        opened.append(self.name)
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", spy)
    detect_agents([pane(11)], sessions_dir=sd, proc_root=proc)
    assert not any(n.endswith(".key") for n in opened)
    assert not any(n == "environ" for n in opened)
    assert "11.json" in opened


def test_stale_and_mismatched(tmp_path):
    proc, sd = tmp_path / "proc", tmp_path / "s"
    sd.mkdir()
    proc.mkdir()
    mkproc(proc, 11, 1, "claude", ["claude"], start=1000)
    sess(sd, 11, 999)  # procStart mismatch
    a = detect_agents([pane(11)], sessions_dir=sd, proc_root=proc)
    assert a[0].state is AgentState.UNKNOWN and a[0].session_uuid is None and a[0].confidence == 0.0
    # json for dead pid is ignored
    sess(sd, 99, 1)
    assert len(detect_agents([pane(11)], sessions_dir=sd, proc_root=proc)) == 1
    # pid field mismatch inside file
    (sd / "11.json").write_text(json.dumps({"pid": 5, "procStart": "1000", "status": "idle"}))
    assert detect_agents([pane(11)], sessions_dir=sd, proc_root=proc)[0].state is AgentState.UNKNOWN
    (sd / "11.json").write_text("{bad")
    assert detect_agents([pane(11)], sessions_dir=sd, proc_root=proc)[0].state is AgentState.UNKNOWN


def test_other_kinds_and_none(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    mkproc(proc, 1, 0, "init", ["init"])
    mkproc(proc, 20, 1, "bash", ["bash"])
    mkproc(proc, 21, 20, "sh", ["sh", "-c", "x"])
    mkproc(proc, 22, 21, "codex", ["/home/u/.local/bin/codex"])
    mkproc(proc, 30, 1, "bash", ["bash"])
    mkproc(proc, 40, 1, "node", ["node", "/a/cursor-agent", "x"])
    mkproc(proc, 50, 1, "vim", ["vim"])
    a = detect_agents([pane(20, 1), pane(30, 2), pane(40, 3), pane(50, 4), pane(777, 5)],
                      sessions_dir=tmp_path / "none", proc_root=proc, home=tmp_path / "nohome")
    assert [(x.id, x.kind) for x in a] == [("codex-1", AgentKind.CODEX), ("cursor-agent-3", AgentKind.CURSOR)]
    assert a[0].state is AgentState.UNKNOWN


def test_resources(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "uptime").write_text("100.0 0\n")
    mkproc(proc, 5, 1, "a b)c", ["x"], start=5000, ut=1000, st=0, rss=512)
    cpu, rss, age = sample_once(5, proc_root=proc, clk_tck=TCK, page_size=4096)
    assert age == 50.0 and cpu == 20.0 and rss == 2.0
    assert sample_once(6, proc_root=proc) is None
    t = [0.0]
    s = ResourceSampler(proc_root=proc, clk_tck=TCK, page_size=4096, clock=lambda: t[0])
    s.sample(5)
    t[0] = 10.0
    (proc / "5" / "stat").write_text((proc / "5" / "stat").read_text().replace(" 1000 0 ", " 1500 0 ", 1))
    assert s.sample(5)[0] == 50.0  # 500 ticks / 10s


def test_classify():
    W, R, I, F, U = (AgentState.WAITING, AgentState.RUNNING, AgentState.IDLE, AgentState.FAILED, AgentState.UNKNOWN)
    c = lambda t: classify_pane_text(t, AgentKind.CODEX)  # noqa: E731
    assert c("Do you want to proceed?\n❯ 1. Yes\n  2. No")[0] is W
    assert c("Run command? (y/n)")[0] is W
    assert c("Allow this tool call?")[0] is W
    assert c("✻ Thinking… (esc to interrupt)")[0] is R
    assert c("Error: boom\n")[0] is F
    assert c("done\n│ > \n  ? for shortcuts")[0] is I
    assert c("")[0] is U
    assert c("hello world")[0] is U
    for t in ("Allow?", "esc to interrupt", "Error: x"):
        assert 0 < c(t)[1] < 1


# ---------------------------------------------------------------- last_active
def _claude_env(tmp_path, updated=None, status=1700000000000):
    proc, sd, home = tmp_path / "proc", tmp_path / "s", tmp_path / "home"
    for d in (proc, sd, home):
        d.mkdir()
    (proc / "uptime").write_text("100.0 0\n")
    mkproc(proc, 11, 1, "claude", ["claude"], start=1000)
    data = {"pid": 11, "procStart": "1000", "status": "idle", "sessionId": "11111111-2222-3333-4444-555555555555",
            "cwd": "/proj/x", "statusUpdatedAt": status}
    if updated is not None:
        data["updatedAt"] = updated
    (sd / "11.json").write_text(json.dumps(data))
    return proc, sd, home


def test_last_active_unknown_is_zero(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    mkproc(proc, 22, 1, "codex", ["codex"])
    a = detect_agents([pane(22)], sessions_dir=tmp_path / "n", proc_root=proc, home=tmp_path / "h")
    assert a[0].last_active == 0.0


def test_last_active_max_of_json_transcript_and_tmux(tmp_path):
    import os

    proc, sd, home = _claude_env(tmp_path, updated=1700000500000)
    a = detect_agents([pane(11)], sessions_dir=sd, proc_root=proc, home=home)
    assert a[0].last_active == 1700000500.0  # updatedAt beats statusUpdatedAt; no transcript yet
    tdir = home / ".claude" / "projects" / "-proj-x"
    tdir.mkdir(parents=True)
    tr = tdir / "11111111-2222-3333-4444-555555555555.jsonl"
    tr.write_text("{}\n")
    os.utime(tr, (1700009000, 1700009000))
    a = detect_agents([pane(11)], sessions_dir=sd, proc_root=proc, home=home)
    assert a[0].last_active == 1700009000.0
    p = pane(11)  # %12, window @1
    a = detect_agents([p], sessions_dir=sd, proc_root=proc, home=home, window_activity={"@1": 1700009500.5})
    assert a[0].last_active == 1700009500.5
    a = detect_agents([p], sessions_dir=sd, proc_root=proc, home=home,
                      window_activity={"%12": 1700000001.0, "@1": 1700000002.0, "@9": 9e12})
    assert a[0].last_active == 1700009000.0  # older tmux data never lowers it; other windows ignored


def test_last_active_stable_across_calls_and_not_wallclock(tmp_path):
    proc, sd, home = _claude_env(tmp_path)
    one = detect_agents([pane(11)], sessions_dir=sd, proc_root=proc, home=home)[0].last_active
    two = detect_agents([pane(11)], sessions_dir=sd, proc_root=proc, home=home)[0].last_active
    assert one == two == 1700000000.0


def test_last_active_devin_transcript_mtime(tmp_path):
    import os

    proc, home = tmp_path / "proc", tmp_path / "home"
    proc.mkdir()
    mkproc(proc, 30, 1, "devin", ["devin"])
    mkproc(proc, 31, 30, "devin", ["devin", "acp"])
    root = home / ".local" / "share" / "devin" / "cli"
    (root / "session_locks").mkdir(parents=True)
    (root / "transcripts").mkdir()
    (root / "session_locks" / "calm-fox.lock").write_text("31")
    tr = root / "transcripts" / "calm-fox.json"
    tr.write_text("{}")
    os.utime(tr, (1700001234, 1700001234))
    a = detect_agents([pane(30, 7)], sessions_dir=tmp_path / "n", proc_root=proc, home=home)
    assert a[0].kind is AgentKind.DEVIN and a[0].last_active == 1700001234.0


def test_live_session_id(tmp_path):
    from pm.chat.sources.claude import live_session_id

    proc, sd, _ = _claude_env(tmp_path)
    assert live_session_id(11, sessions_dir=sd, proc_root=proc) == "11111111-2222-3333-4444-555555555555"
    assert live_session_id(12, sessions_dir=sd, proc_root=proc) is None


# ------------------------------------------------- idle while sub-agents run
def test_idle_with_recent_subagent_is_running(tmp_path):
    import os
    import time

    proc, sd, home = _claude_env(tmp_path)  # status "idle"
    tdir = home / ".claude" / "projects" / "-proj-x"
    sub = tdir / "11111111-2222-3333-4444-555555555555" / "subagents"
    sub.mkdir(parents=True)
    (tdir / "11111111-2222-3333-4444-555555555555.jsonl").write_text("{}\n")
    f = sub / "agent-abc.jsonl"
    f.write_text("{}\n")
    a = detect_agents([pane(11)], sessions_dir=sd, proc_root=proc, home=home)
    assert a[0].state is AgentState.RUNNING  # sub-agent wrote just now
    old = time.time() - 300
    os.utime(f, (old, old))
    a = detect_agents([pane(11)], sessions_dir=sd, proc_root=proc, home=home)
    assert a[0].state is AgentState.IDLE  # sub-agent quiet for 5 min: really finished


def test_idle_without_subagents_stays_idle(tmp_path):
    proc, sd, home = _claude_env(tmp_path)
    a = detect_agents([pane(11)], sessions_dir=sd, proc_root=proc, home=home)
    assert a[0].state is AgentState.IDLE


def test_devin_lock_map_read_once_per_call(tmp_path, monkeypatch):
    from pm.chat.sources import devin as devin_src

    proc = tmp_path / "proc"
    proc.mkdir()
    mkproc(proc, 22, 1, "codex", ["codex"])
    mkproc(proc, 30, 1, "devin", ["devin"])
    mkproc(proc, 40, 1, "devin", ["devin"])
    calls = []
    monkeypatch.setattr(devin_src, "lock_map", lambda home, root: calls.append(root) or {})
    a = detect_agents([pane(22, 1), pane(30, 2), pane(40, 3)], sessions_dir=tmp_path / "n",
                      proc_root=proc, home=tmp_path / "h")
    assert [x.kind for x in a] == [AgentKind.CODEX, AgentKind.DEVIN, AgentKind.DEVIN]
    assert calls == [proc]  # lazy: once, and only because a Devin was found
    calls.clear()
    detect_agents([pane(22, 1)], sessions_dir=tmp_path / "n", proc_root=proc, home=tmp_path / "h")
    assert calls == []


def test_auth_login_processes_are_not_agents(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    mkproc(proc, 1, 0, "init", ["init"])
    runs = [
        ["/home/u/.local/bin/claude", "auth", "login"],
        ["node", "/a/codex", "login", "--device-auth"],
        ["/home/u/.local/bin/devin", "auth", "status"],
        ["/home/u/.local/bin/cursor-agent", "logout"],
        ["/home/u/.local/bin/claude", "--resume", "x"],  # a real agent still counts
    ]
    for i, argv in enumerate(runs):
        mkproc(proc, 100 + i, 1, "bash", ["bash"])
        mkproc(proc, 200 + i, 100 + i, Path(argv[0]).name, argv)
    a = detect_agents([pane(100 + i, i) for i in range(len(runs))],
                      sessions_dir=tmp_path / "none", proc_root=proc, home=tmp_path / "nohome")
    assert [(x.id, x.kind) for x in a] == [("claude-4", AgentKind.CLAUDE)]


def test_failed_line_is_the_newest_failing_line():
    from pm.agents.state import failed_line

    assert failed_line("ok\nError: first\nmore\nAPI Error: 529 overloaded\n> ") == "API Error: 529 overloaded"
    assert failed_line("all good\n> ") == "" and failed_line(None) == ""
    assert failed_line("Error: too old\n" + "x\n" * 10) == ""  # only the tail counts, like classify_pane_text
