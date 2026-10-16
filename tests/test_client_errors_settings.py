import json

from helpers import make_client, make_settings

from pm.core import prefs
from pm.notify import clienterrors
from pm.security import sessions_store as sstore


def _client(fake_tmux, tmp_path):
    s = make_settings(fake_tmux, tmp_path, state="st", disable_poll=True)
    (tmp_path / "dev").mkdir(exist_ok=True)
    c = make_client(s, logged_in=True)
    return c, s


def _csrf(c):
    import re
    return re.search(r'name="csrf-token" content="([^"]+)"', c.get("/settings").text).group(1)


def test_sanitize_strips_query_and_origin():
    e = clienterrors.sanitize({"kind": "htmx", "url": "https://pm.example.test/a/x?token=abc#f",
                               "msg": "failed /y?secret=1 now", "status": "500", "method": "post"}, "1.2.3.4")
    assert e["url"] == "/a/x" and "secret" not in e["msg"] and e["status"] == 500 and e["method"] == "POST"
    assert clienterrors.sanitize({"kind": "nope"}, "x") is None


def test_ring_rotates(tmp_path):
    log = clienterrors.ClientErrorLog(tmp_path / "ce.jsonl")
    for i in range(clienterrors.MAX_ENTRIES + 30):
        log.append({"ts": i, "kind": "js", "msg": f"m{i}"})
    n = len((tmp_path / "ce.jsonl").read_text().splitlines())
    assert n <= clienterrors.MAX_ENTRIES
    assert log.page(1)["rows"][0]["msg"] == f"m{clienterrors.MAX_ENTRIES + 29}"
    assert log.page(1, "htmx")["total"] == 0


def test_report_requires_csrf_origin_and_limits(fake_tmux, tmp_path):
    c, s = _client(fake_tmux, tmp_path)
    tok = _csrf(c)
    body = json.dumps({"kind": "js", "msg": "boom", "url": "/static/x.js?v=1", "line": 3})
    h = {"X-CSRF-Token": tok, "Origin": "https://pm.example.test", "Content-Type": "application/json"}
    assert c.post("/client-errors", content=body, headers={**h, "X-CSRF-Token": "bad"}).status_code == 403
    assert c.post("/client-errors", content=body, headers={**h, "Origin": "https://evil.example"}).status_code == 403
    assert c.post("/client-errors", content="x" * 5000, headers=h).status_code == 413
    assert c.post("/client-errors", content="{}", headers=h).status_code == 400
    assert c.post("/client-errors", content=body, headers=h).status_code == 204
    line = json.loads(s.client_errors_file.read_text().splitlines()[0])
    assert line["url"] == "/static/x.js" and line["msg"] == "boom"
    page = c.get("/audit/client-errors", headers={"HX-Request": "true"}).text
    assert "boom" in page and "/static/x.js" in page
    assert "Client errors" in c.get("/audit").text
    assert "boom" not in c.get("/audit/client-errors?ce_kind=htmx", headers={"HX-Request": "true"}).text
    for _ in range(40):
        last = c.post("/client-errors", content=body, headers=h).status_code
    assert last == 429


def test_clear_audits_first_and_fails_closed(fake_tmux, tmp_path, monkeypatch):
    c, s = _client(fake_tmux, tmp_path)
    tok = _csrf(c)
    h = {"X-CSRF-Token": tok, "Origin": "https://pm.example.test"}
    body = json.dumps({"kind": "js", "msg": "boom", "url": "/x.js"})
    assert c.post("/client-errors", content=body, headers={**h, "Content-Type": "application/json"}).status_code == 204
    from pm.security import audit as audit_mod
    real = audit_mod.AuditLog.write

    def no_result(self, entry):  # the guard's attempt line works, the outcome line fails
        if entry.result != "attempt":
            raise audit_mod.AuditError("disk full")
        return real(self, entry)

    monkeypatch.setattr(audit_mod.AuditLog, "write", no_result)
    assert c.post("/audit/client-errors/clear", headers=h).status_code == 503
    assert "boom" in s.client_errors_file.read_text()  # not cleared without its audit line
    monkeypatch.setattr(audit_mod.AuditLog, "write", real)
    assert c.post("/audit/client-errors/clear", headers=h).status_code == 204
    assert not s.client_errors_file.exists()
    rows = [json.loads(x) for x in s.audit_file.read_text().splitlines()]
    assert [r["result"] for r in rows if r["action"] == "client_errors_clear"] == ["attempt", "attempt", "ok"]


def test_report_needs_login(fake_tmux, tmp_path):
    c, _ = _client(fake_tmux, tmp_path)
    c.cookies.clear()
    assert c.post("/client-errors", content="{}").status_code == 401


def test_settings_sessions_and_passkeys(fake_tmux, tmp_path):
    c, s = _client(fake_tmux, tmp_path)
    app = c.app
    other = app.state.sessions.create("9.9.9.9")
    tok = _csrf(c)
    h = {"Origin": "https://pm.example.test"}
    panel = c.get("/settings/panel").text
    assert "this session" in panel and "9.9.9.9" in panel and other.id not in panel
    oh = sstore.sid_hash(other.id)
    r = c.post("/settings/sessions/revoke", data={"csrf": tok, "h": oh}, headers=h)
    assert r.status_code == 200 and "revoked" in r.text.lower()
    assert app.state.sessions.get(other.id) is None
    cur = sstore.sid_hash(app.state.sessions.from_request(
        type("R", (), {"cookies": c.cookies})()).id)
    r = c.post("/settings/sessions/revoke", data={"csrf": tok, "h": cur}, headers=h)
    assert "confirm" in r.text.lower() and c.get("/settings").status_code == 200
    r = c.post("/settings/sessions/revoke", data={"csrf": tok, "h": cur, "confirm": "1"}, headers=h)
    assert r.headers.get("hx-redirect") == "/login"
    assert c.post("/settings/sessions/revoke", data={"csrf": tok, "h": cur}).status_code in (401, 403)


def test_settings_requires_csrf(fake_tmux, tmp_path):
    c, _ = _client(fake_tmux, tmp_path)
    r = c.post("/settings/prefs", data={"csrf": "x"}, headers={"Origin": "https://pm.example.test"})
    assert r.status_code == 403


def test_prefs_save_and_passkey_revoke(fake_tmux, tmp_path):
    c, s = _client(fake_tmux, tmp_path)
    tok = _csrf(c)
    h = {"Origin": "https://pm.example.test"}
    from pm.security.passkey import manager_of
    mgr = manager_of(c.app)
    mgr._save([{"id": "abc", "public_key": "k", "sign_count": 0, "name": "phone", "created": 1.0}])
    assert "phone" in c.get("/settings/panel").text
    r = c.post("/settings/passkeys/revoke", data={"csrf": tok, "id": "abc"}, headers=h)
    assert "removed" in r.text.lower() and not mgr.has_credentials()
    r = c.post("/settings/prefs", data={"csrf": tok, "launch_agent": "claude", "launch_model": "opus",
                                         "notify_finish": "1"}, headers=h)
    assert r.status_code == 200
    p = c.get("/api/prefs").json()
    assert p["launch"]["agent"] == "claude" and p["notify"]["finish"] is True and p["notify"]["health"] is False
    assert c.post("/settings/prefs", data={"csrf": tok, "launch_agent": "evil"}, headers=h).status_code == 400
    audit = s.audit_file.read_text()
    assert "prefs_save" in audit and "passkey_revoke" in audit


def test_prefs_validation(tmp_path):
    out = prefs.save(tmp_path / "p.json", {"launch": {"model": "a;rm -rf"}, "notify": {"finish": "no"}})
    assert out["launch"]["model"] == "" and out["notify"]["finish"] is True


def test_settings_fonts_section_and_script(fake_tmux, tmp_path):
    c, _ = _client(fake_tmux, tmp_path)
    html = c.get("/settings").text
    assert 'id="fonts-form"' in html and "/static/js/fonts.js" in html
    assert html.index("fonts.js") < html.index("</head>")
