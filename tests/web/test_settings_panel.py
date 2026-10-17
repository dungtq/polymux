"""Settings reply contract (web.settings_panel): panel context, HTML + HX-Trigger pm:notice for htmx, JSON
``{ok, notice}`` only for an explicit ``Accept: application/json``; revoke all other sessions."""

import json
import re

from helpers import ORIGIN, make_client, make_settings
from starlette.requests import Request

from pm.security import sessions_store as sstore
from pm.security.auth import COOKIE_NAME
from pm.web import settings_panel

H = {"Origin": ORIGIN}
JSON = {**H, "Accept": "application/json"}


def _client(fake_tmux, tmp_path):
    s = make_settings(fake_tmux, tmp_path, state="st", disable_poll=True)
    (tmp_path / "dev").mkdir(exist_ok=True)
    return make_client(s, logged_in=True), s


def _csrf(c):
    return re.search(r'name="csrf" value="([^"]+)"', c.get("/settings/panel").text).group(1)


def _req(c, accept: str = "", method: str = "POST") -> Request:
    headers = [(b"cookie", f"{COOKIE_NAME}={c.cookies.get(COOKIE_NAME)}".encode())]
    if accept:
        headers.append((b"accept", accept.encode()))
    return Request({"type": "http", "app": c.app, "method": method, "path": "/settings", "headers": headers,
                    "query_string": b""})


def _notice(r):
    return json.loads(r.headers["hx-trigger"])["pm:notice"]


def _audit(s):
    return [json.loads(x) for x in s.audit_file.read_text().splitlines()]


def test_context_is_the_panel_data(fake_tmux, tmp_path):
    c, _ = _client(fake_tmux, tmp_path)
    ctx = settings_panel.context(_req(c))
    assert {"sessions", "passkeys", "prefs", "agents", "pm_key", "user_keys", "servers", "srv_results", "git_hosts",
            "notify_rules", "composer", "now", "plugin_sections"} <= set(ctx)
    assert "gitdev" not in ctx  # the push remote comes from page_ctx (push_remote)
    h = c.get("/settings/panel").text
    assert 'Default remote</span><span class="mono">origin</span>' in h and "pm pushes only to" in h
    assert "notice" not in ctx and "csrf" not in ctx  # page_ctx and notice are added by the renderer
    assert [r["current"] for r in ctx["sessions"]] == [True]


def test_wants_json_only_when_explicit():
    def req(accept):
        return Request({"type": "http", "headers": [(b"accept", accept.encode())] if accept else []})
    assert settings_panel.wants_json(req("application/json"))
    assert settings_panel.wants_json(req("text/html;q=0.5, Application/JSON; q=0.9"))
    for a in ("", "*/*", "text/html, */*; q=0.01", "application/jsonx"):
        assert not settings_panel.wants_json(req(a)), a


def test_reply_header_is_ascii_json(fake_tmux, tmp_path):
    c, _ = _client(fake_tmux, tmp_path)
    r = settings_panel.reply(_req(c), "Đã lưu \"x\"", ok=False, body=lambda: "<p>x</p>")
    raw = r.headers["hx-trigger"]
    assert raw.isascii() and json.loads(raw) == {"pm:notice": {"text": "Đã lưu \"x\"", "ok": False}}
    assert r.body == b"<p>x</p>" and r.headers["cache-control"] == "no-store"
    j = settings_panel.reply(_req(c, "application/json"), "Saved.", body=lambda: 1 / 0)  # body not rendered
    assert json.loads(j.body) == {"ok": True, "notice": "Saved."}
    assert "hx-trigger" not in settings_panel.reply(_req(c), "", body=lambda: "").headers
    g = settings_panel.reply(_req(c, "application/json", "GET"), "", body=lambda: "<p>lazy</p>")
    assert g.body == b"<p>lazy</p>"  # a GET section stays HTML


def test_refusal_and_success_carry_ok(fake_tmux, tmp_path):
    c, _ = _client(fake_tmux, tmp_path)
    tok = _csrf(c)
    bad = {"csrf": tok, "paste_lines": "x", "exts": "md"}
    r = c.post("/settings/composer", data=bad, headers=H)
    assert r.status_code == 200 and "<section" in r.text and _notice(r) == {
        "text": "Paste threshold must be a number (0 = off).", "ok": False}
    r = c.post("/settings/composer", data=bad, headers=JSON)
    assert r.status_code == 200 and r.json() == {"ok": False, "notice": "Paste threshold must be a number (0 = off)."}
    r = c.post("/settings/composer", data={**bad, "paste_lines": "5"}, headers=JSON)
    assert r.json() == {"ok": True, "notice": "Composer settings saved."} and _notice(r)["ok"] is True
    r = c.post("/settings/autoexit", data={"csrf": tok, "hours": "nope"}, headers=H)
    assert r.status_code == 200 and _notice(r)["ok"] is False and "<" in r.text
    assert c.post("/settings/autoexit", data={"csrf": tok, "hours": "2"}, headers=JSON).json()["ok"] is True
    assert c.get("/settings/autoexit", headers=JSON).text.startswith("<")
    r = c.post("/settings/passkeys/revoke", data={"csrf": tok, "id": "nope"}, headers=JSON)
    assert r.json() == {"ok": False, "notice": "Passkey not found."}


def test_revoke_others_keeps_this_session(fake_tmux, tmp_path):
    c, s = _client(fake_tmux, tmp_path)
    tok = _csrf(c)
    store = c.app.state.sessions
    others = [store.create("a"), store.create("b")]
    assert c.post("/settings/sessions/revoke-others", data={}, headers=H).status_code == 403  # no csrf
    assert all(store.get(o.id) for o in others)
    r = c.post("/settings/sessions/revoke-others", data={"csrf": tok}, headers=JSON)
    assert r.json() == {"ok": True, "notice": "Revoked 2 other sessions."}
    assert not any(store.get(o.id) for o in others) and c.get("/settings/panel").status_code == 200
    ok = [a for a in _audit(s) if a["action"] == "session_revoke" and a["result"] == "ok"]
    assert ok[-1]["target"] == "others" and ok[-1]["detail"] == {"n": 2}
    r = c.post("/settings/sessions/revoke-others", data={"csrf": tok}, headers=H)
    assert _notice(r) == {"text": "No other sessions.", "ok": True}


def test_revoke_current_session_json_redirects(fake_tmux, tmp_path):
    c, _ = _client(fake_tmux, tmp_path)
    tok = _csrf(c)
    h = sstore.sid_hash(c.app.state.sessions.from_request(_req(c)).id)
    r = c.post("/settings/sessions/revoke", data={"csrf": tok, "h": h}, headers=JSON)
    assert r.json()["ok"] is False and "confirm" in r.json()["notice"]
    r = c.post("/settings/sessions/revoke", data={"csrf": tok, "h": h, "confirm": "1"}, headers=JSON)
    assert r.json() == {"ok": True, "notice": "Logged out.", "redirect": "/login"}
    assert COOKIE_NAME in r.headers["set-cookie"]


def test_panel_renders_off_the_event_loop(fake_tmux, tmp_path, monkeypatch):
    """GET /settings/panel reads prefs, servers and keys from disk: it renders in a worker thread, never inside
    the running event loop (blocking file IO in an async handler)."""
    import asyncio

    from pm.web.routes import settings as settings_routes

    c, _ = _client(fake_tmux, tmp_path)
    seen = []

    def render(request, notice=""):
        try:
            asyncio.get_running_loop()
            seen.append("loop")
        except RuntimeError:
            seen.append("thread")
        return settings_panel.render(request, notice)

    monkeypatch.setattr(settings_routes, "render_panel", render)
    r = c.get("/settings/panel")
    assert r.status_code == 200 and 'id="settings-panel"' in r.text and seen == ["thread"]
