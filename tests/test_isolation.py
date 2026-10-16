"""Test-run isolation: no network to the example.test site hosts, no stray private tmux sockets."""

import asyncio
import logging
import os
import subprocess
import uuid

import httpx
import pytest
from helpers import PMTEST_SOCK_RE, is_fake_host, kill_private_tmux, sweep_pmtest_sockets, tmux_socket_dir

from pm.core.config import Settings
from pm.notify import feed as ntfy_feed
from pm.notify import ntfy


# ------------------------------------------------------------------ example.test never reaches the network
def test_example_test_host_gets_empty_200_without_network():
    r = httpx.get("https://ntfy.example.test/agent/json", timeout=2)
    assert (r.status_code, r.text) == (200, "")


def test_example_test_host_async_stream_is_empty():
    async def go():
        async with httpx.AsyncClient(timeout=2) as c, c.stream("GET", "https://ntfy.example.test/agent/json") as r:
            return r.status_code, [ln async for ln in r.aiter_lines()]

    assert asyncio.run(go()) == (200, [])


@pytest.mark.parametrize("host,fake", [
    ("example.test", True), ("ntfy.example.test", True), ("A.Example.Test", True),
    ("example.test.evil.com", False), ("notexample.test", False), ("127.0.0.1", False), ("", False),
])
def test_is_fake_host(host, fake):
    assert is_fake_host(host) is fake


def test_other_hosts_still_use_the_real_transport():
    # port 1 on loopback is closed: proves the patch passes non-example.test hosts through
    with pytest.raises(httpx.ConnectError):
        httpx.get("http://127.0.0.1:1/", timeout=2)


def test_feed_fetch_without_transport_logs_no_warning(tmp_path, caplog, monkeypatch):
    ntfy_feed.reset()
    monkeypatch.setattr(ntfy_feed, "_transport", None)
    s = Settings(state_dir=tmp_path, tmux_socket="pmtest-unused")
    assert s.ntfy_topic_url.startswith("https://ntfy.example.test/")
    with caplog.at_level(logging.WARNING, logger="pm.ntfy_feed"):
        assert ntfy_feed._fetch(s) == []
    ntfy_feed.reset()
    assert [r for r in caplog.records if r.name == "pm.ntfy_feed"] == []


def test_publish_without_transport_succeeds_offline(tmp_path):
    s = Settings(state_dir=tmp_path, tmux_socket="pmtest-unused")
    assert s.ntfy_url.startswith("https://ntfy.example.test/")
    assert ntfy.publish(s, "title", "message") is True


# ------------------------------------------------------------------ private tmux sockets
_seen: dict = {}


def test_private_tmux_socket_exists_while_in_use(private_tmux):
    path = tmux_socket_dir() / private_tmux.tmux_socket
    assert path.exists()
    _seen["path"] = path


def test_private_tmux_socket_removed_after_teardown():
    if "path" not in _seen:  # only under a reordering plugin
        pytest.skip("runs after test_private_tmux_socket_exists_while_in_use")
    assert not _seen["path"].exists()


def test_kill_private_tmux_stops_server_and_removes_socket():
    name = f"pmtest-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    subprocess.run(["tmux", "-L", name, "-f", "/dev/null", "new-session", "-d", "-s", "pmtest-x"], check=True,
                   timeout=10, env={**os.environ, "TMUX": "", "SHELL": "/bin/sh"})
    path = tmux_socket_dir() / name
    assert path.exists()
    kill_private_tmux(name, path)
    assert not path.exists()
    r = subprocess.run(["tmux", "-L", name, "list-sessions"], capture_output=True, timeout=10, check=False)
    assert r.returncode != 0
    kill_private_tmux(name, path)  # second call: server and file already gone, no error


@pytest.mark.parametrize("name,ok", [
    ("pmtest-123-abcdef", True), ("pmtest-dev-8105", False), ("pmtest-fake", False), ("pmtest-semi-123", False),
    ("pmtest-123-ABCDEF", False), ("pmtest-123-abcdef0", False), ("pmtest-123-abcde", False),
    ("pmtest--abcdef", False), ("xpmtest-123-abcdef", False), ("pmtest-unused", False),
])
def test_sweep_regex(name, ok):
    assert bool(PMTEST_SOCK_RE.match(name)) is ok


def test_sweep_removes_own_and_dead_runs_only(tmp_path):
    p = subprocess.Popen(["true"])
    p.wait(timeout=10)
    dead = p.pid
    try:
        os.kill(dead, 0)
        pytest.skip("pid reused already")
    except ProcessLookupError:
        pass
    mine = f"pmtest-{os.getpid()}-abcdef"
    gone = f"pmtest-{dead}-012345"
    live = "pmtest-1-abcdef"  # pid 1 is always alive
    keep = [live, "pmtest-dev-8105", "pmtest-fake", "pmtest-semi-123", "other"]
    for n in [mine, gone, *keep]:
        (tmp_path / n).touch()  # plain files: nothing listens, so no tmux call is made
    assert sweep_pmtest_sockets(tmp_path) == 2
    assert sorted(os.listdir(tmp_path)) == sorted(keep)


def test_sweep_missing_dir_is_a_noop(tmp_path):
    assert sweep_pmtest_sockets(tmp_path / "nope") == 0
