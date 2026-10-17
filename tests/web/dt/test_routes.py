"""pm.web.dt.routes: tab flows through the real app (dt plugin loaded), fragments, JSON, hub events, audit."""

import inspect
import json
import re

from pm.notify.events import Hub
from pm.security.audit import AuditError
from pm.web import render
from pm.web.dt import prefs, routes, shell, stores

from .conftest import ORIGIN, agent, make_snap

AID, BID = "claude-aaa111", "codex-bbb222"
HERE = {"HX-Current-URL": ORIGIN + "/", "HX-Request": "true"}


def strip(r) -> list[tuple[str, str, bool]]:
    """(kind, id, cur) of every tab in a strip fragment, home first."""
    return [(k, i, bool(c)) for k, i, c in
            re.findall(r'data-tab-kind="(\w+)" data-tab-id="([^"]*)"[^>]*><a [^>]*?( aria-current="page")?>', r.text)]


def opened(env, *ids, kind="agent"):
    for i in ids:
        assert env.post("/dt/tabs/open", kind=kind, id=i, headers=HERE).status_code == 200


def test_template_global_registered(env):
    assert render.env.globals["dt_shell"] is shell.dt_shell


def test_open_renders_strip_and_publishes(env, snap):
    snap.value = make_snap([agent(AID, "alpha", window="hub")])
    r = env.post("/dt/tabs/open", kind="agent", id=AID, headers=HERE)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert strip(r) == [("home", "", True), ("agent", AID, False)]
    assert 'data-rev="1"' in r.text and f">{AID}<" in r.text
    assert env.events == [("ui", {"area": "tabs", "rev": 1})]
    assert stores.load(env.s).tabs == (("agent", AID),)


def test_open_twice_focuses(env):
    opened(env, AID, BID)
    r = env.post("/dt/tabs/open", kind="agent", id=AID, headers=HERE)
    assert [i for _, i, _ in strip(r)] == ["", AID, BID]
    st = stores.load(env.s)
    assert st.current == ("agent", AID) and st.rev == 3


def test_strip_marks_the_page_tab_current(env):
    opened(env, AID, BID)
    r = env.post("/dt/tabs/open", kind="term", id="$4", headers={"HX-Current-URL": ORIGIN + "/a/" + BID})
    assert strip(r) == [("home", "", False), ("agent", AID, False), ("agent", BID, True), ("term", "$4", False)]
    assert 'href="/term/s4"' in r.text


def test_close_and_reopen(env):
    opened(env, AID, BID)
    r = env.post("/dt/tabs/close", kind="agent", id=AID, headers=HERE)
    assert r.status_code == 200 and [i for _, i, _ in strip(r)] == ["", BID]
    r = env.post("/dt/tabs/reopen", headers=HERE)
    assert r.status_code == 200 and r.headers["x-dt-href"] == "/a/" + AID
    assert {i for _, i, _ in strip(r)} == {"", AID, BID}
    r = env.post("/dt/tabs/reopen", headers=HERE)
    assert r.status_code == 204 and "x-dt-href" not in r.headers
    assert [e[1]["rev"] for e in env.events] == [1, 2, 3, 4, 4]


def test_close_home_is_400(env):
    assert env.post("/dt/tabs/close", kind="home", id="").status_code == 400


def test_move_by_id(env):
    opened(env, AID, BID)
    env.post("/dt/tabs/open", kind="term", id="$4")
    r = env.post("/dt/tabs/move", kind="term", id="$4", before_kind="agent", before_id=AID, headers=HERE)
    assert [i for _, i, _ in strip(r)] == ["", "$4", AID, BID]
    r = env.post("/dt/tabs/move", kind="term", id="$4", before_kind="", before_id="", headers=HERE)
    assert [i for _, i, _ in strip(r)] == ["", AID, BID, "$4"]
    assert env.post("/dt/tabs/move", kind="agent", id="claude-ffffff", before_kind="", before_id="").status_code == 400
    assert env.post("/dt/tabs/move", kind="agent", id=AID, before_kind="agent", before_id="x y").status_code == 400


def test_current(env):
    opened(env, AID, BID)
    r = env.post("/dt/tabs/current", kind="agent", id=AID)
    assert r.status_code == 204 and stores.load(env.s).current == ("agent", AID)
    assert env.post("/dt/tabs/current", kind="home", id="").status_code == 204
    assert stores.load(env.s).current is None
    assert env.events[-1] == ("ui", {"area": "tabs", "rev": 4})


def test_bad_ids_are_400_and_audited(env):
    for kind, id_ in (("agent", "../../etc/passwd"), ("term", "@1"), ("term", "s4"), ("file", AID), ("agent", "")):
        r = env.post("/dt/tabs/open", kind=kind, id=id_)
        assert r.status_code == 400, (kind, id_)
    assert not stores.path(env.s).exists() and env.events == []
    lines = [json.loads(x) for x in env.s.audit_file.read_text().splitlines()]
    denied = [x for x in lines if x.get("result") == "denied"]
    assert len(denied) == 5 and all(x["action"] == "dt.tabs.open" for x in denied)


def test_ok_is_audited(env):
    opened(env, AID)
    lines = [json.loads(x) for x in env.s.audit_file.read_text().splitlines()]
    assert [x["result"] for x in lines if x["action"] == "dt.tabs.open"] == ["attempt", "ok"]
    assert lines[-1]["target"] == "agent:" + AID


def test_busy_store_is_503(env, monkeypatch):
    def busy(*a, **k):
        raise OSError("tabs are busy")

    monkeypatch.setattr(stores, "apply", busy)
    assert env.post("/dt/tabs/open", kind="agent", id=AID).status_code == 503
    assert env.events == []


def _audit_lines(env, action):
    return [json.loads(x)["result"] for x in env.s.audit_file.read_text().splitlines()
            if json.loads(x)["action"] == action]


def test_busy_prefs_is_503(env, monkeypatch):
    def busy(*a, **k):
        raise OSError("prefs are busy")

    monkeypatch.setattr(prefs, "apply", busy)
    r = env.post("/dt/prefs", run_status="plain")
    assert r.status_code == 503 and r.json()["detail"] == "prefs are busy"
    assert env.events == [] and not prefs.path(env.s).exists()
    assert _audit_lines(env, "dt.prefs.save") == ["attempt", "error"]


def test_failed_audit_write_is_503(env, monkeypatch):
    """Fail closed: the result line cannot be written -> 503 and no hub event (tabs and prefs alike)."""
    def broken(*a, **k):  # the guard's "attempt" line is written by the guard itself, not through routes
        raise AuditError("disk full")

    monkeypatch.setattr(routes, "audit_result", broken)
    assert env.post("/dt/prefs", run_status="plain").status_code == 503
    assert env.post("/dt/tabs/open", kind="agent", id=AID).status_code == 503
    assert env.events == []


def test_real_hub_gets_the_ui_event(env):
    hub = env.app.state.hub = Hub()
    opened(env, AID)
    (ev,) = hub.replay(0)
    assert (ev.type, ev.data) == ("ui", {"area": "tabs", "rev": 1})


def test_get_tabs_fragment_and_etag(env, snap):
    opened(env, AID)
    r = env.c.get("/dt/tabs", headers=HERE)
    assert r.status_code == 200 and strip(r) == [("home", "", True), ("agent", AID, False)]
    assert 'data-state="ended"' in r.text  # not in the snapshot
    again = env.c.get("/dt/tabs", headers={**HERE, "If-None-Match": r.headers["etag"]})
    assert again.status_code == 204


def test_tabs_json(env, snap):
    snap.value = make_snap([agent(AID, "alpha", window="hub")])
    opened(env, AID, BID)
    env.post("/dt/tabs/open", kind="term", id="$4")
    env.post("/dt/tabs/close", kind="agent", id=BID)
    r = env.c.get("/dt/tabs.json")
    assert r.headers["cache-control"] == "no-store"
    assert r.json() == {
        "rev": 4,
        "tabs": [{"kind": "agent", "id": AID, "title": AID, "href": "/a/" + AID, "state": "running"},
                 {"kind": "term", "id": "$4", "title": "logs", "href": "/term/s4", "state": ""}],
        "closed": [{"kind": "agent", "id": BID, "href": "/a/" + BID}]}


def test_side_fragment(env, snap):
    snap.value = make_snap([agent(AID, "alpha"), agent(BID, "beta")])
    opened(env, BID)
    r = env.c.get("/dt/side", headers={"HX-Current-URL": ORIGIN + "/a/" + AID, "HX-Request": "true"})
    assert r.status_code == 200 and "etag" in r.headers
    assert re.findall(r'data-project="(\w+)"', r.text) == ["alpha", "beta"]
    assert f'data-sess-id="{AID}" data-state="running" aria-current="page"' in r.text
    assert f'data-sess-id="{BID}" data-state="running" data-open>' in r.text


def test_side_fragment_asks_for_the_out_of_band_nav_rows(env, snap, monkeypatch):
    snap.value = make_snap([agent(AID, "alpha")])
    seen = {}
    real = routes._render
    monkeypatch.setattr(routes, "_render", lambda request, name, sh, **extra: seen.update(extra) or real(request, name, sh, **extra))
    assert env.c.get("/dt/side", headers=HERE).status_code == 200 and seen == {"side_oob": True}


def test_other_ui_theme_gets_204_and_posts_still_apply(env, snap):
    """Cookie pm_ui=classic (another tab switched the UI): no dt templates there, so no fragment, but no 500."""
    snap.value = make_snap([agent(AID, "alpha")])
    env.c.cookies.set("pm_ui", "classic")
    for url in ("/dt/side", "/dt/tabs"):
        r = env.c.get(url, headers=HERE)
        assert r.status_code == 204 and r.text == "", url
    r = env.post("/dt/tabs/open", kind="agent", id=AID, headers=HERE)
    assert r.status_code == 204 and stores.load(env.s).tabs == (("agent", AID),)
    assert env.events == [("ui", {"area": "tabs", "rev": 1})]
    assert env.post("/dt/tabs/close", kind="agent", id=AID, headers=HERE).status_code == 204
    r = env.post("/dt/tabs/reopen", headers=HERE)
    assert r.status_code == 204 and "x-dt-href" not in r.headers and stores.load(env.s).tabs == (("agent", AID),)
    assert env.c.get("/dt/tabs.json").json()["rev"] == 3  # JSON renders no template: still answered
    env.c.cookies.set("pm_ui", "dt")
    assert env.c.get("/dt/tabs", headers=HERE).status_code == 200


def test_get_handlers_run_in_the_threadpool():
    from pm.web.dt import routes

    assert not any(inspect.iscoroutinefunction(f) for f in (routes.side, routes.tabs, routes.tabs_json))
