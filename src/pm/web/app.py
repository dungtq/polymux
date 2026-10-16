from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pm.agents import autoexit, chatfeed, deliver, pastsessions
from pm.core import db as pmdb
from pm.core import executors, timefmt
from pm.core.config import Settings, load_settings
from pm.core.models import Agent, AgentState
from pm.git import ctl as gitctl
from pm.infra import snapshot as services
from pm.infra import unitctl
from pm.notify import agentlive, alerts, events, reloadneed, watchdog
from pm.notify import feed as ntfy_feed
from pm.security import auth
from pm.security.mask import configure as configure_mask
from pm.security.unlock import Unlocker
from pm.web import chatctx, plugins, themes
from pm.web.assets import NoCacheStatic
from pm.web.render import STATIC_DIR
from pm.web.render import env as pages_router_env
from pm.web.routes import ROUTERS

_IDLE_STATES = (AgentState.IDLE, AgentState.WAITING)


def _agent_access(app: FastAPI, settings: Settings):
    """``(get_agent, is_idle, publish)`` for the chat loops (deliver, chatfeed): the agent and its state
    as the event tracker sees them (poll + hook overlay), else the last published snapshot."""

    def get_agent(agent_id: str) -> Agent | None:
        a = next((x for x in app.state.tracker.agents if x.id == agent_id), None)
        if a is None:
            snap = services.published(settings)
            a = snap.agent(agent_id) if snap is not None else None
        return a

    def is_idle(agent: Agent) -> bool:
        cur = get_agent(agent.id) or agent
        return cur.state in _IDLE_STATES

    def publish(type_: str, data: dict) -> None:
        app.state.hub.publish(type_, data)

    return get_agent, is_idle, publish


def _chat_access(settings: Settings, get_agent, is_idle):
    """``(get_agent, is_idle)`` for chatfeed only: a live agent, else the stand-in of an ended one
    (``chatctx.ended_standin``), so an open ended page still gets its items; an ended agent is idle
    (its open turn gets its end item). The deliverer keeps the live-only pair: it never types into
    an ended agent."""

    def chat_agent(agent_id: str) -> Agent | None:
        a = get_agent(agent_id)
        if a is None:
            snap = services.published(settings)
            a = chatctx.ended_standin(settings, snap, agent_id) if snap is not None else None
        return a

    def chat_idle(agent: Agent) -> bool:
        return get_agent(agent.id) is None or is_idle(agent)

    return chat_agent, chat_idle


def _ntfy_on_new(app: FastAPI, settings: Settings):
    """The feed's ``on_new`` (``notify.feed.start``): new ntfy items -> one ``inbox`` payload per live agent that sent
    them (``agentlive.for_items``). Injected here so the feed never imports agentlive (which reads the feed)."""

    async def on_new(items) -> list[dict]:
        agents = {a.id: a for a in app.state.tracker.agents}  # read on the loop, used on the collectors pool
        return await asyncio.wait_for(executors.run_collectors(agentlive.for_items, settings, items, agents.get),
                                      agentlive.TIMEOUT_S)

    return on_new


def _head_subject(root) -> str | None:
    """``reloadneed``'s ``head_fn``: the subject of the checkout's last commit (notify never imports git)."""
    last = gitctl.commit_log(root, 1)
    return last[0]["subject"] if last else None


def _restart_globals(settings: Settings) -> dict:
    """Jinja globals of the "New code: restart pm" bar (both themes): ``reload_ok`` (POST /service/reload would run),
    ``reload_unit`` (the by-hand restart command otherwise), ``pm_code`` (the watcher's verdict, no IO)."""
    return {"reload_ok": unitctl.reload_allowed(settings), "reload_unit": settings.reload_unit or "pm.service",
            "pm_code": reloadneed.code}


def create_app(settings: Settings | None = None, *, plugin_eps=None) -> FastAPI:
    """The pm app. ``plugin_eps``: entry points to load instead of the installed ``pm.plugins`` (tests)."""
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # tests set disable_poll so they never poll a real tmux server
        loop = asyncio.get_running_loop()
        # anything still using the loop's default executor / to_thread is bounded; blocking work
        # has its own pools (pm.core.executors)
        loop.set_default_executor(ThreadPoolExecutor(8, thread_name_prefix="pm-default"))
        wd = None
        if not settings.disable_poll or settings.watchdog:
            wd = watchdog.Watchdog(settings.watchdog_stall_log_s, settings.watchdog_exit_s)
            wd.start()
        app.state.watchdog = wd
        if not settings.disable_poll:
            from pm.agents.inventory import read as agentsinv

            loop.run_in_executor(None, lambda: agentsinv.get_inventory(background=True))  # warm
            events.start(app)
            if settings.alerts_enabled:
                alerts.start(app)
            autoexit.start(app, services.published)
            pastsessions.start(app, services.published)
            ntfy_feed.start(app, on_new=_ntfy_on_new(app, settings))
            pmdb.start_backup(app)
            reloadneed.start(app, app.state.hub.publish, head_fn=_head_subject)
            get_agent, is_idle, publish = _agent_access(app, settings)
            deliver.start(app, is_idle=is_idle, get_agent=get_agent, publish=publish)
            chat_agent, chat_idle = _chat_access(settings, get_agent, is_idle)
            chatfeed.start(app, is_idle=chat_idle, get_agent=chat_agent, publish=publish)
            await plugins.start_loops(app)
        try:
            yield
        finally:
            await plugins.stop_loops(app)
            await deliver.stop(app)
            await chatfeed.stop(app)
            await events.stop(app)
            await alerts.stop(app)
            await autoexit.stop(app)
            await pastsessions.stop(app)
            await ntfy_feed.stop(app)
            await pmdb.stop_backup(app)
            await reloadneed.stop(app)
            if wd is not None:
                await wd.stop()
            app.state.watchdog = None

    app = FastAPI(title="pm", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(themes.UIThemeMiddleware, default=settings.theme)  # ?ui= / cookie pm_ui: the look per request
    pages_router_env.globals["dev_banner"] = bool(settings.dev_banner)
    pages_router_env.globals.update(_restart_globals(settings))
    timefmt.configure(settings)  # GUI timestamps use settings.display_utc_offset_h (UTC+7)
    # /events needs these even when polling is disabled (tests, private instances)
    app.state.hub = events.Hub()
    app.state.tracker = events.AgentTracker()

    auth.install(app, settings)  # token file, session store, /login, /logout
    configure_mask(literals=[settings.token_file.read_text().strip()])
    app.state.unlocker = Unlocker(settings.totp_file, settings.unlock_ttl_s)

    for router in ROUTERS:
        app.include_router(router)
    plugins.install(app, settings, plugin_eps)  # routers, theme, nav, /static/t + /static/p before /static
    app.mount("/static", NoCacheStatic(directory=STATIC_DIR), name="static")

    @app.get("/healthz")
    async def healthz() -> dict:
        wd = getattr(app.state, "watchdog", None)
        age = services.snapshot_age_s(settings)
        return {
            "ok": True,
            "loop_lag_ms": wd.loop_lag_ms if wd is not None else None,
            "snapshot_age_s": None if age is None else round(age, 1),
        }

    return app
