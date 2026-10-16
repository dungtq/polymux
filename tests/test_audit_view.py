import json

from pm.security import audit_view


def _w(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _row(ts, result, action="terminal.ticket", client="c1", target=""):
    return {"ts": ts, "client": client, "action": action, "target": target, "result": result, "detail": {}}


def test_paging_newest_first_and_total(tmp_path):
    p = tmp_path / "a.jsonl"
    _w(p, [_row(i, "ok", action=f"a{i}") for i in range(1, 121)])
    p1 = audit_view.read_page(p, 1)
    assert [r["ts"] for r in p1.rows][:2] == [120, 119] and len(p1.rows) == 50
    assert p1.total == 120 and p1.has_older
    p3 = audit_view.read_page(p, 3)
    assert len(p3.rows) == 20 and not p3.has_older and p3.rows[-1]["ts"] == 1


def test_missing_and_garbage_lines(tmp_path):
    assert audit_view.read_page(tmp_path / "none", 1).rows == []
    p = tmp_path / "a.jsonl"
    p.write_text('not json\n' + json.dumps(_row(1, "ok")) + "\n\n[1]\n")
    assert len(audit_view.read_page(p).rows) == 1


def test_reverse_read_small_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_view, "_CHUNK", 16)
    p = tmp_path / "a.jsonl"
    _w(p, [_row(i, "ok") for i in range(30)])
    assert [e["ts"] for e in audit_view.iter_reverse(p)] == list(range(29, -1, -1))


def test_filters(tmp_path):
    p = tmp_path / "a.jsonl"
    _w(p, [_row(1, "ok", "tmux.new"), _row(2, "denied", "tmux.kill"), _row(3, "error", "git.push"),
           _row(4, "attempt", "git.push", client="c9")])
    assert [r["ts"] for r in audit_view.read_page(p, result="ok").rows] == [1]
    assert [r["ts"] for r in audit_view.read_page(p, result="denied").rows] == [3, 2]
    assert [r["ts"] for r in audit_view.read_page(p, result="attempt").rows] == [4]
    assert [r["ts"] for r in audit_view.read_page(p, action="TMUX").rows] == [2, 1]


def test_attempt_collapse_display_only(tmp_path):
    p = tmp_path / "a.jsonl"
    rows = [_row(1, "attempt"), _row(2, "ok", target="$1"), _row(3, "attempt", client="c2"),
            _row(4, "attempt"), _row(5, "denied", target="x")]
    _w(p, rows)
    before = p.read_text()
    pg = audit_view.read_page(p)
    assert [r["ts"] for r in pg.rows] == [5, 3, 2] and pg.hidden == 2  # c2 attempt has no outcome
    shown = audit_view.read_page(p, show_attempts=True)
    assert [r["ts"] for r in shown.rows] == [5, 4, 3, 2, 1] and shown.rows[1]["_hidden"]
    assert p.read_text() == before


def test_scan_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_view, "SCAN_CAP", 10)
    p = tmp_path / "a.jsonl"
    _w(p, [_row(i, "ok") for i in range(100)])
    pg = audit_view.read_page(p)
    assert pg.truncated and pg.total == 10



def _det(ts, result, action="a.x", client="c1", target="", **detail):
    return {"ts": ts, "client": client, "action": action, "target": target, "result": result, "detail": detail}


def test_filter_words_cover_target_client_detail_casefolded(tmp_path):
    p = tmp_path / "a.jsonl"
    _w(p, [_det(1, "ok", "agent_message", target="claude-0e1da5", outbox=77),
           _det(2, "ok", "stack_restart", client="192.0.2.12", target="web", scope="Docker"),
           _det(3, "denied", "login_token", reason="bad token")])
    ts = lambda q: [r["ts"] for r in audit_view.read_page(p, action=q).rows]  # noqa: E731
    assert ts("claude-0E1") == [1] and ts("192.0.2") == [2] and ts("docker") == [2]  # target, client, detail value
    assert ts("BAD token") == [3] and ts("bad web") == [] and ts("restart web") == [2]  # every word must match
    assert ts("scope") == [] and ts("77") == [1]  # detail values, not keys


def test_result_classes_refused_failed_and_classic_denied(tmp_path):
    p = tmp_path / "a.jsonl"
    _w(p, [_row(1, "ok"), _row(2, "denied"), _row(3, "error"), _row(4, "weird"), _row(5, "attempt", client="c9")])
    ts = lambda r: [e["ts"] for e in audit_view.read_page(p, result=r).rows]  # noqa: E731
    assert ts("denied") == [4, 3, 2]  # classic meaning kept: everything not ok or attempt
    assert ts("refused") == [2] and ts("failed") == [4, 3] and ts("nonsense") == [5, 4, 3, 2, 1]
    assert audit_view.result_class("error") == "failed" and audit_view.result_class("denied") == "refused"


def test_counts_match_each_result_total(tmp_path):
    p = tmp_path / "a.jsonl"
    rows = [_row(1, "attempt"), _row(2, "ok"), _row(3, "attempt", client="c2"), _row(4, "attempt"),
            _row(5, "denied"), _row(6, "error", action="git.push"), _row(7, "ok", action="git.pull")]
    _w(p, rows)
    for attempts in (False, True):
        for q in ("", "git"):
            c = audit_view.read_page(p, action=q, show_attempts=attempts).counts
            assert set(c) == set(audit_view.RESULTS)
            for r in audit_view.RESULTS:
                assert c[r] == audit_view.read_page(p, result=r, action=q, show_attempts=attempts).total, (r, q)
    c = audit_view.read_page(p).counts
    assert c == {"all": 5, "ok": 2, "denied": 2, "refused": 1, "failed": 1, "attempt": 3}


def test_category_map_and_cat_param(tmp_path):
    cat = audit_view.category
    assert [cat(a) for a in ("agents_rescan", "agents_apply", "agent_message", "agent_autoexit")] == ["agents"] * 4
    assert [cat(a) for a in ("stack_restart", "container_start", "proc_prog_term", "proc_signal", "agent_kill",
                             "agent_stop", "tmux_session_kill", "pm_reload", "vm_start", "pvm_delete")] == ["control"] * 10
    assert [cat(a) for a in ("session_revoke", "ssh_hostkey_repin", "prefs_save", "passkey_revoke")] == ["settings"] * 4
    assert [cat(a) for a in ("login_token", "login_passkey", "logout", "passkey_auth_finish")] == ["login"] * 4
    assert [cat(a) for a in ("terminal.ticket", "tmux_window_new")] == ["terminal"] * 2
    assert [cat(a) for a in ("issue_add", "todo_toggle", "tracker_done", "git_push")] == ["files"] * 4
    assert cat("inbox_read") == "other" and cat("") == "other"
    assert cat("dt.prefs.save") == "settings" and cat("dt.tabs.close") == "other"  # plugin namespace dropped
    assert set(audit_view.CATEGORIES) >= {cat(a) for a in ("x", "agent_x", "login_x")}
    p = tmp_path / "a.jsonl"
    _w(p, [_row(1, "ok", "agent_message"), _row(2, "ok", "agent_kill"), _row(3, "denied", "login_token")])
    pg = audit_view.read_page(p, cat="control")
    assert [r["ts"] for r in pg.rows] == [2] and pg.counts["all"] == 1
    assert audit_view.read_page(p, cat="bogus").total == 3  # unknown category: no filter


def test_csv_lines_filters_and_formula_guard(tmp_path):
    import csv
    p = tmp_path / "a.jsonl"
    _w(p, [_det(1, "ok", "agent_message", target="=HYPERLINK(1)", note="@x"),
           _det(2, "denied", "agent_kill", client="+1", target="-3"), _det(3, "ok", "login_token")])
    rows = list(csv.reader("".join(audit_view.csv_lines(p, q="agent")).splitlines()))
    assert rows[0] == list(audit_view.CSV_COLUMNS) and len(rows) == 3
    kill, msg = rows[1], rows[2]
    assert kill[2:7] == ["'+1", "agent_kill", "control", "'-3", "denied"] and kill[0] == "1970-01-01T00:00:02Z"
    assert msg[5] == "'=HYPERLINK(1)" and json.loads(msg[7]) == {"note": "@x"}
    assert [r[3] for r in csv.reader("".join(audit_view.csv_lines(p, result="refused")).splitlines())][1:] == [
        "agent_kill"]
    assert [audit_view.csv_cell(v) for v in ("\tx", "\rx", "ok", 5, "a=b")] == ["'\tx", "'\rx", "ok", "5", "a=b"]

# ---------------------------------------------------------------- /audit page: filter box, live list, pager
import re  # noqa: E402

from helpers import login, make_client, make_settings  # noqa: E402
from helpers import pages_env as env  # noqa: E402,F401  (fixture)

from pm.core.models import AuditEntry  # noqa: E402
from pm.security.audit import AuditLog  # noqa: E402

HX = {"HX-Request": "true"}


def _fill(s):
    log = AuditLog(s.audit_file)
    for i in range(60):
        log.write(AuditEntry(1000.0 + i, "1.2.3.4", f"act.{i}", "", "ok", {}))
    log.write(AuditEntry(2000.0, "9.9.9.9", "bad.thing", "", "denied", {}))


def test_audit_page_has_the_live_filter_box(env):
    s, c = env
    login(s, c)
    _fill(s)
    h = c.get("/audit?q=act.1").text
    form = re.search(r'<form class="aud-filter"[^>]*>.*?</form>', h, re.S).group(0)
    assert 'action="/audit"' in form and 'data-filter-live data-filter-target="#aud-list"' in form
    assert 'name="q" data-filter value="act.1"' in form  # the box, restored from ?q=
    assert ">Filter</button>" not in h and 'class="aud-clear"' not in h and 'name="action"' not in h
    assert form.index("data-filter") < form.index("aud-seg")  # the box is the first control
    assert 'href="/audit?result=ok&amp;q=act.1" data-qlink' in form  # chips keep the query
    assert re.search(r'<div id="aud-list" hx-get="/audit\?result=all&amp;q=act.1" hx-trigger="pm:refresh"', h)
    assert "act.10<" in h and "bad.thing" not in h
    assert 'hx-get="/audit/client-errors?q=act.1"' in h  # Client errors starts with the same query


def test_audit_fragment_is_only_the_list_and_action_alias_still_works(env):
    s, c = env
    login(s, c)
    _fill(s)
    f = c.get("/audit?q=BAD", headers=HX).text
    assert "bad.thing" in f and "act.5<" not in f and "<form" not in f and "<html" not in f.lower()
    assert 'class="aud-pager"' in f and "1 entries" in f
    assert "bad.thing" in c.get("/audit?result=denied&action=BAD").text  # old ?action= links
    p2 = c.get("/audit?page=2&q=act", headers=HX).text
    assert 'href="/audit?page=1&amp;result=all&amp;q=act"' in p2  # pager keeps q


def _ce_client(fake_tmux, tmp_path):
    s = make_settings(fake_tmux, tmp_path, state="st", disable_poll=True)
    (tmp_path / "dev").mkdir(exist_ok=True)
    c = make_client(s, logged_in=True)
    for i, (k, msg) in enumerate([("js", "alpha boom"), ("htmx", "beta fail"), ("js", "gamma")]):
        s.client_errors_file.parent.mkdir(parents=True, exist_ok=True)
        with s.client_errors_file.open("a") as fh:
            fh.write(json.dumps({"ts": 1000 + i, "kind": k, "msg": msg, "url": f"/x/{i}", "page": "/"}) + "\n")
    return c


def test_client_errors_box_q_and_list_only(fake_tmux, tmp_path):
    c = _ce_client(fake_tmux, tmp_path)
    full = c.get("/audit/client-errors?q=boom", headers=HX).text
    assert 'id="ce-panel"' in full and 'data-filter-live data-filter-target="#ce-list"' in full
    assert 'name="q" data-filter value="boom"' in full and ">Filter</button>" not in full
    assert "alpha boom" in full and "beta fail" not in full
    part = c.get("/audit/client-errors?q=beta", headers={**HX, "HX-Target": "ce-list"}).text
    assert part.lstrip().startswith('<div id="ce-list"') and 'id="ce-panel"' not in part and "<form" not in part
    assert "beta fail" in part and "alpha boom" not in part
    assert 'hx-trigger="ce-refresh from:body, pm:refresh"' in part
    assert "alpha boom" in c.get("/audit/client-errors?ce_q=alpha", headers=HX).text  # old ?ce_q= links
    assert "beta fail" not in c.get("/audit/client-errors?ce_kind=js", headers=HX).text


def test_client_errors_navigation_redirects_to_audit(fake_tmux, tmp_path):
    c = _ce_client(fake_tmux, tmp_path)
    r = c.get("/audit/client-errors?q=al%20pha&ce_kind=js", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/audit?q=al+pha#client-errors"
    r = c.get("/audit/client-errors", headers={"Sec-Fetch-Mode": "navigate"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/audit#client-errors"
    assert c.get("/audit/client-errors", headers=HX, follow_redirects=False).status_code == 200
