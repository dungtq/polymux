import json
import os
import stat

from pm.core.config import Settings
from pm.security.auth import SessionStore


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def mk(tmp_path, clock):
    st = tmp_path / "state"
    return Settings(token_file=tmp_path / "token", state_dir=st), clock


def store(settings, clock):
    return SessionStore(settings, clock)


def test_restart_keeps_cookie_and_csrf(tmp_path):
    from pm.security.csrf import Csrf
    settings, clock = mk(tmp_path, Clock())
    a = store(settings, clock)
    s = a.create("1.2.3.4")
    cookie = a.cookie_value(s)
    tok = Csrf(settings.state_dir, ()).token(s.id)
    b = store(settings, clock)
    s2 = b.session_from_cookie(cookie)
    assert s2 is not None and s2.id == s.id and s2.csrf == s.csrf
    assert Csrf(settings.state_dir, ()).verify(s2.id, tok)


def test_unlock_not_restored(tmp_path):
    settings, clock = mk(tmp_path, Clock())
    a = store(settings, clock)
    s = a.create()
    s.unlocked_until = clock.t + 600
    a.mark_seen(s)
    a._save()
    b = store(settings, clock)
    assert b.session_from_cookie(a.cookie_value(s)).unlocked_until == 0.0


def test_expired_and_idle_dropped_on_load(tmp_path):
    settings, clock = mk(tmp_path, Clock())
    a = store(settings, clock)
    _old, idle = a.create(), a.create()
    clock.t += settings.cookie_ttl_s - 10
    idle_cookie = a.cookie_value(idle)
    fresh = a.create()
    a.mark_seen(fresh)
    a._save()
    clock.t += 20  # `old` and `idle` past absolute ttl
    b = store(settings, clock)
    assert len(b._pending) == 1 and b.get(fresh.id)
    assert b.session_from_cookie(idle_cookie) is None
    clock.t += settings.idle_ttl_s + 1
    assert len(store(settings, clock)._pending) == 0


def test_logout_revokes_persistently(tmp_path):
    settings, clock = mk(tmp_path, Clock())
    a = store(settings, clock)
    s = a.create()
    cookie = a.cookie_value(s)
    a.revoke(s.id)
    assert store(settings, clock).session_from_cookie(cookie) is None


def test_corrupt_file_starts_empty(tmp_path):
    settings, clock = mk(tmp_path, Clock())
    a = store(settings, clock)
    a.create()
    (settings.state_dir / "sessions.json").write_text("{not json")
    assert store(settings, clock)._pending == {}
    (settings.state_dir / "sessions.json").write_text('{"sessions": [{"h": 1}]}')
    assert store(settings, clock)._pending == {}


def test_modes_and_no_raw_id(tmp_path):
    settings, clock = mk(tmp_path, Clock())
    a = store(settings, clock)
    s = a.create()
    cookie = a.cookie_value(s)
    p = settings.state_dir / "sessions.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(settings.state_dir.stat().st_mode) == 0o700
    sec = settings.state_dir / "session_secret"
    assert stat.S_IMODE(sec.stat().st_mode) == 0o600
    raw = p.read_text()
    assert s.id not in raw and cookie not in raw and sec.read_text().strip() not in raw


def test_cap_enforced(tmp_path):
    settings, clock = mk(tmp_path, Clock())
    a = store(settings, clock)
    for _ in range(30):
        clock.t += 1
        a.create()
    assert len(a._sessions) == 20
    assert len(json.loads((settings.state_dir / "sessions.json").read_text())["sessions"]) == 20
    assert len(store(settings, clock)._pending) == 20


def test_atomic_write_leaves_no_partial(tmp_path, monkeypatch):
    settings, clock = mk(tmp_path, Clock())
    a = store(settings, clock)
    s = a.create()
    good = (settings.state_dir / "sessions.json").read_text()

    def boom(*_):
        raise OSError("x")
    monkeypatch.setattr(os, "replace", boom)
    a.create()  # save failure is tolerated
    monkeypatch.undo()
    assert (settings.state_dir / "sessions.json").read_text() == good
    assert [f.name for f in settings.state_dir.iterdir() if f.name.endswith(".tmp")] == []
    assert store(settings, clock).get(s.id)


def test_touch_throttled(tmp_path):
    settings, clock = mk(tmp_path, Clock())
    a = store(settings, clock)
    s = a.create()
    p = settings.state_dir / "sessions.json"
    before = p.read_text()
    clock.t += 5
    a.touch(s.id)
    assert p.read_text() == before
    clock.t += 30
    a.touch(s.id)
    assert p.read_text() != before
