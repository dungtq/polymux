"""notify.owner.agent_ntfy / agent_feed: newest ntfy item (and unread count) per agent id; live agents get theirs
in the page contexts."""
from types import SimpleNamespace as NS

from pm.core.models import NtfyItem
from pm.notify import feed as ntfy_feed
from pm.notify import owner as ntfy_owner


def _patch(monkeypatch, items, owners):
    monkeypatch.setattr(ntfy_feed, "recent", lambda settings, project_path=None: items)
    monkeypatch.setattr(ntfy_owner, "build_index", lambda settings, since: None)
    monkeypatch.setattr(ntfy_owner, "owner_for",
                        lambda settings, index, title, t: NS(agent_id=owners[title]) if owners.get(title) else None)


def test_agent_ntfy_newest_per_wanted_id(monkeypatch):
    new = NtfyItem(id="2", time=20.0, title="a - new", message="m2")
    old = NtfyItem(id="1", time=10.0, title="a - old", message="m1")
    other = NtfyItem(id="3", time=15.0, title="b - x", message="m3")
    anon = NtfyItem(id="4", time=5.0, title="sys", message="m4")
    _patch(monkeypatch, [new, other, old, anon], {"a - new": "a", "a - old": "a", "b - x": "b", "sys": None})
    assert ntfy_owner.agent_ntfy(None, {"a"}, None) == {"a": new}
    assert ntfy_owner.agent_ntfy(None, ["a", "b"], "/p") == {"a": new, "b": other}


def test_agent_ntfy_empty(monkeypatch):
    _patch(monkeypatch, [], {})
    assert ntfy_owner.agent_ntfy(None, {"a"}) == {}
    _patch(monkeypatch, [NtfyItem(id="1", time=1.0, title="a - t", message="m")], {"a - t": "a"})
    assert ntfy_owner.agent_ntfy(None, set()) == {}


def test_agent_feed_counts_unread_in_the_same_pass(monkeypatch):
    calls = []
    new = NtfyItem(id="2", time=20.0, title="a - new", message="m2")
    old = NtfyItem(id="1", time=10.0, title="a - old", message="m1")
    other = NtfyItem(id="3", time=15.0, title="b - x", message="m3")
    _patch(monkeypatch, [new, other, old], {"a - new": "a", "a - old": "a", "b - x": "b"})
    monkeypatch.setattr(ntfy_owner, "build_index", lambda settings, since: calls.append(since))
    got = ntfy_owner.agent_feed(None, {"a", "b"}, unread=lambda i: i.id in ("1", "2", "3"))
    assert got == {"a": ntfy_owner.AgentFeed(new, 2), "b": ntfy_owner.AgentFeed(other, 1)}
    assert calls == [10.0]  # one index for the whole pass
    assert ntfy_owner.agent_feed(None, {"a"})["a"].unread == 0  # no predicate: no count


def test_live_agent_gets_item_in_ctx(monkeypatch, tmp_path):
    import asyncio

    from pm.core.config import Settings
    from pm.web.routes import pages

    item = NtfyItem(id="1", time=1.0, title="claude-12 - t", message="m")
    _patch(monkeypatch, [item], {"claude-12 - t": "claude-12"})
    monkeypatch.setattr(pages.registry, "ended_sessions", lambda *a, **k: ([], 0))
    snap = NS(agents=[], project=lambda n: None)
    ctx = asyncio.run(pages._ended_ctx(Settings(state_dir=tmp_path), snap, live_ids=["claude-12", "claude-99"]))
    assert ctx["agent_ntfy"] == {"claude-12": item} and ctx["ended_ntfy"] == {}
    assert ctx["agent_feed"] == {"claude-12": ntfy_owner.AgentFeed(item, 0)}  # 1970: outside the unread window
