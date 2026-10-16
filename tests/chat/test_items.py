"""pm.chat.items: schema, global rev, read API (after_rev / before_seq / newest page), outbox link, cap."""

import os
import sqlite3
import stat

from pm.chat import items, outbox
from pm.core import db
from pm.core.models import ChatItem


def _write(settings, fn):
    con = items.connect(settings)
    try:
        con.execute("BEGIN IMMEDIATE")
        out = fn(con)
        con.commit()
        return out
    finally:
        con.close()


def _add(settings, aid, uid, kind="text", **kw):
    return _write(settings, lambda con: items.add(con, aid, "c", uid, kind, **kw))


def test_add_and_list_in_order(settings):
    s1 = _add(settings, "a", "u1", "user", text="hi", ts=1)
    s2 = _add(settings, "a", "u1:0", "text", text="hello", name="claude-opus-5-5", turn="u1", ts=2)
    _add(settings, "b", "x", "user", text="other agent")
    got = items.list_items(settings, "a")
    assert [i.seq for i in got] == [s1, s2]
    assert isinstance(got[1], ChatItem) and got[1].name == "claude-opus-5-5" and got[1].turn == "u1"
    assert got[0].rev < got[1].rev
    assert items.latest(settings, "a") == (s2, got[1].rev)
    assert items.latest(settings, "nobody") == (0, 0)


def test_duplicate_uid_keeps_seq_and_rev(settings):
    s1 = _add(settings, "a", "u1", "user", text="hi")
    _, rev = items.latest(settings, "a")
    assert _add(settings, "a", "u1", "user", text="changed") == s1
    assert items.latest(settings, "a") == (s1, rev)
    assert items.list_items(settings, "a")[0].text == "hi"  # text is never updated


def test_first_page_newest_and_before_seq(settings):
    seqs = [_add(settings, "a", f"u{i}", "user", text=str(i)) for i in range(10)]
    assert [i.seq for i in items.list_items(settings, "a", limit=3)] == seqs[-3:]
    assert [i.seq for i in items.list_items(settings, "a", before_seq=seqs[-3], limit=3)] == seqs[-6:-3]


def test_after_rev_new_and_changed_lowest_first(settings):
    _add(settings, "a", "t1", "tool", name="Bash", status="run")
    s2 = _add(settings, "a", "u2", "user", text="x")
    _, cursor = items.latest(settings, "a")
    assert items.list_items(settings, "a", after_rev=cursor) == []
    assert _write(settings, lambda con: items.set_result(con, "a", "c", "t1", "ok", 5.0, "exit 0"))
    s3 = _add(settings, "a", "u3", "user", text="y")
    got = items.list_items(settings, "a", after_rev=cursor)
    assert [(i.uid, i.status, i.note) for i in got] == [("t1", "ok", "exit 0"), ("u3", "", "")]
    assert got[0].seq < s2 < s3 and got[0].ended == 5.0
    # bounded: the lowest revs first (the changed tool), so the cursor never skips one
    assert [i.uid for i in items.list_items(settings, "a", after_rev=cursor, limit=1)] == ["t1"]


def test_set_result_bumps_rev_only_on_change(settings):
    _add(settings, "a", "t1", "tool", name="Bash", status="run", note="background")
    assert _write(settings, lambda con: items.set_result(con, "a", "c", "t1", "ok", 3.0, ""))
    t = items.list_items(settings, "a")[0]
    assert (t.status, t.ended, t.note) == ("ok", 3.0, "background")  # an empty note keeps the old one
    _, rev = items.latest(settings, "a")
    assert not _write(settings, lambda con: items.set_result(con, "a", "c", "t1", "ok", 3.0, ""))
    assert not _write(settings, lambda con: items.set_result(con, "a", "c", "nope", "ok", 3.0, ""))
    assert items.latest(settings, "a")[1] == rev


def test_link_outbox(settings):
    s1 = _add(settings, "a", "u1", "user", text="hi")
    s2 = _add(settings, "a", "u1:0", "text", text="hello")
    _, rev = items.latest(settings, "a")
    assert items.link_outbox(settings, "a", s1, 7)
    assert not items.link_outbox(settings, "a", s1, 7)  # already linked
    assert not items.link_outbox(settings, "a", s2, 7)  # not a user item
    assert not items.link_outbox(settings, "b", s1, 7)  # other agent
    got = items.list_items(settings, "a", after_rev=rev)
    assert [(i.seq, i.outbox) for i in got] == [(s1, 7)]


def test_rev_never_reused_after_drop(settings):
    _add(settings, "a", "u1", "user", text="hi")
    _, rev = items.latest(settings, "a")
    _write(settings, lambda con: items.drop_conv(con, "a", "c"))
    _add(settings, "a", "u2", "user", text="again")
    assert items.latest(settings, "a")[1] > rev


def test_cap_per_agent(settings, monkeypatch):
    monkeypatch.setattr(items, "MAX_PER_AGENT", 3)
    for i in range(5):
        _add(settings, "a", f"u{i}", "user", text=str(i))
    _write(settings, lambda con: items.cap(con, "a"))
    assert [i.uid for i in items.list_items(settings, "a")] == ["u2", "u3", "u4"]


def test_bounds_and_private_file(settings):
    _add(settings, "a", "t", "tool", name="n" * 100, line="l" * 300, text="x" * 30000)
    t = items.list_items(settings, "a")[0]
    assert len(t.name) == items.MAX_NAME and len(t.line) == items.MAX_LINE and len(t.text) == items.MAX_TEXT
    assert not stat.S_IMODE(os.stat(settings.db_file).st_mode) & 0o077


def test_read_errors_never_raise(settings, monkeypatch):
    def boom(*a, **k):
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(db, "connect", boom)
    assert items.list_items(settings, "a") == []
    assert items.latest(settings, "a") == (0, 0)


def test_page_full_older_poll(settings):
    for i in range(5):
        _add(settings, "a", f"u{i}", "user", text=str(i))
    full = items.page(settings, "a", limit=3)
    assert [i.uid for i in full.items] == ["u2", "u3", "u4"] and full.more and not full.reset
    assert full.oldest == full.items[0].seq and full.rev == items.latest(settings, "a")[1] and full.outbox == []
    older = items.page(settings, "a", before=full.oldest, limit=3)
    assert [i.uid for i in older.items] == ["u0", "u1"] and not older.more and older.outbox is None
    assert items.page(settings, "a", after_rev=full.rev).items == []
    _add(settings, "a", "u5", "user", text="5")
    poll = items.page(settings, "a", after_rev=full.rev, lo=full.items[0].seq, hi=full.items[-1].seq)
    assert [i.uid for i in poll.items] == ["u5"] and poll.rev > full.rev and not poll.reset


def test_page_resets_when_held_items_are_gone(settings):
    _add(settings, "a", "u1", "user", text="hi")
    p = items.page(settings, "a")
    seq = p.items[0].seq
    _write(settings, lambda con: items.drop_conv(con, "a", "c"))
    _add(settings, "a", "u1", "user", text="hi")  # re-created with a new seq
    got = items.page(settings, "a", after_rev=p.rev, lo=seq, hi=seq)
    assert got.reset and got.items == [] and got.rev == p.rev and got.outbox is None


def test_page_outbox_skips_linked_and_cancelled(settings):
    m1, m2, m3 = (outbox.add(settings, "a", t) for t in ("one", "two", "three"))
    outbox.set_state(settings, m3.id, "cancelled")
    seq = _add(settings, "a", "u1", "user", text="one")
    items.link_outbox(settings, "a", seq, m1.id)
    assert [m.id for m in items.page(settings, "a").outbox] == [m2.id]
