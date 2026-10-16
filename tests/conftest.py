import os
import stat
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest
from helpers import (
    PMTEST_SOCK_RE,
    apply_site_defaults,
    is_fake_host,
    kill_private_tmux,
    sweep_pmtest_sockets,
    tmux_socket_dir,
)

from pm.core.config import Settings

apply_site_defaults()  # tests run against a full site; see helpers.SITE
os.environ["PM_NO_PLUGINS"] = "1"  # never load installed plugins (pm.web.plugins); tests inject entry points instead

FAKE = Path(__file__).parent / "bin" / "tmux"

# Shared builders (make_settings, make_client, login, the pages/chat/builder/ntfy app fixtures,
# the agents-route ``fake`` + mk_client/post) live in tests/helpers.py: ``from helpers import ...``.
# Never import helpers from another test_*.py file; move them to helpers.py instead.


@pytest.fixture(scope="session", autouse=True)
def _no_network_to_example_test():
    """Requests to ``example.test`` hosts (the site defaults) get an empty 200, never the network.

    Session scope: the ntfy feed refresh / collect threads are daemons that outlive single tests.
    Tests that pass their own transport (``httpx.MockTransport``) never reach these methods.
    """
    sync_orig = httpx.HTTPTransport.handle_request
    async_orig = httpx.AsyncHTTPTransport.handle_async_request

    def handle_request(self, request):
        if is_fake_host(request.url.host):
            return httpx.Response(200, text="", request=request)
        return sync_orig(self, request)

    async def handle_async_request(self, request):
        if is_fake_host(request.url.host):
            return httpx.Response(200, text="", request=request)
        return await async_orig(self, request)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(httpx.HTTPTransport, "handle_request", handle_request)
        mp.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle_async_request)
        yield


@pytest.fixture(scope="session", autouse=True)
def _sweep_private_tmux_sockets():
    """At the end of the run remove ``pmtest-<pid>-<hex6>`` sockets of this run and of dead pytest runs."""
    yield
    sweep_pmtest_sockets()


@pytest.fixture
def private_tmux():
    sock = f"pmtest-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    assert PMTEST_SOCK_RE.match(sock)
    s = Settings(tmux_socket=sock)
    base = ["tmux", "-L", sock]
    path = tmux_socket_dir() / sock
    try:
        subprocess.run(base + ["-f", "/dev/null", "new-session", "-d", "-s", "pmtest-base",
                               "-x", "120", "-y", "30"], check=True, timeout=10,
                       env={**os.environ, "TMUX": "", "SHELL": "/bin/sh"})  # no user rc files in test panes
        r = subprocess.run(base + ["display-message", "-p", "#{socket_path}"], capture_output=True, text=True,
                           timeout=10, check=False)
        if r.returncode == 0 and r.stdout.strip().endswith("/" + sock):
            path = Path(r.stdout.strip())
        yield s
    finally:
        kill_private_tmux(sock, path)  # check=False: a test may have killed the server itself


@pytest.fixture
def settings(tmp_path):
    return Settings(tmux_socket="pmtest-unused", state_dir=tmp_path, user_ssh_dir=tmp_path / "home-ssh")


@pytest.fixture
def fake_tmux(tmp_path, monkeypatch):
    """Settings whose tmux_bin is a stub printing $FAKE_TMUX_OUT; argv logged to $FAKE_TMUX_LOG."""
    log = tmp_path / "argv.log"
    monkeypatch.setenv("FAKE_TMUX_LOG", str(log))
    FAKE.chmod(FAKE.stat().st_mode | stat.S_IXUSR)
    s = Settings(tmux_bin=str(FAKE), tmux_socket="pmtest-fake", state_dir=tmp_path,
                 user_ssh_dir=tmp_path / "home-ssh")
    s.extras["log"] = log
    return s


@pytest.fixture(autouse=True)
def _no_stale_account_roots():
    """``create_app`` registers a process-wide extra-roots provider for named accounts: clear it around every test."""
    from pm.agents import head

    head.register_extra_roots(None)
    yield
    head.register_extra_roots(None)


@pytest.fixture(autouse=True)
def _pane_ids_unless_real_registry(request, monkeypatch):
    """Most tests use fake agents with pane based ids: keep them. A test module that sets
    ``REAL_REGISTRY = True`` exercises the permanent-id registry instead."""
    if getattr(request.module, "REAL_REGISTRY", False):
        return
    from pm.agents import registry

    monkeypatch.setattr(registry, "assign", lambda settings, found, **kw: found)
