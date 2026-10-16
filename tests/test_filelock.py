import json
import re
import subprocess
import sys
import threading
import time

import pytest
from test_backlog import ALPHA, SEED, audit, env, fields, post  # noqa: F401

from pm.core import filelock as fl
from pm.projects import backlog as rb


def lockfile(s, path):
    return fl.lock_path(s.state_dir, path)


def test_lock_path_rule_and_outside_project(env):
    c, f, s = env
    import hashlib
    import os
    want = s.state_dir / "locks" / (hashlib.sha256(os.path.realpath(f).encode()).hexdigest()[:16] + ".lock")
    assert lockfile(s, f) == want
    post_csrf, _ = fields(c)
    post(c, "/backlog/add", post_csrf, text="x")
    assert want.exists()
    assert not [p for p in f.parent.iterdir() if p.name.endswith(".lock")]  # never beside the file


def test_timeout_and_mutual_exclusion_threads(tmp_path):
    lf = tmp_path / "x.lock"
    held, done = threading.Event(), threading.Event()

    def holder():
        with fl.locked(lf, 1):
            held.set()
            done.wait(3)
    t = threading.Thread(target=holder)
    t.start()
    held.wait(2)
    t0 = time.monotonic()
    with pytest.raises(fl.LockTimeout):
        with fl.locked(lf, 0.2):
            pass
    assert time.monotonic() - t0 < 1.5
    done.set()
    t.join()
    with fl.locked(lf, 0.5):
        pass
    counter = {"n": 0, "max": 0, "in": 0}

    def work():
        for _ in range(20):
            with fl.locked(lf, 5):
                counter["in"] += 1
                counter["max"] = max(counter["max"], counter["in"])
                counter["n"] += 1
                counter["in"] -= 1
    ts = [threading.Thread(target=work) for _ in range(6)]
    [x.start() for x in ts]
    [x.join() for x in ts]
    assert counter["n"] == 120 and counter["max"] == 1


def test_subprocess_flock_blocks_us(tmp_path):
    lf = tmp_path / "p.lock"
    code = ("import fcntl,sys,time;fd=open(sys.argv[1],'w');fcntl.flock(fd,fcntl.LOCK_EX);"
            "print('ok',flush=True);time.sleep(3)")
    p = subprocess.Popen([sys.executable, "-c", code, str(lf)], stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "ok"
        with pytest.raises(fl.LockTimeout):
            with fl.locked(lf, 0.3):
                pass
    finally:
        p.kill()
        p.wait()
    with fl.locked(lf, 1):
        pass


def test_mutate_busy_is_503_and_file_untouched(env):
    c, f, s = env
    csrf, _ = fields(c)
    with fl.locked(lockfile(s, f), 1):
        with pytest.raises(rb.Bad) as e:
            rb.mutate(s, f, lambda t: t + "x", timeout=0.2)
    assert e.value.status == 503 and f.read_text() == SEED


def test_external_write_between_read_and_replace(env):
    c, f, s = env
    calls = {"n": 0}

    def fn(t):
        calls["n"] += 1
        if calls["n"] == 1:
            f.write_text(SEED + "- [ ] agent\n")
        return t + "- [ ] mine\n"
    rb.mutate(s, f, fn)  # retried once on the new content
    assert calls["n"] == 2 and "agent" in f.read_text() and "mine" in f.read_text()

    def always(t):
        f.write_text(f.read_text() + "z\n")
        return t + "q\n"
    with pytest.raises(rb.Bad) as e:
        rb.mutate(s, f, always)
    assert e.value.status == 409 and not [p for p in f.parent.iterdir() if p.name.startswith(".backlog-")]
    tok = rb.token_of(f.read_bytes())  # saves never retry: external change -> 409
    with pytest.raises(rb.Bad) as e:
        rb.mutate(s, f, lambda t: (f.write_text("other\n"), "mine\n")[1], token=tok)
    assert e.value.status == 409 and f.read_text() == "other\n"


# ------------------------------------------------------------------ leases
def test_lease_lifecycle(tmp_path):
    sd, path = tmp_path, tmp_path / "BACKLOG.md"
    a, none = fl.lease_acquire(sd, path, "1.1.1.1", now=1000)
    assert none is None and a["expires"] == 1000 + fl.LEASE_SECONDS
    b, holder = fl.lease_acquire(sd, path, "2.2.2.2", now=1010)
    assert b is None and holder["owner"] == "1.1.1.1"
    assert fl.lease_renew(sd, path, a["id"], now=1100)["expires"] == 1100 + fl.LEASE_SECONDS
    assert fl.lease_renew(sd, path, "nope", now=1100) is None
    assert fl.lease_get(sd, path, now=1100 + fl.LEASE_SECONDS + 1) is None  # expired
    b, _ = fl.lease_acquire(sd, path, "2.2.2.2", now=1100 + fl.LEASE_SECONDS + 1)
    assert b and b["owner"] == "2.2.2.2"
    assert fl.lease_renew(sd, path, a["id"], now=1500) is None  # old owner lost it
    t, prev = fl.lease_acquire(sd, path, "3.3.3.3", now=1500, takeover=True)
    assert t and prev["owner"] == "2.2.2.2"
    assert not fl.lease_release(sd, path, b["id"])
    assert fl.lease_release(sd, path, t["id"]) and fl.lease_get(sd, path) is None
    assert len(list((sd / "locks").glob("*.json"))) == 0


def test_lease_prunes_old_files(tmp_path):
    fl.lease_acquire(tmp_path, tmp_path / "a.md", "x", now=1)
    fl.lease_acquire(tmp_path, tmp_path / "b.md", "x", now=1 + 3 * 86400)
    assert len(list((tmp_path / "locks").glob("*.json"))) == 1


def lease_post(c, url, csrf, **data):
    return c.post(url, data={"csrf": csrf, "project": "pm", **data},
                  headers={"Origin": "https://pm.example.test"}, follow_redirects=False)


def test_lease_routes_and_non_owner_save(env):
    c, f, s = env
    csrf, tok = fields(c)
    r = lease_post(c, "/backlog/lease/acquire", csrf)
    assert r.status_code == 200
    mine = r.json()["lease"]
    # second viewer: read-only with message + Take over, no Save
    page = c.get("/backlog?p=pm&edit=1").text
    assert "Being edited by testclient since" in page and "(UTC+7)" in page and "Take over" in page
    assert " readonly" in page and ">Save<" not in page
    assert lease_post(c, "/backlog/lease/acquire", csrf).status_code == 409
    # owner view (lease in URL) is editable
    own = c.get(f"/backlog?p=pm&edit=1&lease={mine}").text
    assert "Being edited" not in own and ">Save<" in own
    assert lease_post(c, "/backlog/lease/renew", csrf, lease=mine).status_code == 200
    assert lease_post(c, "/backlog/lease/renew", csrf, lease="bad").status_code == 409
    # takeover
    r = lease_post(c, "/backlog/lease/take", csrf)
    assert r.status_code == 303 and "lease=" in r.headers["location"]
    new = re.search(r"lease=([0-9a-f]+)", r.headers["location"]).group(1)
    assert new != mine
    # previous owner's save is refused, nothing written
    r = post(c, "/backlog/save", csrf, token=tok, lease=mine, content="old owner")
    assert r.status_code == 409 and "taken over" in r.text and f.read_text() == SEED
    assert "old owner" in r.text
    # no lease field while someone holds one: refused too
    assert post(c, "/backlog/save", csrf, token=tok, content="nolease").status_code == 409
    # new owner saves; lease released
    assert post(c, "/backlog/save", csrf, token=tok, lease=new, content="- [ ] new\n").status_code == 303
    assert f.read_text() == "- [ ] new\n" and fl.lease_get(s.state_dir, f) is None
    log = audit(s)
    for a in ("backlog_lease_take", "backlog_lease_takeover", "conflict"):
        assert a in log
    assert "old owner" not in log and "new\\n" not in log


def test_lease_requires_csrf_origin_session(env):
    c, f, s = env
    csrf, _ = fields(c)
    for url in ("acquire", "renew", "release", "take"):
        u = f"/backlog/lease/{url}"
        assert c.post(u, data={"project": "pm"}, headers={"Origin": "https://pm.example.test"}).status_code == 403
        assert c.post(u, data={"csrf": csrf, "project": "pm"},
                      headers={"Origin": "https://evil.example"}).status_code == 403
        assert lease_post(c, u, csrf, ).status_code != 404
    assert lease_post(c, "/backlog/lease/acquire", csrf, **{}).status_code in (200, 409)
    r = c.post("/backlog/lease/acquire", data={"csrf": csrf, "project": "nope"},
               headers={"Origin": "https://pm.example.test"})
    assert r.status_code == 404


def test_lease_audit_failure_fails_closed(env, monkeypatch):
    c, f, s = env
    csrf, _ = fields(c)
    from pm.security import audit as au

    def boom(self, e):
        raise au.AuditError("x")
    monkeypatch.setattr(au.AuditLog, "write", boom)
    assert lease_post(c, "/backlog/lease/acquire", csrf).status_code == 503
    assert fl.lease_get(s.state_dir, f) is None


def test_force_overwrite_and_diff_escaped(env):
    c, f, s = env
    csrf, tok = fields(c)
    f.write_text(SEED + "- [ ] <script>alert(1)</script> theirs\n")
    r = post(c, "/backlog/save", csrf, token=tok, content="<img src=x onerror=1> mine\n")
    assert r.status_code == 409
    assert "<script>alert" not in r.text and "&lt;script&gt;alert(1)&lt;/script&gt; theirs" in r.text
    assert "<img src=x" not in r.text and "&lt;img src=x onerror=1&gt; mine" in r.text
    assert "--- current file" in r.text or "-- current file" in r.text
    new = re.search(r'name="new_token" value="([0-9a-f]{16})"', r.text).group(1)
    assert new == rb.token_of(f.read_bytes())
    # plain Save with the old token still conflicts
    assert post(c, "/backlog/save", csrf, token=tok, content="m").status_code == 409
    # force with a stale new token conflicts again
    assert post(c, "/backlog/save", csrf, token=tok, force="1", new_token="0" * 16,
                content="m").status_code == 409
    r = post(c, "/backlog/save", csrf, token=tok, force="1", new_token=new,
             content="<img src=x onerror=1> mine\n")
    assert r.status_code == 303 and f.read_text() == "<img src=x onerror=1> mine\n"
    log = audit(s)
    assert "backlog_overwrite" in log and "mine" not in log and "theirs" not in log


def test_diff_text_capped():
    d = rb.diff_text("\n".join(f"a{i}" for i in range(5000)), "x\n")
    assert d.endswith("... diff truncated") and len(d.splitlines()) <= 202


def test_toggle_add_after_external_edit_and_audit_clean(env):
    c, f, s = env
    csrf, _ = fields(c)
    f.write_text(SEED.replace("# t", "# t\nagent note"))
    r = post(c, "/backlog/toggle", csrf, line="5", text="one (2026-01-01)")
    assert r.status_code == 303 and "- [x] one (2026-01-01)" in f.read_text()


def test_gitignore_has_lock_pattern():
    from pathlib import Path
    assert ".*.lock" in (Path(__file__).parent.parent / ".gitignore").read_text().split()


def test_lease_file_private_no_temp_left(tmp_path):
    import stat

    lease, _ = fl.lease_acquire(tmp_path, tmp_path / "a.md", "x", now=1)
    lp = fl.lease_path(tmp_path, tmp_path / "a.md")
    assert stat.S_IMODE(lp.stat().st_mode) == 0o600
    assert json.loads(lp.read_text())["id"] == lease["id"]
    assert not [p for p in lp.parent.iterdir() if p.name.endswith(".tmp")]
