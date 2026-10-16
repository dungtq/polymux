"""tmux window name shown next to the agent label, searchable via ?q=."""

import httpx
import pytest
from helpers import make_client, make_settings

from pm.agents import labels
from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, AgentState
from pm.infra import snapshot as services
from pm.notify import ntfy as notify
from pm.notify.events import AgentTracker
from pm.tmux.ctl import SEP
from pm.web.render import env as _env
from pm.web.routes.pages import router


def _line(sid, sname, wid, widx, wname, pane, cwd):
    return SEP.join([sid, sname, "1", wid, str(widx), wname, "1", pane, "4242", "claude", cwd, "1", "t"]) + "\n"


def _agent(n, pane, state=AgentState.RUNNING, **kw):
    return Agent(id=f"claude-{n}", kind=AgentKind.CLAUDE, pane_id=pane, session_id="$1", pid=n,
                 cwd="/x", state=state, state_since=1.0, **kw)


@pytest.fixture
def env(fake_tmux, tmp_path, monkeypatch):
    dev = tmp_path / "dev"
    (dev / "alpha").mkdir(parents=True)
    s = make_settings(fake_tmux, tmp_path)
    out = (_line("$1", "dev-web", "@6", 6, "a1-hub", "%182", str(dev / "alpha"))
           + _line("$2", "cmux-config", "@7", 6, "pm-ntfy", "%180", str(dev / "alpha"))
           + _line("$2", "cmux-config", "@8", 2, "<b>x</b>", "%181", str(dev / "alpha")))
    monkeypatch.setenv("FAKE_TMUX_OUT", out)
    found = [_agent(182, "%182", AgentState.WAITING), _agent(180, "%180"),
             _agent(181, "%181", AgentState.IDLE)]
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes: list(found))
    c = make_client(s, router, static=True, logged_in=True)
    return s, c


def test_window_name_filled_from_snapshot(env):
    s, _ = env
    snap = services.build_snapshot(s, force=True)
    by = {a.id: a for a in snap.agents}
    assert by["claude-182"].label == "dev-web-6-claude" and by["claude-182"].window_name == "a1-hub"
    assert by["claude-180"].window_name == "pm-ntfy"


def test_clean_window_name():
    assert labels.clean_window_name("a\x1b[31m\nb" + "z" * 100) == ("a [31m b" + "z" * 100)[:48]
    assert len(labels.clean_window_name("é" * 100)) == 48 and labels.clean_window_name(None) == ""


def test_tracker_and_merge_keep_window_name():
    tr = AgentTracker(clock=lambda: 5.0)
    tr.update([_agent(1, "%1", window_name="a1-hub")])
    assert tr.agents[0].window_name == "a1-hub"
    tr.update([_agent(1, "%1", AgentState.IDLE, window_name="a1-hub")])
    assert tr.agents[0].window_name == "a1-hub"
    snap = services.Snapshot(0.0, (), (_agent(1, "%1", window_name="a1-hub"),), (), ())
    assert services.merge_tracker(snap, tr).agents[0].window_name == "a1-hub"


def test_macro_autoescapes_window_name():
    html = _env.from_string(
        '{% from "_macros.html" import agent_label %}{{ agent_label(a) }}'
    ).render(a=_agent(1, "%1", label="s-1-claude", window_name="<b>x</b>"))
    assert "s-1-claude" in html and "&lt;b&gt;x&lt;/b&gt;" in html and "<b>" not in html


def test_inbox_shows_label_and_window(env):
    _, c = env
    t = c.get("/").text
    # agent column = agent id; the tmux column shows "<session>-<idx> >_ <window name>" (autoescaped)
    assert ">claude-182</strong>" in t and ">claude-180</strong>" in t and ">dev-web-6-claude<" not in t
    assert '<span class="wslot">web-6</span>' in t and '<span class="wslot">cmux-config-6</span>' in t
    assert "window a1-hub" in t and "window pm-ntfy" in t and '<span class="wtxt">a1-hub</span>' not in t
    assert "&lt;b&gt;x&lt;/b&gt;" in t and "<b>x</b>" not in t
    assert 'id="inbox-filter"' in t and 'name="q"' in t and t.count('<input type="search" name="q" data-filter ') == 1
    assert 'placeholder="Filter..."' in t and "Filter</button>" not in t
    assert "<style" not in t and "onclick" not in t
    assert "<script>" not in t.replace("<script src", "")


def test_filter_q(env):
    _, c = env
    for q, want in (("a1-hub", "claude-182"), ("PM-NTFY", "claude-180"), ("dev-web", "claude-182"),
                    ("claude-180", "claude-180"), ("cmux-config-6", "claude-180")):
        t = c.get("/", params={"q": q}).text
        hits = {i for i in ("claude-180", "claude-181", "claude-182") if f'data-key="{i}"' in t}
        assert hits == {want}, q
        assert "1 of 3 agents" in t


def test_filter_composes_with_n_and_sort(env):
    _, c = env
    t = c.get("/", params={"q": "cmux", "n": "10", "sort": "state"}).text
    assert t.count('class="nrow ') == 2 and "2 of 3 agents" in t
    assert "q=cmux" in t and "sort=state" in t
    t = c.get("/", params={"q": "claude", "n": "10"}).text
    assert "3 of 3 agents" in t
    # limit after filtering
    s, _ = env
    t = c.get("/", params={"q": "cmux", "n": "10", "sort": "idle"}, headers={"hx-request": "true"}).text
    assert 'data-key="claude-182"' not in t and 'id="inbox-filter"' in t and 'data-keep' in t


def test_no_match_and_clear(env):
    _, c = env
    t = c.get("/", params={"q": "zzz"}).text
    assert "No agents match" in t and "0 of 3 agents" in t
    assert "Clear filter</a>" not in t and 'value="zzz"' in t  # no Clear link; the box keeps the text, its x clears it
    assert c.get("/").text.count('class="nrow ') == 3


def test_notify_title_has_window_name():
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(200)

    a = _agent(182, "%182", label="dev-web-6-claude", window_name="a1-hub", project="web")
    st = Settings(state_dir=None) if False else Settings()
    notify.notify_state_change(st, a, AgentState.RUNNING, AgentState.WAITING, now=0,
                               transport=httpx.MockTransport(h))
    r = seen[0]
    assert "a1-hub" in r.headers["title"] and "a1-hub" in r.content.decode()
    assert r.headers["click"].endswith("/a/claude-182")
