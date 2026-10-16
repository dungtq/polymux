"""Shared fixtures of tests/web (and tests/web/dt)."""

import pytest

from pm.web import assets, render, themes


@pytest.fixture
def ui_globals(monkeypatch):
    """create_app with plugins changes process-wide state: the theme registry, the template env (loader, globals, NAV,
    the default theme, plugin template dirs, per-theme overlays) and the static dirs. Undo all of it after the test."""
    monkeypatch.setattr(themes, "_registry", dict(themes.BUILTIN))
    monkeypatch.setattr(themes, "_warned", set())
    monkeypatch.setattr(render.env, "loader", render.env.loader)
    monkeypatch.setattr(render.env, "globals", {**render.env.globals, "NAV": render.NAV})
    monkeypatch.setattr(render, "_default_theme", render._default_theme)
    monkeypatch.setattr(render, "_extra_dirs", render._extra_dirs)
    monkeypatch.setattr(render, "_overlays", {})  # use_theme clears it in place: give the test its own dict
    monkeypatch.setattr(assets, "_dirs", {})
    yield
    render.env.cache.clear()
