"""System messages (pm's own alerts, no agent): out of the ntfy tables, into the topbar bell."""

import re
import time

from helpers import login, ntfy_item
from helpers import ntfy_env as env  # noqa: F401  (fixture)

from pm.agents import inbox
from pm.core.models import NtfyItem

HDR = {"origin": "https://pm.example.test"}


def _sys(n, path, **kw):
    kw.setdefault("title", f"pm alert -- host: disk {n}")
    kw.setdefault("time", time.time() + n * 0.001)
    return ntfy_item(100 + n, path, **kw)


def _tok(c):
    return re.search(r'name="csrf" value="([^"]+)"', c.get("/").text).group(1)


def test_is_system_predicate():
    assert inbox.is_system(NtfyItem("1", 1.0, "pm alert -- h: cpu", "m"))
    assert inbox.is_system(NtfyItem("1", 1.0, "pm recovered -- h: cpu", "m"))
    assert not inbox.is_system(NtfyItem("1", 1.0, "claude-7cd08a - done", "m"))
    assert not inbox.is_system(NtfyItem("1", 1.0, "alpha is waiting", "m", click="https://pm.example.test/a/claude-1"))
    assert not inbox.is_system(NtfyItem("1", 1.0, "pm alert -- h", "m", click="https://pm.example.test/a/claude-1"))


def test_panels_exclude_system_rows(env):  # noqa: F811
    s, c, st, dev = env
    login(s, c)
    p = str(dev / "alpha")
    st["items"] = [ntfy_item(1, p, title="T1"), _sys(1, p)]
    t = c.get("/ntfy/panel?scope=inbox").text
    assert "T1" in t and "pm alert" not in t
    t = c.get("/ntfy/panel?scope=all").text  # the ntfy page lists agent and system rows
    assert "T1" in t and "pm alert" in t
    t = c.get("/ntfy/panel?scope=system").text
    assert "pm alert" in t and "T1" not in t and "System messages" in t


def test_bell_returns_max_five_and_counts_unread(env):  # noqa: F811
    s, c, st, dev = env
    login(s, c)
    p = str(dev / "alpha")
    st["items"] = [ntfy_item(1, p, time=time.time() - 5)]
    c.get("/inbox/unread")  # baseline: now
    st["items"] += [_sys(i, p) for i in range(1, 8)]
    r = c.get("/ntfy/bell")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.text.count('class="bell-row') == 5 and "7 unread" in r.text
    assert "More (7)" in r.text and 'href="/notifications"' in r.text
    assert c.get("/inbox/unread").json() == {"unread": 0, "system": 7}  # Inbox badge ignores them


def test_bell_requires_login(env):  # noqa: F811
    s, c, st, dev = env
    assert c.get("/ntfy/bell", follow_redirects=False).status_code in (303, 401)
    assert c.get("/notifications", follow_redirects=False).status_code in (303, 401)


def test_read_system_csrf_and_scope(env):  # noqa: F811
    s, c, st, dev = env
    login(s, c)
    p = str(dev / "alpha")
    st["items"] = [ntfy_item(1, p, time=time.time() - 5)]
    c.get("/inbox/unread")
    st["items"] += [_sys(1, p), ntfy_item(2, p, time=time.time() + 0.002)]
    assert c.get("/inbox/unread").json() == {"unread": 1, "system": 1}
    assert c.post("/inbox/read-system", headers=HDR).status_code == 403  # no CSRF token
    assert c.post("/inbox/read-system", data={"csrf": _tok(c)}, headers={"origin": "https://evil.example"}).status_code == 403
    assert c.post("/inbox/read-system", data={"csrf": _tok(c)}, headers=HDR).status_code == 200
    assert c.get("/inbox/unread").json() == {"unread": 1, "system": 0}  # agent row untouched
    assert "inbox_read_system" in s.audit_file.read_text()


def test_bell_row_read_via_inbox_read(env):  # noqa: F811
    s, c, st, dev = env
    login(s, c)
    p = str(dev / "alpha")
    c.get("/inbox/unread")
    st["items"] = [_sys(1, p)]
    key = re.search(r'data-bell-read="([^"]+)"', c.get("/ntfy/bell").text).group(1)
    assert c.post("/inbox/read", data={"csrf": _tok(c), "key": key}, headers=HDR).status_code == 200
    assert c.get("/inbox/unread").json()["system"] == 0
    assert "data-bell-read" not in c.get("/ntfy/bell").text


def test_markup_bell_after_audit_and_servers_badge(env):  # noqa: F811
    s, c, st, dev = env
    login(s, c)
    h = c.get("/").text
    assert h.index('href="/audit"') < h.index('id="bell-menu"') < h.index("data-qchat-open")
    assert 'hx-get="/ntfy/bell"' in h and h.count("data-system-badge") >= 2  # bell + Servers (menu)
    n = c.get("/notifications")
    assert n.status_code == 200 and "ntfy-system" in n.text
    js = c.get("/static/js/inbox.js").text
    assert "data-system-badge" in js and "data-bell-read" in js


def test_bell_n_and_unread_first(env):  # noqa: F811
    s, c, st, dev = env
    login(s, c)
    p = str(dev / "alpha")
    c.get("/inbox/unread")  # baseline: now
    st["items"] = [_sys(i, p) for i in range(1, 13)]
    assert c.get("/ntfy/bell").text.count('class="bell-row') == 5  # classic default
    assert c.get("/ntfy/bell?n=8").text.count('class="bell-row') == 8
    assert c.get("/ntfy/bell?n=10").text.count('class="bell-row') == 10
    for bad in ("0", "11", "x"):
        assert c.get(f"/ntfy/bell?n={bad}").status_code == 422
    assert c.get("/ntfy/bell?unread_first=2").status_code == 422
    newest = max(st["items"], key=lambda i: i.time)
    key = inbox.ntfy_key(newest)
    assert c.post("/inbox/read", data={"csrf": _tok(c), "key": key}, headers=HDR).status_code == 200
    plain = c.get("/ntfy/bell?n=2").text
    first = c.get("/ntfy/bell?n=2&unread_first=1").text
    assert newest.title in plain and newest.title not in first  # the read newest one drops behind the unread
    assert "11 unread" in first
