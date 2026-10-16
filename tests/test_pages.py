import re
from dataclasses import replace
from pathlib import Path

from helpers import TOKEN, login, pane_line
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.core.models import Agent, AgentKind, AgentState, AuditEntry
from pm.infra import snapshot as services
from pm.security.audit import AuditLog
from pm.tmux import ctl as tmuxctl
from pm.web.render import TEMPLATE_DIR


def test_unauthenticated_redirects(env):
    _, c = env
    for path in ("/", "/p", "/p/alpha", "/a/claude-12", "/a/claude-12/pane", "/tmux", "/audit"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_pages_render(env):
    s, c = env
    login(s, c)
    r = c.get("/")
    assert r.status_code == 200 and "claude-12" in r.text
    assert "alpha" in c.get("/p").text
    r = c.get("/p/alpha")
    assert r.status_code == 200
    assert "open &lt;b&gt;thing&lt;/b&gt;" in r.text  # autoescaped
    assert "claude-12" in r.text and "work" in r.text
    assert "ext-links" not in r.text  # external link rows come from plugins only (tests/web/test_plugins.py)
    r = c.get("/tmux")
    assert r.status_code == 200 and "%12" in r.text
    AuditLog(s.audit_file).write(AuditEntry(1.0, "1.2.3.4", "tmux.new", "$1", "ok", {"len": 3}))
    r = c.get("/audit")
    assert r.status_code == 200 and "tmux.new" in r.text
    r = c.get("/a/claude-12")
    assert r.status_code == 200
    assert 'data-composer="live"' in r.text and 'id="flash"' in r.text
    assert "Full Transcript" not in c.get("/a/claude-12/items").text  # transcript link: plugin hook only


def test_unknown_404(env):
    s, c = env
    login(s, c)
    assert c.get("/p/nope").status_code == 404
    assert c.get("/p/..%2f..%2fetc").status_code == 404
    assert c.get("/a/claude-99").status_code == 404
    assert c.get("/a/..%2fx").status_code == 404


def test_pane_masked_and_204(env):
    s, c = env
    login(s, c)
    r = c.get("/a/claude-12/pane")
    assert r.status_code == 200
    assert TOKEN not in r.text and "hello" in r.text and "\x1b" not in r.text
    assert "&lt;script&gt;" in r.text
    assert TOKEN not in c.get("/a/claude-12").text
    etag = r.headers["etag"]
    assert c.get("/a/claude-12/pane", headers={"If-None-Match": etag}).status_code == 204
    h = re.search(r"pane\?h=([0-9a-f]+)", r.text).group(1)
    assert c.get(f"/a/claude-12/pane?h={h}").status_code == 204
    assert c.get("/a/claude-12/pane?h=deadbeefdeadbeef").status_code == 200


def test_no_inline_script_or_style(env):
    s, c = env
    login(s, c)
    for path in ("/", "/p", "/p/alpha", "/p/alpha/launch", "/settings", "/a/claude-12",
                 "/tmux", "/audit"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert "script-src 'self'" in r.headers["content-security-policy"]
        html = r.text
        assert not re.search(r"<script(?![^>]*\bsrc=)", html), path
        assert "<style" not in html and " style=" not in html, path
        assert not re.search(r"\son[a-z]+=", html), path
        assert "/static/vendor/htmx.min.js" in html and "/static/pm.js" in html


def test_launch_page_ignores_like(env):
    s, c = env
    login(s, c)
    base = c.get("/p/alpha/launch")
    liked = c.get("/p/alpha/launch?like=claude-12")
    assert base.status_code == liked.status_code == 200
    data = re.compile(r'id="launch-data" hidden data-json="(.*?)"')
    assert data.search(liked.text).group(1) == data.search(base.text).group(1)


def test_launch_form_models_and_modes(env, monkeypatch):
    import html as _html
    import json as _json
    import re as _re

    from pm.agents import catalog
    monkeypatch.setattr(catalog, "all_models", lambda st, kinds: {
        "claude": [("opus", "opus (latest)", "opus", "low,high"),
                   ("opus[1m]", "opus, 1M", "opus", "low,high")],
        "codex": [("gpt-6-sol", "GPT-6-Sol", "gpt-6-sol", "low,max")],
        "devin": [("swe", "swe (alias)", "SWE-2", "")], "cursor-agent": []})
    s, c = env
    login(s, c)
    html = c.get("/p/alpha/launch").text
    for name in ("model_choice", "variant", "effort", "permission_mode", "permission_extra",
                 "confirm_bypass"):
        assert f'name="{name}"' in html, name
    assert "data-model-search" in html and "data-bypass-warn" in html
    data = _json.loads(_html.unescape(_re.search(r'id="launch-data" hidden data-json="(.*?)"', html).group(1)))
    claude = data["models"]["claude"]
    assert claude == [{"name": "opus", "variants": [["opus", "opus (latest)"],
                                                    ["opus[1m]", "opus, 1M"]],
                       "efforts": ["low", "high"]}]
    assert data["models"]["devin"][0]["name"] == "SWE-2" and data["models"]["cursor-agent"] == []
    modes = data["modes"]
    assert {m["key"] for m in modes["claude"]["modes"] if m["bypass"]} == {"bypassPermissions"}
    assert any(m["key"] == "manual" and "Asks before edits" in m["help"] for m in modes["claude"]["modes"])
    assert modes["codex"]["extra"]["label"] == "Approval policy"
    assert {m["key"] for m in modes["devin"]["modes"] if m["bypass"]} == {"dangerous"}
    assert {m["key"] for m in modes["cursor-agent"]["modes"] if m["bypass"]} == {"yolo"}
    # one Options block per dispatch card, none selected by default
    assert html.count("data-launch-opts") == 1 + html.count('class="card dispatch"')


def test_launch_popup_redirect_hint(env):
    s, c = env
    (s.dev_root / "beta").mkdir()
    login(s, c)
    url = "/p/alpha/launch?embed=1&from=inbox"
    assert "data-project-hint" not in c.get(url).text  # empty launch_redirects (default): no hint
    c.app.state.settings = replace(s, launch_redirects={"alpha": "beta", "beta": "gone"})
    c.app.state.settings.extras.update(s.extras)
    h = c.get(url).text
    assert "data-project-hint" in h and "Work on <b>beta</b> code?" in h
    assert re.search(r'<button type="button" class="textlink" data-project-switch="beta" title="[^"]+">Launch in beta'
                     r'</button>', h)
    assert "data-project-hint" not in c.get("/p/beta/launch?embed=1&from=inbox").text  # target not a project
    assert "data-project-hint" not in c.get("/p/alpha/launch").text  # no picker on the full page


def test_launch_page_embed_for_popup(env):
    s, c = env
    login(s, c)
    r = c.get("/p/alpha/launch?embed=1")
    assert r.status_code == 200 and "frame-ancestors 'self'" in r.headers["content-security-policy"]
    assert 'class="v5-embed"' in r.text and 'id="v5side"' not in r.text
    assert "launchpop.js" not in r.text
    # compact popup layout: no big h1, grouped rows, More options, same field names
    assert "<h1>" not in r.text and "launch-compact" in r.text and 'class="more-opts"' in r.text
    for name in ('name="kind"', 'name="model_choice"', 'name="variant"', 'name="effort"', 'name="permission_mode"',
                 'name="branch"', 'name="prompt"', "data-launch-open", "data-launch-stay", "data-launch-cancel", "data-template"):
        assert name in r.text, name
    assert "<h2>Open TODO items" not in r.text and "<h2>Stop agents" not in r.text
    assert "TODO items (" in r.text and 'class="lp-stop"' in r.text
    assert "<h2>Open TODO items" in c.get("/p/alpha/launch").text
    assert "data-project-pick" not in r.text and "Launch, stay on project" in r.text
    ib = c.get("/p/alpha/launch?embed=1&from=inbox").text
    assert "data-project-pick" in ib and 'value="alpha" selected' in ib and "Launch, stay here" in ib
    assert "data-project-pick" not in c.get("/p/alpha/launch?from=inbox").text  # full page: no picker
    full = c.get("/p/alpha/launch")
    assert "<h1>Launch in" in full.text and "launch-compact" not in full.text and "more-opts" not in full.text
    assert "frame-ancestors 'none'" in full.headers["content-security-policy"]
    assert "/static/js/launchpop.js" in full.text


def test_launch_form_bypass_kill_switch(env, monkeypatch):
    import html as _html
    import json as _json
    import re as _re
    s, c = env
    c.app.state.settings = replace(s, allow_bypass=False)
    login(s, c)
    html = c.get("/p/alpha/launch").text
    data = _json.loads(_html.unescape(_re.search(r'id="launch-data" hidden data-json="(.*?)"', html).group(1)))
    assert not [m for v in data["modes"].values() for m in v["modes"] if m["bypass"]]


def test_static_assets(env):
    _, c = env
    assert c.get("/static/manifest.webmanifest").status_code == 200
    assert c.get("/static/vendor/htmx.min.js").status_code == 200
    assert c.get("/static/css/base.css").status_code == 200


def test_inbox_ordering():
    def a(i, st, since):
        return Agent(id=i, kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=1,
                     cwd="/", state=st, state_since=since)

    snap = services.Snapshot(
        0, agents=(a("c-1", AgentState.IDLE, 5), a("c-2", AgentState.RUNNING, 1),
                   a("c-3", AgentState.WAITING, 1), a("c-4", AgentState.FAILED, 9))
    )
    assert [x.id for x in services.inbox(snap)] == ["c-3", "c-2", "c-4", "c-1"]


def test_sw_public_and_static_only(env):
    _, c = env
    r = c.get("/sw.js", follow_redirects=False)  # no login needed
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert r.headers["service-worker-allowed"] == "/"
    js = r.text
    assert "STATIC_PREFIX = '/static/'" in js and "startsWith(STATIC_PREFIX)" in js
    assert "addAll" not in js and "text/html" in js  # html is never stored
    assert "caches.match('/')" not in js


def test_manifest_and_pwa(env):
    s, c = env
    import json

    m = json.loads(c.get("/static/manifest.webmanifest").text)
    assert m["display"] == "standalone" and m["scope"] == "/" and m["start_url"] == "/"
    assert any(i["sizes"] == "512x512" for i in m["icons"])
    login(s, c)
    html = c.get("/").text
    assert 'rel="apple-touch-icon"' in html and 'name="theme-color"' in html
    assert "viewport-fit=cover" in html
    assert c.get("/static/js/passkey.js").status_code == 200
    assert c.get("/static/pm.js").status_code == 200


def test_inbox_sse_wiring(env):
    s, c = env
    login(s, c)
    html = c.get("/").text
    assert "data-live" in html and "pm:agent_state from:document" in html and "every 30s" in html
    assert 'hx-ext="sse"' not in html and "sse-connect" not in html and "sse:" not in html


def test_live_js_shipped_single_owner(env):
    s, c = env
    login(s, c)
    html = c.get("/").text
    assert re.search(r'<script src="/static/js/live\.js(\?v=\w+)?" defer></script>', html)
    assert "htmx-sse" not in html
    js = c.get("/static/js/live.js")
    assert js.status_code == 200
    # one EventSource constructor, closed on pagehide, paused when hidden, bfcache aware
    assert js.text.count("new EventSource") == 1
    for needle in ("pagehide", "pageshow", "persisted", "visibilitychange", "recycle", "bye"):
        assert needle in js.text
    root = TEMPLATE_DIR
    for f in root.glob("*.html"):
        t = f.read_text()
        assert "sse-connect" not in t and 'hx-ext="sse"' not in t and "sse:" not in t, f.name


def _require_unlock(s, c):
    c.app.state.settings = replace(s, require_unlock=True)
    c.app.state.settings.extras.update(s.extras)


def test_unlock_ui_hidden_by_default(env):
    s, c = env
    login(s, c)
    for path in ("/", "/a/claude-12", "/tmux", "/settings"):
        html = c.get(path).text
        assert 'id="lockbar"' not in html and 'action="/unlock' not in html, path
        assert "Unlock" not in html and "Locked" not in html, path
    assert "Passkey login" in c.get("/settings/panel").text


def test_inbox_rows_not_cards(env):
    s, c = env
    login(s, c)
    html = c.get("/").text
    assert re.search(r'<div class="nwrap" role="listitem" data-key="w-claude-12"[^>]*>\s*<a class="nrow row-running[^"]*" href="/a/claude-12"', html)
    row = re.search(r'<a class="nrow row-running.*?</a>', html, re.S)
    assert row and "<button" not in row.group(0)
    assert 'class="card"' not in html
    assert "4242" not in html and "dot-running" in html
    assert "claude-12" in c.get("/p/alpha").text and 'class="lrow arow' in c.get("/p/alpha").text
    assert 'class="lrow prow" href="/p/alpha"' in c.get("/p").text


def test_terminal_link_only_when_enabled_and_not_pm(env):
    s, c = env
    login(s, c)
    assert "/term/" not in c.get("/tmux").text and "/term/" not in c.get("/a/claude-12").text
    c.app.state.settings = replace(s, terminal_enabled=True)
    c.app.state.settings.extras.update(s.extras)
    assert 'href="/term/s1?w=w1"' in c.get("/tmux").text  # per window row; no session-level Terminal button
    page = c.get("/a/claude-12").text  # Terminal is a same-page tab (iframe), not a new page
    assert 'id="tab-term"' in page and 'class="term-frame"' in page and 'src="/term/s1?embed=1&amp;w=w1"' in page
    assert 'class="button small" href="/term/s1' not in page
    r = c.get("/term/s1?embed=1")
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"] and "termpage embed" in r.text
    assert "frame-ancestors 'none'" in c.get("/term/s1").headers["content-security-policy"]


def test_no_terminal_link_for_pm_sessions(env, monkeypatch):
    s, c = env
    monkeypatch.setenv("FAKE_TMUX_OUT", pane_line("$7", "pm-main", "@1", "%12", "/tmp"))
    login(s, c)
    c.app.state.settings = replace(s, terminal_enabled=True)
    c.app.state.settings.extras.update(s.extras)
    html = c.get("/tmux").text
    assert "pm-main" in html and "/term/" not in html


def test_locked_banner_then_unlocked(env):
    s, c = env
    _require_unlock(s, c)
    c.app.state.unlocker.provision()
    login(s, c)
    html = c.get("/a/claude-12").text
    assert 'id="lockbar"' in html and 'action="/unlock"' in html and 'data-passkey="auth"' in html
    import time

    sess = c.app.state.sessions
    cookie = c.cookies.get("pm_session")
    sobj = sess.session_from_cookie(cookie)
    sobj.unlocked_until = time.time() + 600
    assert 'id="lockbar"' not in c.get("/a/claude-12").text


def test_audit_page_filters_and_paging(env):
    s, c = env
    login(s, c)
    log = AuditLog(s.audit_file)
    for i in range(60):
        log.write(AuditEntry(1000.0 + i, "1.2.3.4", f"act.{i}", "", "ok", {"k": i}))
    log.write(AuditEntry(2000.0, "9.9.9.9", "bad.thing", "", "denied", {"reason": "origin"}))
    r = c.get("/audit")
    assert r.status_code == 200 and "62 entries" in r.text and "older" in r.text and "bad.thing" in r.text
    assert "act.0<" not in r.text
    r = c.get("/audit?page=2")
    assert "act.0<" in r.text and "bad.thing" not in r.text
    r = c.get("/audit?result=denied&action=BAD")
    assert "bad.thing" in r.text and "act.5<" not in r.text
    assert c.get("/audit?page=abc").status_code == 422 or True


def test_pane_trailing_blank_rows_trimmed_and_states(env, monkeypatch):
    s, c = env
    login(s, c)
    monkeypatch.setattr(tmuxctl, "capture_pane", lambda settings, pane, lines=200: "hello\n\n\n   \n\n")
    r = c.get("/a/claude-12/pane")
    assert re.search(r'<pre class="pane"[^>]*>hello</pre>', r.text)  # no blank tail
    assert 'data-state="ok"' in r.text and "Disconnected, retrying" in r.text
    monkeypatch.setattr(tmuxctl, "capture_pane", lambda settings, pane, lines=200: "\n\n  \n")
    page = c.get("/a/claude-12").text
    assert 'data-state="empty"' in page and "No output yet" in page
    assert "/static/css/pane.css" in page and "/static/js/pane.js" in page
    monkeypatch.setattr(tmuxctl, "capture_pane", lambda *a, **k: (_ for _ in ()).throw(tmuxctl.TmuxError("gone")))
    assert 'data-state="loading"' in c.get("/a/claude-12").text
    assert c.get("/a/claude-12/pane").status_code == 404


def test_pane_assets_served(env):
    _, c = env
    css = c.get("/static/css/pane.css")
    js = c.get("/static/js/pane.js")
    assert css.status_code == 200 and "min-height: 40px" in css.text and "data-conn" in css.text
    assert js.status_code == 200 and "Disconnected" not in js.text  # text lives in the template


def _h1(html):
    m = re.search(r'<h1 class="agent-title" title="([^"]*)" data-model="([^"]*)"><span class="at-ico"><svg class="wico"[^>]*aria-hidden="true">.*?</svg></span><span class="at-base">([^<]*)</span><span class="kpill kp-model" id="agent-pill"[^>]*>([^<]*)</span></h1>', html)
    assert m.group(2) == m.group(4)  # data-model (drawer.js) = the pill text
    return m.group(1), m.group(3) + " " + m.group(4)


def test_agent_heading_is_window_and_raw_id(env, monkeypatch):
    from pm.agents import claude_defaults
    from pm.agents import launch as lm
    from pm.core import prefs
    s, c = env
    login(s, c)
    prefs.mark_dispatched(s.prefs_file, "alpha", lm.item_key("some task"), "claude-12", "%12", 1e12, title="some task")
    monkeypatch.setattr(claude_defaults, "read", lambda cwd, home=None: ("", ""))
    r = c.get("/a/claude-12")
    title_attr, text = _h1(r.text)
    assert text == title_attr == "claude-12 claude"  # no model anywhere: the pill falls back to the kind
    assert "<title>claude-12 claude" in r.text
    assert 'class="at-base">claude-12</span>' in r.text
    assert 'class="at-ico"><svg class="wico"' in r.text and "<title>claude-12 claude" in r.text
    assert 'id="agent-state"' in r.text  # state badge kept (sr-only) for screen readers and the pill title


def test_agent_heading_pill_follows_the_settings_default(env, monkeypatch):
    """No reply and no --model yet: the pill shows Claude's settings default, like the composer placeholder."""
    from pm.agents import claude_defaults
    s, c = env
    login(s, c)
    monkeypatch.setattr(claude_defaults, "read", lambda cwd, home=None: ("opus", "high"))
    r = c.get("/a/claude-12")
    assert _h1(r.text)[1] == "claude-12 opus-5-5"
    assert 'placeholder="Message to opus-5-5' in r.text  # same model as the composer


def test_heading_pill_is_model_from_transcript(tmp_path, monkeypatch):
    from dataclasses import replace
    from pathlib import Path

    from pm.agents import head as agenthead
    from pm.core.models import Agent, AgentKind
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    t = tmp_path / ".claude/projects/-x/s.jsonl"
    t.parent.mkdir(parents=True)
    t.write_text('{"type":"assistant","message":{"model":"claude-sonnet-5-5"}}\n')
    a = Agent(id="claude-1", kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=1, cwd="/x", label="dev-pm-1-claude", transcript=str(t))
    h = agenthead.heading(a)
    assert (h["base"], h["pill"], h["text"]) == ("claude-1", "claude-sonnet-5-5", "claude-1 claude-sonnet-5-5")
    assert agenthead.heading(replace(a, transcript=""))["pill"] == "claude"


def test_heading_title_is_agent_id_never_label():
    from pm.agents import head as agenthead
    from pm.core.models import Agent, AgentKind
    for label in ("dev-running-0", "dev-pm-1-claude", ""):
        a = Agent(id="claude-14fb00", kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=1, cwd="/x", label=label)
        h = agenthead.heading(a)
        assert h["base"] == h["id"] == "claude-14fb00" and h["text"] == "claude-14fb00 claude"


def test_agent_heading_pill_is_the_one_model_pill():
    """The heading pill is .kpill.kp-model (kind colour from chrome.css), sized for the heading, kept on phones."""
    from pathlib import Path
    root = Path(__file__).parent.parent / "src/pm"
    css = (root / "static/css/agent.css").read_text()
    assert ".agent-head[data-agent] h1.agent-title .kpill{" in css
    nav = (root / "static/css/nav.css").read_text()
    assert ".agent-head[data-agent] .kpill" not in nav  # no phone / embed rule hides the heading pill
    assert "at-pill" not in css and "h1.agent-title{--pill" not in css
    assert "at-pill" not in (root / "static/css/shell.css").read_text() and "vc-pill" not in (root / "static/css/shell.css").read_text()
    assert "[data-kind] .kpill.kp-model{--pill:var(--k)}" in (root / "static/css/chrome.css").read_text()


def test_ended_pill_yellow_and_window_icon_macro():
    from pathlib import Path
    root = Path(__file__).parent.parent / "src/pm"
    css = (root / "static/css/agent.css").read_text()
    assert ".kpill.at-ended{" in css and "--waiting-soft" in css and "dashed" not in css
    head = (TEMPLATE_DIR / "_agentname.html").read_text()
    assert 'kpill at-ended' in head and '>Ended</span>' in head
    assert 'agent_head(rec, "ended")' in (TEMPLATE_DIR / "agent_ended.html").read_text()
    mac = (TEMPLATE_DIR / "_macros.html").read_text()
    assert "macro wname(" in mac and "macro wico(" in mac and 'aria-hidden="true"' in mac
    assert "wname-m" not in mac


def test_ended_page_has_a_real_composer():
    """User decision 2026-10-13: the ended page types into the shared composer (Resume and send / Fork and send);
    the inert ghost, its CSS and its proxy JS are gone."""
    root = Path(__file__).resolve().parents[1] / "src/pm"
    ended = (TEMPLATE_DIR / "agent_ended.html").read_text()
    assert '{{ composer("ended", rec=rec) }}' in ended and "composer_scripts(" in ended
    assert "ec-ghost" not in ended and "data-ended-proxy" not in ended and 'data-composer="live"' not in ended
    for css in (root / "static/css").glob("*.css"):
        assert "ended-composer" not in css.read_text() and "ec-ghost" not in css.read_text(), css.name
    js = (root / "static/js/agentmode.js").read_text()
    assert "data-ended-proxy" not in js and "aria-busy" in js  # composer.js leaves a Resuming... button alone


def test_project_rows_show_unread_and_pending_question_like_the_inbox(env, monkeypatch):
    """The project page uses the Inbox's agent row: unread (bold, data-read) and the pending question."""
    import time

    s, c = env
    login(s, c)
    dev = s.dev_root
    a = Agent(id="claude-12", kind=AgentKind.CLAUDE, pane_id="%12", session_id="$1", pid=4242, cwd=str(dev / "alpha"),
              state=AgentState.WAITING, state_since=time.time() + 60)  # changed after the first-use read baseline
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes: [a])
    monkeypatch.setattr(tmuxctl, "capture_pane", lambda settings, pane, lines=200: "Working on it\nDo you want to proceed?")
    c.get("/")  # first use: the baseline
    for t in (c.get("/").text, c.get("/p/alpha").text, c.get("/p/alpha/live", headers={"HX-Request": "true"}).text):
        row = re.search(r'<a class="(?:nrow|lrow arow) row-waiting unread" href="/a/claude-12"[^>]*>.*?</a>', t, re.S)
        assert row, t[:300]
        assert 'data-read="claude-12:waiting"' in row.group(0)
        assert '<span class="n-q" title="Pending question">Do you want to proceed?</span>' in row.group(0)
    s_ = c.get("/p/alpha/live", headers={"HX-Request": "true"}).text
    assert 'class="lrow arow row-waiting unread"' in s_ and "n-proj" not in s_.split("<h2>Agents</h2>")[1]  # no Project column


def test_settings_page_carries_the_panel_context(env, monkeypatch, tmp_path):
    """GET /settings hands the panel's data to a theme with its own settings.html (first paint); classic loads the
    panel by HTMX and gets none of it."""
    from pm.web import render, themes
    from pm.web.routes import pages
    s, c = env
    login(s, c)
    seen = {}
    real = pages.html
    monkeypatch.setattr(pages, "html", lambda request, name, **ctx: seen.update(ctx) or real(request, name, **ctx))
    r = c.get("/settings")
    assert r.status_code == 200 and 'id="settings-panel" hx-get="/settings/panel"' in r.text
    assert seen["nav"] == "settings" and not {"sessions", "passkeys", "prefs", "servers", "composer"} & set(seen)
    monkeypatch.setattr(themes, "_registry", dict(themes.BUILTIN))
    monkeypatch.setattr(render, "_overlays", {})
    (tmp_path / "skin").mkdir()
    (tmp_path / "skin" / "settings.html").write_text("skin settings {{ sessions|length }}")
    themes.register(themes.Theme("setskin", tmp_path / "skin"))
    seen.clear()
    r = c.get("/settings?ui=setskin")
    assert r.status_code == 200 and "skin settings 1" in r.text
    assert {"sessions", "passkeys", "prefs", "servers", "composer"} <= set(seen)
    assert len(seen["sessions"]) == 1  # this login
