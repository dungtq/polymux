"""Agent chat page sidebar: TMUX tree of the agent's session + HISTORY of ended agents (server-rendered)."""
import re
import time

import pytest
from helpers import make_client, make_settings

from pm.agents import registry
from pm.core.models import Agent, AgentKind, AgentState
from pm.infra import snapshot as services
from pm.tmux import ctl as tmuxctl
from pm.tmux.ctl import SEP

UUID = "0b5e6f3c-1111-4222-8333-444455556666"


def _line(sid, sname, wid, widx, wname, pid, cmd, cwd):
    return SEP.join([sid, sname, "1", wid, widx, wname, "1", pid, "4242", cmd, cwd, "1", "t"]) + "\n"


@pytest.fixture
def env(fake_tmux, tmp_path, monkeypatch):
    dev = tmp_path / "dev"
    (dev / "alpha").mkdir(parents=True)
    s = make_settings(fake_tmux, tmp_path, terminal_enabled=True)
    cwd = str(dev / "alpha")
    out = (_line("$1", "work", "@1", "1", "claude", "%12", "claude", cwd)
           + _line("$1", "work", "@1", "1", "claude", "%13", "tail", cwd)
           + _line("$1", "work", "@2", "2", "fish", "%14", "fish", cwd))
    monkeypatch.setenv("FAKE_TMUX_OUT", out)
    ag = Agent(id="claude-12", kind=AgentKind.CLAUDE, pane_id="%12", session_id="$1", pid=4242, cwd=cwd,
               project="alpha", state=AgentState.RUNNING, state_since=1.0, label="work-1-claude")
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes, **kw: [ag])
    monkeypatch.setattr(tmuxctl, "capture_pane", lambda settings, pane, lines=200: "hello")
    now = time.time()
    con = registry._connect(s)
    for i, (aid, kind, ident, lab) in enumerate((("claude-aaaaaa", "claude", UUID, "work-3-claude"),
                                                  ("devin-bbbbbb", "devin", None, "work-4-devin"))):
        con.execute("INSERT INTO agent_records VALUES (?,?,?,?,?,?,'%1','alpha',?,?,'',?,'')",
                    (aid, kind, ident, 11 + i, now - 7200 - i, now - 3600 - i, cwd, lab, now - 3600 - i))
    con.commit()
    con.close()
    c = make_client(s, logged_in=True)
    return s, c


def test_tree_current_and_links_by_id(env):
    _, c = env
    h = c.get("/a/claude-12").text
    assert 'class="v5-side ts-side"' in h and "/static/css/side-tmux.css" in h and 'id="v5side"' in h
    assert h.count('<aside class="v5-side') == 1
    # current agent: exactly one on-row, server-side, pointing at the agent
    assert re.findall(r'class="vs-row ts-win on" href="([^"]+)" aria-current="page"', h) == ["/a/claude-12"]
    side = h[h.index("<aside class=\"v5-side"):h.index("</aside>")]
    assert side.count("aria-current=\"page\"") == 1
    # keyed by the project: the list holds the project's live agents (any tmux session), tagged with the session
    assert '<span class="vs-c">work</span>' in side and "vs-pdn" in side and 'class="vs-t"' not in side
    assert "ts-h\">ACTIVE" in side and "/term/" not in side
    assert 'class="vs-all"' not in side


def test_history_rows_and_resume_only_where_offered(env):
    _, c = env
    h = c.get("/a/claude-12").text
    assert 'href="/a/claude-aaaaaa"' in h and 'href="/a/devin-bbbbbb"' in h
    assert h.count('class="ts-res"') == 1 and 'aria-label="Resume claude-aaaaaa"' in h
    assert "1h 0m" in h


def test_filter_and_fold_hooks_kept(env):
    _, c = env
    h = c.get("/a/claude-12").text
    assert 'class="fbox vs-filter"' in h and 'data-filter-scope="#v5side"' in h and 'class="vs-toggle"' in h
    assert 'class="vs-empty" data-fnone' in h
    assert "/static/js/side.js" in h and "/static/js/side-init.js" in h and 'data-label="Windows"' in h


def test_embed_and_other_pages_unchanged(env):
    _, c = env
    assert 'id="v5side"' not in c.get("/a/claude-12?embed=1").text
    p = c.get("/p/alpha").text
    assert '<aside class="v5-side' in p and "ts-side" not in p and "side-tmux.css" not in p
    assert "ts-side" not in c.get("/tmux/s1").text


def test_short_names_and_kind_pills(env):
    _, c = env
    h = c.get("/a/claude-12").text
    side = h[h.index('<aside class="v5-side'):h.index("</aside>")]
    # agent rows: the id then the pill (kind stands in: the fixtures carry no transcript), no label, no second pill
    row = re.search(r'<a class="vs-row ts-win on".*?</a>', side, re.S).group(0)
    assert '<strong class="aname">claude-12</strong><span class="kpill kp-claude"' in row and "work-1" not in row
    assert row.count("kpill") == 1 and "ts-kp" not in row
    # history: id + one pill
    hist = side[side.index("ts-histsec"):]
    assert '<strong class="aname">claude-aaaaaa</strong><span class="kpill kp-claude"' in hist
    assert '<strong class="aname">devin-bbbbbb</strong><span class="kpill kp-devin"' in hist
    assert "work-3" not in hist and "ts-k\"" not in hist and 'class="vs-c ts-k"' not in hist
    assert "ts-ago" in hist and "ts-dur" in hist


def test_scoped_box_rows_render_name_type_q(env):
    """The agent sidebar rows carry data-q from names (agent id, kind, model), the box has its own scope."""
    _, c = env
    h = c.get("/a/claude-12").text
    side = h.split('id="v5side"', 1)[1].split("</aside>", 1)[0]
    assert 'data-filter-scope="#v5side"' in side and 'name="q"' not in side.split("vs-filter", 1)[1].split("</span>", 1)[0]
    qs = re.findall(r'class="vs-row[^"]*"[^>]*data-q="([^"]*)"', side)
    assert qs and all(q == q.lower() for q in qs)


def test_ended_agent_is_current_in_history(env):
    """An ended agent's page keeps it in HISTORY as the one current (selected) row."""
    _, c = env
    h = c.get("/a/claude-aaaaaa").text
    side = h[h.index('<aside class="v5-side'):h.index("</aside>")]
    hist = side[side.index("ts-histsec"):]
    assert 'href="/a/claude-aaaaaa"' in hist and 'href="/a/devin-bbbbbb"' in hist
    assert re.findall(r'class="vs-row ts-hist on" data-n="([^"]+)"', hist) == ["claude-aaaaaa"]
    assert re.findall(r'class="ts-hl" href="([^"]+)" aria-current="page"', hist) == ["/a/claude-aaaaaa"]
    assert side.count('aria-current="page"') == 1
