import json
import logging
import os
import stat
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from pm.core import fsutil
from pm.core.errors import Denied, Invalid
from pm.core.fsutil import JsonStore, atomic_write, safe_join


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def _tmps(d):
    return [f.name for f in Path(d).iterdir() if f.name.endswith(".tmp")]


# ---------------------------------------------------------------- atomic_write
def test_atomic_write_str_bytes_and_mode(tmp_path):
    p = tmp_path / "a.txt"
    atomic_write(p, "héllo")
    assert p.read_bytes() == "héllo".encode() and _mode(p) == 0o600
    atomic_write(p, b"x", mode=0o644)
    assert p.read_bytes() == b"x" and _mode(p) == 0o644
    atomic_write(p, "y", encoding="latin-1", fsync=False, fsync_dir=True)
    assert p.read_text() == "y"
    assert _tmps(tmp_path) == []


def test_mode_none_keeps_existing_else_0600(tmp_path):
    p = tmp_path / "m"
    atomic_write(p, "1", mode=None)
    assert _mode(p) == 0o600
    os.chmod(p, 0o640)
    atomic_write(p, "2", mode=None)
    assert _mode(p) == 0o640 and p.read_text() == "2"


def test_failure_cleans_temp_and_keeps_old(tmp_path, monkeypatch):
    p = tmp_path / "f"
    atomic_write(p, "old")

    def boom(*_):
        raise OSError("x")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write(p, "new")
    monkeypatch.undo()
    assert p.read_text() == "old" and _tmps(tmp_path) == []


def test_before_replace_runs_before_and_aborts(tmp_path):
    p = tmp_path / "b"
    atomic_write(p, "old")
    seen = []
    atomic_write(p, "new", before_replace=lambda: seen.append(p.read_text()))
    assert seen == ["old"] and p.read_text() == "new"

    def veto():
        raise ValueError("no")
    with pytest.raises(ValueError):
        atomic_write(p, "newer", before_replace=veto)
    assert p.read_text() == "new" and _tmps(tmp_path) == []


def test_temp_name_hidden_unique(tmp_path, monkeypatch):
    names = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (names.append(Path(a).name), real(a, b)))
    atomic_write(tmp_path / "x.json", "1")
    atomic_write(tmp_path / "x.json", "2")
    assert all(n.startswith(".x.json.") and n.endswith(".tmp") for n in names) and names[0] != names[1]


# ---------------------------------------------------------------- JsonStore
def test_store_load_default_save(tmp_path):
    s = JsonStore(tmp_path / "s.json", dict, indent=1, sort_keys=True)
    assert s.load() == {}
    s.save({"b": 1, "a": 2})
    assert (tmp_path / "s.json").read_text() == json.dumps({"a": 2, "b": 1}, indent=1)
    assert _mode(tmp_path / "s.json") == 0o600 and s.load() == {"a": 2, "b": 1}


def test_store_corrupt_and_invalid(tmp_path, caplog):
    p = tmp_path / "c.json"
    p.write_text("{nope")
    lg = logging.getLogger("t_fsutil")  # outside "pm": setup_logging may stop pm.* propagating to caplog
    with caplog.at_level(logging.WARNING, logger="t_fsutil"):
        assert JsonStore(p, list, logger=lg).load() == []
        assert not caplog.records
        assert JsonStore(p, list, warn_corrupt=True, logger=lg).load() == []
    assert any(r.name == "t_fsutil" and "c.json" in r.getMessage() for r in caplog.records)
    p.write_text('{"a": 1}')
    assert JsonStore(p, list, validate=lambda o: isinstance(o, list)).load() == []
    assert JsonStore(p, list, validate=lambda o: o["missing"]).load() == []
    assert JsonStore(p, list, validate=lambda o: True).load() == {"a": 1}


def test_store_update_locked(tmp_path):
    s = JsonStore(tmp_path / "u.json", lambda: {"n": 0})

    def inc(o):
        o["n"] += 1
        return o
    ts = [threading.Thread(target=lambda: [s.update(inc) for _ in range(50)]) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert s.load() == {"n": 200}
    assert s.update(lambda o: {"n": -1}) == {"n": -1}


def test_store_lock_factory_and_lock_object(tmp_path):
    entered = []

    @contextmanager
    def flock():
        entered.append(1)
        yield
    s = JsonStore(tmp_path / "l.json", list, lock=flock)
    s.update(lambda o: [*o, 1])
    lk = threading.Lock()
    s2 = JsonStore(tmp_path / "l.json", list, lock=lk)
    s2.update(lambda o: (o.append(lk.locked()), o)[1])
    assert entered == [1] and s2.load() == [1, True]


def test_no_filelock_import():
    src = Path(fsutil.__file__).read_text()
    assert "filelock" not in src and "fastapi" not in src


# ---------------------------------------------------------------- safe_join
@pytest.mark.parametrize("rel", ["", "a\0b", "/etc/passwd", "..", "a/../b", "../x", "a/.."])
def test_safe_join_invalid(tmp_path, rel):
    with pytest.raises(Invalid):
        safe_join(tmp_path, rel)


def test_safe_join_ok_unresolved(tmp_path):
    (tmp_path / "d").mkdir()
    assert safe_join(tmp_path, "d/new.txt") == tmp_path / "d" / "new.txt"
    assert safe_join(str(tmp_path), Path("d")) == tmp_path / "d"
    real = tmp_path / "real"
    real.mkdir()
    link_root = tmp_path / "lr"
    link_root.symlink_to(real)
    assert safe_join(link_root, "f") == link_root / "f"  # root itself may be a symlink; path kept unresolved


def test_safe_join_symlinks(tmp_path):
    root, out = tmp_path / "root", tmp_path / "out"
    root.mkdir()
    out.mkdir()
    (root / "esc").symlink_to(out)
    (root / "in.txt").write_text("x")
    (root / "leaf").symlink_to(root / "in.txt")
    with pytest.raises(Denied):
        safe_join(root, "esc/f")
    with pytest.raises(Denied):
        safe_join(root, "esc", allow_symlink_leaf=True)
    with pytest.raises(Denied):
        safe_join(root, "leaf")
    assert safe_join(root, "leaf", allow_symlink_leaf=True) == root / "leaf"


def test_jsonstore_fsync_dir_is_passed(tmp_path, monkeypatch):
    from pm.core import fsutil
    seen = {}
    real = fsutil.atomic_write

    def spy(path, data, **kw):
        seen.update(kw)
        return real(path, data, **kw)

    monkeypatch.setattr(fsutil, "atomic_write", spy)
    fsutil.JsonStore(tmp_path / "s.json", dict, fsync_dir=True).save({"a": 1})
    assert seen["fsync_dir"] is True
    assert fsutil.JsonStore(tmp_path / "s.json", dict).load() == {"a": 1}


def test_encode_cwd_one_copy_reexported_by_the_claude_source():
    from pm.chat.sources import claude

    assert fsutil.encode_cwd("/home/me/dev/pm-wt/a.b_c") == "-home-me-dev-pm-wt-a-b-c"
    assert claude.encode_cwd is fsutil.encode_cwd  # pm.chat.live / sources.past import it from there
