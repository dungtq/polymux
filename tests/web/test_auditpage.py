"""/audit (web/routes/auditpage.py): CSV export, the Client errors first paint and the tab counts."""
import csv
import json
import re

import pytest
from helpers import login, make_client, make_settings

from pm.core.models import AuditEntry
from pm.security.audit import AuditLog
from pm.web.routes import auditpage


@pytest.fixture
def env(fake_tmux, tmp_path):
    s = make_settings(fake_tmux, tmp_path, state="st", disable_poll=True)
    (tmp_path / "dev").mkdir(exist_ok=True)
    c = make_client(s)
    log = AuditLog(s.audit_file)
    for i, (action, target, result) in enumerate([("agent_message", "claude-1", "ok"),
                                                  ("agent_kill", "=cmd|' /c calc'!A1", "denied"),
                                                  ("stack_restart", "pv", "error")]):
        log.write(AuditEntry(1000.0 + i, "1.2.3.4", action, target, result, {"n": i}))
    s.client_errors_file.parent.mkdir(parents=True, exist_ok=True)
    s.client_errors_file.write_text("".join(json.dumps(e) + "\n" for e in [
        {"ts": 1, "kind": "js", "msg": "boom", "url": "/x", "page": "/a"},
        {"ts": 2, "kind": "js", "msg": "boom", "url": "/x", "page": "/b"},
        {"ts": 3, "kind": "csp", "msg": "Refused img-src", "url": "/", "page": "/p/pv"}]))
    return s, c


def _rows(text):
    return list(csv.reader(text.splitlines()))


def test_export_needs_a_session(env):
    _, c = env
    r = c.get("/audit/export.csv", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=/audit/export.csv")
    assert c.get("/audit/export.csv", headers={"HX-Request": "true"}).status_code == 401


def test_export_csv_download_filters_and_guard(env):
    s, c = env
    login(s, c)
    r = c.get("/audit/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert re.fullmatch(r'attachment; filename="pm-audit-\d{8}-\d{6}\.csv"', r.headers["content-disposition"])
    assert r.headers["cache-control"] == "no-store"
    rows = _rows(r.text)
    assert rows[0][:4] == ["time", "ts", "client", "action"]
    acts = [row[3] for row in rows[1:]]
    assert acts == ["login_token", "stack_restart", "agent_kill", "agent_message"]  # newest first
    kill = next(row for row in rows if row[3] == "agent_kill")
    assert kill[5] == "'=cmd|' /c calc'!A1" and kill[4] == "control"  # formula guarded, category column
    only = _rows(c.get("/audit/export.csv?q=agent&result=denied").text)
    assert [row[3] for row in only[1:]] == ["agent_kill"]
    assert [row[3] for row in _rows(c.get("/audit/export.csv?cat=control&action=pv").text)[1:]] == ["stack_restart"]
    failed = _rows(c.get("/audit/export.csv?result=failed").text)
    assert [row[3] for row in failed[1:]] == ["stack_restart"]


def test_audit_page_context_tabs_counts_and_errors(env, monkeypatch):
    s, c = env
    login(s, c)
    seen = {}
    real = auditpage.html

    def spy(request, name, **ctx):
        seen.clear()
        seen.update(ctx, _name=name)
        return real(request, name, **ctx)

    monkeypatch.setattr(auditpage, "html", spy)
    assert c.get("/audit?tab=errors&ce_kind=js").status_code == 200
    assert seen["_name"] == "audit.html" and seen["tab"] == "errors"
    assert seen["tab_counts"]["errors"] == 3 and seen["tab_counts"]["activity"] == seen["ap"].counts["all"] >= 4
    assert seen["ce_kind"] == "js" and seen["ce"]["total"] == 2 and seen["kinds"][-1] == "csp"
    g = seen["ce_groups"]
    assert g["total"] == 1 and g["rows"][0]["count"] == 2 and g["rows"][0]["pages"] == ["/b", "/a"]
    assert g["counts"]["csp"] == 1 and seen["page_size"] == 50 and seen["ce_page_size"] == 25
    c.get("/audit?tab=bogus&cat=control&q=pv")
    assert seen["tab"] == "activity" and seen["f_cat"] == "control" and seen["ap"].total == 1
    assert seen["tab_counts"]["errors"] == 1  # the page's one query filters both tabs ("/p/pv")
    assert seen["ap"].counts == {"all": 1, "ok": 0, "denied": 1, "refused": 0, "failed": 1, "attempt": 0}


def test_classic_page_ignores_the_tab_param(env):
    s, c = env
    login(s, c)
    strip = lambda t: re.sub(r'content="[0-9a-f]{32,}"|"csrf": "[0-9a-f]+"', "", t)  # noqa: E731
    assert strip(c.get("/audit?tab=errors").text) == strip(c.get("/audit").text)
    frag = c.get("/audit?q=agent", headers={"HX-Request": "true"}).text
    assert "agent_kill" in frag and "stack_restart" not in frag and "<html" not in frag.lower()
