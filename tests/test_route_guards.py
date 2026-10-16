"""Every mutating route (POST/PUT/PATCH/DELETE) depends on security.guard.mutation_guard (or the
csrf_session_guard it is built on).

The routes are walked from a real ``create_app(...)`` app (recursing into included routers), so a
router that is mounted but missing from ``ROUTERS`` is still checked. The two exemption sets below,
keyed by ``(METHOD, path)``, may only shrink: a new unguarded route fails the walk, and a removed
exempt route fails test_exempt_routes_still_exist.
"""
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles

from pm.core.config import Settings
from pm.core.models import Tier
from pm.security import auth
from pm.security.guard import csrf_session_guard, mutation_guard
from pm.web.app import create_app
from pm.web.routes import ROUTERS

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
GUARD_MODULE = "pm.security.guard"

# Routes that cannot or must not use mutation_guard.
EXEMPT = {
    ("POST", "/login"): "no session yet; Origin check + rate limit + audit in auth.login_submit",
    ("POST", "/logout"): "require_session + Origin check; only drops the caller's own session",
    ("POST", "/login/passkey/begin"): "no session yet; pre-login Origin check + rate limit",
    ("POST", "/login/passkey/finish"): "no session yet; pre-auth nonce cookie + rate limit + audit",
    ("POST", "/hooks/claude"): "local Claude hook: loopback peer + shared hook secret, no browser session",
    ("POST", "/client-errors"): "session + Origin + CSRF checked inline; not audited per report (own bounded log)",
    ("POST", "/a/{aid}/canvas/preview"): "read-only render of posted text; session + Origin + CSRF checked inline",
}

# Routes with a hand-written guard that should become mutation_guard / csrf_session_guard (Phase 2).
OWN_GUARD_TO_MERGE: dict[tuple[str, str], str] = {}

ALLOWED = EXEMPT.keys() | OWN_GUARD_TO_MERGE.keys()


GUARD_QUALNAMES = ("mutation_guard.<locals>.dep", "csrf_session_guard.<locals>.dep")


def _is_guard(call) -> bool:
    return (getattr(call, "__qualname__", "") in GUARD_QUALNAMES
            and getattr(call, "__module__", None) == GUARD_MODULE)


def _guarded(dependant) -> bool:
    for dep in dependant.dependencies:
        if _is_guard(dep.call) or _guarded(dep):
            return True
    return False


class _Route:
    """One mutating endpoint: full path, methods, and whether a mutation_guard dependency applies."""

    def __init__(self, route: Route, prefix: str, extra_deps: list) -> None:
        self.path = prefix + route.path
        api = isinstance(route, APIRoute)
        # a plain Starlette Route has no dependencies (never guarded); methods None = every method
        self.methods = set(route.methods or (() if api else MUTATING)) & MUTATING
        ep = route.endpoint
        self.where = f"{getattr(ep, '__module__', '?')}.{getattr(ep, '__name__', type(ep).__name__)}"
        self.guarded = api and (_guarded(route.dependant) or any(_is_guard(getattr(d, "dependency", None))
                                                                  for d in extra_deps))

    def keys(self) -> set[tuple[str, str]]:
        return {(m, self.path) for m in self.methods}


def _walk(routes, prefix: str = "", deps: list | None = None, depth: int = 0) -> Iterator[_Route]:
    """APIRoutes under ``routes``, flattened or behind include wrappers (FastAPI's ``_IncludedRouter``)."""
    assert depth < 20, "router nesting too deep (cycle?)"
    deps = deps or []
    for r in routes:
        if isinstance(r, Route):  # APIRoute or a plain Starlette Route
            yield _Route(r, prefix, deps)
            continue
        ctx = getattr(r, "include_context", None)
        sub_prefix = prefix + (getattr(ctx, "prefix", "") or "")
        sub_deps = [*deps, *(getattr(ctx, "dependencies", None) or [])]
        for attr in ("original_router", "router"):
            inner = getattr(r, attr, None)
            if inner is not None and hasattr(inner, "routes"):
                yield from _walk(inner.routes, sub_prefix, sub_deps, depth + 1)
                break
        else:
            # Mount / plain Starlette Route: only recurse if it carries APIRoutes (not static files)
            inner_routes = getattr(r, "routes", None)
            if inner_routes:
                yield from _walk(inner_routes, prefix + (getattr(r, "path", "") or ""), deps, depth + 1)


# Routes no dependency can guard: websockets check Origin by hand; Mounts other than static files are opaque apps.
WEBSOCKETS = {"/term/ws": "term.py: session + Origin (require_present) checked inline"}


def _opaque(routes, prefix: str = "", depth: int = 0) -> Iterator[str]:
    """Websocket routes and non-static Mounts under ``routes`` (full paths), through include wrappers."""
    assert depth < 20
    for r in routes:
        if isinstance(r, WebSocketRoute) or type(r).__name__ == "APIWebSocketRoute":
            yield prefix + r.path
        elif isinstance(r, Mount):
            if getattr(r, "routes", None):
                yield from _opaque(r.routes, prefix + r.path, depth + 1)
            elif not isinstance(r.app, StaticFiles):
                yield prefix + r.path
        elif getattr(r, "include_context", None) is not None:
            yield from _opaque(r.original_router.routes, prefix + (r.include_context.prefix or ""), depth + 1)


def _routers_walk() -> list[_Route]:
    return [m for m in _walk([*ROUTERS, auth.router]) if m.methods]


@pytest.fixture(scope="module")
def routes(tmp_path_factory) -> list[_Route]:
    t: Path = tmp_path_factory.mktemp("guards")
    s = Settings(tmux_socket="pmtest-unused", state_dir=t / "state", user_ssh_dir=t / "home-ssh",
                 token_file=t / "cfg/token", totp_file=t / "cfg/totp", disable_poll=True)
    found = [m for m in _walk(create_app(s).routes) if m.methods]
    fallback = _routers_walk()
    if not found:  # create_app's routes could not be enumerated: fall back to ROUTERS + auth
        found = fallback
    n_app = sum(len(m.keys()) for m in found)
    n_routers = sum(len(m.keys()) for m in fallback)
    assert n_routers > 0 and n_app >= n_routers, f"app walk found {n_app} mutating routes, ROUTERS {n_routers}"
    return found


def test_every_mutating_route_uses_mutation_guard(routes):
    bad = sorted(f"{sorted(r.methods)} {r.path} ({r.where})"
                 for r in routes if not r.guarded and not r.keys() <= ALLOWED)
    assert not bad, "mutating routes without mutation_guard:\n" + "\n".join(bad)


def test_exempt_routes_still_exist(routes):
    mutating = set().union(*(r.keys() for r in routes))
    gone = sorted(ALLOWED - mutating)
    assert not gone, f"drop these from the exemption sets: {gone}"


def test_guarded_exempt_routes_leave_the_sets(routes):
    """Once a route gains mutation_guard it must leave the exemption sets."""
    now_guarded = sorted(k for r in routes if r.guarded for k in r.keys() & ALLOWED)
    assert not now_guarded, f"now guarded, remove from the exemption sets: {now_guarded}"


def test_no_unguardable_routes(tmp_path):
    t: Path = tmp_path
    s = Settings(tmux_socket="pmtest-unused", state_dir=t / "state", user_ssh_dir=t / "home-ssh",
                 token_file=t / "cfg/token", totp_file=t / "cfg/totp", disable_poll=True)
    found = set(_opaque(create_app(s).routes))
    assert found == set(WEBSOCKETS), f"websocket / non-static mount routes changed: {sorted(found ^ set(WEBSOCKETS))}"


def test_walker_flags_raw_routes_and_opaque_mounts():
    from fastapi import APIRouter

    async def h(request):
        return None

    class App:
        async def __call__(self, scope, receive, send):
            pass

    r = APIRouter()
    r.add_route("/raw-post", h, methods=["POST"])
    r.add_route("/raw-any", App())
    r.add_route("/raw-get", h, methods=["GET"])
    r.routes.append(Mount("/opaque", app=App()))

    @r.websocket("/ws")
    async def ws(websocket):
        pass

    outer = APIRouter()
    outer.include_router(r, prefix="/p")
    bad = {k for m in _walk(outer.routes) if not m.guarded for k in m.keys()}
    assert ("POST", "/p/raw-post") in bad and ("DELETE", "/p/raw-any") in bad
    assert not any(path == "/p/raw-get" for _, path in bad)
    assert set(_opaque(outer.routes)) == {"/p/opaque", "/p/ws"}


def test_guard_identity_is_strict():
    def fake():
        return None

    for name in GUARD_QUALNAMES:
        fake.__qualname__ = name  # right name, wrong module
        assert not _is_guard(fake)
    fake.__module__, fake.__qualname__ = GUARD_MODULE, "mutation_guard.<locals>.other"
    assert not _is_guard(fake)
    assert _is_guard(mutation_guard("x", Tier.SAFE))
    assert _is_guard(csrf_session_guard("x", stepup_only=True, check_locked=False))


def test_plugin_mutating_routes_are_walked_and_guarded(tmp_path):
    """A plugin's routers are included like core ones: the walk sees them, and an unguarded one never loads."""
    from fastapi import APIRouter, Depends

    from pm.web import render, themes

    ok, bad = APIRouter(), APIRouter()

    @ok.post("/plugin-guarded")
    def guarded(_=Depends(mutation_guard("plugin_test", Tier.SAFE))):
        return {}

    @bad.post("/plugin-open")
    def unguarded():
        return {}

    class EP:
        def __init__(self, name, router):
            self.name, self.router = name, router

        def load(self):
            return type("P", (), {"name": self.name, "routers": lambda _self: [self.router]})

    t: Path = tmp_path
    s = Settings(tmux_socket="pmtest-unused", state_dir=t / "state", user_ssh_dir=t / "home-ssh",
                 token_file=t / "cfg/token", totp_file=t / "cfg/totp", disable_poll=True)
    loader, nav = render.env.loader, render.env.globals["NAV"]
    try:
        app = create_app(s, plugin_eps=[EP("okplug", ok), EP("badplug", bad)])
    finally:
        render.env.loader, render.env.globals["NAV"] = loader, nav
    found = {k: r for r in _walk(app.routes) if r.methods for k in r.keys()}
    assert found[("POST", "/plugin-guarded")].guarded
    assert ("POST", "/plugin-open") not in found
    assert themes.get("classic") is themes.CLASSIC
