"""pm project reload notice: restart (backend newer than the process) vs refresh (static / restarted)."""
import os
import time
from dataclasses import replace

from helpers import login
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.infra import snapshot as services
from pm.notify import reloadneed


def pkg(tmp_path, back_age, static_age):
    root = tmp_path / "pkg"
    (root / "static" / "css").mkdir(parents=True)
    (root / "templates").mkdir()
    now = time.time()
    for f, age in (("app.py", back_age), ("templates/a.html", back_age), ("static/css/a.css", static_age)):
        p = root / f
        p.write_text("x")
        os.utime(p, (now - age,) * 2)
    return root


def test_need_restart_refresh_none(tmp_path):
    now = time.time()
    start, served = now - 100, now - 100
    # backend newer than the process start: restart (even if static changed too)
    p = pkg(tmp_path / "a", back_age=10, static_age=5)
    assert reloadneed.need(served, start, p)["need"] == "restart"
    # only static newer than the page: refresh
    p = pkg(tmp_path / "b", back_age=500, static_age=5)
    r = reloadneed.need(served, start, p)
    assert r["need"] == "refresh" and "no restart" in r["msg"]
    # nothing changed since the page was served
    p = pkg(tmp_path / "c", back_age=500, static_age=500)
    assert reloadneed.need(now, start, p)["need"] == "none"
    # service restarted after this page was rendered: refresh
    assert reloadneed.need(now - 1000, now - 50, p)["need"] == "refresh"


def test_route_and_chip(env, monkeypatch):
    s, c = env
    login(s, c)
    j = c.get("/pm/reload-need?served=0").json()
    assert j["need"] in ("restart", "refresh", "none") and "msg" in j
    monkeypatch.setattr(reloadneed, "need", lambda served: {"need": "refresh", "msg": "m"})
    assert c.get("/pm/reload-need?served=abc").status_code == 422
    assert c.get("/pm/reload-need?served=1").json() == {"need": "refresh", "msg": "m"}
    # chip only on agents of the pm project
    base = services.agents_mod.detect_agents
    (s.dev_root / "pm").mkdir()
    monkeypatch.setattr(services.agents_mod, "detect_agents",
                        lambda panes: [replace(a, cwd=str(s.dev_root / "pm")) for a in base(panes)])
    h = c.get("/a/claude-12").text
    assert 'class="pmreload" data-cmp-reload' in h and "/static/js/pmreload.js" in h and 'action="/service/reload"' in h


def test_chip_absent_for_other_projects(env):
    s, c = env
    login(s, c)
    h = c.get("/a/claude-12").text
    assert "pmreload.js" not in h and "Restart pm" not in h and "Refresh page" not in h  # the row exists everywhere (reload.js), the pm-only buttons and poller do not


def test_route_requires_login(env):
    s, c = env
    assert c.get("/pm/reload-need", follow_redirects=False).status_code in (302, 303, 401)
