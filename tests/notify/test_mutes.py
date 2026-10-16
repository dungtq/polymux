import pytest

from pm.core.config import Settings
from pm.core.errors import Conflict, Invalid, NotFound
from pm.core.models import NtfyItem
from pm.notify import feed, mutes


@pytest.fixture
def s(tmp_path):
    return Settings(state_dir=tmp_path / "state")


def item(n, title):
    return NtfyItem(id=f"id{n}", time=1_700_000_000.0 + n, title=title, message="m")


def test_mute_unmute_and_expiry(s):
    assert mutes.load(s) == {}
    mutes.mute(s, "pm alert -- h: disk /", now=1000.0)
    assert mutes.load(s, now=1000.0) == {"pm alert -- h: disk /": 1000.0 + mutes.MUTE_S}
    assert mutes.load(s, now=1000.0 + mutes.MUTE_S) == {}  # 24 h later it is over
    mutes.mute(s, "pm alert -- h: disk /", now=2000.0)  # again: the time restarts
    assert mutes.load(s, now=2000.0)["pm alert -- h: disk /"] == 2000.0 + mutes.MUTE_S
    mutes.unmute(s, "pm alert -- h: disk /", now=2000.0)
    assert mutes.load(s, now=2000.0) == {}
    with pytest.raises(NotFound):
        mutes.unmute(s, "pm alert -- h: disk /")
    assert (s.state_dir / "ntfy-mutes.json").stat().st_mode & 0o777 == 0o600


def test_bad_titles(s):
    for bad in ("", "   ", "a\nb", "x\x00", "x" * 201, None, 3):
        assert not mutes.valid_title(bad)
        with pytest.raises(Invalid):
            mutes.mute(s, bad)
    assert mutes.valid_title("claude-7cd08a - Fix login")


def test_cap(s, monkeypatch):
    monkeypatch.setattr(mutes, "MAX_MUTES", 2)
    mutes.mute(s, "a", now=1.0)
    mutes.mute(s, "b", now=1.0)
    with pytest.raises(Conflict):
        mutes.mute(s, "c", now=1.0)
    mutes.mute(s, "a", now=1.0)  # re-muting a muted title is fine at the cap
    mutes.mute(s, "c", now=1.0 + mutes.MUTE_S)  # expired ones do not count


def test_load_is_cached_by_mtime(s, monkeypatch):
    mutes.mute(s, "a")
    reads = []
    real = mutes.JsonStore.load
    monkeypatch.setattr(mutes.JsonStore, "load", lambda self: reads.append(1) or real(self))
    mutes._cache.update(sig=None)
    assert "a" in mutes.load(s) and "a" in mutes.load(s)
    assert reads == [1]
    mutes.mute(s, "b")
    assert set(mutes.load(s)) == {"a", "b"}


def test_corrupt_file(s):
    s.state_dir.mkdir(parents=True)
    (s.state_dir / "ntfy-mutes.json").write_text("[1, 2")
    assert mutes.load(s) == {}
    (s.state_dir / "ntfy-mutes.json").write_text('{"ok": 9e99, "bad\\n": 9e99, "n": "x"}')
    assert mutes.load(s) == {"ok": 9e99}


def test_feed_recent_drops_muted_titles(s, monkeypatch):
    items = [item(1, "claude-1 - spam"), item(2, "claude-1 - real"), item(3, "pm alert -- h: cpu")]
    monkeypatch.setattr(feed, "_snapshot", lambda settings: (list(items), True))
    assert len(feed.recent(s)) == 3
    mutes.mute(s, "claude-1 - spam")
    mutes.mute(s, "pm alert -- h: cpu")
    assert [i.id for i in feed.recent(s)] == ["id2"]
    assert mutes.drop_muted(s, items, now=9e12) == items  # long after: the mutes are over


def test_max_title_matches_the_feed():
    assert mutes.MAX_TITLE == feed.MAX_TITLE  # mutes cannot import feed (feed imports mutes)
