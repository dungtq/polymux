"""New Chat: /clear to the agent pane plus tmux clear-history, by pane id, guarded like any message."""
import dataclasses
import os
from pathlib import Path

import pytest
from helpers import audit_text, fake, mk_client, post, snap_with  # noqa: F401

from pm.agents import slash
from pm.core.models import Agent, AgentKind, Tier
from pm.infra import snapshot as services
from pm.tmux import ctl as tmuxctl
from pm.web.render import TEMPLATE_DIR
from pm.web.routes import agents as routes_agents


@pytest.fixture(autouse=True)
def fast_wait(monkeypatch):
    monkeypatch.setattr(routes_agents, "NEWCHAT_POLL_S", 0)
    monkeypatch.setattr(routes_agents, "NEWCHAT_TRIES", 3)

    async def published_only(settings, max_age_s=0):  # never build; use what snap_with published
        return services.published(settings)
    monkeypatch.setattr(services, "snapshot_async", published_only)


def free_agent(kind=AgentKind.CLAUDE, aid="claude-2"):
    return Agent(id=aid, kind=kind, pane_id="%2", session_id="$2", pid=1, cwd="/")


def test_clear_cmd_kinds():
    assert slash.clear_cmd("claude") == "/clear" and slash.clear_cmd("codex") == "/clear"
    assert slash.clear_cmd("other") is None


def test_clear_history_argv(fake):
    st, calls, _ = fake
    tmuxctl.clear_history(st, "%7")
    assert calls == [["clear-history", "-t", "%7"]]
    import pytest
    with pytest.raises(tmuxctl.TmuxError):
        tmuxctl.clear_history(st, "%7; x")


def test_newchat_sends_clear_and_clears_history(fake, monkeypatch):
    st, calls, _ = fake
    a = free_agent()
    snap_with(monkeypatch, [a])
    app, c, sess, tok, st = mk_client(st)
    r = post(c, tok, "/a/claude-2/newchat")
    assert r.status_code == 200 and r.headers["HX-Redirect"] == "/a/claude-2"  # no new id seen: pane id
    assert ["send-keys", "-l", "-t", "%2", "--", "/clear"] in calls
    assert ["send-keys", "-t", "%2", "--", "Enter"] in calls
    assert calls[-1] == ["clear-history", "-t", "%2"]
    assert '"action":"agent_newchat"' in audit_text(st) and '"result":"ok"' in audit_text(st)


def test_newchat_guards(fake, monkeypatch):
    st, calls, _ = fake
    snap_with(monkeypatch, [free_agent()])
    app, c, sess, tok, st = mk_client(st)
    assert post(c, tok, "/a/claude-2/newchat", csrf=False).status_code == 403
    assert post(c, "bad", "/a/claude-2/newchat").status_code == 403
    assert post(c, tok, "/a/claude-2/newchat", origin="https://evil.example").status_code == 403
    app, c, sess, tok, st = mk_client(st, ceiling=Tier.SAFE)
    assert post(c, tok, "/a/claude-2/newchat").status_code == 403
    assert not calls
    app, c, sess, tok, st = mk_client(st)
    assert post(c, tok, "/a/nope-9/newchat").status_code == 404


def test_newchat_allowed_in_protected_session_but_not_unsupported_kind(fake, monkeypatch):
    st, calls, agent = fake  # claude-1 lives in the protected session "prot": allowed, like /message
    other = free_agent(AgentKind.OTHER, "other-2")
    snap_with(monkeypatch, [agent, other])
    app, c, sess, tok, st = mk_client(st)
    assert post(c, tok, "/a/other-2/newchat").status_code == 409
    assert not any(a[0] in ("send-keys", "clear-history") for a in calls)
    assert post(c, tok, "/a/claude-1/newchat").status_code == 200
    assert calls[-1] == ["clear-history", "-t", "%1"]


def test_drawer_pills_and_button_wiring():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "src" / "pm"
    from pm.web.render import env
    drawer = env.get_template("_drawer.html").render()
    assert 'id="vc-id"' not in drawer
    assert 'id="vc-model"' in drawer and 'class="kpill kp-model vc-model' in drawer  # the one model pill
    js = (root / "static/js/drawer.js").read_text()
    assert "pills(" in js and "data-model" in js  # the aside pill reads the framed heading's data-model
    head = (TEMPLATE_DIR / "agent.html").read_text() + (TEMPLATE_DIR / "_composer_macros.html").read_text()  # New Chat lives in the agent header, after Builder
    assert "/newchat" in head and "New Chat" in head and "data-newchat" in head
    assert head.index(">Builder</a>") < head.index("data-newchat")
    assert "data-newchat" not in (TEMPLATE_DIR / "_chat.html").read_text()


def _seq(monkeypatch, snaps):
    """services.snapshot_async yields these snapshots in turn (the last one repeats)."""
    import time
    items = [services.Snapshot(ts=time.time(), agents=tuple(x), sessions=()) for x in snaps]
    calls = []

    async def fake_snap(settings, max_age_s=0):
        calls.append(1)
        return items[min(len(calls) - 1, len(items) - 1)]
    monkeypatch.setattr(services, "snapshot_async", fake_snap)
    return calls


def test_newchat_redirects_to_the_new_agent_id(fake, monkeypatch):
    st, calls, _ = fake
    old = free_agent()
    new = dataclasses.replace(old, id="claude-abc123")
    snap_with(monkeypatch, [old])
    polls = _seq(monkeypatch, [[old], [old], [new]])  # the registry shows the new id on the 3rd look
    app, c, sess, tok, st = mk_client(st)
    r = post(c, tok, "/a/claude-2/newchat")
    assert r.status_code == 200 and r.headers["HX-Redirect"] == "/a/claude-abc123"
    assert len(polls) == 3


def test_newchat_embed_keeps_embed(fake, monkeypatch):
    from helpers import ORIGIN
    st, calls, _ = fake
    old = free_agent()
    new = dataclasses.replace(old, id="claude-abc123")
    snap_with(monkeypatch, [old])
    _seq(monkeypatch, [[old], [new]])
    app, c, sess, tok, st = mk_client(st)
    r = c.post("/a/claude-2/newchat", data={"csrf": tok},
               headers={"Origin": ORIGIN, "HX-Request": "true",
                        "HX-Current-URL": f"{ORIGIN}/a/claude-2?embed=1"})
    assert r.headers["HX-Redirect"] == "/a/claude-abc123?embed=1"


def test_newchat_other_kind_quits_and_relaunches(fake, monkeypatch):
    st, calls, _ = fake
    old = free_agent(AgentKind.DEVIN, "devin-2")
    new = dataclasses.replace(old, id="devin-abc123")
    snap_with(monkeypatch, [old])
    _seq(monkeypatch, [[old], [new]])
    seen = []
    monkeypatch.setattr(routes_agents.msg, "restart_chat", lambda s, a, cmd: seen.append((a.id, cmd)))
    app, c, sess, tok, st = mk_client(st)
    r = post(c, tok, "/a/devin-2/newchat")
    assert r.status_code == 200 and r.headers["HX-Redirect"] == "/a/devin-abc123"
    assert seen == [("devin-2", "/exit")]
    assert '"restart":1' in audit_text(st)


def test_relaunch_argv_keeps_flags_drops_resume_and_prompt(tmp_path):
    from pm.agents import message as m
    d = tmp_path / "7"
    d.mkdir()
    (d / "cmdline").write_bytes(b"devin\0--permission-mode\0dangerous\0-r\0old-one\0--\0do it\0")
    a = free_agent(AgentKind.DEVIN, "devin-2")
    a = dataclasses.replace(a, pid=7)
    assert m._relaunch_argv(a, tmp_path) == ["devin", "--permission-mode", "dangerous"]
    (d / "cmdline").write_bytes(b"/home/u/.local/bin/agent\0--use-system-ca\0/x/index.js\0--yolo\0")
    assert m._relaunch_argv(a, tmp_path) == ["/home/u/.local/bin/agent", "--yolo"]
    (d / "cmdline").write_bytes(b"/home/u/.local/bin/agy\0--mode\0plan\0--conversation\0c-1\0-i\0hi\0")
    assert m._relaunch_argv(a, tmp_path) == ["/home/u/.local/bin/agy", "--mode", "plan"]


def _proc(root, pid, ppid, argv):
    d = root / str(pid)
    d.mkdir()
    (d / "cmdline").write_bytes(b"\0".join(x.encode() for x in argv) + b"\0")
    (d / "stat").write_text(f"{pid} (x) S {ppid} " + " ".join(["0"] * 30))
    return d


def _tpane(pid=5, command="fish", dead=False):
    from pm.core.models import TmuxPane
    return TmuxPane(id="%2", window_id="@2", session_id="$2", pid=pid, command=command, cwd="/", dead=dead)


@pytest.fixture(autouse=True)
def _no_account(monkeypatch):
    """These restart tests pass settings=None: no registry lookup (the default account)."""
    from pm.agents import registry
    monkeypatch.setattr(registry, "account_of", lambda s, i: "")
    monkeypatch.setattr(registry, "expect_account", lambda *a: None)


def test_restart_chat_in_an_interactive_shell_waits_for_the_agent_then_types_command(tmp_path, monkeypatch):
    from pm.agents import message as m
    _proc(tmp_path, 5, 1, ["-fish"])  # the user's interactive shell runs the agent: the pane outlives it
    agent_dir = _proc(tmp_path, 7, 5, ["devin", "--permission-mode", "dangerous"])
    a = dataclasses.replace(free_agent(AgentKind.DEVIN, "devin-2"), pid=7)
    log = []
    monkeypatch.setattr(m, "verify_target", lambda *a, **k: None)
    monkeypatch.setattr(m, "send_message", lambda s, ag, t, **k: log.append(("msg", t)))
    # the mode check, then the wait: agent running, a shell listed while its pid still lives (no proof), exited
    looks = [("fish", True), ("devin", True), ("fish", True), ("fish", False)]

    def find(s, p):
        cmd, alive = looks.pop(0)
        if not alive and agent_dir.exists():
            import shutil
            shutil.rmtree(agent_dir)
        return _tpane(command=cmd)
    monkeypatch.setattr(m, "_find_pane", find)
    monkeypatch.setattr(m.tmuxctl, "clear_history", lambda s, p: log.append(("clear", p)))
    monkeypatch.setattr(m.tmuxctl, "send_keys", lambda s, p, t: log.append(("keys", t)))
    monkeypatch.setattr(m.tmuxctl, "send_key", lambda s, p, k: log.append(("key", k)))
    monkeypatch.setattr(m.tmuxctl, "respawn_pane", lambda *a: log.append(("respawn",)))
    m.restart_chat(None, a, "/exit", proc_root=tmp_path, sleep=lambda x: None)
    assert log == [("msg", "/exit"), ("clear", "%2"), ("keys", "clear; devin --permission-mode dangerous"),
                   ("key", "Enter")]
    assert not looks


def test_restart_chat_shell_listed_but_agent_alive_times_out(tmp_path, monkeypatch):
    """A shell listed as the pane command is no proof the agent quit; its pid must be gone."""
    from pm.agents import message as m
    _proc(tmp_path, 5, 1, ["fish"])
    _proc(tmp_path, 7, 5, ["devin"])
    a = dataclasses.replace(free_agent(AgentKind.DEVIN, "devin-2"), pid=7)
    monkeypatch.setattr(m, "verify_target", lambda *a, **k: None)
    monkeypatch.setattr(m, "send_message", lambda *a, **k: None)
    monkeypatch.setattr(m, "_find_pane", lambda s, p: _tpane())
    monkeypatch.setattr(m.tmuxctl, "send_keys", lambda *a: pytest.fail("typed while the agent runs"))
    with pytest.raises(m.Refused):
        m.restart_chat(None, a, "/exit", proc_root=tmp_path, sleep=lambda x: None)


@pytest.mark.parametrize("shell,typed", [
    ("fish", "clear; devin 'x\\\\' '; touch /tmp/M #'"),
    ("bash", "clear; devin 'x\\' '; touch /tmp/M #'"),
])
def test_restart_chat_quotes_the_relaunch_for_the_pane_shell(tmp_path, monkeypatch, shell, typed):
    """The command line typed into the pane is quoted for the shell that reads it (fish reads \\ and \\'
    inside single quotes, sh does not), so an argument ending in a backslash cannot end the quote early."""
    from pm.agents import message as m
    _proc(tmp_path, 5, 1, ["-" + shell])
    agent_dir = _proc(tmp_path, 7, 5, ["devin", "x\\", "; touch /tmp/M #"])
    a = dataclasses.replace(free_agent(AgentKind.DEVIN, "devin-2"), pid=7)
    log = []
    monkeypatch.setattr(m, "verify_target", lambda *a, **k: None)
    monkeypatch.setattr(m, "send_message", lambda *a, **k: None)
    looks = [(shell, True), (shell, False)]

    def find(s, p):
        cmd, alive = looks.pop(0)
        if not alive and agent_dir.exists():
            import shutil
            shutil.rmtree(agent_dir)
        return _tpane(command=cmd)
    monkeypatch.setattr(m, "_find_pane", find)
    monkeypatch.setattr(m.tmuxctl, "clear_history", lambda s, p: None)
    monkeypatch.setattr(m.tmuxctl, "send_keys", lambda s, p, t: log.append(t))
    monkeypatch.setattr(m.tmuxctl, "send_key", lambda s, p, k: None)
    m.restart_chat(None, a, "/exit", proc_root=tmp_path, sleep=lambda x: None)
    assert log == [typed]


def test_restart_chat_refuses_a_command_it_cannot_type(tmp_path, monkeypatch):
    """A control character in the agent's argv (a typed newline would run the line early): refuse before
    /exit is sent, so the agent is never left stopped without its relaunch; nothing is typed."""
    from pm.agents import message as m
    _proc(tmp_path, 5, 1, ["fish"])
    _proc(tmp_path, 7, 5, ["devin", "a\nb"])
    a = dataclasses.replace(free_agent(AgentKind.DEVIN, "devin-2"), pid=7)
    monkeypatch.setattr(m, "verify_target", lambda *a, **k: None)
    monkeypatch.setattr(m, "send_message", lambda *a, **k: pytest.fail("sent /exit for a command it cannot type"))
    monkeypatch.setattr(m, "_find_pane", lambda s, p: _tpane())
    monkeypatch.setattr(m, "_wait_exit", lambda *a, **k: pytest.fail("waited for an exit it never asked for"))
    monkeypatch.setattr(m.tmuxctl, "send_keys", lambda *a: pytest.fail("typed an untypeable command"))
    with pytest.raises(m.Refused, match="cannot be typed"):
        m.restart_chat(None, a, "/exit", proc_root=tmp_path, sleep=lambda x: None)


@pytest.mark.parametrize("root", [["fish", "-c", "claude --model haiku -- 'do it'"], None],
                         ids=["shell-c-parent", "agent-is-pane-process"])
def test_restart_chat_on_a_launched_pane_respawns_it(tmp_path, monkeypatch, root):
    """A pane Launch started ends with its agent: keep it (remain-on-exit) and respawn the command."""
    from pm.agents import message as m
    if root:
        _proc(tmp_path, 5, 1, root)
        _proc(tmp_path, 7, 5, ["claude", "--model", "haiku", "--", "do it"])
    else:
        _proc(tmp_path, 5, 1, ["claude", "--model", "haiku", "--", "do it"])
    a = dataclasses.replace(free_agent(), pid=7 if root else 5, cwd="/proj")
    log = []
    monkeypatch.setattr(m, "verify_target", lambda *a, **k: None)
    monkeypatch.setattr(m, "send_message", lambda s, ag, t, **k: log.append(("msg", t)))
    looks = iter([_tpane(), _tpane(), _tpane(dead=True)])
    monkeypatch.setattr(m, "_find_pane", lambda s, p: next(looks))
    monkeypatch.setattr(m.tmuxctl, "set_remain_on_exit", lambda s, p, on: log.append(("remain", p, on)))
    monkeypatch.setattr(m.tmuxctl, "clear_history", lambda s, p: log.append(("clear", p)))
    monkeypatch.setattr(m.tmuxctl, "respawn_pane", lambda s, p, cwd, cmd, **kw: log.append(("respawn", p, cwd, cmd)))
    monkeypatch.setattr(m.tmuxctl, "send_keys", lambda *a: pytest.fail("typed into a pane that closes"))
    r = m.restart_chat(None, a, "/exit", proc_root=tmp_path, sleep=lambda x: None)
    assert r.ok and r.pane_id == "%2"
    assert log == [("remain", "%2", True), ("msg", "/exit"), ("clear", "%2"),
                   ("respawn", "%2", "/proj", ["claude", "--model", "haiku"]), ("remain", "%2", False)]
    looks = iter([_tpane()] + [_tpane()] * 100)  # never exits: remain-on-exit is reset all the same
    log.clear()
    with pytest.raises(m.Refused):
        m.restart_chat(None, a, "/exit", proc_root=tmp_path, sleep=lambda x: None)
    assert log[0] == ("remain", "%2", True) and log[-1] == ("remain", "%2", False)
    assert not any(e[0] == "respawn" for e in log)


def test_remain_on_exit_and_respawn_argv(fake):
    st, calls, _ = fake
    tmuxctl.set_remain_on_exit(st, "%7", True)
    tmuxctl.set_remain_on_exit(st, "%7", False)
    tmuxctl.respawn_pane(st, "%7", "/p", ["claude", "--model", "x; echo INJECTED #"])
    assert calls == [["set-option", "-p", "-t", "%7", "remain-on-exit", "on"],
                     ["set-option", "-p", "-u", "-t", "%7", "remain-on-exit"],
                     ["show-options", "-gv", "default-shell"],
                     ["respawn-pane", "-t", "%7", "-c", "/p", "--", "/bin/sh", "-c", 'exec "$@"', "sh",
                      "claude", "--model", "x; echo INJECTED #"]]
    with pytest.raises(tmuxctl.TmuxError):
        tmuxctl.respawn_pane(st, "%7; x", "/p", ["y"])
    with pytest.raises(tmuxctl.TmuxError):
        tmuxctl.respawn_pane(st, "%7", "/p", [])


def test_verify_target_ignores_view_sessions_and_duplicate_listings(tmp_path, monkeypatch):
    from pm.agents import message as m
    from pm.core.models import TmuxPane, TmuxSession, TmuxWindow
    for pid, ppid in ((7, 5), (5, 1)):
        (tmp_path / str(pid)).mkdir()
        (tmp_path / str(pid) / "stat").write_text(f"{pid} (x) S {ppid} " + " ".join(["0"] * 30))
    pane = lambda sid: TmuxPane(id="%2", window_id="@1", session_id=sid, pid=5, command="devin",
                                cwd="/", active=True, title="", dead=False)
    sess = lambda sid, name: TmuxSession(id=sid, name=name, attached=0, protected=False, windows=(
        TmuxWindow(id="@1", session_id=sid, index=0, name="w", active=True, panes=(pane(sid),)),))
    monkeypatch.setattr(m.tmuxctl, "list_sessions",
                        lambda s: (sess("$336", "x-view"), sess("$2", "setup")))
    a = dataclasses.replace(free_agent(AgentKind.DEVIN, "devin-2"), pid=7)
    m.verify_target(None, a, proc_root=tmp_path)  # matches its own session although another lists the pane first
    with pytest.raises(m.NotAnAgent):
        m.verify_target(None, dataclasses.replace(a, session_id="$9"), proc_root=tmp_path)


def test_newchat_claude_restarts_when_update_pending(fake, monkeypatch):
    st, calls, _ = fake
    old = free_agent()
    new = dataclasses.replace(old, id="claude-abc123")
    snap_with(monkeypatch, [old])
    _seq(monkeypatch, [[old], [new]])
    seen = []
    monkeypatch.setattr(routes_agents.msg, "update_pending", lambda s, a: True)
    monkeypatch.setattr(routes_agents.msg, "restart_chat", lambda s, a, cmd: seen.append((a.id, cmd)))
    app, c, sess, tok, st = mk_client(st)
    r = post(c, tok, "/a/claude-2/newchat")
    assert r.status_code == 200 and seen == [("claude-2", "/exit")]


def test_newchat_restart_with_a_removed_account_is_a_400(fake, monkeypatch):
    from pm.core.errors import Invalid
    st, calls, _ = fake
    old = free_agent()
    snap_with(monkeypatch, [old])
    monkeypatch.setattr(routes_agents.msg, "update_pending", lambda s, a: True)

    def gone(s, a, cmd):
        raise Invalid("unknown claude account 'gone'")
    monkeypatch.setattr(routes_agents.msg, "restart_chat", gone)
    app, c, sess, tok, st = mk_client(st)
    r = post(c, tok, "/a/claude-2/newchat")
    assert r.status_code == 400 and "chat restart refused" in r.text
    assert '"action":"agent_newchat"' in audit_text(st)


def test_update_pending_reads_status_line(monkeypatch):
    from pm.agents import message as m
    monkeypatch.setattr(m.tmuxctl, "capture_pane",
                        lambda s, p, n: "✔ Update installed · Restart to update")
    assert m.update_pending(None, free_agent())
    assert not m.update_pending(None, free_agent(AgentKind.DEVIN, "devin-2"))
    monkeypatch.setattr(m.tmuxctl, "capture_pane", lambda s, p, n: "idle")
    assert not m.update_pending(None, free_agent())


# ---- New Chat (restart path) on a pane pm's Launch started (`new-window -- "<agent ...>"`) -------------------
FAKE_AGENT = """#!/usr/bin/env python3
import sys, time
print("fake agent ready", flush=True)
for line in sys.stdin:
    if line.strip() == "/exit":
        time.sleep(1)  # an agent takes a moment to quit after /exit
        break
"""


def _launched_agent(s, script, wrapped):
    """A pane started the way Launch starts one: tmux runs the command through the default shell. ``wrapped``:
    the shell stays as the pane's root process with the agent as its child (fish -c does that; ``; true``
    makes sh do it too), and a non-interactive shell has no job control, so pane_current_command names the
    shell while the agent still runs. Unwrapped: the agent is the pane process itself (bash -c execs it)."""
    import time
    sid = tmuxctl.new_session(s, "pmtest-launch")
    cmd = str(script) + "; true" if wrapped else "exec " + str(script)
    out = tmuxctl.run(s, ["new-window", "-d", "-P", "-F", "#{pane_id} #{pane_pid}", "-t", sid + ":",
                          "-n", "agent-devin", "--", cmd]).split()
    pane, root = out[0], int(out[1])
    for _ in range(50):
        kids = [p for p in os.listdir("/proc") if p.isdigit() and _ppid(int(p)) == root]
        pid = int(kids[0]) if wrapped and kids else root
        if not wrapped or kids:
            break
        time.sleep(0.05)
    for _ in range(50):
        if "fake agent ready" in tmuxctl.capture_pane(s, pane):
            break
        time.sleep(0.05)
    return Agent(id="devin-9", kind=AgentKind.DEVIN, pane_id=pane, session_id=sid, pid=pid, cwd=str(script.parent))


def _ppid(pid):
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return -1


def _pane(s, pane_id):
    return next((p for ss in tmuxctl.list_sessions(s) for w in ss.windows for p in w.panes if p.id == pane_id),
                None)


@pytest.mark.parametrize("wrapped", [True, False], ids=["shell-c-parent", "exec"])
def test_restart_chat_keeps_a_launched_pane_and_relaunches_in_it(private_tmux, tmp_path, wrapped):
    """/exit ended the launched command, so tmux closed the pane and its window; New Chat must
    restart the agent in the same pane instead (the window, and the session it keeps, persist)."""
    import time

    from pm.agents import message as m
    s = private_tmux
    script = tmp_path / "fakeagent"
    script.write_text(FAKE_AGENT)
    script.chmod(0o755)
    a = _launched_agent(s, script, wrapped)
    old_root = _pane(s, a.pane_id).pid
    m.restart_chat(s, a, "/exit", sleep=time.sleep, wait_s=8)
    p = None
    for _ in range(50):
        p = _pane(s, a.pane_id)
        if p and not p.dead and p.pid != old_root and "fake agent ready" in tmuxctl.capture_pane(s, a.pane_id):
            break
        time.sleep(0.1)
    assert p is not None, "the pane (and its window) closed with the old agent"
    assert not p.dead and p.pid != old_root
    assert "fake agent ready" in tmuxctl.capture_pane(s, a.pane_id)  # the agent command runs again
    assert tmuxctl.run(s, ["show-options", "-p", "-v", "-t", a.pane_id, "remain-on-exit"]).strip() == ""


# ---- the typed relaunch in a real interactive fish / bash pane --------------------------------------------------
ARGV_AGENT = """#!/bin/sh
for a in "$@"; do printf '<%s>' "$a"; done >> {out}
echo >> {out}
echo "fake agent ready"
while read -r line; do [ "$line" = /exit ] && break; done
"""


@pytest.mark.parametrize("shell", ["fish", "bash"])
def test_restart_chat_types_a_literal_relaunch_into_a_real_shell(private_tmux, tmp_path, shell):
    """The relaunch typed into an interactive fish (or bash) pane runs the agent with exactly its old argv:
    an argument ending in a backslash followed by '; cmd' stays two literal arguments."""
    import shutil
    import time

    from pm.agents import message as m
    sh = shutil.which(shell)
    if not sh:
        pytest.skip(f"{shell} not installed")
    s = private_tmux
    out, marker = tmp_path / "argv.out", tmp_path / "PWNED"
    script = tmp_path / "fakeagent"
    script.write_text(ARGV_AGENT.format(out=out))
    script.chmod(0o755)
    flags = ["--no-config", "--private"] if shell == "fish" else ["--norc", "--noprofile", "-i"]
    sid = tmuxctl.new_session(s, "pmtest-shell", str(tmp_path))
    env = [x for k in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME") for x in ("-e", f"{k}={tmp_path}")]
    pane, root = tmuxctl.run(s, ["new-window", "-d", "-P", "-F", "#{pane_id} #{pane_pid}", "-t", sid + ":",
                                 "-c", str(tmp_path), *env, "--", sh, *flags]).split()
    argv = ["/bin/sh", str(script), "x\\", f"; touch {marker} #"]
    time.sleep(0.5)  # let the shell draw its prompt
    tmuxctl.send_keys(s, pane, tmuxctl.quote_for_shell(shell, argv))
    tmuxctl.send_key(s, pane, "Enter")
    pid = None
    for _ in range(100):
        kids = [int(p) for p in os.listdir("/proc") if p.isdigit() and _ppid(int(p)) == int(root)]
        if kids and "fake agent ready" in tmuxctl.capture_pane(s, pane):
            pid = kids[0]
            break
        time.sleep(0.05)
    assert pid, tmuxctl.capture_pane(s, pane)
    a = Agent(id="devin-9", kind=AgentKind.DEVIN, pane_id=pane, session_id=sid, pid=pid, cwd=str(tmp_path))
    m.restart_chat(s, a, "/exit", sleep=time.sleep, wait_s=8)
    for _ in range(100):
        if out.exists() and len(out.read_text().splitlines()) == 2:
            break
        time.sleep(0.05)
    assert out.read_text().splitlines() == [f"<x\\><; touch {marker} #>"] * 2
    assert not marker.exists()


@pytest.mark.parametrize("pane_ends", [True, False], ids=["respawn", "typed-line"])
def test_restart_chat_keeps_the_stored_account_env(tmp_path, monkeypatch, pane_ends):
    from pm.agents import message as m
    from pm.agents import registry
    if pane_ends:
        _proc(tmp_path, 5, 1, ["claude", "--model", "haiku"])
        pid = 5
    else:
        _proc(tmp_path, 5, 1, ["-bash"])
        agent_dir = _proc(tmp_path, 7, 5, ["claude", "--model", "haiku"])
        pid = 7
    a = dataclasses.replace(free_agent(), pid=pid, cwd="/proj")
    env = {"CLAUDE_CONFIG_DIR": "/st/accounts/claude/work"}
    seen, log = [], []
    monkeypatch.setattr(registry, "account_of", lambda s, i: "work")
    monkeypatch.setattr(registry, "expect_account", lambda s, k, p, acc: seen.append((k, p, acc)))
    monkeypatch.setattr(m, "account_env", lambda s, k, n: env if (k, n) == ("claude", "work") else pytest.fail(n))
    monkeypatch.setattr(m, "verify_target", lambda *a, **k: None)
    monkeypatch.setattr(m, "send_message", lambda *a, **k: None)
    looks = iter([_tpane(), _tpane(), _tpane(dead=True)] if pane_ends else
                 [_tpane(command="bash"), _tpane(command="claude"), _tpane(command="bash")])

    def find(s, p):
        pane = next(looks)
        if pane.command == "bash" and not pane_ends and agent_dir.exists():
            import shutil
            shutil.rmtree(agent_dir)  # the agent process is gone once the shell shows again
        return pane
    monkeypatch.setattr(m, "_find_pane", find)
    monkeypatch.setattr(m.tmuxctl, "set_remain_on_exit", lambda *a: None)
    monkeypatch.setattr(m.tmuxctl, "clear_history", lambda *a: None)
    monkeypatch.setattr(m.tmuxctl, "respawn_pane", lambda s, p, cwd, cmd, **kw: log.append(("respawn", cmd, kw)))
    monkeypatch.setattr(m.tmuxctl, "send_keys", lambda s, p, t: log.append(("keys", t)))
    monkeypatch.setattr(m.tmuxctl, "send_key", lambda *a: None)
    m.restart_chat(None, a, "/exit", proc_root=tmp_path, sleep=lambda x: None)
    if pane_ends:
        assert log == [("respawn", ["claude", "--model", "haiku"], {"env": env})]
    else:
        assert log == [("keys", "clear; env 'CLAUDE_CONFIG_DIR=/st/accounts/claude/work' claude --model haiku")]
    assert seen == [("claude", "%2", "work")]
