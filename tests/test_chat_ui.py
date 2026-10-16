import re
from dataclasses import replace
from types import SimpleNamespace

from helpers import agent_page_csrf, chat_view, login, png_bytes, set_chat_view
from helpers import chat_env as env  # noqa: F401  (fixture)

from pm.core.models import AgentState, Ask, AskOption, ChatItem, ChatView
from pm.tmux import ctl as tmuxctl
from pm.tmux.ctl import SEP
from pm.web.plugins import Plugin
from pm.web.render import STATIC_DIR, TEMPLATE_DIR
from pm.web.routes import chat as routes_chat


def test_auth_and_404(env, monkeypatch):
    s, c = env
    set_chat_view(monkeypatch, chat_view())
    r = c.get("/a/claude-12/chat", follow_redirects=False)
    assert r.status_code in (303, 401)
    login(s, c)
    assert c.get("/a/codex-99/chat").status_code == 404
    assert c.get("/a/claude-12/chat").status_code == 200


def _reply(monkeypatch, text, kind="text"):
    it = ChatItem(seq=1, rev=1, uid="u:0", conv="c", kind=kind, turn="u", ts=1.0, text=text)
    monkeypatch.setattr("pm.chat.items.list_items", lambda *a, **k: [it])


def test_bubble_escaped_and_long(env, monkeypatch):
    s, c = env
    login(s, c)
    _reply(monkeypatch, "<script>alert(1)</script>\nline2")
    t = c.get("/a/claude-12/items").text
    assert "&lt;script&gt;" in t and "<script>" not in t
    assert "<details>" not in t
    _reply(monkeypatch, "a" * 15000)
    r = c.get("/a/claude-12/items")
    assert "<details" not in r.text and "Show more" not in r.text
    assert "a" * 15000 in r.text
    assert r.headers["cache-control"] == "no-store"
    set_chat_view(monkeypatch, chat_view(last_text="<script>alert(1)</script>", state=AgentState.RUNNING))
    assert "alert(1)" not in c.get("/a/claude-12/chat").text  # the live strip never shows message text


def test_markdown_in_bubble(env, monkeypatch):
    s, c = env
    login(s, c)
    for kind in ("text", "user"):
        _reply(monkeypatch, "# Title\n\n- one\n- **two**\n\n```\ncode\n```", kind)
        t = c.get("/a/claude-12/items").text
        assert "<h1>Title</h1>" in t and "<strong>two</strong>" in t and "<pre><code>" in t, kind


def test_changed_message_not_suppressed(env, monkeypatch):
    s, c = env
    login(s, c)
    set_chat_view(monkeypatch, chat_view(queued="first"))
    r1 = c.get("/a/claude-12/chat")
    h = r1.headers["etag"].strip('"')
    assert c.get(f"/a/claude-12/chat?h={h}").status_code == 204
    set_chat_view(monkeypatch, chat_view(queued="second"))
    r2 = c.get(f"/a/claude-12/chat?h={h}", headers={"If-None-Match": r1.headers["etag"]})
    assert r2.status_code == 200 and "second" in r2.text
    assert r2.headers["etag"] != r1.headers["etag"]
    set_chat_view(monkeypatch, chat_view(last_text="second", state=AgentState.RUNNING))
    r3 = c.get("/a/claude-12/chat", headers={"If-None-Match": r2.headers["etag"]})
    assert r3.status_code == 200
    ask = Ask("choice", "Q?", (AskOption("1", "Yes"),))
    set_chat_view(monkeypatch, chat_view(last_text="second", state=AgentState.RUNNING, asking=ask))
    r4 = c.get("/a/claude-12/chat", headers={"If-None-Match": r3.headers["etag"]})
    assert r4.status_code == 200 and "Needs you" in r4.text


def test_ask_text_full(env, monkeypatch):
    s, c = env
    login(s, c)
    set_chat_view(monkeypatch, chat_view(asking=Ask("choice", "q" * 5000, (AskOption("1", "Yes"),))))
    t = c.get("/a/claude-12/chat").text
    assert "q" * 5000 in t and "<details" not in t


def test_pane_fragment_pin_markup(env):
    s, c = env
    login(s, c)
    t = c.get("/a/claude-12/pane").text
    assert "data-pin" in t and "data-pane-jump" in t and 'id="pane"' in t
    assert c.get("/a/claude-12/pane").headers["cache-control"] == "no-store"
    page = c.get("/a/claude-12").text
    assert "data-pin" in page
    js = (STATIC_DIR / "js/chat.js").read_text()
    assert "htmx:beforeSwap" in js and "scrollHeight" in js


def test_asking_card(env, monkeypatch):
    s, c = env
    login(s, c)
    ask = Ask("choice", "Proceed <b>?</b>", (AskOption("1", "Yes"), AskOption("2", "No")))
    set_chat_view(monkeypatch, chat_view(state=AgentState.WAITING, asking=ask))
    t = c.get("/a/claude-12/chat").text
    assert "Needs you" in t and "Proceed &lt;b&gt;?&lt;/b&gt;" in t
    forms = re.findall(r'<form[^>]*action="/a/claude-12/key"[^>]*>.*?</form>', t, re.S)
    assert len(forms) == 2
    assert 'name="csrf" value="' in forms[0] and 'value=""' not in forms[0].split("csrf")[1][:20]
    assert 'name="key" value="1"' in forms[0] and ">Yes<" in forms[0]
    assert 'name="key" value="2"' in forms[1]
    assert 'action="/a/claude-12/message"' in t and 'name="text"' in t
    assert "chat-bubble" not in t  # the strip holds the ask card, never message bubbles


def test_permission_has_no_free_text(env, monkeypatch):
    s, c = env
    login(s, c)
    set_chat_view(monkeypatch, chat_view(asking=Ask("permission", "Run ls?", (AskOption("y", "Allow"),))))
    assert "chat-answer" not in c.get("/a/claude-12/chat").text


def test_etag_no_change(env, monkeypatch):
    s, c = env
    login(s, c)
    set_chat_view(monkeypatch, chat_view())
    r = c.get("/a/claude-12/chat")
    etag = r.headers["etag"]
    assert c.get("/a/claude-12/chat", headers={"If-None-Match": etag}).status_code == 204
    h = etag.strip('"')
    assert c.get(f"/a/claude-12/chat?h={h}").status_code == 204
    assert f"?h={h}" in r.text
    set_chat_view(monkeypatch, chat_view(last_text="changed"))
    assert c.get(f"/a/claude-12/chat?h={h}").status_code == 200


def test_empty_and_running(env, monkeypatch):
    s, c = env
    login(s, c)
    set_chat_view(monkeypatch, ChatView(agent_id="claude-12", state=AgentState.IDLE))
    t = c.get("/a/claude-12/chat").text
    assert "working" not in t
    assert "No messages yet." in c.get("/a/claude-12/items").text
    set_chat_view(monkeypatch, chat_view(state=AgentState.RUNNING))
    assert "working" in c.get("/a/claude-12/chat").text


def test_agent_page_tabs(env):
    s, c = env
    login(s, c)
    t = c.get("/a/claude-12").text
    assert re.search(r'id="tab-chat"[^>]*checked', t)
    assert not re.search(r'id="tab-pane"[^>]*checked', t)
    assert "Chat</label>" in t and "Pane</label>" in t
    assert 'hx-get="/a/claude-12/chat"' in t and 'id="pane"' in t and 'data-composer="live"' in t
    assert not re.search(r"<script(?![^>]*\bsrc=)", t)
    assert "<style" not in t and not re.search(r"\son[a-z]+=", t) and 'style="' not in t


def pad_and_tools(t):
    return t[t.index('id="tab-pane"'):t.index('data-composer="live"')]


def test_keypad_and_composer(env):
    from pm.agents.message import QUICK_KEYS

    s, c = env
    login(s, c)
    t = c.get("/a/claude-12").text
    k0 = t.index('id="keypad-wrap"'); pad = t[k0:t.index("</details>", k0)]
    forms = re.findall(r'<form class="keyform".*?</form>', pad, re.S)
    assert len(forms) == len(QUICK_KEYS)
    for f, _k in zip(sorted(forms, key=lambda f: 0), forms):
        assert 'hx-post="/a/claude-12/key"' in f and 'action="/a/claude-12/key"' in f
        assert 'hx-sync=' in f and 'name="csrf" value="' in f
    got = set(re.findall(r'name="key" value="([^"]+)"', pad))
    assert got == set(QUICK_KEYS)
    for g in ("answers", "navigation", "editing", "control"):
        assert f'data-group="{g}"' in pad
    assert 'id="keypad-wrap" class="keypad-wrap" open' in t
    # composer once, posts text + csrf to /message; its answers land in its own notice line (no second #flash)
    assert t.count('action="/a/claude-12/message"') == 1
    send = re.search(r'<form class="cform" data-cmp-form.*?</form>', t, re.S).group(0)
    assert 'name="text"' in send and 'name="csrf" value="' in send and 'hx-target="[data-cmp-flash]"' in send
    assert t.count('id="flash"') == 1 and 'class="flash-inline" data-cmp-flash' in t
    # chat composer: one box; textarea + icon toolbar (Keys toggle) + the green square Send, all inside .cbox
    comp = t[t.index('data-composer="live"'):t.index("</section>", t.index('data-composer="live"'))]
    assert "<select" not in send and 'class="morekeys"' not in comp and ">Send<" not in comp and ">Keys<" not in comp
    box = comp[comp.index('class="cbox"'):comp.index("</form>", comp.index('class="cbox"'))]
    for tok in ("<textarea", "data-cmp-input", "data-keys", 'aria-label="Keys: show quick keys and the pane"',
                'title="Keys: show quick keys and the pane"', 'title="Send message (Enter)"', 'class="csend tip tip-r" data-cmp-send',
                'type="submit"', 'aria-label="Send message (Enter)"', 'data-stop-title="Interrupt agent (sends Ctrl-C, then Esc)"'):
        assert tok in box
    # Interrupt: one place in the composer (the Send button turns into Stop), plus the Pane tab's own tool row
    assert "interrupt" not in box and 'class="agent-actions mb-act"' in comp
    assert "Interrupt</button>" not in comp.split('class="mrow"')[1].split("</form>")[0]
    stop = re.search(r'<form class="cstop" data-cmp-stop[^>]*>', comp).group(0)
    assert 'hx-post="/a/claude-12/interrupt"' in stop and 'hx-confirm="Interrupt ' in stop and " hidden" in stop
    assert t.count('action="/a/claude-12/interrupt"') == 2  # the composer's Stop and the Pane tab tools
    assert comp.index("</div>", comp.index('class="cbar"')) < comp.index("</form>", comp.index('class="cbar"'))
    # pane tab keeps tools
    pt = pad_and_tools(t)
    assert re.search(r'<form[^>]*/interrupt"[^>]*hx-confirm="Interrupt [^"]*Ctrl-C/Esc[^"]*"[^>]*data-confirm=', pt)
    assert 'action="/a/claude-12/interrupt"' in pad_and_tools(t)
    # no geometric-shape glyphs, no inline script/style
    from pm.web.render import STATIC_DIR as sd
    for name in ("agent.html", "_chat.html", "_pane.html"):
        src = (TEMPLATE_DIR / name).read_text()
        assert not any(0x25A0 <= ord(ch) <= 0x25FF for ch in src)
    assert not re.search(r"<script(?![^>]*\bsrc=)", t) and "<style" not in t and 'style="' not in t


def test_composer_rows_and_autohide_assets(env):
    s, c = env
    login(s, c)
    t = c.get("/a/claude-12").text
    ta = re.search(r"<textarea[^>]*>", t).group(0)
    assert 'rows="2"' in ta and "data-cmp-input" in ta  # two lines, then it grows (composer.js; no CSS field-sizing)
    # no separate "Message" handle anywhere: re-show is scroll-up / bottom / tap
    assert "msgbar-handle" not in t and ">Message<" not in t and "has-msgbar" not in t
    css = (STATIC_DIR / "css/composer.css").read_text()
    for sel in ("body.msgbar-hidden .composer--dock", "prefers-reduced-motion", "pointer:coarse", "--msgbar-h",
                "body.kbd .composer--dock", "var(--kb"):
        assert sel in css
    assert "field-sizing" not in css and "will-change" not in css  # a transformed bar would trap the fixed menus
    for name in ("chat.css", "agent.css", "base.css", "chrome.css", "shell.css", "builder.css"):
        other = (STATIC_DIR / "css" / name).read_text()
        assert "msgbar-handle" not in other and ".msgbar" not in other and "has-msgbar" not in other, name
    # hide/keyboard rules only inside the phone/touch media query
    assert css.index("body.msgbar-hidden .composer--dock") > css.index("@media (pointer:coarse),(max-width:699px)")
    js = (STATIC_DIR / "js/composer-dock.js").read_text()
    for tok in ("msgbar-hidden", "scrollingElement", "matchMedia", "visualViewport", "touchmove",
                "passive: true", "HIDE_PX = 24", "KBD_PX = 100", "blur()", "composer--dock", "--msgbar-h"):
        assert tok in js
    assert "msgbar-handle" not in js and "lockUntilUp" not in js
    assert not re.search(r"\bon[a-z]+\s*=\s*[\"']", js.replace("addEventListener", ""))


def test_pane_fragment_204_unchanged_200_changed(env, monkeypatch):
    s, c = env
    login(s, c)
    r = c.get("/a/claude-12/pane")
    h = r.headers["etag"].strip('"')
    assert c.get(f"/a/claude-12/pane?h={h}").status_code == 204
    assert c.get("/a/claude-12/pane", headers={"If-None-Match": r.headers["etag"]}).status_code == 204
    monkeypatch.setattr(tmuxctl, "capture_pane", lambda settings, pane, lines=200: "y")
    assert c.get(f"/a/claude-12/pane?h={h}").status_code == 200


def test_pane_js_updates_in_place():
    js = (STATIC_DIR / "js/chat.js").read_text()
    assert "shouldSwap = false" in js and "nodeValue" in js and "updatePaneInPlace" in js


def test_chat_swap_has_no_settle_delay(env):
    s, c = env
    login(s, c)
    assert "swap:0ms settle:0ms" in c.get("/a/claude-12").text


def test_terminal_ids_second_window(env, monkeypatch):
    s, c = env
    login(s, c)
    other = ["$1", "work", "1", "@0", "0", "first", "0", "%11", "1", "bash", "/tmp", "0", "t"]
    mine = ["$1", "work", "1", "@7", "1", "main", "1", "%12", "4242", "claude", "/tmp", "1", "t"]
    monkeypatch.setenv("FAKE_TMUX_OUT", SEP.join(other) + "\n" + SEP.join(mine) + "\n")
    seen = {}
    real = routes_pages_html()

    def spy(request, name, **ctx):
        if name == "agent.html":
            seen.update(ctx)
        return real(request, name, **ctx)

    monkeypatch.setattr("pm.web.routes.pages.html", spy)
    assert c.get("/a/claude-12").status_code == 200
    assert seen["term_session_id"] == "$1" and seen["term_window_id"] == "@7"
    assert seen["term_back"] == "/a/claude-12"


def routes_pages_html():
    from pm.web.routes import pages as routes_pages
    return routes_pages.html


def test_copy_button_and_composer_assets(env):
    s, c = env
    login(s, c)
    css = (STATIC_DIR / "css/composer.css").read_text()
    for tok in (".cbox", ".cbar", ".csend", "40vh", "focus-within", ".csend.is-stop"):
        assert tok in css
    assert ".chat-copy" in (STATIC_DIR / "css/chat.css").read_text()
    js = (STATIC_DIR / "js/chat.js").read_text() + (STATIC_DIR / "js/composer.js").read_text()
    for tok in ("pm.copy(", "data-raw", "htmx:afterSettle", "[data-keys]", "data-cmp-send"):
        assert tok in js
    core = (STATIC_DIR / "js/core.js").read_text()  # the one clipboard helper (pm.copy)
    assert "navigator" in core and ".clipboard" in core and "writeText" in core and "execCommand('copy')" in core
    assert "Enter" in js and "requestSubmit" in js


def test_chat_fragment_copy_button_escapes_raw():
    from pm.web.render import env as jenv
    it = ChatItem(seq=3, rev=3, uid="u:0", conv="c", kind="text", turn="u", ts=1.0, text='a "q" <b>&</b>\n**md**')
    agent = type("A", (), {"id": "claude-12", "kind": type("K", (), {"value": "claude"}), "project": None})
    from pm.web.chatctx import blocks
    out = jenv.get_template("_chatitems.html").render(mode="poll", agent=agent, ended=False, blocks=blocks([it]),
                                                      outbox=None, model_name="Claude", csrf="t", now=2.0)
    assert "<strong>Claude</strong>" in out
    assert 'data-raw="a &#34;q&#34; &lt;b&gt;&amp;&lt;/b&gt;\n**md**"' in out
    assert 'aria-label="Copy message"' in out and 'title="Copy message"' in out and 'data-tip="Copy message"' in out and "<b>&amp;" not in out


# ---------------------------------------------------------------- md panel / image / task


def test_md_view_any_path_but_no_dotdirs(env, monkeypatch, tmp_path):
    s, c = env
    login(s, c)
    proj = tmp_path / "dev" / "alpha"
    (proj / "docs").mkdir()
    (proj / "docs" / "a.md").write_text("# Hello\n\n<script>x</script>\n")
    (proj / ".hidden").mkdir()
    (proj / ".hidden" / "s.md").write_text("secret")
    (proj / "notes.txt").write_text("x")
    (tmp_path / "outside.md").write_text("nope")
    (proj / "link.md").symlink_to(tmp_path / "outside.md")
    r = c.get("/a/claude-12/md", params={"path": "docs/a.md"})
    assert r.status_code == 200 and "<h1>Hello</h1>" in r.text and "<script>" not in r.text
    assert c.get("/a/claude-12/md", params={"path": str(proj / "docs/a.md")}).status_code == 200
    for ok in ("../../outside.md", str(tmp_path / "outside.md"), "link.md"):  # any .md anywhere
        assert c.get("/a/claude-12/md", params={"path": ok}).status_code == 200, ok
    for bad in (".hidden/s.md", "notes.txt", "docs/missing.md", "/etc/passwd", ""):
        assert c.get("/a/claude-12/md", params={"path": bad}).status_code == 404, bad


def test_md_relative_ref_found_among_the_files_the_chat_named(env, monkeypatch, tmp_path):
    """#249: an agent in dev/alpha wrote `SECURITY.md` / `docs/CODE.md` about files of another repo it worked on."""
    s, c = env
    login(s, c)
    other = tmp_path / "dev" / "polymux"
    (other / "docs").mkdir(parents=True)
    (other / "SECURITY.md").write_text("# Security\n")
    (other / "docs" / "CODE.md").write_text("# Code\n")
    (other / ".git").mkdir()
    (other / ".git" / "HIDDEN.md").write_text("no")
    items = [ChatItem(seq=1, rev=1, uid="t1", conv="c", kind="tool", turn="u", ts=1.0, name="Read",
                      line=str(other / "SECURITY.md")),
             ChatItem(seq=2, rev=2, uid="t2", conv="c", kind="text", turn="u", ts=2.0,
                      text=f"Edited `{other / 'docs' / 'CODE.md'}` and {other / '.git' / 'HIDDEN.md'}.")]
    calls = []

    def list_items(settings, agent_id, **kw):
        calls.append((agent_id, kw.get("before_seq"), kw.get("limit")))
        return items

    monkeypatch.setattr("pm.chat.items.list_items", list_items)
    r = c.get("/a/claude-12/md", params={"path": "SECURITY.md"})
    assert r.status_code == 200 and "<h1>Security</h1>" in r.text
    assert f'data-path="{other / "SECURITY.md"}"' in r.text
    assert calls == [("claude-12", None, 1000)]  # one page: fewer than 1000 items
    assert "<h1>Code</h1>" in c.get("/a/claude-12/md", params={"path": "docs/CODE.md"}).text
    assert c.get("/a/claude-12/md", params={"path": "HIDDEN.md"}).status_code == 404  # hidden stays refused
    calls.clear()
    (tmp_path / "dev" / "alpha" / "SECURITY.md").write_text("# Here\n")
    assert "<h1>Here</h1>" in c.get("/a/claude-12/md", params={"path": "SECURITY.md"}).text
    assert calls == []  # the agent's own folder has it: the chat is not read


def test_md_relative_ref_in_a_deleted_worktree_or_below_the_folder(env, monkeypatch, tmp_path):
    """#249 live case: the chat did `cd <dev>/polymux-wt-dt; cat SECURITY.md` (worktree since deleted, the file is
    in the polymux project now; beta has one too), and named RESUME-AGENTS.md (in alpha/docs) bare."""
    s, c = env
    dev = tmp_path / "dev"
    for f, body in (("polymux/SECURITY.md", "# Poly\n"), ("beta/SECURITY.md", "# Beta\n"),
                    ("alpha/docs/RESUME-AGENTS.md", "# Resume\n"), ("polymux/docs/dt/CODE.md", "# Code\n"),
                    ("alpha/x/DUP.md", "a"), ("alpha/y/DUP.md", "b"), ("polymux/.git/H.md", "no")):
        (dev / f).parent.mkdir(parents=True, exist_ok=True)
        (dev / f).write_text(body)
    login(s, c)
    items = [ChatItem(seq=1, rev=1, uid="t1", conv="c", kind="tool", turn="u", ts=1.0, name="Bash",
                      line=f"cd {dev}/polymux-wt-dt; sed -n 1,12p README.md; cat SECURITY.md")]
    calls = []

    def list_items(settings, agent_id, **kw):
        calls.append(agent_id)
        return items

    monkeypatch.setattr("pm.chat.items.list_items", list_items)
    r = c.get("/a/claude-12/md", params={"path": "SECURITY.md"})
    assert r.status_code == 200 and "<h1>Poly</h1>" in r.text
    assert calls == ["claude-12"]  # the chat is read once for the mentions and the folders
    assert "<h1>Code</h1>" in c.get("/a/claude-12/md", params={"path": "docs/dt/CODE.md"}).text  # one project has it
    assert "<h1>Resume</h1>" in c.get("/a/claude-12/md", params={"path": "RESUME-AGENTS.md"}).text
    for bad in ("DUP.md", "H.md", ".git/H.md", "pasted-text.md"):
        assert c.get("/a/claude-12/md", params={"path": bad}).status_code == 404, bad
    items.clear()
    assert c.get("/a/claude-12/md", params={"path": "SECURITY.md"}).status_code == 404  # two projects: no guess


def test_md_long_file_not_truncated(env, tmp_path):
    s, c = env
    login(s, c)
    proj = tmp_path / "dev" / "alpha"
    (proj / "docs").mkdir(exist_ok=True)
    (proj / "docs" / "long.md").write_text("".join(f"line {i} of the plan\n\n" for i in range(3000)) + "THE END\n")
    r = c.get("/a/claude-12/md", params={"path": "docs/long.md"})
    assert "THE END" in r.text and "Truncated" not in r.text  # ~60 KB used to be cut at 20000 chars


def test_md_requires_login(env):
    s, c = env
    r = c.get("/a/claude-12/md", params={"path": "x.md"}, follow_redirects=False)
    assert r.status_code in (303, 401)


def test_image_upload_saved_and_limits(env):
    s, c = env
    login(s, c)
    tok = agent_page_csrf(c)
    r = c.post("/a/claude-12/image", files={"image": ("a.png", png_bytes(), "image/png")},
               headers={"origin": "https://pm.example.test", "x-csrf-token": tok})
    assert r.status_code == 200, r.text
    m = re.search(r'data-image-path="([^"]+)"', r.text)
    p = m.group(1)
    assert p.startswith(str(s.state_dir / "uploads")) and open(p, "rb").read() == png_bytes()
    bad = c.post("/a/claude-12/image", files={"image": ("a.png", b"not an image", "image/png")},
                 headers={"origin": "https://pm.example.test", "x-csrf-token": tok})
    assert bad.status_code == 415
    big = c.post("/a/claude-12/image", files={"image": ("a.png", png_bytes() + b"0" * (10 * 1024 * 1024), "image/png")},
                 headers={"origin": "https://pm.example.test", "x-csrf-token": tok})
    assert big.status_code == 413
    nocsrf = c.post("/a/claude-12/image", files={"image": ("a.png", png_bytes(), "image/png")},
                    headers={"origin": "https://pm.example.test"})
    assert nocsrf.status_code == 403
    audit = s.audit_file.read_text()
    assert "agent_image" in audit and "not an image" not in audit


def test_chat_task_quick_answers_and_stamp(env, monkeypatch):
    s, c = env
    login(s, c)
    ask = Ask("question", "Pick one:\n1. a\n2. b", ())
    set_chat_view(monkeypatch, chat_view(task="Fix the bug", asking=ask, last_ts=1700000000.0))
    t = c.get("/a/claude-12/chat").text
    assert t.count('name="text" value=') == 3 and 'value="1"' in t and 'value="y"' not in t
    set_chat_view(monkeypatch, chat_view(asking=Ask("question", "Continue?", ())))
    t = c.get("/a/claude-12/chat").text
    assert 'value="y"' in t and 'value="n"' in t


def test_agent_page_has_panel_and_buttons(env):
    s, c = env
    login(s, c)
    t = c.get("/a/claude-12").text
    assert 'id="mdpane"' in t and "data-attach" in t and 'class="agent-actions"' in t
    assert "data-md-full" in t and "data-md-close" in t


def test_chat_has_no_last_request_card(env, monkeypatch):
    """The items above #chat hold every user message: no separate "Last request" card any more."""
    s, c = env
    login(s, c)
    set_chat_view(monkeypatch, chat_view(task="Fix the bug"))
    t = c.get("/a/claude-12/chat").text
    assert "Last request" not in t and "chat-req" not in t and "hello" not in t  # no message text in the strip


def test_chat_full_transcript_link_once_in_log(env, monkeypatch):
    s, c = env
    login(s, c)
    set_chat_view(monkeypatch, chat_view(task="Fix the bug"))
    monkeypatch.setattr(c.app.state, "plugins", [Plugin("tr", SimpleNamespace(
        agent_transcript_url=lambda a: f"https://viewer.example/sessions/{a.session_uuid}"))])
    monkeypatch.setattr("pm.web.chatctx.live_or_ended", lambda r, snap, aid: (replace(snap.agent(aid), session_uuid="u1"), False))
    assert "Full Transcript" not in c.get("/a/claude-12/chat").text
    t = c.get("/a/claude-12/items").text
    assert t.count("Full Transcript") == 1 and t.index("Full Transcript") < t.index('class="ci-items"')
    assert "Full Transcript" not in c.get("/a/claude-12/items?after_rev=0").text


def test_composer_paste_image_handler_shipped():
    js = (STATIC_DIR / "js" / "composer-attach.js").read_text()
    assert "addEventListener('paste'" in js and "uploadOne(" in js and "navigator.clipboard.read" in js


def test_upload_accepts_pdf_and_text_rejects_others(env):
    s, c = env
    login(s, c)
    h = {"origin": "https://pm.example.test", "x-csrf-token": agent_page_csrf(c)}
    def post(name, data):
        return c.post("/a/claude-12/image", files={"image": (name, data, "application/octet-stream")}, headers=h)
    ok = post("plan.md", b"# plan\n")
    assert ok.status_code == 200 and ".md" in ok.text
    assert post("doc.pdf", b"%PDF-1.4 x").status_code == 200
    assert post("run.sh", b"echo hi").status_code == 415 and post("a.txt", b"\x00\x01").status_code == 415


def test_sniff_file_types():
    from pm.web.routes.chat import sniff_file
    assert sniff_file(b"%PDF-1.7 x", "a.bin") == "pdf"
    assert sniff_file(b"# hi\n", "notes.MD") == "md"
    assert sniff_file(b"a\x00b", "x.txt") is None and sniff_file(b"\xff\xfe\xfa", "x.txt") is None
    assert sniff_file(b"echo hi", "run.sh") is None and sniff_file(b"x", "a.png") is None


def test_outbox_and_dnd_handlers_shipped():
    js = (STATIC_DIR / "js" / "chat-send.js").read_text() + (STATIC_DIR / "js" / "composer-attach.js").read_text()
    assert "chatItems.addOutbox" in js and "shouldSwap = false" in js  # the POST answers with the bubble
    assert "addEventListener('drop'" in js and "readAsDataURL" in js
    assert "outbox-item" not in js and "placePending" not in js  # no client-only optimistic bubble any more


def test_stats_route_and_tabs_row_slot(env, monkeypatch):
    from pm.chat.sources.common import Stats
    s, c = env
    login(s, c)
    assert 'id="chat-stats"' in c.get("/a/claude-12").text
    monkeypatch.setattr("pm.chat.stats.stats_for", lambda agent, settings=None: Stats(turns=7, input=1000, output=2500, cache_write=500, cache_read=9))
    t = c.get("/a/claude-12/stats").text
    assert "4.0k tokens" in t and "7 turns" in t and "Cache read" in t and "Token statistics" in t
    monkeypatch.setattr("pm.chat.stats.stats_for", lambda agent, settings=None: None)
    assert "tokens" not in c.get("/a/claude-12/stats").text


def test_stats_levels_and_panel(env, monkeypatch):
    from pm.chat.sources.common import Stats
    s, c = env
    login(s, c)
    for ctx, lvl in ((50_000, "ok"), (130_000, "warn"), (170_000, "red")):
        monkeypatch.setattr("pm.chat.stats.stats_for", lambda a, settings=None, ctx=ctx: Stats(turns=3, input=ctx, ctx=ctx))
        t = c.get("/a/claude-12/stats").text
        assert f"lvl-{lvl}" in t and "Context window" in t
    assert "/compact" in t and "Nearly full" in t


def test_context_meter_has_compact_and_clear_when_full(env, monkeypatch):
    from pm.chat.sources.common import Stats
    s, c = env
    login(s, c)
    set_chat_view(monkeypatch, chat_view())
    monkeypatch.setattr("pm.chat.stats.stats_for", lambda a, settings=None: Stats(ctx=190_000, model="x"))
    t = c.get("/a/claude-12/stats").text
    summ = t[t.index("<summary"):t.index("</summary>")]
    # small buttons right after the meter, before "190k / 200k"; htmx posts with CSRF, confirmed in-page
    assert summ.index('class="ts-bar"') < summ.index('class="ts-act"') < summ.index("ts-abs")
    assert '"text": "/compact"}' in summ and "/clear" not in summ and ">Clear<" not in summ
    assert '"csrf": "' in summ and summ.count('hx-post="/a/claude-12/message"') == 1
    assert "<form" not in summ  # no form inside <summary>
    monkeypatch.setattr("pm.chat.stats.stats_for", lambda a, settings=None: Stats(ctx=130_000))
    assert 'class="ts-act"' not in c.get("/a/claude-12/stats").text  # amber: only red shows them (as the old banner)
    monkeypatch.setattr("pm.chat.stats.stats_for", lambda a, settings=None: Stats(ctx=1_000))
    assert 'class="ts-act"' not in c.get("/a/claude-12/stats").text
    # the live strip carries no context banner any more
    monkeypatch.setattr("pm.chat.stats.stats_for", lambda a, settings=None: Stats(ctx=190_000))
    t = c.get("/a/claude-12/chat").text
    assert "chat-suggest" not in t and "/continue" not in t and 'value="/clear"' not in t
    set_chat_view(monkeypatch, chat_view(last_role="user"))
    assert "/continue" not in c.get("/a/claude-12/chat").text


def test_agent_page_has_exit_when_idle(env, monkeypatch):
    s, c = env
    login(s, c)
    t = c.get("/a/claude-12").text
    assert 'action="/a/claude-12/exit"' in t
    assert "/stop" not in t and "keepalive" not in t


def test_limits_parse_windows_and_staleness(tmp_path):
    import json
    import os
    import time

    from pm.agents import limits
    now = time.time()
    f = tmp_path / limits.FILE
    f.write_text(json.dumps({"five_hour": {"used_percentage": 42.4, "resets_at": now + 5400},
                             "seven_day": {"used_percentage": 85, "resets_at": now + 3 * 86400},
                             "seven_day_opus": {"used_percentage": 10, "resets_at": now - 5},
                             "bogus": {"used_percentage": 1}}))
    w = limits.read_limits(tmp_path, now)
    assert [(x.label, x.pct, x.level) for x in w] == [("5h limit", 42, "ok"), ("Week limit", 85, "red"), ("Opus week limit", 0, "ok")]
    assert w[0].reset == "in 1h 30m" and w[0].name == "5-hour limit" and w[1].name == "Weekly limit" and w[1].reset.startswith("in 3d") and w[1].abs and w[1].when != w[1].reset and w[2].reset == "reset"
    os.utime(f, (now - 7 * 3600, now - 7 * 3600))
    assert limits.read_limits(tmp_path, now) == []
    f.write_text("not json")
    assert limits.read_limits(tmp_path, now) == []
    assert limits.read_limits(tmp_path / "nope", now) == []


def test_devin_usage_parse():
    import time

    from pm.agents import limits
    now = time.time()
    out = limits.parse_devin_usage(
        "⟩ /usage\n\n"
        "Daily   ▇▇▇▇           12% used   · resets in 12h 57m\n"
        "Weekly  ▇▇             47% used   · resets Oct 18, 3:00 PM (UTC+7)\n"
        "\nThis session\nTokens   25k in · 604 out\n", now)
    assert out["daily"]["used_percentage"] == 12 and out["daily"]["resets_at"] == now + 12 * 3600 + 57 * 60
    assert out["weekly"]["used_percentage"] == 47
    assert limits._local(out["weekly"]["resets_at"]).strftime("%H:%M") == "15:00"
    # placeholder bars below a live "Fetching" line are not final
    assert limits.parse_devin_usage(
        "⟩ /usage\nFetching account usage…\nDaily   ▇▇  0% used\nWeekly  ▇  0% used\n", now) == {}
    # an old block above a new fetch is not the result either
    assert limits.parse_devin_usage(
        "Daily  ▇▇ 12% used · resets in 1h\nWeekly ▇ 47% used · resets in 3d\n"
        "⟩ /usage\nFetching account usage…\n", now) == {}
    # done fetch after an older block: last block wins
    assert limits.parse_devin_usage(
        "Daily  ▇▇ 9% used · resets in 1h\n⟩ /usage\n"
        "Daily  ▇▇ 12% used · resets in 5h\nWeekly ▇ 3% used · resets in 2m\n",
        now)["daily"]["used_percentage"] == 12
    assert limits.parse_devin_usage("random text\n", now) == {}


def test_devin_limits_read_and_staleness(tmp_path):
    import json
    import os
    import time
    from types import SimpleNamespace

    from pm.agents import limits
    from pm.core.models import AgentKind, AgentState
    now = time.time()
    f = tmp_path / limits.DEVIN_FILE
    f.write_text(json.dumps({"daily": {"used_percentage": 34, "resets_at": now + 3600},
                             "weekly": {"used_percentage": 81, "resets_at": now + 3 * 86400}}))
    w = limits.read_devin_limits(tmp_path, now=now)
    assert [(x.label, x.pct, x.level, x.tag) for x in w] == [("Day limit", 34, "ok", "D"), ("Week limit", 81, "red", "W")]
    ag = SimpleNamespace(kind=AgentKind.DEVIN, state=AgentState.RUNNING)
    assert limits.for_agent(ag, SimpleNamespace(state_dir=tmp_path), now=now) == w
    os.utime(f, (now - 7 * 3600, now - 7 * 3600))
    assert limits.read_devin_limits(tmp_path, now=now) == []


def test_devin_limits_refresh_gating(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace

    from pm.agents import limits
    from pm.agents import message as msg
    from pm.core.models import AgentKind, AgentState
    idle = SimpleNamespace(kind=AgentKind.DEVIN, state=AgentState.IDLE, pane_id="%99")
    running = SimpleNamespace(kind=AgentKind.DEVIN, state=AgentState.RUNNING, pane_id="%99")
    sent = []

    monkeypatch.setattr(msg, "send_message", lambda settings, agent, text, **kw: sent.append(text))
    monkeypatch.setattr(limits, "DEVIN_WAIT_S", 0.3)
    monkeypatch.setattr(limits, "DEVIN_POLL_S", 0.05)
    screens = iter(["…\n Message to swe-2-medium in dev-pm:9\n",  # idle, empty composer
                    "⟩ /usage\nFetching account usage…\nDaily ▇ 0% used\n",  # placeholder
                    "Daily ▇ 21% used · resets in 5h 10m\nWeekly ▇ 9% used · resets Oct 18, 3:00 PM (UTC+7)\n"])
    monkeypatch.setattr(limits, "_devin_pane_text", lambda settings, pane: next(screens, ""))
    s = SimpleNamespace(state_dir=tmp_path)
    # running agent: never sends
    assert limits.read_devin_limits(tmp_path, agent=running, settings=s) == []
    assert not sent
    limits._DEVIN_SWR.clear()
    # idle + empty composer + no file: schedules a fetch on the jobs pool; the file lands shortly
    assert limits.read_devin_limits(tmp_path, agent=idle, settings=s) == []
    f = tmp_path / limits.DEVIN_FILE
    for _ in range(60):
        if f.exists() and sent:
            break
        time.sleep(0.05)
    assert sent == ["/usage"]
    w = limits.read_devin_limits(tmp_path, now=time.time())
    assert [(x.label, x.pct) for x in w] == [("Day limit", 21), ("Week limit", 9)]


def test_devin_limits_busy_composer_no_send(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from pm.agents import limits
    from pm.agents import message as msg
    from pm.core.models import AgentKind, AgentState
    idle = SimpleNamespace(kind=AgentKind.DEVIN, state=AgentState.IDLE, pane_id="%99")
    sent = []
    monkeypatch.setattr(msg, "send_message", lambda settings, agent, text, **kw: sent.append(text))
    # a draft replaces the "Message to" placeholder; a running turn shows "Guide Devin"
    import time
    f = tmp_path / limits.DEVIN_FILE
    for screen in ("│ fix the tests\n", "Guide Devin while it works\n"):
        limits._DEVIN_SWR.clear()
        f.unlink(missing_ok=True)
        monkeypatch.setattr(limits, "_devin_pane_text", lambda settings, pane, s=screen: s)
        limits.read_devin_limits(tmp_path, agent=idle, settings=SimpleNamespace(state_dir=tmp_path))
        for _ in range(60):
            if f.exists():
                break
            time.sleep(0.05)
        assert not sent
        assert f.exists()  # failed attempt still throttles retries


def test_stats_limits_inline_and_204_unchanged(env, monkeypatch):
    import json
    import time

    from pm.chat.sources.common import Stats
    s, c = env
    login(s, c)
    monkeypatch.setattr("pm.chat.stats.stats_for", lambda agent, settings=None: Stats(turns=3, input=10, output=20))
    (s.state_dir).mkdir(parents=True, exist_ok=True)
    (s.state_dir / "claude-limits.json").write_text(json.dumps(
        {"five_hour": {"used_percentage": 30, "resets_at": time.time() + 7200},
         "seven_day": {"used_percentage": 12, "resets_at": time.time() + 86400 * 2}}))
    r = c.get("/a/claude-12/stats")
    assert r.status_code == 200 and "Usage limits" in r.text and "30% used" in r.text and "Weekly limit" in r.text
    assert '<span class="tl-l">5h</span><span class="tl-x" aria-hidden="true">/</span><span class="tl-p">30%</span>' in r.text
    assert '<span class="tl-l">W</span>' in r.text and '<span class="tl-p">12%</span>' in r.text
    assert 'title="5-hour limit: 30% used, resets in 1h' in r.text and "5h limit" not in r.text
    assert "tl-r" not in r.text  # the compact summary line keeps the reset in tooltips only
    assert 'Resets in 1h' in r.text and 'class="lim-d"' in r.text  # panel rows show it
    assert 'hx-get="/a/claude-12/usage"' not in r.text  # no lazy placeholder inside the panel
    et = r.headers["etag"]
    r2 = c.get("/a/claude-12/stats", headers={"If-None-Match": et})
    assert r2.status_code == 204
    assert "data-nodiff" in c.get("/a/claude-12").text.split('id="chat-stats"')[1].split(">")[0]


def test_queued_bubble_and_send_now(env, monkeypatch):
    s, c = env
    login(s, c)
    set_chat_view(monkeypatch, chat_view(queued="do it <b>now</b>"))
    t = c.get("/a/claude-12/chat").text
    assert 'chat-queued' in t and "Queued" in t and "do it &lt;b&gt;now&lt;/b&gt;" in t
    assert 'hx-post="/a/claude-12/key"' in t and 'name="key" value="C-x C-s"' in t and "Send now" in t
    set_chat_view(monkeypatch, chat_view())
    assert "chat-queued" not in c.get("/a/claude-12/chat").text
    set_chat_view(monkeypatch, chat_view(queued="x"))
    monkeypatch.setattr("pm.web.chatctx.live_or_ended", lambda r, snap, aid: (snap.agent(aid), True))
    assert "chat-queued" not in c.get("/a/claude-12/chat").text


def test_chat_send_js_restores_text_on_failure():
    js = (STATIC_DIR / "js/chat-send.js").read_text()
    assert "if (d.successful) { showHint(rec.root); return; }" in js and "rec.ta.value = rec.text" in js
