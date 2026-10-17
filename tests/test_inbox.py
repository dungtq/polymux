import re
import time
from pathlib import Path

from helpers import login, ntfy_item
from helpers import ntfy_env as env  # noqa: F401  (fixture)

from pm.agents import inbox
from pm.core.models import Agent, AgentKind, AgentState, Ask, AskQuestion, NtfyItem
from pm.notify import feed as ntfy_feed


def ag(i, state, since=0.0, active=0.0):
    return Agent(id=i, kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=1, cwd="/x",
                 state=AgentState(state), state_since=since, last_active=active)


def test_question_of_picks_question_and_clips():
    text = "╭──────╮\n│ Edit file x.py │\n│ Do you want to proceed? │\n│ ❯ 1. Yes │\n╰──────╯\n"
    assert inbox.question_of(text) == "Do you want to proceed?"
    assert inbox.question_of("") == "" and inbox.question_of(None) == ""
    assert inbox.question_of("just text\nlast line") == "last line"
    assert len(inbox.question_of("x? " + "y" * 500)) <= 220


ASKQ = Path(__file__).resolve().parent / "fixtures" / "askq"  # real Claude Code AskUserQuestion captures


def test_question_of_askuserquestion_form_never_the_footer():
    """Regression: the "Enter to select · ↑/↓ to navigate · … Esc to cancel" footer matched "select"."""
    want = {"cap_q1_preview.txt": "Which layout for the demo page?",
            "cap_q1_answered_chat.txt": "Which layout for the demo page?",
            "cap_q2.txt": "Which theme should the demo use?",
            "cap_after2.txt": "Ready to submit your answers?"}
    for name, q in want.items():
        text = (ASKQ / name).read_text()
        assert inbox.question_of(text) == q, name


def test_question_of_prefers_merged_form_question():
    text = (ASKQ / "cap_q2.txt").read_text()
    form = (AskQuestion("Theme", "Which theme should the demo use, light or dark?"),)
    asking = Ask("choice", form[0].question, (), form, 0)
    assert inbox.question_of(text, asking=asking) == "Which theme should the demo use, light or dark?"
    assert inbox.question_of(text, asking=Ask("choice", "Pick one\nWhich theme\nshould we use?")) == \
        "Which theme should we use?"


def test_question_of_skips_hint_lines_without_an_ask():
    text = "Some reply text.\nIs this ok?\nEnter to select · ↑/↓ to navigate · Esc to cancel\n"
    assert inbox.question_of(text) == "Is this ok?"
    assert inbox.question_of("done here\nEsc to cancel · Tab to amend") == "done here"


def test_cached_question_returns_fetch_result():
    inbox._qcache.clear()
    calls = []
    assert inbox.cached_question("%9", 1.0, lambda: calls.append(1) or "Q?", now=100.0) == "Q?"
    assert inbox.cached_question("%9", 1.0, lambda: "other", now=101.0) == "Q?" and calls == [1]


def test_buckets_and_store(tmp_path):
    now = time.time()
    st = inbox.load(tmp_path)
    assert inbox.bucket(ag("a", "waiting", 5), st, now) == "waiting"
    assert inbox.bucket(ag("a", "running", 5), st, now) == "running"
    assert inbox.bucket(ag("a", "idle", 5, now - 100), st, now) == "finished"
    assert inbox.bucket(ag("a", "idle", 5, now - 9000), st, now) == "finished"  # default window is 8h
    assert inbox.bucket(ag("a", "idle", 5, now - 9 * 3600), st, now) == "idle"  # too old
    assert inbox.bucket(ag("a", "idle", 5, now - 9000), st, now, window_s=3600) == "idle"  # custom window
    assert inbox.bucket(ag("a", "idle", 5, now - 100), st, now, window_s=60) == "idle"
    inbox.mark(tmp_path, "a:idle", dismiss=True, now=now - 50)
    st = inbox.load(tmp_path)
    assert inbox.bucket(ag("a", "idle", 5, now - 100), st, now) == "idle"
    assert inbox.bucket(ag("a", "idle", 5, now - 10), st, now) == "finished"  # new activity again
    inbox.mark_all(tmp_path, now=now - 1)
    assert not inbox.load(tmp_path).unread("b:waiting", now - 1000)


def test_bad_key_rejected(tmp_path):
    for k in ("", "a b", "x/../y", "a" * 101):
        try:
            inbox.mark(tmp_path, k, dismiss=False)
        except ValueError:
            continue
        raise AssertionError(k)


def test_group_ntfy_merges_agent_event():
    mkn = lambda i, t, ev: NtfyItem(id=str(i), time=t, title="t", message="", tags=("pm", ev),
                                    click="https://pm.example.test/a/claude-3")
    items = [mkn(1, 10, "waiting"), mkn(2, 20, "waiting"), mkn(3, 15, "idle"),
             NtfyItem(id="x", time=5, title="o", message="", tags=("other",))]
    g = inbox.group_ntfy(items)
    assert [(x.key, x.count, x.ts) for x in g] == [
        ("claude-3:waiting", 2, 20), ("claude-3:idle", 1, 15), ("n:x", 1, 5)]


def test_stale_failed_window():
    now = time.time()
    f = ag("a", "failed", now - 7200)
    assert not inbox.stale_failed(f, now)  # 2h < the 8h default
    assert inbox.stale_failed(f, now, window_s=3600)
    assert inbox.stale_failed(ag("a", "failed", now - 9 * 3600), now)


def test_group_agents_and_unread_follow_window(tmp_path):
    now = time.time()
    st = inbox.load(tmp_path)
    a = [ag("a", "idle", now - 7200)]
    keys = lambda w: [s["key"] for s in inbox.group_agents(a, st, now, lambda x: x, window_s=w) if s["total"]]  # noqa: E731
    assert keys(3600) == ["idle"] and keys(8 * 3600) == ["finished"]
    assert inbox.unread_count(a, [], st, now, window_s=3600) == 0
    assert inbox.unread_count(a, [], st, now, window_s=8 * 3600) == 1


def test_group_agents_pinned_section_first_and_out_of_its_bucket(tmp_path):
    now = time.time()
    st = inbox.load(tmp_path)
    a = [ag("a", "idle", now - 9 * 3600), ag("b", "running", now), ag("c", "idle", now - 9 * 3600)]
    secs = {s["key"]: [x.id for x in s["agents"]] for s in inbox.group_agents(a, st, now, lambda x: x, pins={"a", "b"})}
    assert list(secs)[0] == "pinned" and secs["pinned"] == ["a", "b"]
    assert secs["running"] == [] and secs["idle"] == ["c"]
    assert inbox.group_agents(a, st, now, lambda x: x)[0] == {"key": "pinned", "title": "Pinned", "agents": [], "total": 0}


def test_idle_window_clamps_and_default_is_settings():
    from types import SimpleNamespace as NS

    from pm.core.config import Settings
    assert inbox.FINISHED_WINDOW_S == Settings.idle_after_s == 28800
    assert inbox.idle_window(NS(idle_after_s=3600)) == 3600
    assert inbox.idle_window(NS(idle_after_s=5)) == 60
    assert inbox.idle_window(NS(idle_after_s=0)) == 60


def test_unread_count_dedupes_agent_and_ntfy(tmp_path):
    now = time.time()
    a = [ag("claude-3", "waiting", now - 10)]
    g = inbox.group_ntfy([NtfyItem(id="1", time=now - 10, title="t", message="", tags=("pm", "waiting"),
                                   click="https://pm.example.test/a/claude-3")])
    st = inbox.load(tmp_path)
    assert inbox.unread_count(a, g, st, now) == 1
    inbox.mark(tmp_path, "claude-3:waiting", dismiss=False, now=now - 5)
    assert inbox.unread_count(a, g, inbox.load(tmp_path), now) == 0


def test_unread_count_is_one_per_live_agent_row(tmp_path):
    """The badge counts what the Inbox shows: a live agent once (its row unread, or any of its messages: the row's
    unread count), a message from a gone or unknown sender on its own."""
    now = time.time()
    a = [ag("claude-3", "waiting", now - 10)]
    items = [NtfyItem(id="1", time=now - 10, title="t", message="", tags=("pm", "waiting"),
                      click="https://pm.example.test/a/claude-3"),
             NtfyItem(id="2", time=now - 20, title="claude-3 - done", message=""),  # no click link: by owner
             NtfyItem(id="3", time=now - 30, title="t3", message="", tags=("pm", "idle"),
                      click="https://pm.example.test/a/claude-3"),  # an older event of the same agent
             NtfyItem(id="4", time=now - 40, title="claude-9 - gone", message="")]
    g = inbox.group_ntfy(items)
    st = inbox.load(tmp_path)
    assert inbox.unread_count(a, g, st, now, owners={"2": "claude-3", "4": "claude-9"}) == 2  # claude-3, item 4
    assert inbox.unread_count(a, g, st, now) == 3  # owner of item 2 unknown: on its own
    assert inbox.unread_count([], g, st, now, owners={"2": "claude-3"}) == 4  # nobody live: every group


def test_routes_merge_read_dismiss_audit(env):  # noqa: F811
    s, c, st, dev = env
    login(s, c)
    click = "https://pm.example.test/a/claude-3"
    st["items"] = [ntfy_item(i, str(dev / "alpha"), tags=(str(dev / "alpha"), "waiting"), click=click,
                      time=1_700_000_000.0 + i) for i in range(3)]
    t = c.get("/ntfy/panel").text
    assert t.count('<details class="ntfy-row"') == 1 and "&times;3" in t and "data-unread" in t
    assert "ntfy-x" not in t and t.count("<form") == 1  # rows are plain; read happens on open
    tok = re.search(r'name="csrf" value="([^"]+)"', c.get("/").text).group(1)
    hdr = {"origin": "https://pm.example.test"}
    assert c.post("/inbox/read", data={"key": "claude-3:waiting"}, headers=hdr).status_code == 403
    r = c.post("/inbox/read", data={"csrf": tok, "key": "bad key"}, headers=hdr)
    assert r.status_code == 400
    r = c.post("/inbox/read", data={"csrf": tok, "key": "claude-3:waiting"}, headers=hdr)
    assert r.status_code == 200 and r.headers["hx-trigger"] == "pm:refresh"
    assert "data-unread" not in c.get("/ntfy/panel").text
    r = c.post("/inbox/dismiss", data={"csrf": tok, "key": "claude-3:waiting"}, headers=hdr)
    assert r.status_code == 200
    assert "ntfy-row" not in c.get("/ntfy/panel").text
    audit = s.audit_file.read_text()
    assert "inbox_read" in audit and "inbox_dismiss" in audit
    assert c.post("/inbox/read-all", data={"csrf": tok}, headers=hdr).status_code == 200


def test_unread_endpoint(env):  # noqa: F811
    s, c, st, dev = env
    login(s, c)
    st["items"] = [ntfy_item(1, str(dev / "alpha"), time=time.time() - 5)]
    r = c.get("/inbox/unread")
    assert r.status_code == 200 and r.json() == {"unread": 0, "system": 0}  # first use: baseline
    st["items"].append(ntfy_item(2, str(dev / "alpha"), time=time.time() + 60))
    assert c.get("/inbox/unread").json() == {"unread": 1, "system": 0}


def test_inbox_js_marks_read_on_open(env):  # noqa: F811
    s, c, st, dev = env
    login(s, c)
    js = c.get("/static/js/inbox.js").text
    assert '"/inbox/read"' in js and ".nwrap > a[data-read]" in js


def test_side_unread_endpoint_and_badges(env):  # noqa: F811
    s, c, st, dev = env
    login(s, c)
    st["items"] = [ntfy_item(1, str(dev / "alpha"), time=time.time() - 5)]
    c.get("/inbox/unread")  # baseline
    st["items"].append(ntfy_item(2, str(dev / "alpha"), time=time.time() + 60))
    assert c.get("/side/unread?kind=projects").json() == {"alpha": 1}
    assert c.get("/side/unread?kind=sessions").status_code == 404
    assert c.get("/side/unread?kind=x").status_code == 404
    page = c.get("/p/alpha").text
    assert 'data-badge-kind="projects"' in page and 'class="cpill vs-ub" data-ub title' in page
    assert "vs-foot-tg" in page


def test_store_file_private_and_tolerates_garbage(tmp_path):
    import stat

    p = tmp_path / "inbox_state.json"
    p.write_text("[1, 2]")
    assert inbox.load(tmp_path).d == {"all": 0.0, "read": {}, "dismiss": {}, "unread": {}}
    now = time.time()
    inbox.mark(tmp_path, "a:idle", dismiss=False, now=now)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert inbox.load(tmp_path).read_at("a:idle") == now


# ------------------------------------------------------------------ M2: mark unread, last message, row meta
def test_mark_unread_wins_until_read(tmp_path):
    now = time.time()
    inbox.mark_all(tmp_path, now=now)
    assert not inbox.load(tmp_path).unread("a:idle", now - 10)
    inbox.mark_unread(tmp_path, "a:idle", now=now)
    st = inbox.load(tmp_path)
    assert st.marked_unread("a:idle") and st.unread("a:idle", now - 10)  # wins over max(all, read[k])
    assert not st.marked_unread("b:idle")
    inbox.mark(tmp_path, "a:idle", dismiss=False)
    assert not inbox.load(tmp_path).unread("a:idle", now - 10)  # read clears the override
    inbox.mark_unread(tmp_path, "a:idle")
    inbox.mark_many(tmp_path, ["a:idle"])
    assert not inbox.load(tmp_path).marked_unread("a:idle")
    inbox.mark_unread(tmp_path, "a:idle")
    inbox.mark_all(tmp_path)
    assert not inbox.load(tmp_path).marked_unread("a:idle")
    try:
        inbox.mark_unread(tmp_path, "bad key")
        raise AssertionError("bad key accepted")
    except ValueError:
        pass


def test_marked_unread_counts_in_every_bucket(tmp_path):
    now = time.time()
    inbox.mark_all(tmp_path, now=now)
    run = ag("claude-1", "running", now - 50)
    assert inbox.unread_count([run], [], inbox.load(tmp_path), now) == 0
    inbox.mark_unread(tmp_path, inbox.agent_key(run))
    assert inbox.unread_count([run], [], inbox.load(tmp_path), now) == 1
    old = NtfyItem(id="o", time=now - 3 * 86400, title="claude-1 - old", message="")  # outside the 24 h window
    g = inbox.group_ntfy([old])
    assert not inbox.ntfy_unread(inbox.load(tmp_path), g[0].key, old.time, now)
    inbox.mark_unread(tmp_path, g[0].key)
    st = inbox.load(tmp_path)
    assert inbox.ntfy_unread(st, g[0].key, old.time, now) and inbox.item_unread(st, old, now)
    assert inbox.unread_count([], g, st, now) == 1


def test_item_unread_skips_system_and_dismissed(tmp_path):
    now = time.time()
    inbox.mark_all(tmp_path, now=now - 100)
    it = NtfyItem(id="x1", time=now - 10, title="claude-1 - t", message="")
    sys_ = NtfyItem(id="s1", time=now - 10, title="pm alert -- h: disk", message="")
    st = inbox.load(tmp_path)
    assert inbox.item_unread(st, it, now) and not inbox.item_unread(st, sys_, now)
    inbox.mark(tmp_path, inbox.ntfy_key(it), dismiss=True, now=now - 5)
    inbox.mark_unread(tmp_path, inbox.ntfy_key(it))
    assert not inbox.item_unread(inbox.load(tmp_path), it, now)  # dismissed stays out


def test_cached_question_key_has_the_event():
    inbox._qcache.clear()
    assert inbox.cached_question("%9", 1.0, lambda: "Q?", now=100.0) == "Q?"
    assert inbox.cached_question("%9", 1.0, lambda: "Error: boom", now=100.5, event="failed") == "Error: boom"
    assert inbox.cached_question("%9", 1.0, lambda: "other", now=101.0) == "Q?"


def test_error_of_masks_and_clips():
    from helpers import TOKEN

    assert inbox.error_of("working\nError: API error 500\n> ") == "Error: API error 500"
    assert inbox.error_of("all fine\n> ") == "" and inbox.error_of(None) == ""
    got = inbox.error_of(f"Error: bad token {TOKEN}")
    assert TOKEN not in got and got.startswith("Error: bad token")
    assert len(inbox.error_of("Error: " + "x" * 500)) == 220


def test_last_src_matches_the_classic_rule():
    nt = NtfyItem(id="n", time=200.0, title="t", message="m")
    assert inbox.last_src(nt, "u", 300.0) == "user"
    assert inbox.last_src(nt, "u", 100.0) == "ntfy"
    assert inbox.last_src(nt, "u", 0.0) == "ntfy"  # unknown user time loses to the item
    assert inbox.last_src(None, "u", 0.0) == "user"
    assert inbox.last_src(nt, "", 300.0) == "ntfy"
    assert inbox.last_src(None, "", 0.0) == "" and inbox.last_src(None, None, None) == ""


def test_last_message_one_line_masked_clipped(monkeypatch):
    from helpers import TOKEN

    a = ag("claude-1", "idle")
    monkeypatch.setattr(inbox.live, "last_user_task", lambda agent: f"fix   it\nnow {TOKEN}")
    monkeypatch.setattr(inbox.live, "last_user_task_ts", lambda agent: 300.0)
    got = inbox.last_message(a, NtfyItem(id="n", time=200.0, title="t", message="m"))
    assert got["src"] == "user" and got["ts"] == 300.0 and got["text"].startswith("fix it now ")
    assert TOKEN not in got["text"]
    nt = NtfyItem(id="n", time=400.0, title="t", message="y" * 300)
    got = inbox.last_message(a, nt)
    assert got == {"text": "y" * 119 + "…", "src": "ntfy", "ts": 400.0}
    monkeypatch.setattr(inbox.live, "last_user_task", lambda agent: "")
    assert inbox.last_message(a, None) is None
    assert inbox.last_message(a, NtfyItem(id="n", time=1.0, title="only title", message=""))["text"] == "only title"


def test_row_meta_fields(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace as NS

    from pm.core.config import Settings
    from pm.notify import owner as ntfy_owner
    from pm.web import ntfypanel

    s = Settings(state_dir=tmp_path, tmux_socket="pmtest-unused")
    req = NS(app=NS(state=NS(settings=s)))
    now = time.time()
    inbox.mark_all(tmp_path, now=now - 1000)
    run = ag("claude-1", "running", now - 50)
    bad = Agent(id="codex-2", kind=AgentKind.CODEX, pane_id="%2", session_id="$1", pid=1, cwd="/x",
                state=AgentState.FAILED, state_since=now - 5)
    item = NtfyItem(id="n", time=now - 10, title="claude-1 - done", message="all done")
    feed = {"claude-1": ntfy_owner.AgentFeed(item, 3)}
    monkeypatch.setattr(ntfypanel.services, "pane_text", lambda settings, pane, n: "Error: rate limit hit\n")
    monkeypatch.setattr(ntfypanel.chat_stats, "ctx_pct", lambda a, locks=None: 42 if a.id == "claude-1" else None)
    monkeypatch.setattr(inbox.live, "last_user_task", lambda a: "")
    inbox._qcache.clear()
    inbox.mark_unread(tmp_path, inbox.agent_key(run))
    meta = asyncio.run(ntfypanel.row_meta(req, [run, bad], inbox.load(tmp_path), now, 3600, feed=feed))
    m = meta["claude-1"]
    assert m["unread"] is True and m["unread_n"] == 3 and m["ctx_pct"] == 42 and m["err"] == ""
    assert (m["last_src"], m["last_text"], m["last_ts"]) == ("ntfy", "all done", item.time)
    m = meta["codex-2"]
    assert m["err"] == "Error: rate limit hit" and m["q"] == "" and m["unread_n"] == 0 and m["last_src"] == ""
    assert m["ctx_pct"] is None and "tmux_href" not in m
    # without a feed it maps the feed itself (one pass)
    seen = []
    monkeypatch.setattr(ntfypanel, "agent_feeds", lambda settings, ids, store, now: seen.append(ids) or {})
    meta = asyncio.run(ntfypanel.row_meta(req, [run], inbox.load(tmp_path), now, 3600))
    assert seen == [{"claude-1"}] and meta["claude-1"]["unread_n"] == 0


def test_options_of_numbered_masked_capped():
    from helpers import TOKEN

    from pm.core.models import AskOption

    ask = Ask("choice", "Pick?", (AskOption("1", "Yes"), AskOption("2", f"No, use {TOKEN}"), AskOption("Escape", "Esc"),
                                  AskOption("3", "x" * 200), AskOption("4", "four"), AskOption("5", "five")))
    got = inbox.options_of(ask)
    assert [o["n"] for o in got] == [1, 2, 3, 4] and got[0] == {"n": 1, "label": "Yes"}
    assert TOKEN not in got[1]["label"] and len(got[2]["label"]) == 80
    assert inbox.options_of(None) == []
    form = (AskQuestion("Theme", "Which?", (AskOption("", "Light"), AskOption("", "Dark"))),)
    nav = (AskOption("BTab", "← Back"), AskOption("Tab", "Next →"))
    merged = Ask("choice", "Which?", nav, form, 0)
    assert inbox.options_of(merged) == [{"n": 1, "label": "Light"}, {"n": 2, "label": "Dark"}]
    assert inbox.options_of(Ask("choice", "Submit?", nav, form, 1)) == []  # submit step: no numbered options


def test_ask_of_from_real_pane_capture():
    text = (ASKQ / "cap_q2.txt").read_text()
    q, opts = inbox.ask_of(text)
    assert q == "Which theme should the demo use?"
    assert opts and opts[0]["n"] == 1 and all(o["label"] for o in opts) and len(opts) <= 4
    assert inbox.ask_of("") == ("", [])


def test_row_meta_model_and_options(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace as NS

    from pm.core.config import Settings
    from pm.web import ntfypanel

    s = Settings(state_dir=tmp_path, tmux_socket="pmtest-unused")
    req = NS(app=NS(state=NS(settings=s)))
    now = time.time()
    w = ag("claude-7", "waiting", now - 5)
    pane = "Edit x?\n│ Do you want to proceed? │\n│ ❯ 1. Yes │\n│   2. No │\n"
    reads = []
    monkeypatch.setattr(ntfypanel.services, "pane_text", lambda settings, p, n: reads.append(p) or pane)
    monkeypatch.setattr(ntfypanel.live, "chat_view", lambda settings, a, p: NS(asking=None))
    monkeypatch.setattr(ntfypanel.chat_stats, "ctx_pct", lambda a, locks=None: None)
    monkeypatch.setattr(inbox.live, "last_user_task", lambda a: "")
    monkeypatch.setattr(ntfypanel.agenthead, "live_pill", lambda a, **kw: "opus 4.6")
    inbox._qcache.clear()
    meta = asyncio.run(ntfypanel.row_meta(req, [w], inbox.load(tmp_path), now, 3600, feed={}))
    m = meta["claude-7"]
    assert m["q"] == "Do you want to proceed?" and m["options"] == [{"n": 1, "label": "Yes"}, {"n": 2, "label": "No"}]
    assert m["model"] == "opus 4.6"
    asyncio.run(ntfypanel.row_meta(req, [w], inbox.load(tmp_path), now, 3600, feed={}))
    assert reads == ["%1"]  # options are cached with the question: one pane read
    monkeypatch.setattr(ntfypanel.agenthead, "live_pill", lambda a, **kw: ntfypanel.agenthead.model_short(a.kind.value))
    meta = asyncio.run(ntfypanel.row_meta(req, [ag("claude-8", "idle")], inbox.load(tmp_path), now, 3600, feed={}))
    assert meta["claude-8"]["model"] == "" and meta["claude-8"]["options"] == []  # kind only: unknown model
