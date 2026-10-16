"""Control keys must never be pasted as literal caret text when tmux reports a read-only client."""
import pytest

from pm.tmux import ctl as tmuxctl


class S:
    tmux_bin = "tmux"
    tmux_socket = None


def fake(clients, calls):
    def run(settings, args, timeout=0, input_=None):
        calls.append(args)
        if args[0] == "list-clients":
            return clients
        if args[0] == "send-keys" and "-c" not in args:
            raise tmuxctl.TmuxError("client is read-only")
        return ""
    return run


def test_arrow_goes_through_writable_client(monkeypatch):
    calls = []
    monkeypatch.setattr(tmuxctl, "run", fake("/dev/pts/1 1\n/dev/pts/2 0\n", calls))
    tmuxctl.send_key(S, "%5", "Down")
    assert calls[-1] == ["send-keys", "-c", "/dev/pts/2", "-t", "%5", "--", "Down"]
    assert not any(c[0] in ("load-buffer", "paste-buffer") for c in calls)


@pytest.mark.parametrize("key", ["Up", "Escape", "Enter", "C-c", "BSpace", "BTab", "Home", "C-x", "C-s"])
def test_control_key_without_writable_client_fails_loudly(monkeypatch, key):
    calls = []
    monkeypatch.setattr(tmuxctl, "run", fake("/dev/pts/1 1\n", calls))
    with pytest.raises(tmuxctl.TmuxError):
        tmuxctl.send_key(S, "%5", key)
    assert not any(c[0] in ("load-buffer", "paste-buffer") for c in calls)


def test_printable_key_still_pastes(monkeypatch):
    calls = []
    monkeypatch.setattr(tmuxctl, "run", fake("", calls))
    tmuxctl.send_key(S, "%5", "y")
    assert any(c[0] == "paste-buffer" for c in calls)
