import os
import re
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest
from helpers import servers_client

from pm.infra import procctl, resources
from pm.infra.resources import Proc


@pytest.fixture
def sleeper():
    p = subprocess.Popen(["sleep", "60"])
    time.sleep(0.05)
    yield p
    p.kill()
    p.wait()


def _start(pid):
    return resources.scan()[pid].start


def test_mainthread_comm_is_named_from_cmdline(tmp_path):
    d = tmp_path / "7"
    d.mkdir()
    (d / "stat").write_text("7 (MainThread) S 1 " + " ".join(["0"] * 30))
    (d / "cmdline").write_bytes(b"node\0/x/node_modules/.bin/exa-mcp-server\0--flag\0")
    assert resources.scan(tmp_path)[7].comm == "node exa-mcp-server"


def test_signal_term_and_start_mismatch(fake_tmux, monkeypatch, sleeper):
    monkeypatch.setenv("FAKE_TMUX_OUT", "")
    with pytest.raises(procctl.CtlError) as e:
        procctl.signal_process(fake_tmux, "term", sleeper.pid, _start(sleeper.pid) + 1)
    assert e.value.status == 409 and sleeper.poll() is None
    msg = procctl.signal_process(fake_tmux, "term", sleeper.pid, _start(sleeper.pid))
    assert "SIGTERM" in msg and sleeper.wait(5) == -15


def test_signal_pause_resume_kill(fake_tmux, monkeypatch, sleeper):
    monkeypatch.setenv("FAKE_TMUX_OUT", "")
    st = _start(sleeper.pid)
    procctl.signal_process(fake_tmux, "stop", sleeper.pid, st)
    time.sleep(0.1)
    assert Path(f"/proc/{sleeper.pid}/stat").read_text().split(")")[1].split()[0] == "T"
    procctl.signal_process(fake_tmux, "cont", sleeper.pid, st)
    procctl.signal_process(fake_tmux, "kill", sleeper.pid, st)
    assert sleeper.wait(5) == -9


def test_refusals(fake_tmux, monkeypatch, sleeper):
    monkeypatch.setenv("FAKE_TMUX_OUT", "")
    st = _start(sleeper.pid)
    for pid in (0, 1):
        with pytest.raises(procctl.CtlError):
            procctl.signal_process(fake_tmux, "kill", pid, 0)
    with pytest.raises(procctl.CtlError, match="pm itself"):
        procctl.signal_process(fake_tmux, "kill", os.getpid(), _start(os.getpid()))
    with pytest.raises(procctl.CtlError, match="owned"):
        procctl.signal_process(fake_tmux, "kill", sleeper.pid, st, uid=os.getuid() + 1)
    with pytest.raises(procctl.CtlError, match="disabled"):
        procctl.signal_process(replace(fake_tmux, proc_manage=False), "kill", sleeper.pid, st)
    with pytest.raises(procctl.CtlError, match="unknown"):
        procctl.signal_process(fake_tmux, "hup", sleeper.pid, st)
    assert sleeper.poll() is None


def test_protected_tmux_tree_refused(fake_tmux, monkeypatch, sleeper):
    monkeypatch.setenv("FAKE_TMUX_OUT", f"main\t{sleeper.pid}\n")  # all sessions protected by default
    with pytest.raises(procctl.CtlError, match="protected tmux"):
        procctl.signal_process(fake_tmux, "kill", sleeper.pid, _start(sleeper.pid))
    assert sleeper.poll() is None


def test_protected_comm(fake_tmux, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_TMUX_OUT", "")
    d = tmp_path / str(os.getpid() + 50000)
    pid = os.getpid() + 50000
    d.mkdir()
    (d / "stat").write_text(f"{pid} (sshd) S 1 " + " ".join(["0"] * 30))
    with pytest.raises(procctl.CtlError, match="protected"):
        procctl.signal_process(fake_tmux, "kill", pid, 0, proc=tmp_path, me=1)


def test_container_actions(fake_tmux):
    calls = []

    def run(argv, t):
        calls.append(argv)
        return (0, "web\ngitea\nnginx-ui\n") if argv[:2] == ["docker", "ps"] else (0, "")

    assert procctl.container_action(fake_tmux, "kill", "web", run) == "Killed web."
    assert calls[-1] == ["docker", "kill", "--", "web"]
    assert procctl.container_action(fake_tmux, "stop", "web", run) == "Stopped web."
    assert calls[-1][:4] == ["docker", "stop", "--time", "10"]
    for bad, code in (("gitea", 403), ("nginx-ui", 403), ("ghost", 404), ("-rm", 400), ("a b", 400)):
        with pytest.raises(procctl.CtlError) as e:
            procctl.container_action(fake_tmux, "kill", bad, run)
        assert e.value.status == code
    n = len(calls)
    with pytest.raises(procctl.CtlError):
        procctl.container_action(replace(fake_tmux, container_manage=False), "kill", "web", run)
    assert not any(c[1] == "kill" for c in calls[n:])


def test_routes_guarded_and_audited(fake_tmux, tmp_path, monkeypatch, sleeper):
    monkeypatch.setenv("FAKE_TMUX_OUT", "")
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    csrf = re.search(r'name="csrf" value="([^"]+)"', c.get("/servers?t=services").text).group(1)
    h = {"Origin": "https://pm.example.test"}
    form = {"pid": str(sleeper.pid), "start": str(_start(sleeper.pid))}
    assert c.post("/servers/proc/term", data=form).status_code == 403  # no csrf
    assert c.post("/servers/proc/hup", data={**form, "csrf": csrf}, headers=h).status_code == 404
    assert c.post("/servers/proc/term", data={"pid": "x", "start": "1", "csrf": csrf}, headers=h).status_code == 400
    assert sleeper.poll() is None
    r = c.post("/servers/proc/term", data={**form, "csrf": csrf}, headers=h)
    assert r.status_code == 200 and r.headers["hx-trigger"] == "res-changed" and sleeper.wait(5) == -15
    assert "proc_term" in (tmp_path / "st" / "audit.jsonl").read_text()
    assert c.post("/servers/containers/x/explode", data={"csrf": csrf}, headers=h).status_code == 404


def test_resources_tab_has_controls_and_services_has_container_buttons(fake_tmux, tmp_path, monkeypatch):
    items = ((4242, 99, 10**7, 1.0, "S"),)
    res = resources.Resources(time.time(), 4, 10**9, 5 * 10**8, 10.0,
                              (resources.Row("node exa-mcp-server", 1, 1.0, 10**7, 0, items),), (), (), ())
    monkeypatch.setattr(resources, "get", lambda settings, **kw: res)
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    h = c.get("/servers?t=resources").text
    assert "/servers/proc/term" in h and "/servers/proc/kill" in h and 'value="99"' in h
    s = c.get("/servers?t=services").text
    assert "/servers/containers/" in s and "/kill" in s


# ---------------------------------------------------------------- M4: start, compose stacks, whole programs
def test_container_start(fake_tmux):
    calls = []

    def run(argv, t):
        calls.append(argv)
        return (0, "web\n") if argv[:2] == ["docker", "ps"] else (0, "")

    assert procctl.container_action(fake_tmux, "start", "web", run) == "Started web."
    assert calls[-1] == ["docker", "start", "--", "web"]


def _stack_run(calls, members="web\nnginx-edge\ndb\n", ps_rc=0, rc=0):
    def run(argv, t):
        calls.append((argv, t))
        if argv[:2] == ["docker", "ps"]:
            return ps_rc, members
        return rc, "boom\x1b[31m"
    return run


def test_restart_stack_finds_members_and_skips_protected(fake_tmux):
    calls = []
    msg, n = procctl.restart_stack(replace(fake_tmux, container_protected=("nginx*",)), "app", _stack_run(calls))
    assert n == 2 and "Restarted stack app: 2 containers, 1 protected skipped." == msg
    assert calls[0][0] == ["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=app",
                           "--format", "{{.Names}}"]
    assert calls[1][0] == ["docker", "restart", "--time", "10", "--", "db", "web"] and len(calls) == 2


def test_restart_stack_refusals(fake_tmux):
    st = replace(fake_tmux, container_protected=("nginx*",))
    for bad in ("-x", "App", "a/b", "a b", "", "x" * 64, "a\nb"):
        calls = []
        with pytest.raises(procctl.CtlError) as e:
            procctl.restart_stack(st, bad, _stack_run(calls))
        assert e.value.status == 400 and calls == [], bad
    cases = ((dict(members=""), 404), (dict(members="nginx-a\n"), 403), (dict(ps_rc=1), 503), (dict(rc=1), 502))
    for kw, code in cases:
        calls = []
        with pytest.raises(procctl.CtlError) as e:
            procctl.restart_stack(st, "app", _stack_run(calls, **kw))
        assert e.value.status == code and "\x1b" not in str(e.value)
    calls = []
    with pytest.raises(procctl.CtlError, match="disabled"):
        procctl.restart_stack(replace(st, container_manage=False), "app", _stack_run(calls))
    assert calls == []


def _fake_proc(root, rows):
    """rows: (pid, comm, ppid) -> /proc-like stat files (rss > 0, start 7)."""
    for pid, comm, ppid in rows:
        d = root / str(pid)
        d.mkdir()
        f = ["S", str(ppid)] + ["0"] * 30
        f[19], f[21] = "7", "100"
        (d / "stat").write_text(f"{pid} ({comm}) " + " ".join(f))
    return root


def test_signal_program_skips_refused_and_counts(fake_tmux, monkeypatch, tmp_path):
    proc = _fake_proc(tmp_path, [(900001, "worker", 1), (900002, "worker", 1), (900003, "worker", 900099),
                                 (900004, "worker", 2), (900005, "other", 1), (900099, "bash", 1)])
    monkeypatch.setenv("FAKE_TMUX_OUT", "main\t900099\n")  # worker 900003 runs in a protected session
    sent = []
    monkeypatch.setattr(procctl.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    msg, n = procctl.signal_program(fake_tmux, "term", "worker", "worker", proc=proc, me=1)
    assert n == 2 and sorted(p for p, _ in sent) == [900001, 900002]
    assert {s for _, s in sent} == {procctl.signal.SIGTERM}
    assert msg == "Asked to quit (SIGTERM): worker, 2 processes, 2 skipped (protected or gone)."


def test_signal_program_refusals(fake_tmux, monkeypatch, tmp_path):
    proc = _fake_proc(tmp_path, [(900001, "worker", 1), (900002, "worker", 1), (900003, "sshd", 1)])
    monkeypatch.setenv("FAKE_TMUX_OUT", "")
    sent = []
    monkeypatch.setattr(procctl.os, "kill", lambda pid, sig: sent.append(pid))
    cases = [(("kill", "worker", "work"), 403, "confirm"), (("kill", "ghost", "ghost"), 404, "no such"),
             (("kill", "sshd", "sshd"), 403, "may be signalled"), (("hup", "worker", "worker"), 400, "unknown"),
             (("kill", "", ""), 400, "bad program"), (("kill", "a\x1bb", "a\x1bb"), 400, "bad program")]
    for args, code, word in cases:
        with pytest.raises(procctl.CtlError, match=word) as e:
            procctl.signal_program(fake_tmux, *args, proc=proc, me=1)
        assert e.value.status == code, args
    monkeypatch.setattr(procctl, "MAX_PROGRAM_PROCS", 1)
    with pytest.raises(procctl.CtlError) as e:
        procctl.signal_program(fake_tmux, "kill", "worker", "worker", proc=proc, me=1)
    assert e.value.status == 409
    with pytest.raises(procctl.CtlError, match="disabled"):
        procctl.signal_program(replace(fake_tmux, proc_manage=False), "kill", "worker", "worker", proc=proc, me=1)
    assert sent == []


def _csrf_h(c):
    csrf = re.search(r'name="csrf" value="([^"]+)"', c.get("/servers?t=services").text).group(1)
    return csrf, {"Origin": "https://pm.example.test"}


def _audit_rows(tmp_path):
    import json
    return [json.loads(x) for x in (tmp_path / "st" / "audit.jsonl").read_text().splitlines()]


def test_stack_restart_route_guarded_and_audited(fake_tmux, tmp_path, monkeypatch):
    calls = []
    real = procctl.restart_stack
    monkeypatch.setattr(procctl, "restart_stack", lambda st, stack: real(st, stack, _stack_run(calls)))
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    csrf, h = _csrf_h(c)
    assert c.post("/servers/stacks/app/restart", data={}, headers=h).status_code == 403  # no csrf
    assert calls == []
    r = c.post("/servers/stacks/app/restart", data={"csrf": csrf, "containers": "gitea"}, headers=h)
    assert r.status_code == 200 and r.headers["hx-trigger"] == "res-changed" and "Restarted stack app" in r.text
    assert calls[-1][0][-3:] == ["--", "db", "web"]  # a client container list is ignored
    assert c.post("/servers/stacks/Bad/restart", data={"csrf": csrf}, headers=h).status_code == 400
    rows = [a for a in _audit_rows(tmp_path) if a["action"] == "stack_restart"]
    ok = [a for a in rows if a["result"] == "ok"][0]
    assert ok["target"] == "app" and ok["detail"] == {"scope": "docker", "n": 2}
    bad = [a for a in rows if a["target"] == "Bad" and a["result"] == "denied"]
    assert bad[0]["detail"] == {"scope": "docker", "reason": "bad stack name"}


def test_program_route_needs_typed_confirm(fake_tmux, tmp_path, monkeypatch):
    (tmp_path / "proc").mkdir()
    proc = _fake_proc(tmp_path / "proc", [(900001, "node exa-mcp-server", 1)])
    sent = []
    monkeypatch.setattr(procctl.os, "kill", lambda pid, sig: sent.append(pid))
    real = procctl.signal_program
    monkeypatch.setattr(procctl, "signal_program", lambda st, a, p, cf: real(st, a, p, cf, proc=proc, me=1))
    monkeypatch.setenv("FAKE_TMUX_OUT", "")
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    csrf, h = _csrf_h(c)
    form = {"csrf": csrf, "program": "node exa-mcp-server"}
    assert c.post("/servers/proc/program/kill", data=form, headers=h).status_code == 403 and sent == []
    assert c.post("/servers/proc/program/hup", data={**form, "confirm": form["program"]}, headers=h).status_code == 404
    r = c.post("/servers/proc/program/kill", data={**form, "confirm": form["program"]}, headers=h)
    assert r.status_code == 200 and sent == [900001] and "1 process." in r.text
    acts = [(a["action"], a["result"]) for a in _audit_rows(tmp_path) if a["action"].startswith("proc_prog")]
    assert ("proc_prog_kill", "denied") in acts and ("proc_prog_kill", "ok") in acts
    assert ("proc_prog_kill", "attempt") in acts and not any(a == "proc_prog_signal" for a, _ in acts)  # pairs fold


def test_container_start_route(fake_tmux, tmp_path, monkeypatch):
    calls = []
    real = procctl.container_action
    monkeypatch.setattr(procctl, "container_action",
                        lambda st, a, n: real(st, a, n, lambda argv, t: calls.append(argv) or (0, "web\n")))
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    csrf, h = _csrf_h(c)
    r = c.post("/servers/containers/web/start", data={"csrf": csrf}, headers=h)
    assert r.status_code == 200 and calls[-1] == ["docker", "start", "--", "web"]
    rows = [a["result"] for a in _audit_rows(tmp_path) if a["action"] == "container_start"]
    assert rows == ["attempt", "ok"]  # the guard's attempt and the outcome share the action, so the page folds them
