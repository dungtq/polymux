"""GET /a/<id>/items (web/routes/chatitems.py) and the live strip GET /a/<id>/chat that sits under it.

pm.chat.items / pm.chat.outbox / pm.agents.chatfeed are faked (same call shapes, docs/CHAT-SYNC.md)."""

import re
import time
from dataclasses import replace

from fastapi import HTTPException
from helpers import chat_env as env  # noqa: F401  (fixture)
from helpers import chat_view, login, set_chat_view

from pm.chat import items as chat_items
from pm.chat import strip as chat_strip
from pm.core.models import AgentState, ChatItem, OutboxMsg
from pm.web import chatctx


class FakeItems:
    """In-memory pm.chat.items: list_items / latest with the contract's cursor rules."""

    def __init__(self, items):
        self.items = list(items)  # seq order

    def list_items(self, settings, agent_id, *, after_rev=None, before_seq=None, limit=200):
        rows = [i for i in self.items if (before_seq is None or i.seq < before_seq)
                and (after_rev is None or i.rev > after_rev)]
        rows = sorted(rows, key=lambda i: i.rev)[:limit] if after_rev is not None else rows[-limit:]
        return sorted(rows, key=lambda i: i.seq)

    def latest(self, settings, agent_id):
        return max((i.seq for i in self.items), default=0), max((i.rev for i in self.items), default=0)


def item(seq, kind, rev=None, **kw):
    base = dict(seq=seq, rev=rev or seq, uid=f"u{seq}", conv="c", kind=kind, turn="t1", ts=1700000000.0 + seq)
    base.update(kw)
    return ChatItem(**base)


def ob(oid, state, text="later please", **kw):
    return OutboxMsg(id=oid, agent_id="claude-12", text=text, state=state, created=1700000100.0,
                     updated=1700000100.0, **kw)


def turn():
    """user prompt, reply 1 with two tools, reply 2 with one running tool, turn end."""
    return [
        item(1, "user", text="fix the **flaky** test"),
        item(2, "text", text="I'll read it first.", name="opus-5-5"),
        item(3, "tool", name="Read", line="tests/test_ntfy.py", status="ok", parent=2, ended=1700000003.5),
        item(4, "tool", name="Bash", line="uv run pytest -x", status="err", note="exit 1", parent=2,
             ended=1700000010.0),
        item(5, "text", text="Found it:\n\n- freeze the clock", name="opus-5-5"),
        item(6, "tool", name="Bash", line="uv run pytest -q", status="run", parent=5),
        item(7, "end", note="2 replies · 3 tool calls · 41s"),
    ]


def _use(monkeypatch, items=(), outbox=(), project="alpha", ended=False, state=None):
    fake = FakeItems(items)
    monkeypatch.setattr("pm.chat.items.list_items", fake.list_items)
    monkeypatch.setattr("pm.chat.items.latest", fake.latest)
    monkeypatch.setattr("pm.chat.outbox.pending", lambda settings, aid: list(outbox))
    watched = []
    monkeypatch.setattr("pm.agents.chatfeed.watch", watched.append)
    monkeypatch.setattr("pm.web.chatctx.get_chatlog", lambda: None)

    def lore(request, snap, aid):
        a = snap.agent(aid)
        if a is None:
            raise HTTPException(404, "unknown agent")
        return replace(a, project=project, **({"state": state} if state else {})), ended
    monkeypatch.setattr("pm.web.chatctx.live_or_ended", lore)
    monkeypatch.setattr("pm.chat.itemsync.sync", lambda settings, agent, *, idle=False: 0)
    return fake, watched


def test_full_page_groups_tools_under_their_reply(env, monkeypatch):
    s, c = env
    login(s, c)
    _, watched = _use(monkeypatch, turn())
    r = c.get("/a/claude-12/items")
    t = r.text
    assert r.status_code == 200 and r.headers["x-rev"] == "7" and r.headers["cache-control"] == "no-store"
    assert watched == ["claude-12"]
    # conversation order; each tool group right after the reply that started it
    order = [t.index(x) for x in ('id="i-1"', 'id="i-2"', 'id="g-2"', 'id="i-3"', 'id="i-4"', 'id="i-5"',
                                  'id="g-5"', 'id="i-6"', 'id="i-7"')]
    assert order == sorted(order)
    # folded by default: <details> without open; header counts per tool name, running count
    g2 = t[t.index('id="g-2"'):t.index('id="i-5"')]
    assert g2.startswith('id="g-2" class="ci-tg" data-seq="3" data-parent="2" data-turn="t1">')
    assert "<b>2</b> tool calls" in g2 and '<span class="tchip tf-read">Read <b>1</b></span>' in g2
    assert '<span class="tchip tf-shell">Bash <b>1</b></span>' in g2 and "running" not in g2
    g5 = t[t.index('id="g-5"'):t.index('id="i-7"')]
    assert "<b>1</b> tool call<" in g5 and "1 running" in g5 and " open" not in g5
    # rows: chip, masked line, status cell
    assert re.search(r'<li id="i-3" class="ci-tr" data-seq="3" data-name="Read" data-fam="read" data-status="ok">', t)
    assert "0.5s" in g2 and "exit 1 &middot; 6.0s" in g2 and 'class="ci-ts is-err"' in g2
    assert 'class="ci-ts is-run" data-start="1700000006.0"' in g5 and 'class="ci-spin"' in g5
    assert '<p id="i-7" class="ci-end" data-seq="7" data-turn="t1" data-dur="41s"><span>Turn done &middot; 2 replies · 3 tool calls · 41s</span></p>' in t


def test_reply_header_model_and_agent_id_and_actions(env, monkeypatch):
    s, c = env
    login(s, c)
    _use(monkeypatch, turn())
    t = c.get("/a/claude-12/items").text
    art = t[t.index('id="i-2"'):t.index('id="g-2"')]
    assert 'class="chat-bubble role-assistant ci-text" data-kind="claude"' in art
    assert '<strong>opus-5-5</strong> <span class="ci-aid muted">claude-12</span>' in art
    assert art.index("chat-copy") < art.index("data-canvas-open") < art.index("chat-text") < art.index("Continue&hellip;")
    assert '<footer class="ci-foot">' in art and art.index("ci-foot") > art.index("</header>")  # Continue… sits at the bottom of the reply
    assert "Replace chat" not in t and "data-nc-replace" not in t and "New chat from this" not in t
    assert "UTC+7" not in art or "stamp" in art
    user = t[t.index('id="i-1"'):t.index('id="i-2"')]
    assert "<strong>flaky</strong>" in user and "Continue&hellip;" not in user and "ob-st" not in user
    _use(monkeypatch, turn(), project="")
    assert "Continue&hellip;" not in c.get("/a/claude-12/items").text and "ci-foot" not in c.get("/a/claude-12/items").text
    _use(monkeypatch, turn(), ended=True)
    t = c.get("/a/claude-12/items").text
    assert "Continue&hellip;" not in t and "ci-foot" not in t and "data-canvas-open" not in t and "chat-copy" in t


def test_reply_without_model_name_falls_back(env, monkeypatch):
    s, c = env
    login(s, c)
    _use(monkeypatch, [item(1, "text", text="hi")])
    assert "<strong>Claude</strong>" in c.get("/a/claude-12/items").text


def test_poll_after_rev_new_and_changed(env, monkeypatch):
    s, c = env
    login(s, c)
    fake, _ = _use(monkeypatch, turn())
    r = c.get("/a/claude-12/items?after_rev=7")
    assert r.status_code == 200 and r.headers["x-rev"] == "7" and 'class="ci-items">\n</div>' in r.text
    assert "Load older" not in r.text and "ci-more" not in r.text
    fake.items[5] = replace(fake.items[5], rev=9, status="ok", ended=1700000020.0)  # tool 6 finished
    fake.items.append(item(8, "user", rev=8, text="next"))
    r = c.get("/a/claude-12/items?after_rev=7")
    assert r.headers["x-rev"] == "9" and "x-more" not in r.headers
    assert 'id="i-6"' in r.text and 'id="g-5"' in r.text and 'id="i-8"' in r.text and 'id="i-2"' not in r.text
    assert 'data-status="ok"' in r.text and "running" not in r.text


def test_poll_resets_when_the_page_holds_items_that_are_gone(env, monkeypatch):
    s, c = env
    login(s, c)
    fake, _ = _use(monkeypatch, turn())
    r = c.get("/a/claude-12/items?after_rev=7&lo=1&hi=7")
    assert r.status_code == 200 and "x-reset" not in r.headers
    # the conversation was re-read from its file: items dropped and re-created with new seqs
    fake.items = [replace(i, seq=i.seq + 10, rev=i.rev + 10) for i in turn()]
    r = c.get("/a/claude-12/items?after_rev=7&lo=1&hi=7")
    assert r.headers["x-reset"] == "1" and "<article" not in r.text
    assert "x-reset" not in c.get("/a/claude-12/items?after_rev=17&lo=11&hi=17").headers
    fake.items = turn()[:5]  # newest items gone (hi), oldest kept
    assert c.get("/a/claude-12/items?after_rev=7&lo=1&hi=7").headers["x-reset"] == "1"
    assert c.get("/a/claude-12/items?after_rev=7&lo=x").status_code == 400


def test_tools_before_any_text_group_per_turn(env, monkeypatch):
    s, c = env
    login(s, c)
    _use(monkeypatch, [item(1, "user", text="go"), item(2, "tool", name="Read", line="a"),
                       item(3, "tool", name="Read", line="b")])
    t = c.get("/a/claude-12/items").text
    gid = re.search(r'<details id="(g-0-[0-9a-f]{12})"', t).group(1)
    assert t.count("<details") == 1 and "<b>2</b> tool calls" in t
    assert chatctx.blocks([item(9, "tool", name="Read")])[0]["group"] == gid  # same turn: same group


def test_load_older_pages(env, monkeypatch):
    s, c = env
    login(s, c)
    rows = [item(i, "user" if i % 2 else "text", text=f"msg {i}") for i in range(1, 251)]
    _use(monkeypatch, rows)
    t = c.get("/a/claude-12/items").text
    page = chat_items.PAGE
    assert t.count("<article") == page and f'id="i-{250 - page + 1}"' in t and 'id="i-50"' not in t
    assert f'data-ci-older="{250 - page + 1}"' in t and t.index("Load older") < t.index('class="ci-items"')
    r = c.get(f"/a/claude-12/items?before={250 - page + 1}")
    assert r.text.count("<article") == 50 and "Load older" not in r.text and "ci-obs" not in r.text
    assert r.text.index('id="i-1"') < r.text.index('id="i-50"')


def test_bad_params_auth_and_404(env, monkeypatch):
    s, c = env
    _use(monkeypatch, turn())
    assert c.get("/a/claude-12/items", follow_redirects=False).status_code in (303, 401)
    login(s, c)
    for q in ("after_rev=x", "before=-1", "after_rev=１", "before=" + "9" * 30):
        assert c.get(f"/a/claude-12/items?{q}").status_code == 400, q
    assert c.get("/a/codex-99/items").status_code == 404


def test_empty_states(env, monkeypatch):
    s, c = env
    login(s, c)
    _use(monkeypatch)
    t = c.get("/a/claude-12/items").text
    assert "No messages yet." in t and "<article" not in t
    _use(monkeypatch, ended=True)
    assert "No transcript was found for this agent." in c.get("/a/claude-12/items").text
    _use(monkeypatch, outbox=[ob(1, "wait")])
    assert "No messages yet" not in c.get("/a/claude-12/items").text


def test_ended_agent_first_page_is_ingested_once(env, monkeypatch):
    """An ended agent has no other ingest before its page opens: an empty first page syncs once."""
    s, c = env
    login(s, c)
    fake, watched = _use(monkeypatch, ended=True)
    syncs = []

    def sync(settings, agent, *, idle=False):
        syncs.append((agent.id, idle))
        fake.items = turn()
        return 7
    monkeypatch.setattr("pm.chat.itemsync.sync", sync)
    r = c.get("/a/claude-12/items")
    assert syncs == [("claude-12", True)] and watched == ["claude-12"]
    assert 'id="i-2"' in r.text and "No transcript was found" not in r.text and r.headers["X-Rev"] == "7"
    c.get("/a/claude-12/items")  # items exist now: no second sync
    c.get("/a/claude-12/items?after_rev=7")
    assert len(syncs) == 1
    fake.items = []
    c.get("/a/claude-12/items?after_rev=0")  # polls never sync in the route (the feed does)
    assert len(syncs) == 1
    _use(monkeypatch)  # live agent: the feed ingests, the route does not
    monkeypatch.setattr("pm.chat.itemsync.sync", sync)
    c.get("/a/claude-12/items")
    assert len(syncs) == 1


def test_reply_actions_only_on_last_reply_of_a_finished_turn_css():
    """chat-items.js marks the newest reply before the last turn end; CSS hides New chat / Canvas elsewhere."""
    from pm.web.render import STATIC_DIR
    css = (STATIC_DIR / "css" / "chat-items.css").read_text()
    assert ".ci-items > .ci-text:not(.ci-done-last) :is(.chat-meta .cv-open, .ci-foot) { display: none; }" in css
    assert ":has(+ .ci-end)" not in css
    js = (STATIC_DIR / "js" / "chat-items.js").read_text()
    assert "rules.doneLast(" in js and "'ci-done-last'" in js
    assert ".ci-text .ci-foot .cm-cont button, .ci-text .chat-meta .cv-open { min-height: 36px; }" in css
    assert "@media (hover: hover) { .ci-tr:hover" in css and "margin: 0 -4px" not in css


def test_outbox_bubbles_states_and_actions(env, monkeypatch):
    s, c = env
    login(s, c)
    msgs = [ob(1, "saved"), ob(2, "wait", text="also **this**"), ob(3, "sending"), ob(4, "inpane"),
            ob(5, "queued"), ob(6, "failed", error="pane gone"), ob(7, "done")]
    _use(monkeypatch, turn(), outbox=msgs)
    t = c.get("/a/claude-12/items").text
    obs = t[t.index('class="ci-obs"'):]
    for oid, label in ((1, "Saved"), (2, "Waiting for turn end"), (3, "Sending…"), (4, "In pane"),
                       (5, "Queued in Claude"), (6, "Not sent &middot; pane gone"), (7, "In transcript ✓")):
        art = obs[obs.index(f'id="ob-{oid}"'):]
        art = art[:art.index("</article>")]
        assert f'data-ob="{oid}"' in art and label in art, oid
        acts = re.findall(r'data-ob-act="(\w+)"', art)
        assert acts == {2: ["now", "edit", "cancel"], 6: ["retry", "edit", "cancel"]}.get(oid, []), oid
    assert "<strong>this</strong>" in obs  # markdown in the user's own text
    assert "onclick" not in obs and "confirm(" not in obs


def test_outbox_linked_to_an_item_is_not_shown_twice(env, monkeypatch):
    s, c = env
    login(s, c)
    items = turn()
    items[0] = replace(items[0], outbox=7)
    _use(monkeypatch, items, outbox=[ob(7, "done"), ob(8, "cancelled")])
    t = c.get("/a/claude-12/items").text
    assert 'id="ob-7"' not in t and 'id="ob-8"' not in t
    user = t[t.index('id="i-1"'):t.index('id="i-2"')]
    assert 'data-ob="7"' in user and "In transcript ✓" in user


def test_pasted_wrapper_stripped_images_shown_copy_raw(env, monkeypatch):
    s, c = env
    login(s, c)
    d = s.state_dir / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    p1 = d / "2105586e23a3997081fbc0ff.png"
    p1.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    raw = f'<pasted_content id="84bf">\nfix via sub ag <b>x</b>\n{p1}\n</pasted_content id="84bf">'
    gone = d / "aaaaaaaaaaaaaaaaaaaaaaaa.png"
    _use(monkeypatch, [item(1, "user", text=raw), item(2, "text", text=f"see\n{gone}")])
    t = c.get("/a/claude-12/items").text
    user = t[t.index('id="i-1"'):t.index('id="i-2"')]
    body = user[user.index('class="chat-text'):]
    assert "pasted_content" not in body and "&lt;b&gt;x&lt;/b&gt;" in body
    assert body.count('<img src="/uploads/2105586e23a3997081fbc0ff.png"') == 1
    assert "pasted_content" in user  # the copy button keeps the raw text
    assert "<img" not in t[t.index('id="i-2"'):] and "aaaaaaaaaaaaaaaaaaaaaaaa.png" in t


# ---------------------------------------------------------------- live strip (#chat)
def test_strip_running_shows_tool_count_never_text(env, monkeypatch):
    s, c = env
    login(s, c)
    now = time.time()
    items = [replace(i, ts=now - 2) for i in turn()[:-1]]  # turn still open, the running tool is young
    _use(monkeypatch, items)
    set_chat_view(monkeypatch, chat_view(last_text="streaming **now**", state=AgentState.RUNNING))
    t = c.get("/a/claude-12/chat").text
    assert "working" in t and "3 tool calls" in t
    assert "streaming" not in t and "chat-bubble" not in t and "Found it" not in t
    assert "ci-wait" not in t
    set_chat_view(monkeypatch, chat_view(state=AgentState.IDLE))
    assert "working" not in c.get("/a/claude-12/chat").text


def test_strip_waiting_tool_shows_masked_pane_tail(env, monkeypatch):
    s, c = env
    login(s, c)
    now = time.time()
    items = [item(1, "user", text="go", ts=now - 60), item(2, "text", text="running", ts=now - 50),
             item(3, "tool", name="Bash", line="pytest", status="run", parent=2, ts=now - 30),
             item(4, "tool", name="Bash", line="sleep", status="run", note="background", parent=2, ts=now - 40)]
    _use(monkeypatch, items)
    pane = ("tests/a.py ....\ntests/b.py ....\nexport GH=ghp_" + "A" * 36 + "\nline4\nline5\nline6\n"
            "\x1b[31mred\x1b[0m\n" + "─" * 40 + "\n> \n" + "─" * 40 + "\n  ? for shortcuts\n")
    monkeypatch.setattr(chat_strip.tmuxctl, "capture_pane", lambda settings, pane_id, lines=120: pane)
    seen = []
    monkeypatch.setattr(chat_strip, "chat_view",
                        lambda settings, agent, pane_text=None: seen.append(pane_text) or chat_view(state=AgentState.RUNNING))
    t = c.get("/a/claude-12/chat").text
    assert seen == [pane]  # captured once, reused by chat_view
    assert "Waiting &middot; " in t and '<span class="tchip tf-shell">Bash</span>' in t
    assert f'data-start="{items[2].ts}"' in t  # the oldest non-background running tool
    tail = re.search(r'<pre class="ci-tail"[^>]*>(.*?)</pre>', t, re.S).group(1)
    assert tail.splitlines()[1:] == ["line4", "line5", "line6", "red"]
    assert "ghp_" not in tail and "\x1b" not in tail and "shortcuts" not in tail and "a.py" not in tail


def test_strip_short_or_background_tools_do_not_wait(env, monkeypatch):
    s, c = env
    login(s, c)
    now = time.time()
    _use(monkeypatch, [item(1, "user", text="go", ts=now - 9), item(2, "tool", name="Bash", status="run", ts=now - 3),
                       item(3, "tool", name="Bash", status="run", note="background", ts=now - 300)])
    monkeypatch.setattr(chat_strip.tmuxctl, "capture_pane", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    set_chat_view(monkeypatch, chat_view(state=AgentState.RUNNING))
    t = c.get("/a/claude-12/chat").text
    assert "ci-wait" not in t and "2 tool calls" in t


def test_strip_ended_never_reads_pane(env, monkeypatch):
    s, c = env
    login(s, c)
    _use(monkeypatch, turn(), ended=True)
    seen = []
    monkeypatch.setattr(chat_strip, "chat_view",
                        lambda settings, agent, pane_text=None: seen.append(pane_text) or chat_view())
    t = c.get("/a/claude-12/chat").text
    assert seen == [""] and "hx-get" not in t


def test_strip_lists_running_background_tasks(env, monkeypatch):
    s, c = env
    login(s, c)
    now = time.time()
    bg = [item(9, "tool", name="Agent", line="review the diff", status="run", note="background", ts=now - 125),
          item(4, "tool", name="Bash", line="make watch", status="run", note="background", ts=now - 30)]
    _use(monkeypatch, [replace(i, ts=now - 2) for i in turn()])  # turn ended: no open-turn tools
    asked = []
    monkeypatch.setattr("pm.chat.items.running_background",
                        lambda con, aid, conv, limit=10: asked.append((aid, conv)) or bg)
    set_chat_view(monkeypatch, chat_view(state=AgentState.IDLE))
    t = c.get("/a/claude-12/chat").text
    assert asked == [("claude-12", "c")]
    rows = re.findall(r"<li class=\"st\">(.*?)</li>", t, re.S)
    assert len(rows) == 1  # the background sub-agent has its own sub-agent row: not repeated here
    assert "review the diff" not in t
    assert "Background" in rows[0] and ">Bash</span>" in rows[0] and "make watch" in rows[0]
    assert f'data-start="{bg[1].ts}"' in rows[0]
    assert "Found it" not in t and "flaky" not in t  # never message text


def test_strip_no_background_rows_when_ended(env, monkeypatch):
    s, c = env
    login(s, c)
    _use(monkeypatch, turn(), ended=True)
    monkeypatch.setattr("pm.chat.items.running_background", lambda *a, **k: [
        item(9, "tool", name="Bash", line="make watch", status="run", note="background")])
    set_chat_view(monkeypatch, chat_view())
    t = c.get("/a/claude-12/chat").text
    assert "Background" not in t and "make watch" not in t
