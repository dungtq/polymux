"""shell/_top.html: the phone title block (.x-mtitle) for a page, an agent page not open as a tab, a session tab."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import jinja2

from pm.core.models import AgentState
from pm.web import themes
from pm.web.dt import shell
from pm.web.dt.stores import TabState

from .conftest import agent, make_snap

TPL = themes.HERE / "dt" / "templates"
AID = "claude-aaa111"


def _mtitle(sh: shell.Shell, **ctx) -> str:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TPL), autoescape=True)
    cur = next((t for t in sh.tabs if t.cur), None)
    html = env.get_template("shell/_top.html").render(shell=sh, cur_tab=cur, csrf="tok", dev_banner=False, **ctx)
    m = re.search(r'<div class="x-mtitle">(.*?)</div>', html, re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()


def _snap():
    return make_snap([agent(AID, "alpha", state=AgentState.WAITING), agent("codex-bbb222", "beta")])


def test_inbox_shows_only_its_title():
    # #207: the Inbox's own stats line (README A7) carries the totals; the top bar does not repeat them
    assert _mtitle(shell.build(_snap(), TabState(), "/")) == "Inbox"


def test_other_page_shows_the_agent_totals():
    assert _mtitle(shell.build(_snap(), TabState(), "/tmux")) == "tmux 2 agents · 1 running · 1 needs you"


def test_agent_page_not_in_a_tab_shows_its_state_and_project():
    sh = shell.build(_snap(), TabState(), "/a/" + AID, nav="inbox", agent_id=AID)
    assert _mtitle(sh, agent=SimpleNamespace(id=AID, project="alpha")) == f"{AID} waiting · alpha"
    assert _mtitle(sh) == f"{AID} waiting"  # no project in the context


def test_session_tab_shows_state_and_position():
    sh = shell.build(_snap(), TabState(tabs=(("agent", AID),)), "/a/" + AID)
    assert _mtitle(sh) == f"{AID} waiting · tab 2 of 2"
