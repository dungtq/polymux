import os
import stat
import subprocess
import uuid
from pathlib import Path

import pytest
from helpers import apply_site_defaults

from pm.core.config import Settings

apply_site_defaults()  # tests run against a full site; see helpers.SITE
os.environ["PM_NO_PLUGINS"] = "1"  # never load installed plugins (pm.web.plugins); tests inject entry points instead

FAKE = Path(__file__).parent / "bin" / "tmux"

# Shared builders (make_settings, make_client, login, the pages/chat/builder/ntfy app fixtures,
# the agents-route ``fake`` + mk_client/post) live in tests/helpers.py: ``from helpers import ...``.
# Never import helpers from another test_*.py file; move them to helpers.py instead.


@pytest.fixture
def private_tmux():
    sock = f"pmtest-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    assert sock.startswith("pmtest-")
    s = Settings(tmux_socket=sock)
    base = ["tmux", "-L", sock]
    subprocess.run(base + ["-f", "/dev/null", "new-session", "-d", "-s", "pmtest-base",
                           "-x", "120", "-y", "30"], check=True, timeout=10,
                   env={**os.environ, "TMUX": "", "SHELL": "/bin/sh"})  # no user rc files in test panes
    try:
        yield s
    finally:
        assert sock.startswith("pmtest-")
        subprocess.run(base + ["kill-server"], timeout=10, check=False)


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
def _pane_ids_unless_real_registry(request, monkeypatch):
    """Most tests use fake agents with pane based ids: keep them. A test module that sets
    ``REAL_REGISTRY = True`` exercises the permanent-id registry instead."""
    if getattr(request.module, "REAL_REGISTRY", False):
        return
    from pm.agents import registry

    monkeypatch.setattr(registry, "assign", lambda settings, found, **kw: found)
