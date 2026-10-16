"""Theme registry (pm.web.themes): classic is built in and the fallback, plugins add themes, loaders chain dirs."""
import logging
import tomllib

import pytest

from pm.web import render, themes


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    monkeypatch.setattr(themes, "_registry", dict(themes.BUILTIN))
    monkeypatch.setattr(themes, "_warned", set())


def test_classic_is_registered_and_is_the_default_template_dir():
    c = themes.get("classic")
    assert c is themes.CLASSIC and c.static is None and c.label == "Classic"
    assert (c.templates / "base.html").is_file()
    assert render.TEMPLATE_DIR == c.templates
    meta = tomllib.loads((c.templates.parent / "theme.toml").read_text())
    assert meta["name"] == "classic" and meta["label"] == "Classic"
    assert [t.name for t in themes.all_themes()] == ["classic"]


def test_unknown_name_falls_back_to_classic_and_warns_once(caplog):
    with caplog.at_level(logging.WARNING, logger="pm.web.themes"):
        assert themes.get("nope") is themes.CLASSIC
        assert themes.get("nope") is themes.CLASSIC
    assert sum("nope" in r.getMessage() for r in caplog.records) == 1


def test_register_duplicate_is_skipped(tmp_path, caplog):
    t = themes.Theme("x", tmp_path)
    themes.register(t)
    with caplog.at_level(logging.WARNING, logger="pm.web.themes"):
        themes.register(themes.Theme("x", tmp_path / "other"))
        themes.register(themes.Theme("classic", tmp_path))
    assert themes.get("x") is t and themes.get("classic") is themes.CLASSIC
    assert len(caplog.records) == 2


def test_loader_overrides_one_template_and_falls_back_to_classic(tmp_path):
    tpl = tmp_path / "templates"
    tpl.mkdir()
    (tpl / "base.html").write_text("fake base")
    plug = tmp_path / "plug"
    plug.mkdir()
    (plug / "plug_only.html").write_text("from plugin")
    (plug / "base.html").write_text("plugin base")
    themes.register(themes.Theme("fake", tpl))
    from jinja2 import Environment
    e = Environment(loader=themes.loader_for("fake", [plug]))
    assert e.get_template("base.html").render() == "fake base"  # the theme wins over plugins and classic
    assert e.get_template("plug_only.html").render() == "from plugin"
    assert e.get_template("_macros.html").filename.startswith(str(themes.CLASSIC.templates))  # classic fallback


def test_loader_for_classic_has_no_duplicate_dir():
    loader = themes.loader_for("classic")
    assert [ld.searchpath for ld in loader.loaders] == [[str(themes.CLASSIC.templates)]]


def test_static_mounts(tmp_path):
    assert themes.static_mounts() == []  # classic keeps /static
    themes.register(themes.Theme("dt", tmp_path / "templates", tmp_path / "static"))
    assert themes.static_mounts() == [("/static/t/dt", tmp_path / "static")]


def test_use_theme_switches_and_restores_the_env(tmp_path, monkeypatch):
    monkeypatch.setattr(render.env, "loader", render.env.loader)
    (tmp_path / "_sortbar.html").write_text("themed")
    themes.register(themes.Theme("fake", tmp_path))
    render.use_theme("fake")
    assert render.env.get_template("_sortbar.html").render() == "themed"
    render.use_theme("classic")
    assert render.env.get_template("_sortbar.html").render() != "themed"


# ------------------------------------------------------------------ per-request theme (?ui= / cookie pm_ui)
def test_current_is_none_outside_a_request():
    assert themes.current() is None
    assert themes.names() == ["classic"]


def test_choose_param_cookie_default(tmp_path):
    themes.register(themes.Theme("skin", tmp_path))
    assert themes.choose("skin", None, "classic") == ("skin", True)  # a valid param wins and is remembered
    assert themes.choose("skin", "classic", "classic") == ("skin", True)
    assert themes.choose(None, "skin", "classic") == ("skin", False)  # a valid cookie, nothing to set again
    assert themes.choose("bogus", "skin", "classic") == ("skin", False)  # a bad param is ignored
    assert themes.choose("bogus", "bogus", "classic") == ("classic", False)
    assert themes.choose("", None, "skin") == ("skin", False)
    assert themes.choose(None, None, "gone") == ("classic", False)  # an unregistered default -> classic
    assert themes._warned == set()  # raw client values never reach get() (its warn-once set stays bounded)


def _mw_client(default="classic"):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from pm.core import executors

    app = FastAPI()

    @app.get("/async")
    async def a():
        return {"t": themes.current()}

    @app.get("/sync")
    def s():  # Starlette's threadpool
        return {"t": themes.current()}

    @app.get("/pool")
    async def p():
        return {"t": await executors.run_collectors(themes.current)}

    app.add_middleware(themes.UIThemeMiddleware, default=default)
    return TestClient(app, base_url="https://testserver")


@pytest.mark.parametrize("path", ["/async", "/sync", "/pool"])
def test_middleware_sets_the_theme_for_async_sync_and_pool_work(tmp_path, path):
    themes.register(themes.Theme("skin", tmp_path))
    c = _mw_client()
    r = c.get(path)
    assert r.json() == {"t": "classic"} and "set-cookie" not in r.headers
    r = c.get(path + "?ui=skin")
    assert r.json() == {"t": "skin"}
    assert r.headers["set-cookie"] == "pm_ui=skin; Path=/; Max-Age=31536000; Secure; SameSite=Lax"
    r = c.get(path, headers={"cookie": "pm_ui=skin"})  # the cookie alone: no new Set-Cookie
    assert r.json() == {"t": "skin"} and "set-cookie" not in r.headers
    r = c.get(path + "?ui=skin", headers={"cookie": "pm_ui=skin"})  # same as the cookie: not set again
    assert r.json() == {"t": "skin"} and "set-cookie" not in r.headers
    assert themes.current() is None  # reset after the request


def test_middleware_ignores_invalid_names(tmp_path):
    themes.register(themes.Theme("skin", tmp_path))
    c = _mw_client(default="skin")
    for q, cookie in (("?ui=nope", ""), ("?ui=..%2Fx", ""), ("", "pm_ui=nope"), ("?ui=", "pm_ui=x;y")):
        r = c.get("/async" + q, headers={"cookie": cookie} if cookie else {})
        assert r.json() == {"t": "skin"} and "set-cookie" not in r.headers
    r = c.get("/async?ui=classic", headers={"cookie": "pm_ui=nope"})
    assert r.json() == {"t": "classic"} and r.headers["set-cookie"].startswith("pm_ui=classic;")
    assert themes._warned == set()


@pytest.fixture
def _overlays(monkeypatch):
    monkeypatch.setattr(render, "_overlays", {})
    monkeypatch.setattr(render, "_default_theme", "classic")
    monkeypatch.setattr(render, "_extra_dirs", ())


def test_get_template_uses_an_overlay_per_theme(tmp_path, monkeypatch, _overlays):
    (tmp_path / "_sortbar.html").write_text("skin {{ skin_late }} {{ 3 | tokens }}")
    themes.register(themes.Theme("skin", tmp_path))
    classic = render.env.get_template("_sortbar.html").render()
    assert render.get_template("_sortbar.html").render() == classic  # outside a request: the default env
    tok = themes._current.set("skin")
    try:
        monkeypatch.setitem(render.env.globals, "skin_late", "")
        assert render.get_template("_sortbar.html").render() == "skin  3"  # filters shared
        assert list(render._overlays) == ["skin"]
        render.env.globals["skin_late"] = "late"  # a global added after the overlay exists
        assert render.get_template("_sortbar.html").render() == "skin late 3"
        assert render._overlays["skin"].globals is render.env.globals
        themes._current.set("classic")  # the default theme: the default env, no overlay
        assert render.get_template("_sortbar.html").render() == classic
        themes._current.set("unregistered")
        assert render.get_template("_sortbar.html").render() == classic and list(render._overlays) == ["skin"]
    finally:
        themes._current.reset(tok)
    render.use_theme("classic")
    assert render._overlays == {}  # use_theme drops the overlays


def _app_settings(tmp_path):
    from pm.core.config import Settings
    from pm.core.models import Tier
    return Settings(tmux_socket="pmtest-unused", state_dir=tmp_path / "state", user_ssh_dir=tmp_path / "home-ssh",
                    token_file=tmp_path / "cfg/token", totp_file=tmp_path / "cfg/totp", tier_ceiling=Tier.SAFE,
                    disable_poll=True)


def test_picker_only_with_more_than_one_theme_and_routes_render_the_chosen_theme(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from helpers import TESTSERVER, login

    from pm.web.app import create_app

    monkeypatch.setattr(render.env, "loader", render.env.loader)
    s = _app_settings(tmp_path)
    c = TestClient(create_app(s, plugin_eps=[]), base_url=TESTSERVER)
    login(s, c)
    panel = c.get("/settings/panel").text
    assert 'id="ui-theme"' not in panel and "pm_ui" not in panel  # classic alone: the panel as before
    skin = tmp_path / "skin"
    skin.mkdir()
    (skin / "settings.html").write_text("SKIN PAGE {{ ui_theme }}")
    themes.register(themes.Theme("skin", skin, None, "Skin"))
    panel = c.get("/settings/panel").text
    assert '<section id="ui-theme" class="set-section">' in panel and '<select name="ui">' in panel
    assert '<option value="classic" selected>Classic</option>' in panel and '<option value="skin">Skin</option>' in panel
    assert panel.index('id="ui-theme"') > panel.index('id="qchat-settings"')
    assert "SKIN PAGE" not in c.get("/settings").text
    r = c.get("/settings?ui=skin")  # the picker's GET form
    assert r.text == "SKIN PAGE skin" and r.headers["set-cookie"].startswith("pm_ui=skin;")
    assert '<option value="skin" selected>Skin</option>' in c.get("/settings/panel").text  # cookie kept
    assert c.get("/settings").text == "SKIN PAGE skin"
    c.cookies.set("pm_ui", "classic")
    assert "SKIN PAGE" not in c.get("/settings").text
