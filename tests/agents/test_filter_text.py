"""List filter text: sorting.q_text / agent_q / project_q / session_q / filter_agents(model_of) / filter_projects /
filter_sessions and registry.ended_sessions(q=...)."""
import uuid
from types import SimpleNamespace as NS

from pm.agents import registry, sorting
from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, AgentState, GitStatus, Project, StackStatus, TmuxSession, TmuxWindow
from pm.web.render import agent_q as render_agent_q
from pm.web.render import env as jenv

REAL_REGISTRY = True


def _agent(n=1, **kw):
    base = dict(id=f"claude-{n}", kind=AgentKind.CLAUDE, pane_id=f"%{n}", session_id="$1", pid=n, cwd="/work/Dir",
                state=AgentState.RUNNING, label=f"Dev-PM-{n}-Claude", window_name="A1-Hub", project="PmProj")
    base.update(kw)
    return Agent(**base)


# ---- q_text
def test_q_text_joins_lowercases_and_drops_empty():
    assert sorting.q_text("Dev-PM", None, "", "A1 Hub", "X") == "dev-pm a1 hub x"
    assert sorting.q_text() == "" and sorting.q_text(None, "") == ""


# ---- agent_q
def test_agent_q_has_label_window_project_id_kind_and_model_lowercase():
    q = sorting.agent_q(_agent(7), "Opus 4.7")
    assert q == q.lower()
    for want in ("dev-pm-7-claude", "a1-hub", "pmproj", "claude-7", "claude", "opus 4.7"):
        assert want in q
    assert "/work/dir" not in q  # project present: cwd is only the fallback


def test_agent_q_falls_back_to_cwd_without_project_and_has_no_model_by_default():
    q = sorting.agent_q(_agent(project=None))
    assert "/work/dir" in q and "opus" not in q
    assert "/work/dir" in sorting.agent_q(_agent(project=""))


def test_agent_q_accepts_a_record_with_string_kind_and_no_window_name():
    r = NS(id="devin-abc123", kind="devin", label="dev-x-2-devin", project="x", cwd="/c")
    q = sorting.agent_q(r, "sonnet 5")
    assert q == "dev-x-2-devin x devin-abc123 devin sonnet 5"


def test_render_filter_matches_sorting_and_reads_model_from_transcript(tmp_path):
    a = _agent()
    assert render_agent_q(a, "m1") == sorting.agent_q(a, "m1")
    assert render_agent_q(a, "") == sorting.agent_q(a, "")  # explicit empty model is not "unknown"
    assert render_agent_q(a) == sorting.agent_q(a, "")  # no transcript: no model, no crash


# ---- filter_agents
def test_filter_agents_is_case_insensitive_and_trims():
    ags = [_agent(1), _agent(2, label="Other-2-Claude", window_name="", project="zz")]
    assert [a.id for a in sorting.filter_agents(ags, "  A1-HUB ")] == ["claude-1"]
    assert [a.id for a in sorting.filter_agents(ags, "other-2")] == ["claude-2"]
    assert [a.id for a in sorting.filter_agents(ags, "ZZ")] == ["claude-2"]
    assert [a.id for a in sorting.filter_agents(ags, "")] == ["claude-1", "claude-2"]
    assert sorting.filter_agents(ags, None) == ags and sorting.filter_agents(ags, "nope") == []


def test_filter_agents_model_of_adds_the_model_to_the_haystack():
    ags = [_agent(1), _agent(2)]
    models = {"claude-1": "opus 4.7", "claude-2": "sonnet 5"}
    assert sorting.filter_agents(ags, "opus") == []  # model unknown without model_of
    assert [a.id for a in sorting.filter_agents(ags, "OPUS", lambda a: models[a.id])] == ["claude-1"]
    assert [a.id for a in sorting.filter_agents(ags, "sonnet", model_of=lambda a: models[a.id])] == ["claude-2"]
    assert len(sorting.filter_agents(ags, "claude", lambda a: models[a.id])) == 2
    seen = []
    sorting.filter_agents(ags, "", lambda a: seen.append(a))
    assert seen == []  # no query: model_of is never called (no transcript reads)


# ---- registry.ended_sessions(q=...)
def _mk_ended(st, n, project, *, model_tr=""):
    a = Agent(id=f"claude-{n}", kind=AgentKind.CLAUDE, pane_id=f"%{n}", session_id="$1", pid=n, cwd="/c",
              project=project, state=AgentState.IDLE, ident=str(uuid.uuid4()), label=f"dev-{project}-{n}-claude",
              transcript=model_tr)
    return registry.assign(st, [a], now=100.0 + n)[0].id


def test_ended_sessions_q_filters_before_the_limit_cut(tmp_path):
    st = Settings(state_dir=tmp_path / "st")
    ids = [_mk_ended(st, n, "needle" if n == 1 else f"p{n}") for n in range(1, 13)]  # needle is the OLDEST
    registry.assign(st, [], now=1000.0)
    rows, total = registry.ended_sessions(st, limit=3)
    assert total == 12 and len(rows) == 3 and ids[0] not in [r.id for r in rows]  # newest first, needle cut off
    rows, total = registry.ended_sessions(st, limit=3, q="NEEDLE")
    assert total == 1 and [r.id for r in rows] == [ids[0]]  # the match survives the cap; total counts matches
    rows, total = registry.ended_sessions(st, limit=3, q="  dev-p1  ")  # p10, p11, p12 match: three rows
    assert total == 3 and [r.id for r in rows] == [ids[11], ids[10], ids[9]]
    rows, total = registry.ended_sessions(st, limit=2, q="dev-p1")
    assert total == 3 and len(rows) == 2  # limit still applies after the filter
    assert registry.ended_sessions(st, q="nomatch") == ([], 0)


def test_ended_sessions_q_matches_id_kind_and_model_of_but_not_other_text(tmp_path):
    st = Settings(state_dir=tmp_path / "st")
    rid = _mk_ended(st, 1, "alpha")
    other = _mk_ended(st, 2, "beta")
    registry.assign(st, [], now=1000.0)
    assert [r.id for r in registry.ended_sessions(st, q=rid.upper())[0]] == [rid]
    assert registry.ended_sessions(st, q="claude")[1] == 2
    models = {rid: "opus 4.7", other: "sonnet 5"}
    assert registry.ended_sessions(st, q="opus")[1] == 0  # model_of not given
    rows, total = registry.ended_sessions(st, q="opus", model_of=lambda r: models[r.id])
    assert total == 1 and rows[0].id == rid
    seen = []
    registry.ended_sessions(st, model_of=lambda r: seen.append(r) or "")
    assert seen == []  # no query: no model lookups
    # project scope and q compose
    assert registry.ended_sessions(st, project="alpha", q="claude")[1] == 1
    assert registry.ended_sessions(st, project="alpha", q="beta")[1] == 0


# ---- fold_q
def test_fold_q_matches_the_js_folding():
    assert sorting.fold_q("  Foo \t BAR\n baz ") == "foo bar baz"
    assert sorting.fold_q(None) == "" and sorting.fold_q("   ") == ""


# ---- project_q / filter_projects
def _proj(name="Alpha", path="/dev/Alpha", branch="Main", dirty=0, stack=False):
    git = GitStatus(branch, dirty=dirty) if branch is not None else None
    return Project(name, path, git=git, stack=StackStatus(1, 2) if stack else None)


def test_project_q_has_name_path_branch_dirty_and_stack_lowercase():
    q = sorting.project_q(_proj(dirty=3, stack=True))
    assert q == "alpha /dev/alpha main dirty stack"
    q = sorting.project_q(_proj())
    assert "dirty" not in q and "stack" not in q and "main" in q
    assert sorting.project_q(_proj(branch=None)) == "alpha /dev/alpha"  # no git: no branch, no dirty
    assert sorting.project_q(_proj(branch="main", dirty=0, stack=False)).split() == ["alpha", "/dev/alpha", "main"]


def test_jinja_project_q_and_session_q_filters_are_the_sorting_functions():
    p = _proj(dirty=1)
    assert jenv.from_string("{{ p | project_q }}").render(p=p) == sorting.project_q(p)
    s, ags = _sess(), [_agent(1)]
    assert jenv.from_string("{{ s | session_q(a) }}").render(s=s, a=ags) == sorting.session_q(s, ags)


def test_filter_projects_searches_project_q_and_folds_whitespace():
    a = _proj("alpha", "/dev/alpha", "feature/x", dirty=2, stack=True)
    b = _proj("beta", "/srv/beta", "main")
    ps = [a, b]
    f = sorting.filter_projects
    assert f(ps, "FEATURE") == [a] and f(ps, "/srv") == [b]  # branch, path
    assert f(ps, "dirty") == [a] and f(ps, "stack") == [a]
    assert f(ps, "  ALPHA   /dev ") == [a]  # runs of spaces fold to one, like filter.js
    assert f(ps, "") == ps and f(ps, None) == ps and f(ps, "zzz") == []
    assert f(ps, "main", flags=["dirty"]) == []  # composes with the flag filters


# ---- session_q / filter_sessions
def _sess(sid="$1", name="Dev-PM", windows=("A1-Hub", "Shell")):
    return TmuxSession(sid, name, windows=tuple(TmuxWindow(f"@{i}", sid, i, w) for i, w in enumerate(windows)))


def test_session_q_has_session_windows_and_each_agent_label_and_id():
    ags = [_agent(1), _agent(2, label="Other-2-Claude")]
    q = sorting.session_q(_sess(), ags)
    assert q == "dev-pm a1-hub shell dev-pm-1-claude claude-1 other-2-claude claude-2"
    assert sorting.session_q(_sess(windows=())) == "dev-pm" and sorting.session_q(_sess(), []) == "dev-pm a1-hub shell"
    assert "pmproj" not in q and "/work/dir" not in q  # agent project / cwd are not part of a session row


def test_filter_sessions_matches_name_windows_and_agents_via_info():
    s1, s2 = _sess("$1", "Dev-PM", ("A1-Hub",)), _sess("$2", "Ops", ("Logs",))
    info = {"$1": [_agent(1)], "$2": [_agent(2, label="Deploy-2-Claude")]}
    f = sorting.filter_sessions
    assert f([s1, s2], "dev-pm", info) == [s1] and f([s1, s2], "LOGS", info) == [s2]  # session name, window name
    assert f([s1, s2], "deploy", info) == [s2] and f([s1, s2], "claude-1", info) == [s1]  # agent label, id
    assert f([s1, s2], " dev-pm   a1-hub ", info) == [s1]  # whitespace folded
    assert f([s1, s2], "", info) == [s1, s2] and f([s1, s2], None, info) == [s1, s2]
    assert f([s1, s2], "nope", info) == []
    assert f([s1, s2], "claude-2", {}) == []  # a session missing from info has no agents
