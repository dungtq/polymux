"""V7-SEC-1: every mutating /dt route is behind mutation_guard (session + Origin + CSRF + tier + audit), and no GET
writes state. Walks the routes of a real app built with the dt plugin, so a new POST route is checked too."""

import dataclasses

import pytest
from fastapi.routing import APIRoute

from pm.core.models import Tier
from pm.web.dt import prefs, stores

from .conftest import ORIGIN, make_env

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# path -> the mutation_guard action (audit name)
ACTIONS = {"/dt/tabs/open": "dt.tabs.open", "/dt/tabs/close": "dt.tabs.close", "/dt/tabs/reopen": "dt.tabs.reopen",
           "/dt/tabs/current": "dt.tabs.current", "/dt/tabs/move": "dt.tabs.move",
           "/dt/tabs/close-all": "dt.tabs.close-all", "/dt/prefs": "dt.prefs.save"}
EXPECTED_POSTS = set(ACTIONS)
GOOD = {"kind": "agent", "id": "claude-3fa9c1", "before_kind": "", "before_id": "", "run_status": "plain"}


def _untouched(s) -> bool:
    return not stores.path(s).exists() and not prefs.path(s).exists()


def _walk(routes, prefix: str = "", depth: int = 0):
    """(full path, APIRoute) under ``routes``, flattened or behind FastAPI's include wrappers."""
    assert depth < 20
    for r in routes:
        if isinstance(r, APIRoute):
            yield prefix + r.path, r
            continue
        ctx = getattr(r, "include_context", None)
        inner = getattr(r, "original_router", None) or getattr(r, "router", None)
        if inner is not None and hasattr(inner, "routes"):
            yield from _walk(inner.routes, prefix + (getattr(ctx, "prefix", "") or ""), depth + 1)


def _dt_routes(app):
    """The /dt APIRoutes, each with ``.full`` = its full path."""
    out = []
    for path, r in _walk(app.routes):
        if path.startswith("/dt/"):
            r.full = path
            out.append(r)
    return out


def _guard_actions(dependant) -> list[str]:
    """``action`` of every mutation_guard dependency (the guard's closure carries it)."""
    out = []
    for d in dependant.dependencies:
        call = d.call
        if getattr(call, "__module__", "") == "pm.security.guard" and call.__qualname__.endswith("guard.<locals>.dep"):
            cells = dict(zip(call.__code__.co_freevars, (c.cell_contents for c in call.__closure__), strict=True))
            out.append((cells.get("action"), cells.get("tier")))
        out += _guard_actions(d)
    return out


def _posts(app) -> set[str]:
    return {r.full for r in _dt_routes(app) if set(r.methods) & MUTATING}


def test_plugin_routes_loaded(env):
    assert _posts(env.app) == EXPECTED_POSTS  # the loader would drop the plugin on an unguarded route
    assert all(set(r.methods) <= {"GET", "HEAD"} | MUTATING for r in _dt_routes(env.app))


def test_every_post_has_mutation_guard_with_tier(env):
    for r in _dt_routes(env.app):
        if set(r.methods) & MUTATING:
            assert _guard_actions(r.dependant) == [(ACTIONS[r.full], Tier.SAFE)], r.full


@pytest.mark.parametrize("path", sorted(EXPECTED_POSTS))
def test_no_session_401(anon, path):
    r = anon.post(path, csrf=False, **GOOD)
    assert r.status_code == 401
    assert _untouched(anon.s)


@pytest.mark.parametrize("path", sorted(EXPECTED_POSTS))
def test_bad_csrf_403(env, path):
    env.tok = "nope"
    r = env.post(path, **GOOD)
    assert r.status_code == 403 and "csrf" in r.text
    assert _untouched(env.s)


@pytest.mark.parametrize("path", sorted(EXPECTED_POSTS))
def test_missing_csrf_403(env, path):
    assert env.post(path, csrf=False, **GOOD).status_code == 403


@pytest.mark.parametrize("path", sorted(EXPECTED_POSTS))
def test_bad_origin_403(env, path):
    r = env.post(path, origin="https://evil.example.test", **GOOD)
    assert r.status_code == 403 and "origin" in r.text
    assert env.post(path, origin=None, **GOOD).status_code == 403
    assert _untouched(env.s)


@pytest.mark.parametrize("path", sorted(EXPECTED_POSTS))
def test_tier_below_safe_403(settings, plugin, snap, path):
    env = make_env(dataclasses.replace(settings, tier_ceiling=Tier.VIEW), plugin)
    assert env.post(path, **GOOD).status_code == 403
    assert _untouched(env.s)


@pytest.mark.parametrize("path", sorted(EXPECTED_POSTS))
def test_denied_attempts_are_audited(env, path):
    env.post(path, origin="https://evil.example.test", **GOOD)
    text = env.s.audit_file.read_text()
    assert f'"{ACTIONS[path]}"' in text and '"denied"' in text


def test_gets_need_a_session_and_never_write(anon, env, dt_templates):
    (dt_templates / "_add_issue.html").write_text("<div id=bl-quick></div>")  # the stub theme has no such template
    gets = [r.full for r in _dt_routes(env.app) if "GET" in r.methods]
    assert sorted(gets) == ["/dt/add-issue", "/dt/peek/{project}", "/dt/prefs", "/dt/side", "/dt/tabs",
                            "/dt/tabs.json"]
    for p in gets:
        u = p.replace("{project}", "alpha")
        assert anon.c.get(u, headers={"HX-Request": "true"}).status_code == 401
        assert anon.c.get(u).status_code == 303  # browser navigation: to the login page
        # the peek of a fixture project whose folder does not exist: 404 after the session check
        assert env.c.get(u, headers={"Origin": ORIGIN}).status_code == (404 if u != p else 200)
    assert _untouched(env.s)
