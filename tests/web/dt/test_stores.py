"""pm.web.dt.stores: the v7 tabs file (shape, caps, ids, rev) and the pure tab changes."""

import json
import os

import pytest

from pm.core.config import Settings
from pm.core.errors import Invalid
from pm.web.dt import stores
from pm.web.dt.stores import MAX_CLOSED, MAX_OPEN, TabState

A, B, C = ("agent", "claude-aaa111"), ("agent", "codex-bbb222"), ("term", "$4")


@pytest.fixture
def st(tmp_path):
    return Settings(state_dir=tmp_path / "state", token_file=tmp_path / "t", totp_file=tmp_path / "o")


def ag(i: int):
    return ("agent", f"claude-{i:06x}")


# ------------------------------------------------------------------ ids
@pytest.mark.parametrize("kind,id_", [("agent", "claude-3fa9c1"), ("agent", "cursor-agent-1a2b3c"), ("term", "$0"),
                                      ("term", "$123")])
def test_check_ref_ok(kind, id_):
    assert stores.check_ref(kind, id_) == (kind, id_)


@pytest.mark.parametrize("kind,id_", [
    ("agent", ""), ("agent", "Claude-1"), ("agent", "claude"), ("agent", "../etc"), ("agent", "claude-1 x"),
    ("agent", "a-" + "b" * 70), ("term", "@4"), ("term", "%4"), ("term", "4"), ("term", "$4;x"), ("term", "$-1"),
    ("home", ""), ("", "claude-1"), ("file", "claude-1"), ("agent", None), ("term", "$" + "9" * 20)])
def test_check_ref_rejects(kind, id_):
    with pytest.raises(Invalid):
        stores.check_ref(kind, id_)


# ------------------------------------------------------------------ pure changes
def test_open_appends_focuses_and_dedupes():
    s = stores.open_tab(TabState(), A)
    s = stores.open_tab(s, B)
    assert s.tabs == (A, B) and s.current == B
    s2 = stores.open_tab(s, A)
    assert s2.tabs == (A, B) and s2.current == A  # already open: focus only


def test_open_inserts_after_current():
    s = TabState(tabs=(A, B), current=A)
    assert stores.open_tab(s, C).tabs == (A, C, B)
    assert stores.open_tab(TabState(tabs=(A, B)), C).tabs == (A, B, C)


def test_open_cap_drops_leftmost_into_closed():
    s = TabState(tabs=tuple(ag(i) for i in range(MAX_OPEN)), current=ag(MAX_OPEN - 1))
    s = stores.open_tab(s, A)
    assert len(s.tabs) == MAX_OPEN and s.tabs[-1] == A and ag(0) not in s.tabs
    assert s.closed == (ag(0),) and s.current == A


def test_open_drops_it_from_closed():
    s = TabState(tabs=(A,), closed=(B, C))
    s = stores.open_tab(s, B)
    assert s.tabs == (A, B) and s.closed == (C,)  # an open tab is never in the reopen history


def test_close_moves_current_left_then_right():
    s = TabState(tabs=(A, B, C), current=B)
    s1 = stores.close_tab(s, B)
    assert s1.tabs == (A, C) and s1.current == A and s1.closed == (B,)
    s2 = stores.close_tab(TabState(tabs=(A, B), current=A), A)
    assert s2.current == B
    s3 = stores.close_tab(TabState(tabs=(A,), current=A), A)
    assert s3.tabs == () and s3.current is None
    s4 = stores.close_tab(TabState(tabs=(A, B), current=B), A)
    assert s4.current == B  # another tab closed: current stays


def test_close_not_open_is_no_change():
    s = TabState(tabs=(A,), current=A)
    assert stores.close_tab(s, B) is s


def test_closed_cap_and_dedupe():
    s = TabState(tabs=tuple(ag(i) for i in range(MAX_CLOSED + 5)))
    for i in range(MAX_CLOSED + 5):
        s = stores.close_tab(s, ag(i))
    assert len(s.closed) == MAX_CLOSED and s.closed[-1] == ag(MAX_CLOSED + 4) and s.closed[0] == ag(5)
    s = stores.close_tab(stores.open_tab(s, ag(10)), ag(10))
    assert s.closed.count(ag(10)) == 1 and s.closed[-1] == ag(10)


def test_reopen_last_closed():
    s = stores.close_tab(stores.close_tab(TabState(tabs=(A, B, C), current=A), B), C)
    s, ref = stores.reopen_tab(s)
    assert ref == C and C in s.tabs and s.current == C and s.closed == (B,)
    s, ref = stores.reopen_tab(s)
    assert ref == B and s.closed == ()
    s2, ref = stores.reopen_tab(s)
    assert ref is None and s2 is s


def test_set_current():
    s = TabState(tabs=(A, B), current=A)
    assert stores.set_current(s, B).current == B
    assert stores.set_current(s, None).current is None  # home tab
    assert stores.set_current(s, C) is s  # not open: unchanged


def test_move_by_id():
    s = TabState(tabs=(A, B, C))
    assert stores.move_tab(s, C, A).tabs == (C, A, B)
    assert stores.move_tab(s, A, None).tabs == (B, C, A)
    assert stores.move_tab(s, A, C).tabs == (B, A, C)
    assert stores.move_tab(s, B, B) is s
    with pytest.raises(Invalid):
        stores.move_tab(s, ag(9), A)
    with pytest.raises(Invalid):
        stores.move_tab(s, A, ag(9))


# ------------------------------------------------------------------ file
def test_missing_file_is_empty(st):
    assert stores.load(st) == stores.EMPTY and stores.read_cached(st) == stores.EMPTY


def test_apply_writes_shape_and_bumps_rev(st):
    s, _ = stores.apply(st, stores.open_tab, A)
    assert s.rev == 1
    s, _ = stores.apply(st, stores.open_tab, C)
    raw = json.loads(stores.path(st).read_text())
    assert raw == {"rev": 2, "tabs": [{"kind": "agent", "id": A[1]}, {"kind": "term", "id": "$4"}],
                   "current": {"kind": "term", "id": "$4"}, "closed": []}
    assert stores.path(st).name == "v7-tabs.json" and (os.stat(stores.path(st)).st_mode & 0o777) == 0o600
    s, ref = stores.apply(st, stores.reopen_tab)
    assert ref is None and s.rev == 2  # no change: no write, same rev


def test_apply_no_change_does_not_write(st):
    stores.apply(st, stores.open_tab, A)
    before = os.stat(stores.path(st)).st_mtime_ns
    s, _ = stores.apply(st, stores.open_tab, A)  # already open and current
    assert s.rev == 1 and os.stat(stores.path(st)).st_mtime_ns == before


def test_apply_invalid_leaves_file(st):
    stores.apply(st, stores.open_tab, A)
    with pytest.raises(Invalid):
        stores.apply(st, stores.move_tab, B, None)
    assert stores.load(st).rev == 1


def test_broken_or_hostile_file_is_cleaned(st):
    p = stores.path(st)
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    assert stores.load(st) == stores.EMPTY
    p.write_text(json.dumps({
        "rev": -3, "current": {"kind": "agent", "id": "claude-zzz999"},
        "tabs": [{"kind": "agent", "id": A[1]}, {"kind": "agent", "id": A[1]}, {"kind": "term", "id": "@1"},
                 {"kind": "x", "id": "y"}, "junk", {"kind": "term", "id": "$4"}],
        "closed": [{"kind": "agent", "id": "../../x"}, {"kind": "agent", "id": B[1]}]}))
    s = stores.load(st)
    assert s == TabState(0, (A, C), None, (B,))
    p.write_text(json.dumps({"rev": 5, "tabs": [{"kind": "agent", "id": ag(i)[1]} for i in range(50)]}))
    assert len(stores.load(st).tabs) == MAX_OPEN


def test_read_cached_follows_the_file(st):
    stores.apply(st, stores.open_tab, A)
    first = stores.read_cached(st)
    assert first.tabs == (A,) and stores.read_cached(st) is first  # cached
    stores.apply(st, stores.open_tab, C)
    assert stores.read_cached(st).tabs == (A, C)
    stores.path(st).unlink()
    assert stores.read_cached(st) == stores.EMPTY


def test_apply_busy_lock_is_oserror(st, monkeypatch):
    from pm.core import filelock

    def busy(*a, **k):
        raise filelock.LockTimeout("busy")

    monkeypatch.setattr(filelock, "locked", busy)
    with pytest.raises(OSError):
        stores.apply(st, stores.open_tab, A)
