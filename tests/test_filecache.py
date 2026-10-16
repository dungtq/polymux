"""filecache LRU/SWR plus the Files pane render, git status and birth-time caches."""

import os
import subprocess

from pm.core import cache as filecache
from pm.core.cache import LRU, SWR
from pm.git import ctl as gitctl
from pm.projects import projfiles


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_lru_entries_and_bytes():
    c = LRU(max_bytes=100, max_entries=3)
    for i in range(4):
        c.put(i, "x")
    assert 0 not in c and len(c) == 3
    c.get(1)
    c.put(9, "x")
    assert 1 in c and 2 not in c
    b = LRU(max_bytes=10, max_entries=100)
    b.put("a", "12345")
    b.put("b", "12345")
    b.put("c", "12345")
    assert "a" not in b and b.nbytes <= 10
    b.put("big", "x" * 50)  # does not fit: not cached
    assert "big" not in b


def _swr():
    clk, jobs = Clock(), []
    return SWR(clock=clk, executor=lambda fn: jobs.append(fn)), clk, jobs


def test_swr_paths():
    s, clk, jobs = _swr()
    n = [0]

    def comp():
        n[0] += 1
        return n[0]

    assert s.get("k", comp, 3, 60) == 1
    clk.t += 1
    assert s.get("k", comp, 3, 60) == 1 and n[0] == 1  # fresh
    clk.t += 5
    assert s.get("k", comp, 3, 60) == 1 and len(jobs) == 1  # stale: old value, refresh queued
    assert s.get("k", comp, 3, 60) == 1 and len(jobs) == 1  # one in flight
    jobs.pop()()
    assert s.get("k", comp, 3, 60) == 2 and n[0] == 2
    clk.t += 100
    assert s.get("k", comp, 3, 60) == 3  # expired: sync


def test_swr_errors_not_cached_and_refresh_failure_keeps_old():
    s, clk, jobs = _swr()
    try:
        s.get("e", lambda: 1 / 0, 3, 60)
    except ZeroDivisionError:
        pass
    assert s.get("e", lambda: 7, 3, 60) == 7
    clk.t += 10

    def bad():
        raise RuntimeError

    assert s.get("e", bad, 3, 60) == 7
    jobs.pop()()  # swallowed
    assert s.get("e", lambda: 8, 3, 60) == 7  # old value kept, still stale -> refresh queued again
    assert jobs


def test_swr_real_thread_wait():
    clk = Clock()
    s = SWR(clock=clk)
    s.get("k", lambda: 1, 1, 60)
    clk.t += 5
    assert s.get("k", lambda: 2, 1, 60) == 1
    assert s.wait()
    assert s.get("k", lambda: 3, 1, 60) == 2


def test_highlight_cache_hit_and_miss(tmp_path, monkeypatch):
    from pm.core import codehl

    projfiles._render_cache.clear()
    calls = []
    real = codehl.highlight
    monkeypatch.setattr(codehl, "highlight", lambda t, n: (calls.append(1), real(t, n))[1])
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    projfiles.file_view(str(tmp_path), "a.py")
    projfiles.file_view(str(tmp_path), "a.py")
    assert len(calls) == 1
    f.write_text("x = 22\n")
    os.utime(f, ns=(1, 2_000_000_000))
    v = projfiles.file_view(str(tmp_path), "a.py")
    assert len(calls) == 2 and "22" in v["body"]


def test_masked_secret_never_from_cache(tmp_path):
    from helpers import TOKEN

    projfiles._render_cache.clear()
    (tmp_path / "n.txt").write_text(f"GH={TOKEN}\n")
    for _ in range(2):
        v = projfiles.file_view(str(tmp_path), "n.txt")
        assert TOKEN not in v["body"] and TOKEN not in v["text"]
    assert all(TOKEN not in str(x) for x in projfiles._render_cache._d.values())


def test_btimes_cached_per_dir_mtime(tmp_path, monkeypatch):
    (tmp_path / "a").write_text("1")
    calls = []
    real = projfiles._btimes_raw
    monkeypatch.setattr(projfiles, "_btimes_raw", lambda *a: (calls.append(1), real(*a))[1])
    projfiles._btimes_cache.clear()
    projfiles._btimes(str(tmp_path), [], ["a"])
    projfiles._btimes(str(tmp_path), [], ["a"])
    assert len(calls) == 1
    (tmp_path / "b").write_text("2")
    os.utime(tmp_path, ns=(1, 3_000_000_000))
    projfiles._btimes(str(tmp_path), [], ["a", "b"])
    assert len(calls) == 2


def test_git_status_visible_after_stale_window(tmp_path, monkeypatch):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                       capture_output=True)

    git("init", "-q")
    (tmp_path / "f.txt").write_text("1")
    git("add", ".")
    git("commit", "-qm", "i")
    clk, jobs = Clock(), []
    monkeypatch.setattr(gitctl, "_status_cache", SWR(clock=clk, executor=jobs.append))
    monkeypatch.setattr(gitctl, "check_gitdir", lambda *a, **k: None, raising=False)
    gs, ok = gitctl.status_map(str(tmp_path))
    if not ok:
        import pytest
        pytest.skip("repo not accepted by gitctl guard here")
    assert "f.txt" not in gs
    (tmp_path / "f.txt").write_text("2")
    clk.t += 1
    assert "f.txt" not in gitctl.status_map(str(tmp_path))[0]  # fresh
    clk.t += 10
    assert "f.txt" not in gitctl.status_map(str(tmp_path))[0]  # stale served, refresh queued
    jobs.pop()()
    assert gitctl.status_map(str(tmp_path))[0].get("f.txt") == "M"
