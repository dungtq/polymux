"""Projects list: search, filters, pins; project page commit history."""

import re

from helpers import login
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.agents import sorting
from pm.core.models import GitStatus, Project, TodoProgress
from pm.git import ctl as gitctl
from pm.projects import projects


def _csrf(c):
    return re.search(r'name="csrf" value="([^"]+)"', c.get("/p").text).group(1)


def test_filter_projects():
    a = Project("alpha", "/a", git=GitStatus("main", dirty=2), agent_ids=("x",), todo=TodoProgress(1, 2))
    b = Project("beta", "/b", git=GitStatus("main"), todo=TodoProgress(2, 2))
    ps = [a, b]
    f = sorting.filter_projects
    assert f(ps, "ALP") == [a]
    assert f(ps, flags=["dirty"]) == [a] and f(ps, flags=["agents"]) == [a]
    assert f(ps, flags=["todo"]) == [a]  # beta has no open items
    assert f(ps, flags=["pinned"], pins={"beta"}) == [b]
    assert f(ps, "b", ["dirty"]) == []
    assert sorting.pinned_first(ps, {"beta"}) == [b, a]


def test_pins_roundtrip(env):
    s, _ = env
    assert projects.load_pins(s) == set()
    projects.set_pin(s, "alpha", True)
    assert projects.load_pins(s) == {"alpha"}
    assert (projects._pins_file(s).stat().st_mode & 0o777) == 0o600
    projects.set_pin(s, "alpha", False)
    assert projects.load_pins(s) == set()
    projects._pins_file(s).write_text('{"not": "a list"}')
    assert projects.load_pins(s) == set()
    projects._pins_file(s).write_text('["beta", 3]')
    assert projects.load_pins(s) == {"beta"}


def test_projects_page_search_filters(env):
    s, c = env
    login(s, c)
    def main(u):  # the global sidebar always lists every project; only <main> is filtered
        return c.get(u).text.split("<main", 1)[1]

    h = main("/p")
    assert 'type="search"' in h and "Has agents" in h and "Has TODO" in h and "Dirty" in h
    assert 'href="/p/alpha"' in h
    assert 'href="/p/alpha"' not in main("/p?q=zzz")
    assert 'href="/p/alpha"' in main("/p?q=alp&f=agents,todo")
    assert 'href="/p/alpha"' not in main("/p?f=dirty")
    assert 'href="/p/alpha"' in main("/p?f=bogus")


def test_pin_requires_csrf_and_audits(env):
    s, c = env
    object.__setattr__(s, "tier_ceiling", 1)
    login(s, c)
    tok = _csrf(c)
    h = {"Origin": s.allowed_origins[0]}
    assert c.post("/p/alpha/pin", data={"pinned": "1"}, headers=h).status_code == 403
    r = c.post("/p/nope/pin", data={"csrf": tok, "pinned": "1"}, headers=h); assert r.status_code == 404, r.text
    r = c.post("/p/alpha/pin", data={"csrf": tok, "pinned": "1"}, headers=h, follow_redirects=False)
    assert r.status_code == 303 and projects.load_pins(s) == {"alpha"}
    assert "&#9733;" in c.get("/p").text
    r = c.post("/p/alpha/pin", data={"csrf": tok, "pinned": "0"}, headers={**h, "HX-Request": "true"})
    assert r.headers["HX-Refresh"] == "true" and projects.load_pins(s) == set()
    assert "project_pin" in s.audit_file.read_text()


def test_dirty_fragment(env, monkeypatch):
    s, c = env
    login(s, c)
    monkeypatch.setattr(gitctl, "dirty_files", lambda p, limit=60: ([("M", "a<b>.py")], 1))
    r = c.get("/p/alpha/dirty")
    assert "a&lt;b&gt;.py" in r.text and "<html" not in r.text
    assert c.get("/p/nope/dirty").status_code == 404


def test_project_page_commits(env, monkeypatch):
    s, c = env
    login(s, c)
    commits = [{"sha": "a" * 40, "hash": "aaaaaaa", "author": "dt", "ts": 1.0, "subject": "Fix <x>"}]
    monkeypatch.setattr(gitctl, "commit_log", lambda p, n=5: commits)
    monkeypatch.setattr(gitctl, "commit_url_base", lambda p, h=(): "https://git.example.test/dt/alpha/commit/")
    h = c.get("/p/alpha/git/panel").text
    assert f'href="https://git.example.test/dt/alpha/commit/{"a" * 40}"' in h and "Fix &lt;x&gt;" in h
    monkeypatch.setattr(gitctl, "commit_url_base", lambda p, h=(): None)
    h = c.get("/p/alpha/git/panel").text
    assert "aaaaaaa" in h and "git.example.test/dt/alpha/commit" not in h


def test_project_page_tab_script_and_old_stack_block_gone(env):
    s, c = env
    login(s, c)
    h = c.get("/p/alpha").text
    assert "/static/js/projtabs.js" in h and "<h2>Stack</h2>" not in h
    assert "#pj-tabbar .pj-tab" in c.get("/static/js/projview.js").text  # doc tabs ignore the tmux tabs
    # #163: both tab bars use the shared .tabs/.tab (tabs.css) and pm.tabs (tabs.js, loaded by base.html)
    assert '<div class="tabs pj-tabs tabs-line" role="tablist" aria-label="Project documents">' in h
    assert h.count('<a class="tab pj-tab') >= 5 and 'class="pj-tab"' not in h
    assert '<div class="tabs tabs-line" role="tablist" aria-label="tmux, stack and VMs" data-ptabs data-tabs-key="pm.ptab">' in h
    assert '<button type="button" class="tab" role="tab" id="ptab-tmux"' in h
    assert "/static/css/tabs.css" in h and "/static/js/tabs.js" in h
