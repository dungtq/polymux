"""pm project reload notice: restart (backend newer than the process) vs refresh (static / restarted), and the
watcher behind the "New code: restart pm" bar (one verdict per process, published once as pm_code)."""
import asyncio
import os
import time
from dataclasses import replace

import pytest
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
                        lambda panes, **kw: [replace(a, cwd=str(s.dev_root / "pm")) for a in base(panes)])
    h = c.get("/a/claude-12").text
    assert 'class="pmreload" data-cmp-reload' in h and "/static/js/pmreload.js" in h and 'action="/service/reload"' in h


def test_chip_absent_for_other_projects(env):
    s, c = env
    login(s, c)
    h = c.get("/a/claude-12").text
    assert "pmreload.js" not in h and "Refresh page" not in h  # the row exists everywhere (reload.js), the pm-only button and poller do not
    assert "pmr-restart" not in h  # a needed restart is the page bar #pmcode, never the composer row


def test_route_requires_login(env):
    s, c = env
    assert c.get("/pm/reload-need", follow_redirects=False).status_code in (302, 303, 401)


# ---------------------------------------------------------------- watcher ("new code" bar)
@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(reloadneed, "_last", None)


def test_check_restart_with_head(tmp_path, fresh):
    now = time.time()
    repo = tmp_path / "repo"
    p = pkg(repo / "src", back_age=10, static_age=500).rename(repo / "src" / "pm")
    seen = []

    def head(root):
        seen.append(root)
        return "  fix:   the\nbar  " + "x" * 200

    v = reloadneed.check(head, start=now - 100, pkg=p)
    assert v["need"] == "restart" and v["msg"] == reloadneed.CODE_MSG and "head" not in v  # no .git: not pm's repo
    assert seen == []
    (repo / ".git").mkdir()
    v = reloadneed.check(head, start=now - 100, pkg=p)
    assert seen == [repo] and v["head"].startswith("fix: the bar x") and len(v["head"]) == reloadneed.HEAD_MAX
    assert set(reloadneed.check(lambda r: 1 / 0, start=now - 100, pkg=p)) == {"need", "msg"}  # head is optional
    assert reloadneed.check(head, start=now, pkg=p) is None  # nothing newer than the process
    assert reloadneed.code() is None  # check() never sets the verdict


def test_watch_publishes_once_when_it_flips(monkeypatch, fresh):
    verdict = {"need": "restart", "msg": reloadneed.CODE_MSG, "head": "subject"}
    steps = iter([None, None, verdict])
    calls, published = [], []

    def fake_check(head_fn):
        calls.append(head_fn)
        return next(steps)

    monkeypatch.setattr(reloadneed, "check", fake_check)
    asyncio.run(asyncio.wait_for(reloadneed.watch(lambda t, d: published.append((t, d)), head_fn="h", every=0), 5))
    assert len(calls) == 3 and calls[0] == "h"
    assert published == [("pm_code", verdict)]  # once, then the loop ended
    assert reloadneed.code() == verdict
    asyncio.run(asyncio.wait_for(reloadneed.watch(lambda t, d: published.append((t, d)), every=0), 5))
    assert len(published) == 1 and len(calls) == 3  # a restart is final: no second walk, no second event


def test_watch_survives_errors(monkeypatch, fresh):
    steps = iter([OSError("x"), {"need": "restart", "msg": "m"}])

    def fake_check(head_fn):
        v = next(steps)
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(reloadneed, "check", fake_check)
    published = []
    asyncio.run(asyncio.wait_for(reloadneed.watch(lambda t, d: published.append(d), every=0), 5))
    assert published == [{"need": "restart", "msg": "m"}]


def test_start_stop_cancel(fresh):
    from types import SimpleNamespace as NS

    async def run():
        app = NS(state=NS())
        task = reloadneed.start(app, lambda t, d: None)
        assert app.state.reloadneed_task is task and not task.done()
        await reloadneed.stop(app)
        assert task.cancelled() and app.state.reloadneed_task is None
        await reloadneed.stop(app)  # idempotent

    asyncio.run(run())


def test_globals_and_no_watcher_with_poll_off(env):
    from fastapi.testclient import TestClient

    from pm.web import render
    from pm.web.app import create_app
    s, _ = env
    app = create_app(replace(s, disable_poll=True))
    assert render.env.globals["pm_code"] is reloadneed.code and render.env.globals["reload_ok"] is True
    assert render.env.globals["reload_unit"] == "pm.service"
    with TestClient(app):
        assert getattr(app.state, "reloadneed_task", None) is None  # tests never run the watcher
    create_app(replace(s, dev_banner=True))
    assert render.env.globals["reload_ok"] is False
