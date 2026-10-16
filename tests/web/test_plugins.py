"""Plugin loader (pm.web.plugins): routes, nav, settings sections, themes, static, loops; bad plugins are skipped."""
import asyncio
import dataclasses
import logging
import re

import pytest
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from helpers import TESTSERVER, login
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.core.config import Settings
from pm.core.models import Tier
from pm.security.auth import require_session
from pm.security.csrf import Csrf
from pm.security.guard import csrf_session_guard, mutation_guard
from pm.web import assets, plugins, render, themes
from pm.web.app import create_app
from pm.web.render import NavItem

pytestmark = pytest.mark.usefixtures("ui_globals")  # tests/web/conftest.py: undo registry / env / static changes


class EP:
    """Stand-in for an importlib.metadata entry point."""

    def __init__(self, name, target):
        self.name, self._target = name, target

    def load(self):
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


def _fake(tmp_path, events: list):
    tpl, theme_tpl, static = tmp_path / "tpl", tmp_path / "theme/templates", tmp_path / "static"
    for d in (tpl, theme_tpl, static):
        d.mkdir(parents=True)
    (tpl / "plug.html").write_text("plugin plug")
    (tpl / "plug_section.html").write_text("<p>section {{ value }}</p>")
    (theme_tpl / "plug.html").write_text("themed plug")
    (static / "p.css").write_text("body{}")
    router = APIRouter()

    @router.get("/plug", response_class=HTMLResponse, dependencies=[Depends(require_session)])
    def page():
        return render.env.get_template("plug.html").render()

    @router.post("/plug/do", dependencies=[Depends(mutation_guard("plug_do", Tier.SAFE))])
    def do():
        return {"ok": True}

    async def start(app):
        events.append(("start", app.state.settings.port))

    async def stop(app):
        events.append(("stop", app.state.settings.port))

    class Fake:
        name = "fake"
        cfg = None

        def configure(self, cfg, settings):
            Fake.cfg = cfg

        def routers(self):
            return [router]

        def nav_items(self):
            return [NavItem("plug", "/plug", "Plug", more=True)]

        def settings_sections(self):
            return [("Plug settings", "plug_section.html", lambda request: {"value": 42})]

        def background_loops(self):
            return [(start, stop)]

        def themes(self):
            return [themes.Theme("fakeskin", theme_tpl, None, "Fake skin")]

        def templates_dir(self):
            return tpl

        def static_dir(self):
            return static

    return Fake


def _settings(tmp_path, **kw):
    kw = {"disable_poll": True, **kw}
    return Settings(tmux_socket="pmtest-unused", state_dir=tmp_path / "state", user_ssh_dir=tmp_path / "home-ssh",
                    token_file=tmp_path / "cfg/token", totp_file=tmp_path / "cfg/totp", tier_ceiling=Tier.SAFE, **kw)


def _client(s, eps):
    app = create_app(s, plugin_eps=eps)
    return app, TestClient(app, base_url=TESTSERVER)


def test_plugin_route_nav_static_and_settings_section(tmp_path):
    Fake = _fake(tmp_path, [])
    s = _settings(tmp_path, plugins={"fake": {"color": "red"}, "other": {"x": 1}})
    app, c = _client(s, [EP("fake-ep", Fake)])
    assert [p.name for p in app.state.plugins] == ["fake"] and Fake.cfg == {"color": "red"}
    assert c.get("/plug", follow_redirects=False).status_code == 303  # same session rule as core pages
    login(s, c)
    assert c.get("/plug").text == "plugin plug"  # classic theme: the plugin's own template
    r = c.get("/static/p/fake/p.css")
    assert r.status_code == 200 and r.text == "body{}" and r.headers["cache-control"] == "no-cache"
    assert c.get("/static/css/tokens.css").status_code == 200  # core /static untouched
    assert render.env.globals["NAV"][-1] == NavItem("plug", "/plug", "Plug", more=True)
    assert render.env.globals["NAV"][:-1] == render.NAV
    assert 'href="/plug"' in c.get("/settings").text
    panel = c.get("/settings/panel").text
    assert 'class="set-section"' in panel and "Plug settings" in panel and "<p>section 42</p>" in panel
    sect = panel.index('<section id="p-fake-0" class="set-section">')
    assert panel.index('id="qchat-settings"') < sect and panel.rstrip().endswith("</section>\n</div>")
    assert "&lt;section" not in panel  # the section HTML is not escaped twice


def test_plugin_post_goes_through_the_core_guard(tmp_path):
    s = _settings(tmp_path)
    app, c = _client(s, [EP("fake", _fake(tmp_path, []))])
    h = {"Origin": s.allowed_origins[0]}
    assert c.post("/plug/do", headers=h).status_code == 401  # no session
    login(s, c)
    assert c.post("/plug/do", headers=h).status_code == 403  # no CSRF
    sid = next(iter(app.state.sessions._sessions))
    tok = Csrf(s.state_dir, s.allowed_origins).token(sid)
    assert c.post("/plug/do", headers={**h, "X-CSRF-Token": tok}).json() == {"ok": True}
    audit = (s.state_dir / "audit.jsonl").read_text()
    assert '"plug_do"' in audit


def test_plugin_with_unguarded_mutating_route_is_skipped(tmp_path, caplog):
    bad = APIRouter()

    @bad.post("/bad")
    def b():
        return {}

    class Bad:
        name = "bad"

        def routers(self):
            return [bad]

    with caplog.at_level(logging.ERROR, logger="pm.web.plugins"):
        app, c = _client(_settings(tmp_path), [EP("bad", Bad)])
    assert app.state.plugins == [] and "mutation_guard" in caplog.text
    assert c.post("/bad").status_code in (404, 405)


def test_plugin_theme_overrides_and_unknown_theme_falls_back(tmp_path):
    Fake = _fake(tmp_path, [])
    s = _settings(tmp_path, theme="fakeskin")
    _, c = _client(s, [EP("fake", Fake)])
    login(s, c)
    assert c.get("/plug").text == "themed plug"
    assert c.get("/healthz").status_code == 200
    _, c = _client(dataclasses.replace(s, theme="nope"), [EP("fake", Fake)])  # same theme again: no duplicate
    login(s, c)
    assert c.get("/plug").text == "plugin plug"
    assert render.env.loader.loaders[0].searchpath == [str(themes.CLASSIC.templates)]


def test_theme_static_mounted_under_static_t(tmp_path):
    (tmp_path / "ts").mkdir()
    (tmp_path / "ts/tokens.css").write_text(":root{}")

    class Skin:
        name = "skin"

        def themes(self):
            return [themes.Theme("skin", tmp_path, tmp_path / "ts")]

    _, c = _client(_settings(tmp_path), [EP("skin", Skin)])
    assert c.get("/static/t/skin/tokens.css").text == ":root{}"


def test_template_globals_reach_every_theme_and_plugin_static_is_versioned(tmp_path, monkeypatch):
    Fake = _fake(tmp_path, [])
    Fake.template_globals = lambda self: {"fake_shell": lambda: "SHELL"}
    monkeypatch.setitem(render.env.globals, "fake_shell", None)  # removed again after the test
    (tmp_path / "theme/templates/plug.html").write_text("themed {{ fake_shell() }}")
    s = _settings(tmp_path)
    app, c = _client(s, [EP("fake", Fake)])
    assert app.state.plugins[0].template_globals == {"fake_shell": render.env.globals["fake_shell"]}
    login(s, c)
    tok = themes._current.set("fakeskin")  # the theme overlay shares the globals
    try:
        assert render.get_template("plug.html").render() == "themed SHELL"
    finally:
        themes._current.reset(tok)
    out = assets.version_static('<link href="/static/p/fake/p.css">', render.STATIC_DIR)
    assert re.fullmatch(r'<link href="/static/p/fake/p\.css\?v=[0-9a-f]{10}">', out)


@pytest.mark.parametrize("got", [{"shell": 1}, {"fakeshell": 1}, {"fake_ok": 1, "other_x": 2}, {1: 2}, ["fake_x"]])
def test_template_globals_without_the_plugin_prefix_skip_the_plugin(tmp_path, caplog, got):
    Fake = _fake(tmp_path, [])
    Fake.template_globals = lambda self: got
    with caplog.at_level(logging.WARNING, logger="pm.web.plugins"):
        app, _ = _client(_settings(tmp_path), [EP("fake", Fake)])
    assert app.state.plugins == [] and "template_globals()" in caplog.text
    assert not any(k in render.env.globals for k in ("shell", "fakeshell", "fake_ok", "other_x"))


def test_broken_plugins_are_skipped_and_pm_still_serves(tmp_path, caplog):
    class HookRaises:
        name = "hookraises"

        def nav_items(self):
            raise RuntimeError("boom")

    class WrongType:
        name = "wrongtype"

        def routers(self):
            return ["not a router"]

    class BadName:
        name = "../x"

    good = _fake(tmp_path, [])
    eps = [EP("a", ImportError("missing dep")), EP("b", HookRaises), EP("c", WrongType), EP("d", BadName),
           EP("e", good), EP("f", good)]
    with caplog.at_level(logging.WARNING, logger="pm.web.plugins"):
        app, c = _client(_settings(tmp_path), eps)
    assert [p.name for p in app.state.plugins] == ["fake"]
    assert "missing dep" in caplog.text and "boom" in caplog.text and "loaded twice" in caplog.text
    assert c.get("/healthz").json()["ok"] is True


def test_disabled_loads_nothing(tmp_path):
    Fake = _fake(tmp_path, [])
    app, _ = _client(_settings(tmp_path, plugins_enabled=False), [EP("fake", Fake)])
    assert app.state.plugins == [] and render.env.globals["NAV"] == render.NAV


def test_discovery_only_off_with_pm_no_plugins(tmp_path, monkeypatch):
    """disable_poll (dev instance) still discovers installed plugins; PM_NO_PLUGINS=1 (the test suite) does not,
    but injected entry points load either way."""
    found = []
    monkeypatch.delenv("PM_NO_PLUGINS")
    monkeypatch.setattr(plugins, "_discover", lambda: found.append(1) or [EP("x", _fake(tmp_path, []))])
    assert [p.name for p in plugins.load(_settings(tmp_path))] == ["fake"] and found == [1]
    monkeypatch.setenv("PM_NO_PLUGINS", "1")
    assert plugins.load(_settings(tmp_path)) == [] and found == [1]
    assert [p.name for p in plugins.load(_settings(tmp_path), [EP("y", _fake(tmp_path / "y", []))])] == ["fake"]
    assert plugins.load(_settings(tmp_path, plugins_enabled=False)) == [] and found == [1]


def test_suite_never_discovers_installed_plugins():
    import os
    assert os.environ.get("PM_NO_PLUGINS") == "1"  # tests/conftest.py


def test_background_loops_start_and_stop(tmp_path):
    events: list = []
    app, c = _client(_settings(tmp_path, port=9001), [EP("fake", _fake(tmp_path, events))])
    with c:  # lifespan with disable_poll: plugin loops are not started
        pass
    assert events == []
    asyncio.run(plugins.start_loops(app))
    assert events == [("start", 9001)] and len(app.state.plugin_loops) == 1
    asyncio.run(plugins.stop_loops(app))
    assert events == [("start", 9001), ("stop", 9001)] and app.state.plugin_loops == []


def test_failing_loop_start_is_logged_and_not_stopped(tmp_path, caplog):
    stopped = []

    async def start(app):
        raise RuntimeError("no start")

    async def stop(app):
        stopped.append(1)

    class Loopy:
        name = "loopy"

        def background_loops(self):
            return [(start, stop)]

    app, _ = _client(_settings(tmp_path), [EP("loopy", Loopy)])
    with caplog.at_level(logging.ERROR, logger="pm.web.plugins"):
        asyncio.run(plugins.start_loops(app))
        asyncio.run(plugins.stop_loops(app))
    assert "no start" in caplog.text and stopped == []


def test_failing_settings_section_is_left_out(tmp_path):
    class Sect:
        name = "sect"

        def settings_sections(self):
            return [("Broken", "missing_template.html", lambda request: {})]

    s = _settings(tmp_path)
    _, c = _client(s, [EP("sect", Sect)])
    login(s, c)
    r = c.get("/settings/panel")
    assert r.status_code == 200 and "Broken" not in r.text


async def _asgi(scope, receive, send):
    pass


def _raw_post(r):
    r.add_route("/raw", _asgi, methods=["POST"])


class _App:
    async def __call__(self, scope, receive, send):
        pass


def _raw_any(r):
    r.add_route("/raw", _App())  # an ASGI app (not a function): methods None = every method


def _mount(r):
    from starlette.routing import Mount
    r.routes.append(Mount("/m", app=_asgi))


def _websocket(r):
    @r.websocket("/plug/ws")
    async def ws(websocket):
        pass


def _csrf_only(r):
    @r.post("/plug/x", dependencies=[Depends(csrf_session_guard("x"))])  # no tier check
    def x():
        return {}


def _stepup_only(r):
    @r.post("/plug/x", dependencies=[Depends(csrf_session_guard("x", tier=Tier.SAFE, stepup_only=True))])
    def x():
        return {}


def _nested_unguarded(r):
    inner = APIRouter()

    @inner.post("/x")
    def x():
        return {}

    r.include_router(inner, prefix="/plug")


@pytest.mark.parametrize("add", [_raw_post, _raw_any, _mount, _websocket, _csrf_only, _stepup_only, _nested_unguarded])
def test_routes_without_the_core_guard_skip_the_plugin(tmp_path, caplog, add):
    router = APIRouter()
    add(router)

    class Sneaky:
        name = "sneaky"

        def routers(self):
            return [router]

    with caplog.at_level(logging.ERROR, logger="pm.web.plugins"):
        app, c = _client(_settings(tmp_path), [EP("sneaky", Sneaky)])
    assert app.state.plugins == [] and "sneaky" in caplog.text
    assert c.post("/raw").status_code in (404, 405) and c.post("/plug/x").status_code in (404, 405)


def test_allowed_route_shapes_load(tmp_path):
    inner, router = APIRouter(), APIRouter()

    @inner.post("/x")
    def x():
        return {}

    router.include_router(inner, prefix="/plug", dependencies=[Depends(mutation_guard("plug_x", Tier.SAFE))])
    guarded_router = APIRouter(dependencies=[Depends(mutation_guard("plug_y", Tier.SAFE))])

    @guarded_router.post("/plug/y")
    def y():
        return {}

    async def page(request):
        from starlette.responses import PlainTextResponse
        return PlainTextResponse("raw get")

    router.add_route("/plug/raw", page, methods=["GET"])

    class Ok:
        name = "ok"

        def routers(self):
            return [router, guarded_router]

    s = _settings(tmp_path)
    app, c = _client(s, [EP("ok", Ok)])
    assert [p.name for p in app.state.plugins] == ["ok"]
    assert c.get("/plug/raw").text == "raw get"
    h = {"Origin": s.allowed_origins[0]}
    assert c.post("/plug/x", headers=h).status_code == 401 and c.post("/plug/y", headers=h).status_code == 401


# ------------------------------------------------------------------ link hooks
class _Links:
    name = "links"

    def project_links(self, project):
        return ("viewer", [plugins.ExtLink("files", f"https://viewer.example/p/{project.name}/", "Browse the files",
                                           "files"),
                           plugins.ExtLink("evil", "javascript:alert(1)")])

    def agent_cwd_url(self, agent, project):
        return f"https://viewer.example/tree/{project.name}/"

    def agent_transcript_url(self, agent):
        return f"https://viewer.example/sessions/{agent.session_uuid}"


class _Broken:
    name = "broken"

    def project_links(self, project):
        raise RuntimeError("boom")

    def agent_cwd_url(self, agent, project):
        return "javascript:alert(1)"

    def agent_transcript_url(self, agent):
        return 42


def _with(c, monkeypatch, *objs):
    monkeypatch.setattr(c.app.state, "plugins", [plugins.Plugin(o.name, o) for o in objs])


def test_link_hooks_render_on_project_agent_and_items(env, monkeypatch, caplog):
    s, c = env
    login(s, c)
    _with(c, monkeypatch, _Broken(), _Links())
    with caplog.at_level(logging.WARNING, logger="pm.web.plugins"):
        h = c.get("/p/alpha").text
        a = c.get("/a/claude-12").text
        t = c.get("/a/claude-12/items").text
    assert h.count('class="pj-af-row ext-links"') == 1 and "<b>viewer</b>" in h
    assert ('<a class="badge" href="https://viewer.example/p/alpha/" target="_blank" rel="noopener noreferrer" '
            'title="Browse the files" data-icon="files">files</a>') in h
    assert "javascript:" not in h and ">evil<" not in h  # bad URL dropped, the rest of the group kept
    assert 'href="https://viewer.example/tree/alpha/"' in a and "javascript:" not in a  # first http(s) URL wins
    assert 'href="https://viewer.example/sessions/abc-123"' in t and "Full Transcript" in t
    assert "project_links() failed" in caplog.text  # the raising hook is logged, the page still renders


def test_no_plugin_no_links(env):
    s, c = env
    login(s, c)
    assert "ext-links" not in c.get("/p/alpha").text
    a = c.get("/a/claude-12").text
    assert 'title="Open ' not in a and "Full Transcript" not in c.get("/a/claude-12/items").text


def test_link_collectors_ignore_bad_hooks(caplog):
    class Wrong:
        name = "wrong"

        def project_links(self, project):
            return ("t", [("label", "https://x.example/")])  # plain tuples, not ExtLink

    class Empty:
        name = "empty"

        def project_links(self, project):
            return ("t", [plugins.ExtLink("x", "ftp://x.example/")])

    ps = [plugins.Plugin("wrong", Wrong()), plugins.Plugin("empty", Empty()), plugins.Plugin("none", object())]
    assert plugins.project_link_groups(ps, None) == []
    assert plugins.first_url(ps, "agent_cwd_url", None, None) is None
    assert "project_links() returned the wrong type" in caplog.text
