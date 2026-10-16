"""Canvas store + JSON routes (fake agents, no tmux)."""

import dataclasses
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import ORIGIN, audit_text, fake, post, snap_with  # noqa: F401

from pm.core.models import Agent, AgentKind, AgentState, Tier
from pm.projects import canvas
from pm.security import auth
from pm.security.csrf import Csrf
from pm.web.routes import canvas as routes_canvas

A = "claude-1"


def mk(st, ceiling=Tier.SAFE):
    st = dataclasses.replace(st, tier_ceiling=ceiling)
    app = FastAPI()
    app.state.settings = st
    auth.install(app, st)
    app.include_router(routes_canvas.router)
    sess = app.state.sessions.create("1.2.3.4")
    sess.unlocked_until = time.time() + 300
    c = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    c.cookies.set(auth.COOKIE_NAME, app.state.sessions.cookie_value(sess))
    return app, c, Csrf(st.state_dir, st.allowed_origins).token(sess.id), st


@pytest.fixture
def cl(fake, monkeypatch):  # noqa: F811
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])
    return mk(st)


def new(c, tok, title="Doc", text="# hi"):
    r = post(c, tok, f"/a/{A}/canvas", title=title, text=text)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- store
def test_store_versions_limit_and_noop(tmp_path):
    d = canvas.create(tmp_path, A, "T", "v1")
    cid = d["id"]
    assert d["cur"] == 1 and d["versions"][0]["n"] == 1
    same, changed = canvas.save(tmp_path, A, cid, "v1", 1)
    assert not changed and same["cur"] == 1
    for i in range(2, 61):
        d, changed = canvas.save(tmp_path, A, cid, f"v{i}", i - 1)
        assert changed
    assert d["cur"] == 60 and len(d["versions"]) == canvas.MAX_VERSIONS
    assert d["versions"][0]["n"] == 11 and d["versions"][-1]["text"] == "v60"
    with pytest.raises(canvas.CanvasError) as e:
        canvas.save(tmp_path, A, cid, "x", 5)
    assert e.value.status == 409 and e.value.extra == {"error": "stale", "cur": 60}
    r = canvas.restore(tmp_path, A, cid, 20)
    assert r["cur"] == 61 and r["versions"][-1]["text"] == "v20"
    with pytest.raises(canvas.CanvasError) as e:
        canvas.restore(tmp_path, A, cid, 1)  # dropped
    assert e.value.status == 404


def test_store_limits_and_ids(tmp_path):
    with pytest.raises(canvas.CanvasError) as e:
        canvas.create(tmp_path, A, "T", "x" * (canvas.MAX_TEXT + 1))
    assert e.value.status == 413
    canvas.create(tmp_path, A, "T", "x" * canvas.MAX_TEXT)
    for t in ("", "   ", "t" * 121):
        with pytest.raises(canvas.CanvasError) as e:
            canvas.create(tmp_path, A, t, "x")
        assert e.value.status == 400
    for _ in range(canvas.MAX_CANVASES - 1):
        canvas.create(tmp_path, A, "T", "x")
    with pytest.raises(canvas.CanvasError) as e:
        canvas.create(tmp_path, A, "T", "x")
    assert e.value.status == 409 and e.value.extra["error"] == "limit"
    assert len(canvas.list_canvases(tmp_path, A)) == 20
    for bad in ("../x", "c/../..", "C" * 13, "", "cabc", "c" + "0" * 12 + "/", "c" + "g" * 12):
        with pytest.raises(canvas.CanvasError):
            canvas.get(tmp_path, A, bad)
    for bad in ("../x", "A", "x/y", ".."):
        with pytest.raises(canvas.CanvasError):
            canvas.list_canvases(tmp_path, bad)
    assert not any(tmp_path.glob("**/*.tmp"))


def test_store_delete_and_rename(tmp_path):
    d = canvas.create(tmp_path, A, "T", "a")
    d2, ch = canvas.save(tmp_path, A, d["id"], "a", 1, title="New")
    assert ch and d2["title"] == "New" and d2["cur"] == 1
    assert canvas.delete(tmp_path, A, d["id"]) == 1
    with pytest.raises(canvas.CanvasError):
        canvas.get(tmp_path, A, d["id"])


# ---------------------------------------------------------------- routes
def test_route_flow(cl):
    app, c, tok, st = cl
    assert c.get(f"/a/{A}/canvas").json() == {"canvases": []}
    d = new(c, tok, "My doc", "secret-body-text")
    cid = d["id"]
    assert d["cur"] == 1 and d["title"] == "My doc"
    lst = c.get(f"/a/{A}/canvas").json()["canvases"]
    assert lst[0]["id"] == cid and set(lst[0]) == {"id", "title", "cur", "updated"}
    g = c.get(f"/a/{A}/canvas/{cid}").json()
    assert set(g["versions"][0]) == {"n", "ts", "text", "note"}
    r = post(c, tok, f"/a/{A}/canvas/{cid}/save", text="two", base="1", note="n1")
    assert r.status_code == 200 and r.json()["cur"] == 2
    r = post(c, tok, f"/a/{A}/canvas/{cid}/save", text="three", base="1")
    assert r.status_code == 409 and r.json() == {"error": "stale", "cur": 2, "message": "stale"}
    r = post(c, tok, f"/a/{A}/canvas/{cid}/save", text="two", base="2")
    assert r.status_code == 200 and r.json()["cur"] == 2  # no-op
    r = post(c, tok, f"/a/{A}/canvas/{cid}/restore", n="1")
    assert r.json()["cur"] == 3 and r.json()["versions"][-1]["text"] == "secret-body-text"
    assert post(c, tok, f"/a/{A}/canvas/{cid}/restore", n="9").status_code == 404
    assert post(c, tok, f"/a/{A}/canvas/{cid}/restore", n="x").status_code == 404
    assert post(c, tok, f"/a/{A}/canvas/{cid}/save", text="z", base="abc").status_code == 409
    assert post(c, tok, f"/a/{A}/canvas/{cid}/delete").json()["ok"] is True
    assert c.get(f"/a/{A}/canvas/{cid}").status_code == 404
    log = audit_text(st)
    assert "canvas_create" in log and "canvas_save" in log and "canvas_delete" in log
    assert "secret-body-text" not in log and '"two"' not in log and "three" not in log


def test_route_limits(cl):
    app, c, tok, st = cl
    assert post(c, tok, f"/a/{A}/canvas", title="t", text="x" * (canvas.MAX_TEXT + 1)).status_code == 413
    assert post(c, tok, f"/a/{A}/canvas", title="", text="x").status_code == 400
    assert post(c, tok, f"/a/{A}/canvas", title="t" * 121, text="x").status_code == 400
    for _ in range(20):
        new(c, tok)
    assert post(c, tok, f"/a/{A}/canvas", title="t", text="x").status_code == 409


def test_route_guard(cl):
    app, c, tok, st = cl
    cid = new(c, tok)["id"]
    urls = [f"/a/{A}/canvas", f"/a/{A}/canvas/{cid}/save", f"/a/{A}/canvas/{cid}/restore",
            f"/a/{A}/canvas/{cid}/delete", f"/a/{A}/canvas/preview"]
    for u in urls:
        assert post(c, tok, u, csrf=False, title="t", text="x", base="1", n="1").status_code == 403, u
        assert post(c, "bad", u, title="t", text="x", base="1", n="1").status_code == 403, u
        assert post(c, tok, u, origin="https://evil.example", title="t", text="x").status_code == 403, u
        assert post(c, tok, u, origin=None, title="t", text="x").status_code == 403, u
    anon = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    for u in urls:
        assert anon.post(u, data={"csrf": tok}, headers={"Origin": ORIGIN}).status_code in (401, 303), u
    assert anon.get(f"/a/{A}/canvas").status_code in (401, 303)
    assert anon.get(f"/a/{A}/canvas/{cid}").status_code in (401, 303)
    assert c.get(f"/a/{A}/canvas/{cid}").status_code == 200  # nothing was changed
    assert len(c.get(f"/a/{A}/canvas").json()["canvases"]) == 1


def test_route_unknown_agent_and_ids(cl):
    app, c, tok, st = cl
    assert c.get("/a/nope-9/canvas").status_code == 404
    assert post(c, tok, "/a/nope-9/canvas", title="t", text="x").status_code == 404
    assert c.get("/a/Bad_ID/canvas").status_code == 404
    for bad in ("..", "%2e%2e", "c0123456789ab%2F..", "x"):
        assert c.get(f"/a/{A}/canvas/{bad}").status_code == 404
        assert post(c, tok, f"/a/{A}/canvas/{bad}/delete").status_code == 404
    assert not (st.state_dir / "canvas" / "nope-9").exists()


def test_preview_sanitised_and_unaudited(cl):
    app, c, tok, st = cl
    before = audit_text(st)
    r = post(c, tok, f"/a/{A}/canvas/preview", text="# T\n<script>alert(1)</script> [x](javascript:alert(1))")
    assert r.status_code == 200
    h = r.json()["html"]
    assert "<h1>" in h and "<script" not in h and "javascript:" not in h.split("href=")[-1][:12]
    assert audit_text(st) == before
    r = post(c, tok, f"/a/{A}/canvas/preview", text="x" * (canvas.MAX_TEXT + 1))
    assert r.status_code == 413


def test_audit_fail_closed(cl, monkeypatch):
    app, c, tok, st = cl
    cid = new(c, tok)["id"]
    st.audit_file.unlink()
    st.audit_file.mkdir()  # writes now fail
    for u, kw in ((f"/a/{A}/canvas", {"title": "t", "text": "x"}),
                  (f"/a/{A}/canvas/{cid}/save", {"text": "y", "base": "1"}),
                  (f"/a/{A}/canvas/{cid}/delete", {})):
        assert post(c, tok, u, **kw).status_code == 503, u
    assert canvas.get(st.state_dir, A, cid)["cur"] == 1  # attempt line refused: nothing changed
    assert len(canvas.list_canvases(st.state_dir, A)) == 1


def test_ended_agent_keeps_canvases(cl, monkeypatch):
    app, c, tok, st = cl
    cid = new(c, tok)["id"]
    from pm.agents import registry
    snap_with(monkeypatch, [])
    ended = Agent(id=A, kind=AgentKind.CLAUDE, pane_id="%1", session_id="", pid=0, cwd="/",
                  state=AgentState.UNKNOWN)
    monkeypatch.setattr(registry, "resolve", lambda s, snap, i: type("R", (), {"ended": ended})())
    monkeypatch.setattr(registry, "ended_agent", lambda e: e)
    assert c.get(f"/a/{A}/canvas/{cid}").status_code == 200
