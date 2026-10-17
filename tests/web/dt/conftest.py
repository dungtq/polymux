"""dt test fixtures: the pm app with the dt plugin passed explicitly, a stub tmux, a tmp state dir, a fake snapshot.

The real dt templates (``themes/dt``) belong to another part of the work; route tests render small stand-ins written
into ``tmp_path`` (``shell/_tabs.html``, ``shell/_sessions.html``) through a test copy of the dt theme.
"""

import dataclasses
import stat

import pytest
from fastapi.testclient import TestClient

from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, AgentState, Project, TmuxSession
from pm.infra import snapshot as services
from pm.security import auth
from pm.security.csrf import Csrf
from pm.web import render, themes
from pm.web.app import create_app
from pm.web.dt import DtTheme

ORIGIN = "https://pm.example.test"

TABS_TPL = (  # the real strip's shape (SHELL.md §4): div.x-tab hooks > a[role=tab] (+ the close button beside it)
    '<nav id="x-tabs" data-rev="{{ shell.rev }}">'
    '{% for t in [shell.home] + shell.tabs %}<div class="x-tab" role="presentation" data-tab-kind="{{ t.kind }}"'
    ' data-tab-id="{{ t.id }}" data-state="{{ t.state }}"><a role="tab" href="{{ t.href }}"'
    '{% if t.cur %} aria-current="page"{% endif %}>{{ t.title }}</a>'
    '{% if t.kind != "home" %}<button data-act="tabclose">x</button>{% endif %}</div>'
    '{% endfor %}</nav>'
)
SESS_TPL = (
    '{% for g in shell.groups %}<details class="x-sg" data-project="{{ g.project }}">'
    '{% for r in g.rows %}<a class="x-sr" data-sess-id="{{ r.id }}" data-state="{{ r.state }}"'
    '{% if r.open %} data-open{% endif %}{% if r.cur %} aria-current="page"{% endif %}>{{ r.label }}</a>'
    '{% endfor %}</details>{% endfor %}'
)


class EP:
    """Stand-in for an importlib.metadata entry point."""

    def __init__(self, name, target):
        self.name, self._target = name, target

    def load(self):
        return self._target


@pytest.fixture(autouse=True)
def _restore_globals(ui_globals):
    """create_app registers the dt theme and the plugin's template globals process-wide: undone after each test
    (``ui_globals``, tests/web/conftest.py)."""


@pytest.fixture
def settings(tmp_path):
    tmux = tmp_path / "bin" / "tmux"
    tmux.parent.mkdir()
    tmux.write_text("#!/bin/sh\nexit 0\n")
    tmux.chmod(tmux.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "dev").mkdir()
    return Settings(base_url=ORIGIN, dev_root=tmp_path / "dev", extra_projects=(), state_dir=tmp_path / "state",
                    token_file=tmp_path / "cfg/token", totp_file=tmp_path / "cfg/totp",
                    user_ssh_dir=tmp_path / "home-ssh", tmux_bin=str(tmux), tmux_socket="pmtest-dt",
                    disable_poll=True, theme="dt")


@pytest.fixture
def dt_templates(tmp_path):
    d = tmp_path / "dt-templates"
    (d / "shell").mkdir(parents=True)
    (d / "shell" / "_tabs.html").write_text(TABS_TPL)
    (d / "shell" / "_sessions.html").write_text(SESS_TPL)
    return d


@pytest.fixture
def plugin(dt_templates):
    class TestDtPlugin(DtTheme):
        def themes(self):
            return [themes.Theme(name="dt", templates=dt_templates, static=None, label="dt (test)")]

    return TestDtPlugin()


def agent(id_, project, state=AgentState.RUNNING, since=100.0, window="", sid="$1"):
    return Agent(id=id_, kind=AgentKind(id_.rsplit("-", 1)[0]), pane_id="%1", session_id=sid, pid=1, cwd="/x",
                 project=project, state=state, state_since=since, window_name=window)


# the project view's agents (test_project, and app_env below)
NOW = 1_000_000.0
RUN = agent("claude-aaaaa1", "alpha", AgentState.RUNNING, since=NOW - 60)
WAIT = agent("claude-bbbbb2", "alpha", AgentState.WAITING, since=NOW - 30)
FAIL = agent("codex-ccccc3", "beta", AgentState.FAILED, since=NOW - 30)
IDLE = agent("claude-ddddd4", "alpha", AgentState.IDLE, since=NOW - 9000)


def make_snap(agents=(), projects=("alpha", "beta"), sessions=(("$1", "work"), ("$4", "logs"))):
    by_proj = {p: tuple(a.id for a in agents if a.project == p) for p in projects}
    return services.Snapshot(
        ts=1.0, agents=tuple(agents),
        projects=tuple(Project(p, f"/dev/{p}", agent_ids=by_proj[p]) for p in projects),
        sessions=tuple(TmuxSession(sid, name) for sid, name in sessions))


@pytest.fixture
def snap(monkeypatch):
    """A mutable holder: ``snap.value`` is what ``snapshot.published`` returns (None = no build yet)."""
    holder = dataclasses.make_dataclass("Holder", [("value", object)])(make_snap())
    monkeypatch.setattr(services, "published", lambda settings: holder.value)
    return holder


class Env:
    def __init__(self, app, client, tok, settings):
        self.app, self.c, self.tok, self.s = app, client, tok, settings
        self.events: list = []

    def post(self, url, origin=ORIGIN, csrf=True, headers=None, **data):
        h = {"Origin": origin} if origin else {}
        h.update(headers or {})
        if csrf:
            data = {"csrf": self.tok, **data}
        return self.c.post(url, data=data, headers=h)


def make_env(settings, plugin, logged_in=True, more=()):
    app = create_app(settings, plugin_eps=[EP("dt", plugin), *more])
    c = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    tok = ""
    if logged_in:
        sess = app.state.sessions.create("1.2.3.4")
        c.cookies.set(auth.COOKIE_NAME, app.state.sessions.cookie_value(sess))
        tok = Csrf(settings.state_dir, settings.allowed_origins).token(sess.id)
    env = Env(app, c, tok, settings)

    class RecHub:
        def publish(self, type_, data):
            env.events.append((type_, dict(data)))

    app.state.hub = RecHub()
    return env


@pytest.fixture
def env(settings, plugin, snap):
    return make_env(settings, plugin)


@pytest.fixture
def anon(settings, plugin, snap):
    return make_env(settings, plugin, logged_in=False)


@pytest.fixture
def app_env(settings, snap, monkeypatch, tmp_path):
    """The app with the real dt theme over project alpha (README, RUN / WAIT / IDLE) and beta."""
    root = tmp_path / "dev" / "alpha"
    root.mkdir(parents=True)
    (root / "README.md").write_text("# Alpha\n\nHello.\n")
    agents = (RUN, WAIT, IDLE)
    snap.value = services.Snapshot(ts=1.0, agents=agents, projects=(
        Project("alpha", str(root), agent_ids=tuple(a.id for a in agents)), Project("beta", str(tmp_path / "b"))))

    async def fake_snapshot(*_a, **_k):
        return snap.value

    monkeypatch.setattr(services, "snapshot_async", fake_snapshot)
    return make_env(settings, DtTheme())
