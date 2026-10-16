import stat

import pyotp

from pm.security.unlock import Unlocker


class S:
    id = "s"
    unlocked_until = 0.0


def setup(tmp_path):
    u = Unlocker(tmp_path / "d" / "totp", 600)
    uri = u.provision()
    secret = pyotp.parse_uri(uri).secret
    return u, pyotp.TOTP(secret)


def test_provision_perms_and_once(tmp_path, capsys):
    u, _ = setup(tmp_path)
    assert stat.S_IMODE(u.totp_file.stat().st_mode) == 0o600
    assert capsys.readouterr().out == ""
    try:
        u.provision()
        assert False
    except FileExistsError:
        pass


def test_unlock_and_replay(tmp_path):
    u, t = setup(tmp_path)
    now = 1_000_000.0
    code = t.at(now)
    s = S()
    assert u.unlock(s, code, now)
    assert s.unlocked_until == now + 600
    s2 = S()
    assert not u.unlock(s2, code, now + 1)  # replay
    assert s2.unlocked_until == 0.0


def test_window_and_wrong(tmp_path):
    u, t = setup(tmp_path)
    now = 2_000_000.0
    assert u.verify_totp(t.at(now - 30), now=now)
    assert not u.verify_totp(t.at(now - 120), now=now)
    assert not u.verify_totp("abc", now=now)


def test_rate_limit(tmp_path):
    u, t = setup(tmp_path)
    now = 3_000_000.0
    s = S()
    for _ in range(5):
        assert not u.unlock(s, "000000", now, client="c") or True
    assert not u.unlock(s, t.at(now), now, client="c")  # blocked even if right
    assert u.unlock(S(), t.at(now), now, client="other")


def test_unprovisioned(tmp_path):
    assert not Unlocker(tmp_path / "none", 600).verify_totp("123456")
