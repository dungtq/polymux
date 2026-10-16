"""Project page filter box (phase 2): one `input[data-filter]` outside the swapped #proj-live, every row of the tmux
tab / Agents / Idle / Ended carries data-key + name-type data-q + data-frow (client-only filtering, no server q)."""
import re
import uuid
from pathlib import Path

import pytest
from helpers import make_client, make_settings
from test_filter_box import MSG, _ended, _line

import pm
import pm.notify.owner
from pm.core.models import Agent, AgentKind, AgentState, NtfyItem
from pm.infra import snapshot as services
from pm.projects import settings as pset
from pm.web.routes.pages import router

REAL_REGISTRY = True
SRC = Path(pm.__file__).parent


@pytest.fixture
def pbox(fake_tmux, tmp_path, monkeypatch):
    """Project alpha: running + waiting + idle agents in three tmux sessions, two ended sessions (one of them
    with an ntfy message containing MSG). Returns (settings, client, ended ids)."""
    dev = tmp_path / "dev"
    (dev / "alpha").mkdir(parents=True)
    s = make_settings(fake_tmux, tmp_path)
    monkeypatch.setenv("FAKE_TMUX_OUT",
                       _line("$1", "dev-aaa", "@1", 1, "wq-alpha", "%1", str(dev / "alpha"))
                       + _line("$2", "dev-bbb", "@2", 2, "wq-beta", "%2", str(dev / "alpha"))
                       + _line("$3", "dev-ccc", "@3", 3, "wq-gamma", "%3", str(dev / "alpha")))
    live = [Agent(id=f"claude-{n}", kind=AgentKind.CLAUDE, pane_id=f"%{n}", session_id=f"${n}", pid=n,
                  cwd=str(dev / "alpha"), state=st, state_since=1.0, ident=str(uuid.uuid4()))
            for n, st in ((1, AgentState.RUNNING), (2, AgentState.WAITING), (3, AgentState.IDLE))]
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes: list(live))
    ids = {"zed": _ended(s, "dev-zed-1-claude", "alpha", str(dev / "alpha"), 100),
           "yak": _ended(s, "dev-yak-2-claude", "alpha", str(dev / "alpha"), 200)}
    item = NtfyItem(id="n1", time=300.0, title="zed finished", message=f"all done {MSG}")
    monkeypatch.setattr(pm.notify.owner, "agent_feed",
                        lambda settings, rids, path=None, unread=None: {ids["zed"]: pm.notify.owner.AgentFeed(item)})
    c = make_client(s, router, static=True, logged_in=True)
    return s, c, ids


def _tag(html, key):
    """The opening tag that carries data-key=<key>."""
    return re.search(rf'<[a-z]+ [^>]*data-key="{re.escape(key)}"[^>]*>', html).group(0)


def _live(html):
    """The #proj-live block (everything after its opening tag)."""
    return html.split('id="proj-live"', 1)[1]


def test_one_filterbox_outside_the_swapped_live_block(pbox):
    s, c, ids = pbox
    t = c.get("/p/alpha").text
    assert t.count("data-filter") == 1 and t.count('class="fbox"') == 1 and t.count('placeholder="Filter..."') == 1
    assert t.index("data-filter") < t.index('id="proj-live"')
    assert 'class="filterbar" role="search"' in t and "data-filter-live" not in t
    assert 'data-nodiff' in t and "data-filter" not in _live(t)
    bar = re.search(r'<div class="filterbar" role="search">.*?</div>', t, re.S).group(0)
    assert bar.count("<input") == 1 and bar.count("<button") == 1
    # the pj-main child is the box itself, directly above the live wrapper
    assert re.search(r'</div>\s*<div class="ssewrap" data-live>', t) and t.index("filterbar") < t.index("ssewrap")
    # no second box in the Ended head, and the swapped fragment carries none
    live = c.get("/p/alpha/live").text
    assert "data-filter" not in live and "ended-tools" in live
    assert "<input" not in re.search(r'<span class="ended-tools".*?</span>\s*</div>', live, re.S).group(0)


def test_rows_carry_key_q_and_frow_in_every_list(pbox):
    s, c, ids = pbox
    for t in (c.get("/p/alpha").text, c.get("/p/alpha/live").text):
        live = _live(t) if 'id="proj-live"' in t else t
        wkeys = re.findall(r'<div class="nwrap" role="listitem" data-key="(w-[^"]+)"', live)
        assert len(wkeys) == 3  # running, waiting (Agents) and idle (Idle fold)
        keys = ["s-$1", "s-$2", "s-$3", *wkeys, f"e-{ids['zed']}", f"e-{ids['yak']}"]
        for k in keys:
            tag = _tag(live, k)
            assert re.search(r' data-q="[^"]*"', tag), (k, tag)
            assert tag.rstrip(">").endswith(" data-frow"), (k, tag)  # data-frow is the last attribute
            assert tag.index("data-key") < tag.index("data-q") < tag.index("data-frow"), tag
        # idle agent lives in the Idle fold, active ones outside it
        fold = live.split('class="idle-fold"', 1)[1].split("</details>", 1)[0]
        assert len(re.findall(r'<div class="nwrap" role="listitem" data-key="w-', fold)) == 1  # the idle agent only
        assert len(re.findall(r'<div class="nwrap" role="listitem" data-key="w-', live.split("<section", 1)[0])) == 2


def test_data_q_is_name_type_text_only(pbox):
    s, c, ids = pbox
    live = _live(c.get("/p/alpha").text)
    for m in re.finditer(r'data-q="([^"]*)"', live):
        assert m.group(1) == m.group(1).lower() and MSG.lower() not in m.group(1)
    zed = re.search(rf'data-key="e-{ids["zed"]}" data-q="([^"]*)"', live).group(1)
    assert "zed" in zed and "finished" not in zed and "done" not in zed
    # a session row is searchable by its own name, its window names and its agents (id)
    srow = re.search(r'data-key="s-\$1" data-q="([^"]*)"', live).group(1)
    assert "dev-aaa" in srow and "wq-alpha" in srow and "claude-" in srow
    assert "dev-bbb" not in srow and "wq-beta" not in srow


def test_headings_keep_total_counts(pbox):
    s, c, ids = pbox
    live = _live(c.get("/p/alpha").text)
    assert "Idle (1)" in live and "Ended (2)" in live  # totals, never the number of rows left after a filter


def test_filterbox_absent_when_live_area_hidden(pbox):
    s, c, ids = pbox
    pset.save_hidden(s, "alpha", ["live"])
    t = c.get("/p/alpha").text
    assert "data-filter" not in t and 'class="filterbar"' not in t and 'id="proj-live"' not in t
