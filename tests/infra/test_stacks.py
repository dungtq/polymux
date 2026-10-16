"""Compose stacks (pm.infra.stacks): status, URLs, restart, logs, services / view / up / down, with a fake
compose runner; never real Docker."""

import json
import subprocess

import pytest
from helpers import TOKEN

from pm.core.errors import NotFound, PmError
from pm.infra import stacks

PS = [
    {"Service": "web", "Name": "app-web-1", "State": "running", "Health": "healthy", "Image": "nginx:1",
     "Publishers": [{"URL": "0.0.0.0", "TargetPort": 80, "PublishedPort": 8080, "Protocol": "tcp"},
                    {"URL": "::", "TargetPort": 80, "PublishedPort": 8080, "Protocol": "tcp"},
                    {"URL": "", "TargetPort": 443, "PublishedPort": 0, "Protocol": "tcp"}]},
    {"Service": "db", "Name": "app-db-1", "State": "exited", "Health": "", "Image": "postgres:17\x1b[31m",
     "Publishers": None, "Ports": ""},
]


class FakeCompose:
    def __init__(self, rc=0, logs="line\n"):
        self.calls: list[list[str]] = []
        self.rc, self.logs = rc, logs

    def __call__(self, argv, cwd):
        self.calls.append(list(argv))
        verb = argv[2]
        if verb == "ps":
            return subprocess.CompletedProcess(argv, 0, "\n".join(json.dumps(r) for r in PS), "")
        if verb == "logs":
            return subprocess.CompletedProcess(argv, 0, self.logs, "")
        return subprocess.CompletedProcess(argv, self.rc, "", "boom " + TOKEN if self.rc else "")


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "compose.yaml").write_text("services: {}\n")
    return tmp_path


def test_services_fields_and_ports(proj):
    s = stacks.services(proj, FakeCompose())
    assert [(x.name, x.container, x.state, x.health) for x in s] == [("web", "app-web-1", "running", "healthy"),
                                                                     ("db", "app-db-1", "exited", "")]
    assert s[0].ports == "8080->80/tcp" and s[1].ports == "" and "\x1b" not in s[1].image


def test_services_no_compose_and_failure(tmp_path):
    assert stacks.services(tmp_path, FakeCompose()) == []
    (tmp_path / "compose.yml").write_text("services: {}\n")
    fail = lambda a, c: subprocess.CompletedProcess(a, 1, "", "err")
    with pytest.raises(PmError) as e:
        stacks.services(tmp_path, fail)
    assert e.value.status == 502


def test_view_model(proj, tmp_path):
    v = stacks.view(proj, FakeCompose())
    assert v["compose_file"] == "compose.yaml" and (v["running"], v["total"]) == (1, 2)
    assert v["services"][0] == {"name": "web", "container": "app-web-1", "state": "running", "health": "healthy",
                                "ports": "8080->80/tcp", "image": "nginx:1"}
    assert v["templates"] == [] and v["error"] == ""
    json.dumps(v)
    none = stacks.view(tmp_path / "missing", FakeCompose())
    assert none["compose_file"] is None and none["services"] == []
    bad = stacks.view(proj, lambda a, c: subprocess.CompletedProcess(a, 1, "", ""))
    assert bad["error"] and bad["services"] == []


def test_operate_argv_down_keeps_volumes(proj):
    fc = FakeCompose()
    assert stacks.operate(proj, "up", fc) == "Stack up."
    assert stacks.operate(proj, "down", fc).startswith("Stack down")
    assert fc.calls == [["docker", "compose", "up", "-d"], ["docker", "compose", "down"]]
    for a in fc.calls:
        assert not {"-v", "--volumes", "--rmi"} & set(a)


def test_operate_refusals_and_masked_failure(proj, tmp_path):
    with pytest.raises(NotFound):
        stacks.operate(proj, "rm", FakeCompose())
    with pytest.raises(NotFound):
        stacks.operate(proj, "restart", FakeCompose())  # restart stays stacks.restart (the control route)
    with pytest.raises(NotFound):
        stacks.operate(tmp_path / "missing", "up", FakeCompose())
    with pytest.raises(PmError) as e:
        stacks.operate(proj, "up", FakeCompose(rc=1))
    assert e.value.status == 502 and TOKEN not in str(e.value) and "exit 1" in str(e.value)


def test_operate_default_runner_timeout(proj, monkeypatch):
    seen = {}

    def fake(argv, **kw):
        seen.update(kw, argv=argv)
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(stacks, "run_argv", fake)
    stacks.operate(proj, "up")
    assert seen["timeout"] == stacks.OP_TIMEOUT_S and seen["cwd"] == proj


def test_logs_service_filter_masked(proj):
    fc = FakeCompose(logs="\x1b[32mok\x1b[0m token " + TOKEN + "\n")
    out = stacks.logs(proj, 50, fc, service="web")
    assert "ok token" in out and TOKEN not in out and "\x1b" not in out
    assert fc.calls[-1] == ["docker", "compose", "logs", "--no-color", "--tail", "50", "--", "web"]
    stacks.logs(proj, 5, fc)
    assert fc.calls[-1][-1] == "5" and "--" not in fc.calls[-1]
    for bad in ("nope", "-f", "web;x"):
        with pytest.raises(NotFound):
            stacks.logs(proj, 5, fc, service=bad)
    assert fc.calls[-1][2] == "ps"


# ------------------------------------------------------------------ status, urls, restart, logs
def cp(out="", rc=0):
    return subprocess.CompletedProcess([], rc, out, "")


def test_no_compose(tmp_path):
    assert stacks.stack_status(tmp_path, run=lambda a, c: cp()) is None


def test_status_urls(tmp_path):
    (tmp_path / "compose.yaml").write_text('labels:\n  - "Host(`app.example.test`)"\n  x: https://app.example.test/y\n  - VIRTUAL_HOST=b.example.test\n  y: plain.example.test\n')
    calls = []

    def run(argv, cwd):
        calls.append(argv)
        rows = [{"State": "running"}, {"State": "exited"}]
        return cp("\n".join(json.dumps(r) for r in rows))

    s = stacks.stack_status(tmp_path, run=run)
    assert (s.running, s.total, s.urls) == (1, 2, ("https://app.example.test", "https://b.example.test"))
    assert calls[0][:3] == ["docker", "compose", "ps"]


def test_json_array_and_failure(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    s = stacks.stack_status(tmp_path, run=lambda a, c: cp('[{"State":"running"}]'))
    assert s.running == 1
    s = stacks.stack_status(tmp_path, run=lambda a, c: cp("", 1))
    assert (s.running, s.total) == (0, 0)


def test_restart_and_logs_bounded(tmp_path):
    (tmp_path / "compose.yml").write_text("services: {}\n")
    seen = []
    run = lambda a, c: (seen.append(a), cp("log"))[1]
    assert stacks.restart(tmp_path, run=run)
    assert stacks.logs(tmp_path, 10**6, run=run) == "log"
    assert seen[1][-1] == str(stacks.MAX_LOG_LINES)
    assert stacks.restart(tmp_path / "none", run=run) is False
