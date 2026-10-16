"""pm.agents.pinned: the pinned agent sessions (state_dir/agent_pins.json)."""

import pytest

from pm.agents import pinned
from pm.core.config import Settings
from pm.core.errors import Invalid


@pytest.fixture
def st(tmp_path):
    return Settings(state_dir=tmp_path / "state")


def test_roundtrip_and_file_mode(st):
    assert pinned.load(st) == set()
    assert pinned.set_pin(st, "claude-7cd08a", True) == {"claude-7cd08a"}
    assert pinned.set_pin(st, "cursor-agent-1f2e3d", True) == {"claude-7cd08a", "cursor-agent-1f2e3d"}
    assert pinned.load(st) == {"claude-7cd08a", "cursor-agent-1f2e3d"}
    assert (pinned._file(st).stat().st_mode & 0o777) == 0o600
    assert pinned.set_pin(st, "claude-7cd08a", False) == {"cursor-agent-1f2e3d"}
    assert pinned.set_pin(st, "claude-7cd08a", False) == {"cursor-agent-1f2e3d"}  # unpin twice: no error


def test_bad_ids_refused(st):
    for bad in ("", "Claude-1", "claude", "../x", "claude-1 x", "claude-" + "a" * 64):
        with pytest.raises(Invalid):
            pinned.set_pin(st, bad, True)
    assert not pinned._file(st).exists()


def test_broken_file_reads_empty_and_junk_is_dropped(st):
    f = pinned._file(st)
    f.parent.mkdir(parents=True)
    f.write_text('{"not": "a list"}')
    assert pinned.load(st) == set()
    f.write_text('["claude-1", 3, "bad id", "claude-1", "codex-2"]')
    assert pinned.load(st) == {"claude-1", "codex-2"}


def test_cap_drops_the_oldest(st, monkeypatch):
    monkeypatch.setattr(pinned, "MAX_PINS", 3)
    for i in range(5):
        pinned.set_pin(st, f"claude-{i}", True)
    assert pinned.load(st) == {"claude-2", "claude-3", "claude-4"}
    pinned.set_pin(st, "claude-2", True)  # re-pinning makes it the newest
    pinned.set_pin(st, "claude-5", True)
    assert pinned.load(st) == {"claude-4", "claude-2", "claude-5"}
