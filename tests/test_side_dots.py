"""Live sidebar status dots: GET /side/dots (projects / agents) and the side.js wiring (#185)."""
import time
from pathlib import Path
from types import SimpleNamespace as NS

from helpers import login
from helpers import ntfy_env as env  # noqa: F401  (fixture)

from pm.core.models import Agent, AgentKind, AgentState, Project
from pm.infra import snapshot as services
from pm.web.agentsidebar import agent_dots
from pm.web.deps import project_dots
from pm.web.render import TEMPLATE_DIR

ROOT = Path(__file__).resolve().parents[1] / "src/pm"


def ag(i, state, pane, cwd="/x"):
    return Agent(id=i, kind=AgentKind.CLAUDE, pane_id=pane, session_id="$1", pid=1, cwd=cwd,
                 state=AgentState(state))


def snap_of():
    agents = (ag("claude-aaa111", "running", "%1"), ag("claude-bbb222", "waiting", "%2"),
              ag("claude-ccc333", "idle", "%3", cwd="/other"))
    win = NS(index=1, name="w", panes=(NS(id="%1"), NS(id="%2"), NS(id="%3")))
    projects = (Project(name="alpha", path="/x", agent_ids=("claude-aaa111", "claude-bbb222")),
                Project(name="beta", path="/y"))
    return services.Snapshot(ts=time.time(), sessions=(NS(id="$1", name="dev", windows=(win,)),),
                             agents=agents, projects=projects)


def test_project_and_agent_dots():
    snap = snap_of()
    assert project_dots(snap) == {"alpha": "waiting", "beta": ""}
    assert agent_dots(snap, snap.agents[0]) == {"claude-aaa111": "running", "claude-bbb222": "waiting"}


def test_side_dots_endpoint(env, monkeypatch):  # noqa: F811
    s, c, st, dev = env
    snap = snap_of()

    async def fake_snap(settings, max_age_s=0):
        return snap
    monkeypatch.setattr(services, "snapshot_async", fake_snap)
    assert c.get("/side/dots?kind=projects", follow_redirects=False).status_code == 303  # login required
    login(s, c)
    r = c.get("/side/dots?kind=projects")
    assert r.status_code == 200 and r.json() == {"alpha": "waiting", "beta": ""}
    assert r.headers["cache-control"] == "no-store"
    r = c.get("/side/dots?kind=agents&agent=claude-aaa111")
    assert r.json() == {"claude-aaa111": "running", "claude-bbb222": "waiting"}
    assert c.get("/side/dots?kind=x").status_code == 404
    assert c.get("/side/dots?kind=agents").status_code == 404
    assert c.get("/side/dots?kind=agents&agent=../etc").status_code == 404
    assert c.get("/side/dots?kind=agents&agent=claude-zzz999").status_code == 404
    assert c.post("/side/dots?kind=projects").status_code == 405


def test_side_js_dots_wiring():
    js = (ROOT / "static/js/side.js").read_text()
    assert "/side/dots?kind=" in js and "data-dot-kind" in js and "data-dot-agent" in js
    assert "'dot dot-' + dot" in js and "sr-only" in js and "'vs-ps' : 'vs-sp'" in js
    assert "dots();" in js and "if (bl || dl)" in js and "fetch(" not in js.replace("window.pm.fetch(", "")
    assert "innerHTML" not in js.split("function setDot")[1].split("function badges")[0]
    tpl = (TEMPLATE_DIR / "_side.html").read_text()
    assert tpl.count('data-dot-kind="projects"') == 2 and 'data-dot-kind="agents"' in tpl
    assert 'data-a="{{ n.name }}"' in tpl
