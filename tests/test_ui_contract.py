"""Parse every rendered form and assert the URL contract with the POST routes."""

import time
from dataclasses import replace
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from helpers import login
from helpers import pages_env as env  # noqa: F401  (fixture)


class Forms(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms, self.cur = [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.cur = {"action": a.get("action"), "method": (a.get("method") or "").lower(),
                        "hx": a.get("hx-post"), "fields": {}, "buttons": set(), "names": set()}
            self.forms.append(self.cur)
        elif self.cur is not None:
            if tag in ("input", "select", "textarea", "button") and a.get("name"):
                self.cur["names"].add(a["name"])
                if tag == "input" and (a["name"] == "csrf" or a.get("type") == "hidden"):
                    self.cur["fields"][a["name"]] = a.get("value")
                if tag == "button" and a.get("value") is not None:
                    self.cur["buttons"].add((a["name"], a["value"]))

    def handle_endtag(self, tag):
        if tag == "form":
            self.cur = None


def forms(c, path):
    p = Forms()
    p.feed(c.get(path).text)
    return p.forms


def by_action(fs):
    return {f["action"]: f for f in fs if f["action"]}


def test_forms_have_csrf_and_post(env):
    s, c = env
    login(s, c)
    for path in ("/", "/p/alpha", "/p/alpha/launch", "/a/claude-12", "/tmux", "/settings"):
        for f in forms(c, path):
            if f["action"] == "/logout":
                continue  # owned by security.auth
            if f["method"] == "get" and f["action"] in ("/", "/p", "/tmux") and "csrf" not in f["fields"]:
                continue  # read-only list controls (search / Filter / Show)
            assert f["method"] == "post", (path, f)
            assert f["fields"].get("csrf"), (path, f)
            assert f["hx"] in (None, f["action"]), (path, f)


def test_agent_contract(env):
    s, c = env
    c.app.state.settings = replace(s, require_unlock=True)
    c.app.state.settings.extras.update(s.extras)
    c.app.state.unlocker.provision()
    login(s, c)
    fs = by_action(forms(c, "/a/claude-12"))
    for suffix in ("message", "key", "interrupt", "exit", "snippet"):
        assert f"/a/claude-12/{suffix}" in fs, suffix
    assert "text" in fs["/a/claude-12/message"]["names"]
    # keys come from the Pane keypad: one tiny form per key with a hidden `key` field
    # (the Chat tab no longer has a quick-key row); accept buttons too.
    keys = set()
    for f in forms(c, "/a/claude-12"):
        if f["action"] != "/a/claude-12/key":
            continue
        keys |= {v for n, v in f["buttons"] if n == "key"}
        if f["fields"].get("key"):
            keys.add(f["fields"]["key"])
    assert keys >= {"y", "n", "1", "2", "3", "Enter", "Escape", "C-c"}
    assert "index" in fs["/a/claude-12/snippet"]["names"]
    assert "/unlock" in fs and "code" in fs["/unlock"]["names"]
    assert "Full Transcript" not in c.get("/a/claude-12/items").text  # plugin hook only (tests/web/test_plugins.py)


def test_project_and_launch_contract(env):
    s, c = env
    login(s, c)
    fs = by_action(forms(c, "/p/alpha"))
    assert not any(a in fs for a in ("/p/alpha/git/fetch", "/p/alpha/tmux/ensure"))  # moved / removed
    gfs = by_action(forms(c, "/p/alpha/git/panel"))
    for a in ("git/fetch", "git/pull", "git/push"):
        assert f"/p/alpha/{a}" in gfs, a
    assert "/panic" in fs and {"scope", "confirm"} <= fs["/panic"]["names"]
    assert 'href="/p/alpha/launch"' in c.get("/p/alpha").text
    lf = forms(c, "/p/alpha/launch")
    acts = [f["action"] for f in lf]
    launch = next(f for f in lf if f["action"] == "/p/alpha/launch")
    assert {"kind", "model_choice", "variant", "model", "effort", "permission_mode",
            "permission_extra", "confirm_bypass", "branch", "prompt"} <= launch["names"]
    disp = [f for f in lf if f["action"] == "/p/alpha/dispatch"]
    assert disp and {"index", "kind", "variant", "effort", "permission_mode", "permission_extra",
                     "confirm_bypass"} <= disp[0]["names"]
    assert acts.count("/p/alpha/dispatch") == 1  # one open item in the fixture
    html = c.get("/p/alpha/launch").text
    assert 'data-bypass-confirm' in html and 'bypassPermissions' in html
    assert "open &lt;b&gt;thing&lt;/b&gt;" in html


def test_inbox_has_panic(env):
    s, c = env
    login(s, c)
    f = by_action(forms(c, "/"))["/panic"]
    assert {"scope", "confirm"} <= f["names"]


@pytest.fixture
def open_env(env):
    s, c = env
    s.__dict__  # noqa: B018 -- settings is a dataclass; allow the fixture session to be killable
    c.app.state.settings = replace(s, unprotected_sessions=("work",))
    c.app.state.settings.extras.update(s.extras)
    return c.app.state.settings, c


def test_kill_ui_present_when_unprotected(open_env):
    s, c = open_env
    login(s, c)
    fs = by_action(forms(c, "/tmux"))
    assert "/tmux/session/s1/kill" in fs  # window/pane kill buttons left the list with the windows table
    assert {"confirm"} <= fs["/tmux/session/s1/kill"]["names"]
    html = c.get("/tmux").text
    assert "<dialog" in html and "data-kill-open" in html


def test_kill_ui_absent_for_protected(env):
    s, c = env  # default settings: every session is protected
    login(s, c)
    html = c.get("/tmux").text
    assert "data-kill-open" not in html and "/kill" not in html
    assert "&#128274;" in html


def test_kill_ui_absent_for_own_session(open_env, monkeypatch):
    s, c = open_env
    monkeypatch.setenv("TMUX_PANE", "%12")  # pm itself lives in the "work" session
    login(s, c)
    assert "data-kill-open" not in c.get("/tmux").text


def test_no_inline_js_or_css_anywhere(open_env):
    import re

    s, c = open_env
    login(s, c)
    for path in ("/", "/p", "/p/alpha", "/p/alpha/launch", "/a/claude-12", "/tmux", "/audit",
                 "/settings"):
        html = c.get(path).text
        assert not re.search(r"<script(?![^>]*\bsrc=)", html), path
        assert "<style" not in html and not re.search(r"\sstyle=", html), path
        assert not re.search(r"\son[a-z]+=", html), path
        assert "javascript:" not in html, path
        assert "hx-on" not in html, path


def test_tmux_new_session_form_before_first_row(env):
    s, c = env
    login(s, c)
    html = c.get("/tmux").text
    assert html.index('action="/tmux/session/new"') < html.index('data-session="')
    assert "/tmux/session/new" in by_action(forms(c, "/tmux"))
