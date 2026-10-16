import importlib.util
import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pm.core.config import Settings
from pm.web.app import create_app

_spec = importlib.util.spec_from_file_location("devpm", Path(__file__).parent.parent / "tools/devpm.py")
devpm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(devpm)


@pytest.mark.parametrize("port", [8093, 8094, 80])
def test_guard_refuses_real_and_low_ports(port):
    with pytest.raises(SystemExit):
        devpm.guard(port, devpm.root_of(8095))


def test_guard_refuses_real_state():
    with pytest.raises(SystemExit):
        devpm.guard(8095, devpm.REAL_HOME / ".local/state/pm")


def test_guard_accepts_dev_root():
    devpm.guard(8095, devpm.root_of(8095))


def test_tmux_helper_only_private_sockets():
    with pytest.raises(AssertionError):
        devpm.tmux("default", "list-sessions")


def test_dev_banner_only_when_configured(tmp_path):
    tok = tmp_path / "token"
    tok.write_text("t" * 40)
    for flag, expect in ((True, True), (False, False)):
        s = Settings(state_dir=tmp_path, token_file=tok, tmux_socket="pmtest-unused", dev_banner=flag,
                     disable_poll=True)
        c = TestClient(create_app(s), base_url="https://pm.example.test")
        assert ('class="devtag"' in c.get("/login").text) is False  # login has its own template
        from pm.web.render import env as _env
        assert _env.globals["dev_banner"] is expect


def test_fake_agent_session_file_and_exit(tmp_path):
    script = Path(__file__).parent.parent / "tools/devfakeagent.sh"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "DEVFAKE_SESSION": "00000000-0000-4000-8000-000000000001"}
    p = subprocess.Popen(["bash", str(script), "busy"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                         env=env, cwd=tmp_path)
    f = tmp_path / ".claude/sessions" / f"{p.pid}.json"
    for _ in range(50):
        if f.exists():
            break
        time.sleep(0.1)
    d = json.loads(f.read_text())
    assert d["pid"] == p.pid and d["status"] == "busy" and d["sessionId"].endswith("0001")
    assert str(d["procStart"]).isdigit()
    out, _ = p.communicate("hi\n/exit\n", timeout=10)
    assert "> hi" in out and p.returncode == 0 and not f.exists()


def test_fake_agent_resume_is_idle(tmp_path):
    script = Path(__file__).parent.parent / "tools/devfakeagent.sh"
    u = "00000000-0000-4000-8000-0000000000aa"
    p = subprocess.Popen(["bash", str(script), "--resume", u], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}, cwd=tmp_path)
    f = tmp_path / ".claude/sessions" / f"{p.pid}.json"
    for _ in range(50):
        if f.exists():
            break
        time.sleep(0.1)
    d = json.loads(f.read_text())
    assert d["status"] == "idle" and d["sessionId"] == u
    p.communicate("/exit\n", timeout=10)


def test_devntfy_items_parse():
    spec = importlib.util.spec_from_file_location("devntfy", Path(__file__).parent.parent / "tools/devntfy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from pm.notify import feed as ntfy_feed

    text = "".join(json.dumps(i) + "\n" for i in mod.items("/x/fixproj"))
    got = ntfy_feed.parse(text)
    assert len(got) == 6 and any("/x/fixproj" in i.tags for i in got)
    assert any(len(i.title) > 100 for i in got)


def test_running_dev_instance_older_than_the_code_is_stale():
    """Regression: a long-lived dev instance kept serving, 404 on routes added after it started."""
    stamp = devpm.code_stamp()
    assert stamp > 0
    assert devpm.is_stale({"started_at": stamp - 10}, stamp)
    assert devpm.is_stale({}, stamp)
    assert not devpm.is_stale({"started_at": stamp + 1}, stamp)


def test_start_opts_explicit_wins_else_remembered(tmp_path):
    cur = {"pm_bin": "/v/bin/pm", "extra_toml": "/x/plug.toml", "limits": "warn"}
    assert devpm.start_opts(cur, None, None, None) == cur
    assert devpm.start_opts(None, None, None, None) == devpm.OPT_DEFAULTS
    assert devpm.start_opts({"url": "u"}, None, None, None) == devpm.OPT_DEFAULTS  # an older instance.json
    got = devpm.start_opts(cur, "", "", "red")  # '' resets to the default
    assert got == {"pm_bin": "", "extra_toml": "", "limits": "red"}
    rel = devpm.start_opts(cur, "bin/pm", None, None)["pm_bin"]
    assert Path(rel).is_absolute() and rel.endswith("/bin/pm")


@pytest.mark.parametrize("up,stale,intact,same,want", [
    (True, False, True, True, "reuse"),
    (True, True, True, True, "restart"),
    (False, False, True, True, "restart"),
    (True, False, True, False, "restart"),
    (True, True, False, True, "fresh"),
    (False, False, False, True, "fresh"),
])
def test_start_plan(up, stale, intact, same, want):
    cur = {"pm_bin": "/v/bin/pm", "extra_toml": "", "limits": "default"}
    opts = dict(cur) if same else {**cur, "pm_bin": ""}
    assert devpm.start_plan(cur, opts, up=up, stale=stale, intact=intact) == want
    assert devpm.start_plan(None, opts, up=up, stale=stale, intact=intact) == "fresh"


def test_write_toml_base_plus_extra(tmp_path):
    (tmp_path / "home/.config/pm").mkdir(parents=True)
    (tmp_path / "pm.base.toml").write_text('port = 1\n')
    extra = tmp_path / "plug.toml"
    extra.write_text('[plugins.x]\na = 1\n')
    devpm.write_toml(tmp_path, str(extra))
    assert (tmp_path / "home/.config/pm/pm.toml").read_text() == 'port = 1\n\n[plugins.x]\na = 1\n'
    devpm.write_toml(tmp_path, "")  # dropping --extra-toml drops the table, base untouched
    assert (tmp_path / "home/.config/pm/pm.toml").read_text() == 'port = 1\n'


class _Proc:
    pid = 4242

    def poll(self):
        return None


def _fake_start(monkeypatch, tmp_path, *, tmux_rc: int):
    """cmd_start against a temp BASE with no real pm, tmux or ntfy; returns the recorded Popen argvs."""
    monkeypatch.setattr(devpm, "BASE", tmp_path)
    monkeypatch.setattr(devpm, "alive", lambda pid: False)  # the old pm and its ntfy are gone
    monkeypatch.setattr(devpm, "healthy", lambda url: True)
    monkeypatch.setattr(devpm, "tmux", lambda *a, **k: subprocess.CompletedProcess(a, tmux_rc, "", ""))
    calls: list[list[str]] = []
    monkeypatch.setattr(devpm.subprocess, "Popen", lambda argv, **k: calls.append(argv) or _Proc())
    return calls


def _old_instance(root: Path, **opts) -> dict:
    (root / "home/.config/pm").mkdir(parents=True)
    (root / "state").mkdir()
    (root / "projects/fixproj").mkdir(parents=True)
    tok = root / "home/.config/pm/token"
    tok.write_text("t\n")
    (root / "pm.base.toml").write_text("port = 8330\n")
    cur = {"url": "http://127.0.0.1:8330", "port": 8330, "root": str(root), "token_file": str(tok),
           "state_dir": str(root / "state"), "pid": 1, "started_at": 0, "agents": {"running": "a1"}, **opts}
    (root / "instance.json").write_text(json.dumps(cur))
    return cur


def test_stale_restart_keeps_pm_bin_and_fixture_data(monkeypatch, tmp_path):
    """A stale restart (e.g. from uishot) kept dropping --pm-bin and re-seeding the fixtures."""
    calls = _fake_start(monkeypatch, tmp_path, tmux_rc=0)
    root = tmp_path / "8330"
    extra = tmp_path / "plug.toml"
    extra.write_text("[plugins.p]\n")
    _old_instance(root, pm_bin="/plug/.venv/bin/pm", extra_toml=str(extra), limits="warn")
    added = root / "projects/fixproj/agent-note.md"
    added.write_text("kept")
    monkeypatch.setattr(devpm, "seed", lambda *a: pytest.fail("a restart must not re-seed"))
    assert devpm.cmd_start(8330) == 0  # what uishot runs: no --pm-bin
    assert calls[-1] == ["/plug/.venv/bin/pm"]
    info = json.loads((root / "instance.json").read_text())
    assert (info["pm_bin"], info["extra_toml"], info["limits"]) == ("/plug/.venv/bin/pm", str(extra), "warn")
    assert info["pid"] == 4242 and info["agents"] == {"running": "a1"} and info["started_at"] > 0
    assert added.read_text() == "kept"
    assert "[plugins.p]" in (root / "home/.config/pm/pm.toml").read_text()


def test_explicit_pm_bin_overrides_remembered(monkeypatch, tmp_path):
    calls = _fake_start(monkeypatch, tmp_path, tmux_rc=0)
    root = tmp_path / "8330"
    _old_instance(root, pm_bin="/plug/.venv/bin/pm", extra_toml="", limits="default")
    monkeypatch.setattr(devpm, "seed", lambda *a: pytest.fail("a restart must not re-seed"))
    assert devpm.cmd_start(8330, pm_bin="") == 0
    assert calls[-1] == [str(devpm.REPO / ".venv/bin/pm")]
    assert json.loads((root / "instance.json").read_text())["pm_bin"] == ""


def test_fresh_start_after_lost_agents_keeps_pm_bin(monkeypatch, tmp_path):
    """No fake-agent tmux server left: re-seed, but still with the instance's own options."""
    calls = _fake_start(monkeypatch, tmp_path, tmux_rc=1)
    root = tmp_path / "8330"
    _old_instance(root, pm_bin="/plug/.venv/bin/pm", extra_toml="", limits="red")
    seeded: list[str] = []

    def fake_seed(port, limits):
        seeded.append(limits)
        _old_instance(root)
        return {"url": "http://127.0.0.1:8330", "state_dir": str(root / "state")}

    monkeypatch.setattr(devpm, "seed", fake_seed)
    monkeypatch.setattr(devpm, "resolve_ids", lambda info: {**info, "agents": {}})
    assert devpm.cmd_start(8330) == 0
    assert seeded == ["red"] and calls[-1] == ["/plug/.venv/bin/pm"]
    assert json.loads((root / "instance.json").read_text())["pm_bin"] == "/plug/.venv/bin/pm"
