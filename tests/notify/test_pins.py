import json

import pytest

from pm.core.config import Settings
from pm.core.errors import Conflict, Invalid, NotFound
from pm.core.models import NtfyItem
from pm.notify import feed, pins


@pytest.fixture
def s(tmp_path):
    return Settings(state_dir=tmp_path / "state")


def item(n, **kw):
    base = dict(id=f"id{n}", time=1_700_000_000.0 + n, title=f"T{n}", message=f"M{n}", tags=("/x",))
    base.update(kw)
    return NtfyItem(**base)


def test_add_remove_newest_first(s):
    pins.pin(s, item(1))
    pins.pin(s, item(2))
    pins.pin(s, item(1))  # idempotent
    assert [i.id for i in pins.load(s)] == ["id2", "id1"]
    pins.unpin(s, "id2")
    assert [i.id for i in pins.load(s)] == ["id1"]
    with pytest.raises(NotFound):
        pins.unpin(s, "id2")
    assert (s.state_dir / "ntfy-pins.json").stat().st_mode & 0o777 == 0o600


def test_bad_id(s):
    with pytest.raises(Invalid):
        pins.pin(s, item(1, id="a b"))
    with pytest.raises(Invalid):
        pins.unpin(s, "../x")


def test_cap(s, monkeypatch):
    monkeypatch.setattr(pins, "MAX_PINS", 2)
    pins.pin(s, item(1))
    pins.pin(s, item(2))
    pins.pin(s, item(2))  # already pinned: no conflict
    with pytest.raises(Conflict):
        pins.pin(s, item(3))


def test_corrupt_file(s):
    s.state_dir.mkdir(parents=True)
    f = s.state_dir / "ntfy-pins.json"
    f.write_text("{nope")
    assert pins.load(s) == []
    pins.pin(s, item(1))
    assert [i.id for i in pins.load(s)] == ["id1"]
    f.write_text(json.dumps([{"id": "ok", "time": 1}, {"id": "bad id", "time": 1}, "x", {"time": 1}]))
    assert [i.id for i in pins.load(s)] == ["ok"]


def test_clipping(s):
    pins.pin(s, item(1, title="t" * 500, message="m" * 5000))
    p = pins.load(s)[0]
    assert len(p.title) == feed.MAX_TITLE and len(p.message) == feed.MAX_MESSAGE


def test_survives_item_leaving_feed(s):
    pins.pin(s, item(1, project="/p", click="https://pm.example.test/a/x", priority=4))
    p = pins.load(s)[0]  # the feed no longer holds it; the snapshot is complete
    assert (p.title, p.message, p.tags, p.project, p.click, p.priority) == ("T1", "M1", ("/x",), "/p", "https://pm.example.test/a/x", 4)
