from types import SimpleNamespace as N

from pm.agents import inbox
from pm.notify import sidebadges


def _g(key, project, ts):
    return inbox.NtfyGroup(key, N(project=project), 1, ts)


def test_by_project(monkeypatch):
    import time
    now = time.time()
    monkeypatch.setattr(sidebadges, "_unread_groups", lambda st: [
        _g("claude-a:done", "/x/pm", now), _g("claude-a:wait", "/x/pm", now), _g("claude-b:done", "/x/other", now)])
    projs = [N(name="pm", path="/x/pm"), N(name="other", path="/x/other"), N(name="none", path="/y")]
    assert sidebadges.by_project(None, projs) == {"pm": 2, "other": 1}
