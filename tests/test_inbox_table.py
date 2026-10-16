"""v4 port: merged inbox table, Started column/sort, shared ntfy block, project agent list."""

import re
from pathlib import Path

from helpers import login, ntfy_item
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.agents import sorting
from pm.core.models import Agent, AgentKind, AgentState
from pm.infra import snapshot as services
from pm.notify import feed as ntfy_feed


def _ag(i, state, age_s=0.0):
    return Agent(id=f"claude-{i}", kind=AgentKind.CLAUDE, pane_id=f"%{i}", session_id="$1", pid=i,
                 cwd="/x", state=state, state_since=float(i), last_active=float(i), age_s=age_s)


def test_inbox_merged_table_started_and_idle_fold(env, monkeypatch):  # noqa: F811
    s, c = env
    login(s, c)
    ags = [_ag(1, AgentState.RUNNING, 3600.0), _ag(2, AgentState.WAITING, 7200.0), _ag(3, AgentState.IDLE, 60.0)]
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes: ags)
    h = c.get("/").text
    assert h.count('class="nhead has-pin"') == 2 and h.count('<div class="nlist" role="list" data-key="l-active">') == 1  # one header per table: active + the Idle fold
    assert "Running (" not in h and "Waiting for you" not in h
    assert 'class="hcell n-start' in h and "n-state" not in h.split('class="nhead has-pin"')[1]
    assert h.index("row-running") < h.index("row-waiting")
    assert 'class="inbox-title"' in h and "<h1>Inbox</h1>" in h and 'class="inbox-stats muted"' in h
    assert 'class="dot dot-running"' in h and 'class="badge st-' not in h.split('class="nhead has-pin"')[1]
    assert re.search(r'class="n-start muted"><span class="st-full" title="Started \d{4}-\d\d-\d\d', h)


def test_sort_started_newest_first_unknown_last():
    a = [_ag(1, AgentState.IDLE, 5000.0), _ag(2, AgentState.IDLE, 0.0), _ag(3, AgentState.IDLE, 10.0)]
    assert [x.id for x in sorting.sort_agents(a, "started")] == ["claude-3", "claude-1", "claude-2"]
    assert sorting.valid_key("inbox", "-started")


def test_ntfy_block_shared_no_expand_buttons(env, monkeypatch):  # noqa: F811
    s, c = env
    login(s, c)
    # a stubbed feed: the real ntfy server's messages (which may say "Expand all") must not decide this test
    monkeypatch.setattr(ntfy_feed, "_snapshot", lambda settings: ([ntfy_item(1, "/x", title="Build done")], True))
    for url in ("/ntfy/panel?scope=inbox", "/ntfy/panel?scope=all"):
        t = c.get(url).text
        assert "Expand all" not in t and "Collapse all" not in t and "data-ntfy-all" not in t
        assert "ntfy-chip" not in t
        # scope all keeps its filter box in the page's filter row (ntfy.html), not in the fragment
        assert ("data-filter" in t) == url.endswith("inbox") and "ntfy-q" not in t
        assert c.get(url + "&all=1").status_code == 200
    page = c.get("/ntfy").text
    assert page.count("data-filter") == 1 and "ntfy-q" not in page
    assert c.get("/ntfy/panel?scope=inbox&n=all").status_code == 200


def test_project_agents_have_started_column(env, monkeypatch):  # noqa: F811
    s, c = env
    login(s, c)
    r = c.get("/p/alpha/live", headers={"HX-Request": "true"})
    assert r.status_code == 200 and "<h2>Agents</h2>" in r.text


def test_inbox_stats_wraps_on_phone_not_truncated():
    css = (Path(__file__).resolve().parents[1] / "src/pm/static/css/rows.css").read_text()
    phone = [m for m in re.findall(r"@media \(max-width:699px\)\{(.*?)\}\}", css) if ".inbox-stats{" in m]
    assert phone and all("text-overflow:ellipsis" not in m and "nowrap;overflow:hidden" not in m for m in phone)
    assert "white-space:normal" in phone[0]


def test_inbox_unified_columns_live_idle_ended_and_idle_heading(env, monkeypatch):  # noqa: F811
    s, c = env
    login(s, c)
    ags = [_ag(1, AgentState.RUNNING, 3600.0), _ag(3, AgentState.IDLE, 60.0)]
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes: ags)
    h = c.get("/").text
    # live header: Project | Agent | Last message | tmux | Age | Started | cpu / mem
    head = h.split('class="nhead has-pin"')[1].split("</div>")[0]
    order = [head.index(k) for k in ("n-proj", "n-main", "n-msg", "n-tmux", "n-age", "n-start", "n-res")]
    assert order == sorted(order) and "Last message" in head and ">tmux<" in head
    row = re.search(r'<a class="nrow row-running.*?</a>', h, re.S).group(0)
    order = [row.index(f'class="{k}') for k in ("n-dot", "n-proj muted", "n-main", "n-msg muted", "n-tmux muted", "n-age muted", "n-start muted", "n-res muted")]
    assert order == sorted(order)
    assert 'data-key="claude-1" data-pane="%1"' in row and 'href="/a/claude-1"' in row
    assert ">claude-1</strong>" in row and "data-rx" in row
    # idle fold: heading in the Ended style, collapsible
    assert re.search(r'<summary class="ended-head" id="sec-idle"><h2>Idle \(1\)</h2>', h)
    assert 'class="idle-fold" data-key="idle-fold"' in h


def test_running_row_shows_working_and_age_cells_drop_the_words(env, monkeypatch):  # noqa: F811
    s, c = env
    login(s, c)
    ags = [_ag(1, AgentState.RUNNING, 3600.0), _ag(3, AgentState.IDLE, 60.0)]
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes: ags)
    h = c.get("/").text
    run = re.search(r'<a class="nrow row-running.*?</a>', h, re.S).group(0)
    assert re.search(r'<span class="n-age muted working" data-age="\d+"><span title="[^"]+">Working', run)
    idle = re.search(r'<a class="nrow row-idle.*?</a>', h, re.S).group(0)
    assert "working" not in idle and "active " not in idle and " ago</span>" in idle


def test_ended_tables_header_ended_no_cpu_mem_no_ended_word():
    from pm.agents.registry import Record
    from pm.web.render import env as _env
    r = Record(id="claude-aa", kind="claude", ident=None, pid=0, first_seen=900.0, last_seen=1000.0, last_pane="%1",
               project="pm", cwd="/x", label="pm-1", transcript="", ended_at=1000.0)
    tpl = _env.from_string('{% from "_agentrow.html" import ended_list, INBOX_COLS, PROJECT_COLS %}'
                           '{{ ended_list(rows, 1, "", {}, PROJECT_COLS if grid else INBOX_COLS) }}')
    for grid, ac, rc in ((False, "n-age", "n-res"), (True, "c-age", "c-res")):
        h = tpl.render(rows=[r], grid=grid)
        assert f'{ac}">Ended<' in h and "cpu / mem" not in h and "Active" not in h and "Age<" not in h
        assert f'<span class="hcell {rc}"></span>' in h or f'<span class="{rc}"></span>' in h
        assert "ended <" not in h and "n-endw" not in h and " ago</span>" in h


def test_inbox_views_show_one_section_each(env, monkeypatch):  # noqa: F811
    s, c = env
    login(s, c)
    ags = [_ag(1, AgentState.RUNNING, 3600.0), _ag(3, AgentState.IDLE, 60.0)]
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes: ags)
    act, idle, ended = (c.get(f"/?view={v}").text for v in ("active", "idle", "ended"))
    assert "row-running" in act and "sec-idle" not in act and 'id="ended-sessions"' not in act
    assert "row-idle" in idle and "row-running" not in idle and "<details class=\"idle-fold\" data-key=\"idle-fold\" open>" in idle
    assert "row-running" not in ended and "sec-idle" not in ended and 'class="nhead has-pin"' not in ended
    assert 'name="view" value="idle"' in idle and "view=idle" in idle  # filter form + sort links keep the page
    assert 'href="/?view=idle" aria-current="page"' in idle
    assert "row-running" in c.get("/?view=bogus").text and "sec-idle" in c.get("/?view=bogus").text
