"""Token statistics for Devin (sqlite store) and Cursor (pane footer)."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest
from helpers import chat_env as env  # noqa: F401  (fixture)
from helpers import login

from pm.chat import stats as chat_stats
from pm.chat.sources import cursor_stats, devin_stats
from pm.chat.sources.common import Stats


def _msg(role, user=False, rid=None, i=None, o=None, cw=None, cr=None, ntok=None):
    m = {"role": role, "content": "", "metadata": {"is_user_input": True if user else None, "request_id": rid,
                                                   "num_tokens": ntok,
                                                   "metrics": {"input_tokens": i, "output_tokens": o,
                                                               "cache_creation_tokens": cw, "cache_read_tokens": cr}}}
    return json.dumps(m)


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / ".local/share/devin/cli/sessions.db"
    p.parent.mkdir(parents=True)
    c = sqlite3.connect(p)
    c.executescript(
        "CREATE TABLE sessions(id TEXT PRIMARY KEY, working_directory TEXT, model TEXT, created_at INT,"
        " last_activity_at INT, title TEXT, main_chain_id INT);"
        "CREATE TABLE message_nodes(row_id INTEGER PRIMARY KEY, session_id TEXT, node_id INT, parent_node_id INT,"
        " chat_message TEXT, created_at INT, metadata TEXT);"
    )
    c.execute("INSERT INTO sessions VALUES('s1','/w','swe-2-max',1,2,'t',7)")
    rows = [
        (0, None, _msg("system"), None),
        (1, 0, _msg("user", True), None),
        (2, 1, _msg("assistant", rid="r1", i=100, o=10, cr=900, ntok=10), {"num_tokens_preceding": 1000}),
        (3, 2, _msg("tool"), None),
        (4, 3, _msg("user", True), None),
        (5, 4, _msg("assistant", rid="r2", i=200, o=20, cw=50, cr=1000, ntok=20), {"num_tokens_preceding": 1250}),
        (6, 4, _msg("assistant", rid="r2x", i=999, o=999, cr=999, ntok=1), {"num_tokens_preceding": 5}),  # abandoned branch
        (7, 5, _msg("assistant", rid="r2", i=200, o=20, cw=50, cr=1000, ntok=20), {"num_tokens_preceding": 1250}),  # dup request
        (50, None, _msg("user", True), None),  # sub-agent chain, not reachable from the head
        (51, 50, _msg("assistant", rid="sub", i=7777, o=7777, ntok=1), {"num_tokens_preceding": 77777}),
    ]
    for n, par, cm, md in rows:
        c.execute("INSERT INTO message_nodes(session_id,node_id,parent_node_id,chat_message,metadata) VALUES('s1',?,?,?,?)",
                  (n, par, cm, json.dumps(md) if md else None))
    c.execute("INSERT INTO message_nodes(session_id,node_id,parent_node_id,chat_message) VALUES('other',0,NULL,?)", (_msg("user", True),))
    c.commit()
    c.close()
    devin_stats._CACHE.clear()
    return tmp_path


def test_devin_main_chain_sums_ctx_window(db):
    s = devin_stats.session_stats(SimpleNamespace(), home=db, sid="s1")
    assert s.turns == 2 and s.model == "swe-2-max" and s.source == "devin"
    assert (s.input, s.output, s.cache_write, s.cache_read) == (300, 30, 50, 1900)
    assert s.ctx == 1270 and s.window == 262_000 and s.pct == 0  # 1250 + 20 own tokens
    assert devin_stats.session_stats(SimpleNamespace(), home=db, sid="missing") is None


def test_devin_cache_and_missing_db(db, tmp_path):
    a = devin_stats.session_stats(SimpleNamespace(), home=db, sid="s1")
    b = devin_stats.session_stats(SimpleNamespace(), home=db, sid="s1")
    assert a == b and a is not b
    assert devin_stats.session_stats(SimpleNamespace(), home=tmp_path / "nohome", sid="s1") is None


def test_devin_pct_matches_tui_example():
    s = Stats(ctx=24_552, window_hint=devin_stats.WINDOW, source="devin")
    assert s.pct == 9


@pytest.mark.parametrize("text,want", [
    ("blah\n Grok 4.6 High Fast · 11.9%\n", ("Grok 4.6 High Fast", 11.9)),
    ("x\n  Claude 4.5 Sonnet · 100%  \n\n", ("Claude 4.5 Sonnet", 100.0)),
    ("x\n  Auto · 0.4% ctrl+c to stop\n", ("Auto", 0.4)),
    ("\x1b[2mGPT-5 Codex · 42%\x1b[0m", ("GPT-5 Codex", 42.0)),
    ("old · 10%\nnewer · 55%\n", ("newer", 55.0)),
    ("Grok 4.6 High Fast\n> hi", None),
    ("Grok · 250%", None),
    ("", None),
])
def test_cursor_parse_footer(text, want):
    assert cursor_stats.parse_footer(text) == want


def test_cursor_stats_and_turns(tmp_path):
    import hashlib
    import os
    cwd = "/w/proj"
    base = tmp_path / ".cursor/chats" / hashlib.md5(cwd.encode()).hexdigest()
    (base / "c1").mkdir(parents=True)
    (base / "c1/store.db").write_bytes(b"")
    (base / "c1/prompt_history.json").write_text('["a","b","c"]')
    ag = SimpleNamespace(cwd=cwd)
    s = cursor_stats.session_stats(ag, "x\nGrok 4.6 High Fast · 11.9%", home=tmp_path)
    assert (s.pct, s.pct_hint, s.model, s.source, s.exact, s.turns, s.total) == (12, 12, "Grok 4.6 High Fast", "cursor-agent", False, 3, 0)
    assert cursor_stats.session_stats(ag, "no footer", home=tmp_path) is None
    (base / "c2").mkdir()  # a second chat written at the same time: ambiguous, no turns
    (base / "c2/store.db").write_bytes(b"")
    os.utime(base / "c1/store.db", (1, 1))
    os.utime(base / "c2/store.db", (2, 2))
    assert cursor_stats.session_stats(ag, "Auto · 5%", home=tmp_path).turns == 0


def test_panel_non_exact(env, monkeypatch):  # noqa: F811
    s, c = env
    login(s, c)
    monkeypatch.setattr("pm.chat.stats.stats_for",
                        lambda a, settings=None: Stats(turns=0, pct_hint=85, model="Grok 4.6", source="cursor-agent", exact=False))
    t = c.get("/a/claude-12/stats").text
    assert "lvl-red" in t and "85%" in t and "Grok 4.6" in t and "Cursor" in t
    assert "Cursor does not store token counts" in t
    for gone in ("Total tokens", "Cache read", "Input (new)", "0 turns", " tokens</span>"):
        assert gone not in t
    monkeypatch.setattr("pm.chat.stats.stats_for",
                        lambda a, settings=None: Stats(turns=2, input=500, ctx=500, window_hint=262_000, source="devin"))
    t = c.get("/a/claude-12/stats").text
    assert "Devin" in t and "Total tokens" in t and "262k" in t


def test_stats_for_dispatch(monkeypatch):
    from pm.core.models import AgentKind
    ag = lambda k: SimpleNamespace(kind=AgentKind(k), pid=0, cwd="/x", pane_id="", age_s=0)
    assert chat_stats.stats_for(ag("codex")) is None
    assert chat_stats.stats_for(ag("cursor-agent")) is None  # no settings / pane
    r = chat_stats.stats_for(SimpleNamespace(kind=SimpleNamespace(value="antigravity")))
    assert r is None or isinstance(r, Stats)


def test_inbox_rows_read_the_devin_lock_map_once(db, monkeypatch):
    """ctx % of many Devin rows: one lock map for the whole list (each read walks every lock pid's parents)."""
    import asyncio

    from pm.chat.sources import devin as devin_src
    from pm.core.models import Agent, AgentKind, AgentState
    from pm.web import ntfypanel

    tr = db / ".local/share/devin/cli/transcripts"
    tr.mkdir(parents=True)
    (tr / "s1.json").write_text("{}")
    monkeypatch.setenv("HOME", str(db))
    calls = []
    monkeypatch.setattr(devin_src, "lock_map", lambda home, proc_root: calls.append(home) or {101: ["s1"]})

    def ag(n, kind=AgentKind.DEVIN):
        return Agent(id=f"{kind.value}-{n}", kind=kind, pane_id=f"%{n}", session_id="$1", pid=100 + n, cwd="/w",
                     state=AgentState.IDLE, state_since=1.0)
    devins = [ag(n) for n in range(1, 9)]
    old = {a.id: chat_stats.ctx_pct(a) for a in devins}  # no shared map: one read per row
    assert len(calls) == len(devins) and old["devin-1"] == 0 and old["devin-2"] is None
    calls.clear()
    got = asyncio.run(ntfypanel._row_extras(None, devins, {}))
    assert {k: v[0] for k, v in got.items()} == old
    assert calls == [db]
    calls.clear()
    asyncio.run(ntfypanel._row_extras(None, [ag(1, AgentKind.CODEX)], {}))
    assert calls == []  # no Devin row: no lock map
