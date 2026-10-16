"""Project page: Stop agents control, live Agents/tmux fragment."""

from dataclasses import replace

from helpers import login
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.infra import snapshot as services


def test_stop_control_in_header_right_and_panel(env):
    s, c = env
    login(s, c)
    h = c.get("/p/alpha").text
    row = h.split('<span class="head-actions">')[1].split("</span>")[0]
    assert "Stop agents" in row and 'href="#stop-panel"' in row and "launch-btn" in row and "/p/alpha/launch" in row
    assert 'class="actions"' not in h.split('id="stop-panel"')[0] and "Ensure layout" not in h and "tmux/ensure" not in h
    # header order: title, path, tab row; git buttons are not in the page head any more
    assert h.index("pj-pathrow") < h.index('id="pj-tabbar"') < h.index('id="pj-body"')
    assert h.index('class="pj-head"') < h.index('id="pj-tabbar"')
    assert 'id="pj-tabbar"' in h.split('id="pj-right"')[0]
    assert "/git/fetch" not in h and "/git/pull" not in h and "/git/push" not in h
    for tab in ("Browse", "Git", "Skills", "README", "CHANGELOG"):
        assert f">{tab}" in h.split('id="pj-tabbar"')[1].split("pj-close")[0]
    assert 'id="stop-panel"' in h
    assert "sends Esc then Ctrl-C to each" in h and "does not kill sessions" in h
    assert "(1 agent)" in h and "claude-12" in h
    assert 'name="scope" value="project:alpha"' in h and 'pattern="PANIC"' in h
    assert "Stop 1 agent<" in h
    assert 'class="panic"' not in h and "Panic stop" not in h


def test_stop_control_inbox_and_launch(env):
    s, c = env
    login(s, c)
    h = c.get("/").text
    assert "Stop ALL agents" in h and 'name="scope" value="all"' in h
    assert h.index("Stop ALL agents") < h.index('id="inbox"')
    h = c.get("/p/alpha/launch").text
    assert "Stop agents" in h and 'value="project:alpha"' in h
    assert h.index('id="stop-panel"') > h.index("Launch</button>")
    assert h.index("Stop agents</a>") > h.index("Open TODO items")  # away from Launch
    assert h.count("data-submit-lock") >= 1


def test_panic_server_check_unchanged(env):
    s, c = env
    login(s, c)
    r = c.post("/panic", data={"csrf": c.get("/p/alpha").text.split('name="csrf" value="')[1].split('"')[0],
                               "scope": "project:alpha", "confirm": "nope"},
               headers={"Origin": "https://testserver"})
    assert r.status_code == 403


def test_project_live_fragment(env, monkeypatch):
    s, c = env
    login(s, c)
    page = c.get("/p/alpha").text
    assert 'id="proj-live"' in page and "data-live" in page and 'hx-get="/p/alpha/live"' in page
    assert "pm:agent_state from:document" in page and "data-nodiff" in page and 'hx-sync="this:replace"' in page
    r = c.get("/p/alpha/live", headers={"HX-Request": "true"})
    assert r.status_code == 200 and "dot-running" in r.text and 'data-ptab="tmux"' in r.text
    assert "<html" not in r.text
    r2 = c.get("/p/alpha/live", headers={"If-None-Match": r.headers["etag"]})
    assert r2.status_code == 204  # unchanged: nothing swapped
    assert c.get("/p/nope/live").status_code == 404


def test_model_short_and_tmux_slot():
    from pm.agents.head import model_short, tmux_slot
    assert model_short("claude-sonnet-5-5-20261001") == "sonnet-5-5"
    assert model_short("claude-opus-4.5") == "opus-4-5" and model_short("fable-5-1") == "fable-5-1"
    assert model_short("claude-opus-5-5[1m]") == "opus-5-5" and model_short("") == ""
    assert tmux_slot("dev-pm-1-claude", "claude") == "pm-1" and tmux_slot("work-2-claude", "claude") == "work-2"


def test_project_live_agents_table_and_tmux_first(env):
    s, c = env
    login(s, c)
    t = c.get("/p/alpha/live", headers={"HX-Request": "true"}).text
    assert t.index('data-ptab="tmux"') < t.index("<h2>Agents</h2>")
    assert '<span class="c-proj">tmux</span>' in t and ">Project<" not in t
    assert 'href="/a/claude-12"' in t and 'data-pane="' in t and 'class="wslot"' in t
    assert 'class="kpill kp-model"' not in t  # model unknown in the fixture: no empty pill
    page = c.get("/p/alpha").text
    assert 'id="ntfy-project"' not in page and page.index('data-ptab="tmux"') < page.index("<h2>Agents</h2>")


def test_ended_grid_rows_match_agent_columns():
    from pm.agents.registry import Record
    from pm.web.render import env as _env
    r = Record("claude-236c41", "claude", "u", 0, 1.0, 2.0, "%1", "pm", "/x", "dev-pm-1-claude", "", 3.0)
    t = _env.from_string('{% from "_agentrow.html" import ended_list, PROJECT_COLS %}{{ ended_list(rows, 1, "", {}, PROJECT_COLS, m) }}')
    h = t.render(rows=[r], m={r.id: "sonnet-5-5"}, now=10.0)
    assert 'class="lrow arow row-ended" href="/a/claude-236c41"' in h and "ico-dir" not in h and "n-proj" not in h
    assert ">sonnet-5-5</span>" in h and 'class="wslot">pm-1<' in h and '<span class="c-proj">tmux</span>' in h
    assert "claude-236c41</strong>" in h


def test_win_prog_prefers_agent_kind_then_active_pane_command():
    from types import SimpleNamespace as NS

    from pm.agents.head import win_prog
    from pm.core.models import AgentKind, TmuxPane, TmuxWindow
    p1 = TmuxPane("%1", "@1", "$1", 1, "fish", "/x", active=False)
    p2 = TmuxPane("%2", "@1", "$1", 2, "nv\x1bim" + "x" * 50, "/x", active=True)
    w = TmuxWindow("@1", "$1", 1, "edit", True, (p1, p2))
    assert win_prog(w, []) == ("nvim" + "x" * 50)[:32]
    assert win_prog(TmuxWindow("@1", "$1", 1, "e", True, (p1,)), []) == "fish"
    assert win_prog(w, [NS(pane_id="%1", kind=AgentKind.CLAUDE)]) == "claude"
    assert win_prog(TmuxWindow("@2", "$1", 2, "e"), []) == ""


def test_project_live_tmux_row_lists_every_window_with_program(env):
    s, c = env
    login(s, c)
    t = c.get("/p/alpha/live", headers={"HX-Request": "true"}).text
    row = t.split('<div class="lrow srow"')[1].split("</div>")[0]
    assert 'class="c-wins muted"' in row and 'class="swin"' in row and 'class="widx"' in row
    assert 'class="wprog"' not in row and "&gt;_" not in row and "attached)</span>" in row and "…" not in row


def test_project_live_ctx_lists_windows_outside_the_project():
    from types import SimpleNamespace as NS

    from pm.core.models import TmuxPane, TmuxSession, TmuxWindow
    from pm.web.routes.pages import _project_live_ctx
    mine = TmuxPane("%1", "@1", "$1", 1, "claude", "/x", active=True)
    other = TmuxPane("%2", "@2", "$1", 2, "nvim", "/y", active=True)
    s1 = TmuxSession("$1", "work", windows=(TmuxWindow("@1", "$1", 1, "a", True, (mine,)),
                                            TmuxWindow("@2", "$1", 2, "b", False, (other,))))
    s2 = TmuxSession("$2", "elsewhere", windows=(TmuxWindow("@3", "$2", 1, "c", True, (other,)),))
    snap = NS(agents=[], session=lambda i: {"$1": s1, "$2": s2}.get(i))
    proj = NS(agent_ids=set(), pane_ids={"%1"}, sessions=["$1", "$2"])
    _, sessions = _project_live_ctx(snap, proj)
    assert [s.id for s in sessions] == ["$1"]  # a session with no pane of the project stays hidden
    assert [w.id for w in sessions[0].windows] == ["@1", "@2"]  # but its other windows are listed


def test_project_live_requires_login(env):
    _, c = env
    assert c.get("/p/alpha/live", follow_redirects=False).status_code == 303


def test_project_live_reflects_state_change(env, monkeypatch):
    s, c = env
    login(s, c)
    first = c.get("/p/alpha/live").text
    assert "dot-running" in first
    real = services.agents_mod.detect_agents

    def waiting(panes, **kw):
        return [replace(a, state=services.AgentState.WAITING) for a in real(panes, **kw)]

    monkeypatch.setattr(services.agents_mod, "detect_agents", waiting)
    services.build_and_publish(s)  # what the poll loop does on its next tick
    t = c.get("/p/alpha/live").text
    assert "dot-waiting" in t and "dot-running" not in t




def test_git_buttons_live_in_git_tab(env):
    s, c = env
    login(s, c)
    h = c.get("/p/alpha/git/panel").text
    for u, label in (("fetch", "Git fetch"), ("pull", "Git pull"), ("push", "Git push")):
        assert f'hx-post="/p/alpha/git/{u}"' in h and label in h
    assert 'data-confirm="Push to origin?"' in h and 'name="csrf"' in h  # post_btn: in-page confirm (dialog.js)
    c.app.state.settings = replace(s, push_remote="gitdev")  # the confirm names pm.toml push_remote
    assert 'data-confirm="Push to gitdev?"' in c.get("/p/alpha/git/panel").text


def test_restart_stack_only_in_stack_block_when_running(env, monkeypatch):
    from pm.core.models import StackStatus
    s, c = env
    login(s, c)
    for st, shown in ((StackStatus(2, 2, ()), True), (StackStatus(0, 2, ()), False), (None, False)):
        monkeypatch.setattr(services.stacks, "stack_status", lambda path, st=st: st)
        services._state(s).slow.clear()
        services.build_and_publish(s)
        h = c.get("/p/alpha").text
        assert ("/stack/restart" in h) is shown
        if shown:
            assert h.index('data-ptab-panel="stack"') < h.index("/stack/restart") < h.index('id="plans"')
