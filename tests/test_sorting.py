from helpers import login
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.agents import sorting
from pm.core.models import Agent, AgentKind, AgentState, GitStatus, Project, TodoProgress


def ag(i, state="idle", since=0.0, proj=None, cpu=0.0):
    return Agent(id=i, kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=1, cwd="/x",
                 project=proj, state=AgentState(state), state_since=since, cpu_pct=cpu)


def ids(xs):
    return [x.id for x in xs]


A = [ag("a-1", "idle", 5, "zed", 1), ag("a-2", "waiting", 1, "alpha", 9),
     ag("a-3", "running", 9, "alpha", 3), ag("a-4", "running", 9, "beta", 3)]


def test_agent_keys():
    assert ids(sorting.sort_agents(A, "newest")) == ["a-3", "a-4", "a-1", "a-2"]
    assert ids(sorting.sort_agents(A, "oldest")) == ["a-2", "a-1", "a-3", "a-4"]
    assert ids(sorting.sort_agents(A, "state")) == ["a-2", "a-3", "a-4", "a-1"]
    assert ids(sorting.sort_agents(A, "-state")) == ["a-1", "a-3", "a-4", "a-2"]
    assert ids(sorting.sort_agents(A, "name")) == ["a-1", "a-2", "a-3", "a-4"]
    assert ids(sorting.sort_agents(A, "project")) == ["a-3", "a-2", "a-4", "a-1"]
    assert ids(sorting.sort_agents(A, "cpu")) == ["a-2", "a-3", "a-4", "a-1"]


def test_agent_unknown_empty_deterministic():
    assert sorting.sort_agents(A, "bogus") == sorting.sort_agents(A, "newest")
    assert sorting.sort_agents([], "cpu") == []
    assert sorting.sort_agents(reversed(A), "newest") == sorting.sort_agents(A, "newest")


def pr(name, ts=None, dirty=0, agents=(), todo=None):
    g = GitStatus("main", dirty, last_commit_ts=ts) if ts is not None else None
    return Project(name=name, path="/p/" + name, git=g, agent_ids=tuple(agents),
                   todo=TodoProgress(*todo) if todo else None)


P = [pr("b", 100, 1, todo=(1, 4)), pr("a", 50, 5, ["a-3"], todo=(3, 4)),
     pr("nogit2"), pr("c", 10, 0, ["a-1", "a-2"]), pr("nogit1")]
BY = {a.id: a for a in A}


def names(xs):
    return [x.name for x in xs]


def test_project_keys():
    assert names(sorting.sort_projects(P, "newest", BY)) == ["b", "a", "c", "nogit1", "nogit2"]
    assert names(sorting.sort_projects(P, "oldest", BY)) == ["c", "a", "b", "nogit1", "nogit2"]
    assert names(sorting.sort_projects(P, "name")) == ["a", "b", "c", "nogit1", "nogit2"]
    assert names(sorting.sort_projects(P, "dirty"))[:2] == ["a", "b"]
    assert names(sorting.sort_projects(P, "agents"))[:2] == ["c", "a"]
    assert names(sorting.sort_projects(P, "todo"))[:2] == ["b", "a"]
    assert sorting.sort_projects(P, "zzz", BY) == sorting.sort_projects(P, "newest", BY)
    assert sorting.sort_projects([], "newest") == []


def test_activity_uses_agents():
    assert sorting.activity(pr("c", 10, agents=["a-3"]), BY) == 10
    assert sorting.activity(pr("c", 1, agents=["a-3"]), BY) == 9


def test_choose():
    assert sorting.choose("inbox", None, None) == ("newest", False)
    assert sorting.choose("inbox", "cpu", "state") == ("cpu", True)
    assert sorting.choose("inbox", None, "state") == ("state", False)
    assert sorting.choose("inbox", "evil", "state") == ("newest", False)
    assert sorting.choose("inbox", None, "dirty") == ("newest", False)  # projects-only key
    assert sorting.choose("projects", "name", None) == ("name", True)


def test_links_preserve_query_and_escape():
    ls = sorting.sort_cols("inbox", "cpu", "/", {"x": '"><b>', "sort": "old"})
    assert [c["active"] for c in ls.values()].count(True) == 1
    assert "x=%22%3E%3Cb%3E" in ls["age"]["href"] and "sort=newest" in ls["age"]["href"]
    assert ls["res"]["href"].endswith("sort=-cpu") and ls["res"]["arrow"] == "\u25bc"  # click reverses
    flipped = sorting.sort_cols("inbox", "-cpu", "/", {})
    assert flipped["res"]["href"].endswith("sort=cpu") and flipped["res"]["arrow"] == "\u25b2"


def test_route_default_cookie_param(env):  # noqa: F811
    s, c = env
    login(s, c)
    r = c.get("/")
    assert r.status_code == 200 and 'aria-current="true"' in r.text
    assert "set-cookie" not in {k.lower() for k in r.headers if k.lower() == "set-cookie"} or "pm_sort_inbox" not in r.headers.get("set-cookie", "")
    assert 'class="hcell n-age on" href="/?sort=oldest" title="Sort by Age" aria-current="true"' in r.text
    r = c.get("/?sort=cpu&q=1")
    sc = r.headers["set-cookie"]
    assert "pm_sort_inbox=cpu" in sc and "Secure" in sc and "SameSite=lax" in sc and "Max-Age=31536000" in sc
    assert 'href="/?q=1&amp;sort=newest"' in r.text and 'href="/?q=1&amp;sort=-cpu"' in r.text
    r = c.get("/")  # cookie remembered
    assert 'class="hcell n-res on" href="/?sort=-cpu"' in r.text
    r = c.get("/?sort=started")  # explicit wins
    assert 'class="hcell n-start on" href="/?sort=-started"' in r.text
    r = c.get("/?sort=<script>")
    assert r.status_code == 200 and "<script>" not in r.text
    assert 'class="hcell n-age on" href="/?sort=oldest"' in r.text


def test_projects_route(env):  # noqa: F811
    s, c = env
    login(s, c)
    r = c.get("/p")
    assert 'aria-current="true"' in r.text and "pm_sort_projects" not in r.headers.get("set-cookie", "")
    r = c.get("/p?sort=name")
    assert "pm_sort_projects=name" in r.headers["set-cookie"]
    assert 'class="hcell c-main on" href="/p?sort=-name"' in c.get("/p").text
    assert c.get("/p?sort=nope").status_code == 200
    for path in ("/", "/p"):
        h = c.get(path).text
        assert "onclick" not in h and "<style" not in h


# ---- grouping / last_active / ntfy ----
import re  # noqa: E402
from dataclasses import replace as _rep  # noqa: E402

from pm.notify import events  # noqa: E402
from pm.web.routes import pages as routes_pages  # noqa: E402


def la(i, state, active=0.0, since=0.0):
    return _rep(ag(i, state, since), last_active=active)


def test_group_sections_and_order():
    xs = [la("a-1", "idle", 5), la("a-2", "running", 3), la("a-3", "waiting", 1),
          la("a-4", "idle", 9), la("a-5", "failed", 7), la("a-6", "running", 8)]
    g = sorting.group_agents(xs, "newest")
    assert [s["key"] for s in g] == ["waiting", "running", "idle"]
    assert ids(g[1]["agents"]) == ["a-6", "a-2"]
    assert ids(g[2]["agents"]) == ["a-4", "a-5", "a-1"]  # failed folded into idle
    assert [s["key"] for s in sorting.group_agents([la("a-1", "idle", 1)], "cpu")] == ["idle"]


def test_unknown_activity_sinks_and_last_active_wins():
    xs = [la("a-1", "idle", 0, 0), la("a-2", "idle", 50, 999), la("a-3", "idle", 0, 70)]
    assert ids(sorting.sort_agents(xs, "newest")) == ["a-3", "a-2", "a-1"]  # 70 (since) > 50
    assert ids(sorting.sort_agents(xs, "oldest")) == ["a-2", "a-3", "a-1"]  # unknown last


def test_restart_does_not_reorder_idle_agents():
    """Root cause: tracker used `state_since or now` for unseen agents, so a pm restart
    made every idle non-Claude agent (since=0) look just-changed and sort first."""
    clock = [1000.0]
    polled = [la("a-1", "idle", 100), la("a-2", "idle", 0), la("a-3", "idle", 300)]
    t1 = events.AgentTracker(clock=lambda: clock[0])
    t1.update(polled)
    before = ids(sorting.sort_agents(t1.agents, "newest"))
    clock[0] = 9999.0  # restart much later
    t2 = events.AgentTracker(clock=lambda: clock[0])
    t2.update(polled)
    assert ids(sorting.sort_agents(t2.agents, "newest")) == before == ["a-3", "a-1", "a-2"]
    assert all(a.state_since == 0 for a in t2.agents)
    t2.update([la("a-1", "idle", 0), la("a-2", "idle", 0), la("a-3", "idle", 0)])
    assert {a.id: a.last_active for a in t2.agents} == {"a-1": 100, "a-2": 0, "a-3": 300}


def test_ntfy_sort_key():
    ps = [Project(name="a", path="/a"), Project(name="b", path="/b"), Project(name="c", path="/c")]
    got = sorting.sort_projects(ps, "ntfy", None, {"/b": 5, "/c": 2})
    assert [p.name for p in got] == ["b", "c", "a"]
    assert sorting.valid_key("projects", "ntfy")


def test_inbox_groups_and_placeholder(env):  # noqa: F811
    s, c = env
    login(s, c)
    h = c.get("/").text
    assert "nrow row-running" in h and "Idle (" not in h and "Waiting for you" not in h
    assert "n-age" in h and "n-start" in h and "n-state" not in h
    assert 'id="ntfy-inbox"' not in h
    assert not re.search(r"<script(?![^>]*\bsrc=)", h) and "<style" not in h


def test_projects_ntfy_column(env, monkeypatch):  # noqa: F811
    import sys
    import types

    s, c = env
    login(s, c)
    sys.modules.pop("pm.notify.feed", None)
    monkeypatch.setitem(sys.modules, "pm.notify.feed", None)  # import fails
    r = c.get("/p?sort=ntfy")
    assert r.status_code == 200 and "&ndash;" in r.text
    path = next(p.path for p in routes_pages.services.build_snapshot(s, force=True).projects)
    fake = types.SimpleNamespace(counts_by_project=lambda settings: {path: 7})
    monkeypatch.setitem(sys.modules, "pm.notify.feed", fake)
    monkeypatch.setattr("pm.notify.feed", fake, raising=False)
    r = c.get("/p?sort=ntfy")
    assert '<span class="c-ntfy muted">7</span>' in r.text and "<script>" not in r.text
    boom = types.SimpleNamespace(counts_by_project=lambda settings: 1 / 0)
    monkeypatch.setitem(sys.modules, "pm.notify.feed", boom)
    monkeypatch.setattr("pm.notify.feed", boom, raising=False)
    assert c.get("/p").status_code == 200


def test_reverse_keys_and_sessions():
    from types import SimpleNamespace as NS
    ps = [NS(name=n, git=None, agent_ids=tuple(range(i)), todo=None, path="/" + n) for i, n in enumerate("abc")]
    names = lambda k: [p.name for p in sorting.sort_projects(ps, k)]  # noqa: E731
    assert names("name") == ["a", "b", "c"] and names("-name") == ["c", "b", "a"]
    assert names("agents") == ["c", "b", "a"] and names("-agents") == ["a", "b", "c"]
    ss = [NS(id="$1", name="b"), NS(id="$2", name="a")]
    info = {"$1": {"windows": 2, "panes": 3, "agents": [1], "last": 5.0}, "$2": {"windows": 1, "panes": 1, "agents": [], "last": None}}
    ids = lambda k: [s.id for s in sorting.sort_sessions(ss, k, info)]  # noqa: E731
    assert ids("name") == ["$2", "$1"] and ids("-name") == ["$1", "$2"]
    assert ids("counts") == ["$1", "$2"] and ids("newest") == ["$1", "$2"] and ids("oldest") == ["$1", "$2"]
    assert sorting.choose("tmux", None, None) == ("name", False)


def test_ram_key_sorts_by_rss_and_validates(env):  # noqa: F811
    xs = [_rep(ag("a-1"), rss_mb=50.0), _rep(ag("a-2"), rss_mb=900.0), _rep(ag("a-3"), rss_mb=300.0)]
    assert ids(sorting.sort_agents(xs, "ram")) == ["a-2", "a-3", "a-1"]
    assert ids(sorting.sort_agents(xs, "-ram")) == ["a-1", "a-3", "a-2"]
    assert sorting.choose("inbox", "ram", None) == ("ram", True)
    assert sorting.choose("inbox", None, "-ram") == ("-ram", False)  # cookie
    assert not sorting.valid_key("projects", "ram")
    cols = sorting.sort_cols("inbox", "ram", "/", {})
    assert cols["ram"]["active"] and cols["ram"]["href"].endswith("sort=-ram") and not cols["res"]["active"]
    s, c = env
    login(s, c)
    r = c.get("/?sort=ram")
    assert r.status_code == 200 and "pm_sort_inbox=ram" in r.headers["set-cookie"]
    assert "sort=-ram" not in r.text  # classic has no RAM column: no header for it


# ---- segment counts, issues key, tmux segments ----
def test_filter_counts_follow_the_query():
    ps = [pr("alpha", 1, dirty=2, agents=("a",), todo=(1, 3)), pr("beta", 1), pr("alpine", 1, dirty=1)]
    assert sorting.filter_counts(ps, "", ["beta"]) == {"": 3, "dirty": 2, "agents": 1, "todo": 1, "pinned": 1}
    assert sorting.filter_counts(ps, "AL", ["beta"]) == {"": 2, "dirty": 2, "agents": 1, "todo": 1, "pinned": 0}
    assert sorting.filter_counts([], "") == {"": 0, "dirty": 0, "agents": 0, "todo": 0, "pinned": 0}


def test_issues_sort_key():
    ps = [pr("a"), pr("b"), pr("c")]
    counts = {"b": 5, "c": 1}
    assert [p.name for p in sorting.sort_projects(ps, "issues", issues=counts)] == ["b", "c", "a"]
    assert [p.name for p in sorting.sort_projects(ps, "-issues", issues=counts)] == ["a", "c", "b"]
    assert [p.name for p in sorting.sort_projects(ps, "issues")] == ["a", "b", "c"]  # no counts: by name
    assert sorting.choose("projects", "issues", None) == ("issues", True)
    assert sorting.sort_cols("projects", "issues", "/p", {})["issues"]["active"]


def test_tmux_segments_and_counts():
    from types import SimpleNamespace as NS
    ss = [NS(id="$1", name="a", attached=1, protected=True), NS(id="$2", name="b", attached=0, protected=False),
          NS(id="$3", name="c", attached=2, protected=False)]
    info = {"$2": [ag("a-1")], "$1": []}
    ids_ = lambda f: [s.id for s in sorting.filter_tmux(ss, f, info)]  # noqa: E731
    assert ids_("agents") == ["$2"] and ids_("attached") == ["$1", "$3"] and ids_("protected") == ["$1"]
    assert ids_("") == ids_("bogus") == ["$1", "$2", "$3"]
    assert sorting.tmux_counts(ss, info) == {"": 3, "agents": 1, "attached": 2, "protected": 1}
    assert [f for f, _ in sorting.TMUX_FILTERS] == ["agents", "attached", "protected"]
