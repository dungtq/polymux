"""dt Audit page (PRD.md §6.10): audit.html, _audit_list.html (Activity, also the core's HX answer of GET /audit),
_client_errors.html (also GET /audit/client-errors) and the view model pm.web.dt.audit, rendered by the app with
the real dt templates over an audit log and a client error log written into a tmp state dir. Read-only page: the only
POSTs are the core's (client errors clear, issue add); the JS that posts them is checked as text."""

from __future__ import annotations

import json
import re
import time
from html import unescape
from pathlib import Path

import pytest

from pm.core import timefmt
from pm.projects.tracker import mdimport
from pm.projects.tracker import views as tracker_views
from pm.web import themes
from pm.web.dt import DtTheme, audit

from .conftest import make_env

THEME = themes.HERE / "dt"
TPL = THEME / "templates"
JS = THEME / "static" / "js" / "audit.js"
CSS = THEME / "static" / "css" / "audit.css"
HX = {"HX-Request": "true"}
NOW = time.time()


def line(ts, action, target="", result="ok", client="1.2.3.4", **detail) -> dict:
    return {"ts": ts, "client": client, "action": action, "target": target, "result": result, "detail": detail}


ENTRIES = [  # oldest first, as the log is written
    line(NOW - 2 * 86400, "login_token", result="denied", client="10.0.0.9", reason="bad token"),
    line(NOW - 400, "agent_launch", "cursor-agent-a1b2", result="error", error="not logged in"),
    {**line(NOW - 300, "issue_add", "issues", project="pm", num=185), "session": "a1b2c3d4"},  # a session tag
    line(NOW - 250, "terminal.ticket", "$40"),
    line(NOW - 240, "terminal.ticket", "$40"),
    line(NOW - 230, "terminal.ticket", "$40"),
    line(NOW - 200, "container_restart", "ntfy", result="denied", reason="protected"),
    line(NOW - 100, "agent_message", "claude-0e1da5", len=290),
    line(NOW - 10, "dev-pm", "dev-pm"),  # an agent-id-shaped name of no agent kind stays plain
]


@pytest.fixture
def aenv(settings, snap):
    class RealDt(DtTheme):
        def themes(self):
            return [themes.Theme(name="dt", templates=TPL, static=THEME / "static", label="dt")]

    settings.state_dir.mkdir(parents=True, exist_ok=True)
    settings.audit_file.write_text("".join(json.dumps(e) + "\n" for e in ENTRIES))
    errs = [{"ts": NOW - 60 * i, "client": "1.2.3.4", "kind": "js", "page": f"/a/x{i % 2}", "url": "", "method": "",
             "status": 0, "msg": "TypeError: x is null", "line": 84, "col": 17, "stack": "at stick (pane.js:84:17)",
             "ua": "Chrome · Linux"} for i in range(3)]
    errs.append({"ts": NOW - 5, "client": "1.2.3.4", "kind": "fetch", "page": "/", "url": "/qchat/send",
                 "method": "POST", "status": 429, "msg": "POST /qchat/send → 429", "line": 0, "col": 0})
    settings.client_errors_file.write_text("".join(json.dumps(e) + "\n" for e in reversed(errs)))
    _tracker(settings, "alpha")  # one tracker project: Save as alpha issue
    return make_env(settings, RealDt())


def _tracker(s, *names: str) -> None:
    for name in names:
        (s.dev_root / name).mkdir()
        (s.dev_root / name / "BACKLOG.md").write_text("## Inbox\n\n- [ ] first\n")
        mdimport.import_project(s, name, s.dev_root / name, files=("backlog",))


def _rows(html: str) -> list[str]:
    return re.findall(r'class="p-gr [^"]*" role="row" data-row-id="([^"]+)"', html)


def _row(html: str, rid: str) -> str:
    i = html.index(f'data-row-id="{rid}"')
    return html[i:html.index('class="p-det"', i)]


def _menu(html: str, rid: str) -> dict:
    m = re.search(r"data-menu='([^']*)'", _row(html, rid))
    return {x["id"]: x for x in json.loads(unescape(m.group(1))) if "id" in x}


def _rid(i: int) -> str:
    return f"{ENTRIES[i]['ts']:.6f}"


# ---------------------------------------------------------------- the page
def test_page_head_tabs_controls_and_one_table(aenv):
    html = aenv.c.get("/audit").text
    assert "/static/t/dt/css/audit.css" in html and "/static/t/dt/js/audit.js" in html
    assert 'class="x-main "' in html or 'class="x-main"' in html  # a ported page: main without .x-cl
    assert 'id="au-head"' in html and "9 entries" in html and "2 refused" in html and "1 failed" in html
    assert re.search(r'id="au-export"', html) and 'href="/audit/export.csv"' in html
    assert re.search(r'aria-current="page" data-p-tab="activity"', html)
    assert re.search(r'data-p-tab="errors".*?<span class="c bad">4</span>', html, re.S)
    assert html.count('role="table"') == 1 and 'data-list="audit"' in html and 'id="au-act"' in html
    segs = dict(re.findall(r'data-p-seg="(\w+)"[^>]*>[^<]*<span class="c[^"]*">([\d,]+)<', html))
    assert segs == {"all": "9", "ok": "6", "refused": "2", "failed": "1", "attempt": "0"}
    assert re.search(r'data-p-seg="refused".*?<span class="c bad">', html, re.S)
    assert 'data-p-sw="attempts"' in html and 'name="cat"' in html and 'aria-label="View"' in html
    assert "style=" not in html.split('id="main"')[1] and "<script>" not in html


def test_day_bands_with_refused_or_failed_note(aenv):
    html = aenv.c.get("/audit").text
    keys = re.findall(r'data-p-grp="([^"]+)"', html)
    today = timefmt.today()
    assert keys[0] == "d" + today.isoformat() and len(keys) == 2
    assert f"Today · {today.strftime('%m-%d')}" in html
    assert "2 refused or failed" in html and "1 refused or failed" in html and "p-grp au-bad" in html


def test_grouped_folds_runs_and_raw_shows_every_entry(aenv):
    html = aenv.c.get("/audit").text
    rows = _rows(html)
    assert len(rows) == 7 and _rid(5) in rows and _rid(4) not in rows  # the 3 tickets: one row (newest first)
    row = _row(html, _rid(5))
    assert "×3" in row and "$40" in row
    i = html.index(f'data-p-det-for="{_rid(5)}"')
    det = html[i:i + 6000]
    assert "Show the 3 raw entries" in det and det.count("<li>") == 3 and 'href="/audit?view=raw"' in det
    raw = aenv.c.get("/audit?view=raw").text
    assert len(_rows(raw)) == 9 and re.search(r'aria-pressed="true"[^>]*>Raw<', raw)


def test_row_details_every_key_client_session_and_menu(aenv):
    html = aenv.c.get("/audit").text
    i = html.index(f'data-p-det-for="{_rid(1)}"')
    det = html[i:i + 4000]
    for k in ("Result", "Client", "Session", "Action", "Category", "Target", "error"):
        assert f"<dt>{k}</dt>" in det, k
    assert "<dd>—</dd>" in det and "not logged in" in det and "Only agent_launch" in det
    m = _menu(html, _rid(1))
    assert json.loads(m["copy"]["copy"])["action"] == "agent_launch"
    assert m["only"]["href"] == "/audit?q=agent_launch" and m["det"]["det"] is True
    assert m["target"]["href"] == "/a/cursor-agent-a1b2"
    assert 'data-au-run="copy"' in det
    i = html.index(f'data-p-det-for="{_rid(2)}"')
    assert re.search(r"<dt>Session</dt>\s*<dd[^>]*>a1b2c3d4</dd>", html[i:i + 4000])  # the line's session tag
    fold = _menu(html, _rid(5))
    assert len(json.loads(fold["copy"]["copy"])) == 3 and fold["raw"]["href"] == "/audit?view=raw"


def test_target_links(aenv):
    html = aenv.c.get("/audit").text
    assert 'href="/p/pm#issue=185"' in _row(html, _rid(2)) and ">pm #185<" in _row(html, _rid(2))
    assert 'href="/a/claude-0e1da5"' in _row(html, _rid(7))
    assert "<a " not in _row(html, _rid(8)).split('class="gc-act"')[0].split("au-tg")[1]  # dev-pm: plain
    assert audit.target_link("terminal.ticket", "$4", {}, term=False) is None
    assert audit.target_link("terminal.ticket", "$4", {}).href == "/term/s4"
    assert audit.target_link("issue_done", "issues", {"project": "a b", "num": 3}).href == "/p/a%20b#issue=3"
    assert audit.target_link("issue_add", "issues", {"project": "pm", "reason": "x"}) is None


def test_filters_reach_the_core_and_the_fragment_carries_the_head(aenv):
    html = aenv.c.get("/audit?result=refused", headers=HX).text
    assert "<html" not in html and len(_rows(html)) == 2
    assert html.lstrip().startswith('<div class="p-head" id="au-head"')  # out of band (hx-select-oob)
    assert 'hx-select="#au-act" hx-select-oob="#au-head"' in html
    assert 'href="/audit/export.csv?result=refused"' in html
    assert len(_rows(aenv.c.get("/audit?q=ntfy", headers=HX).text)) == 1
    assert len(_rows(aenv.c.get("/audit?cat=terminal&view=raw", headers=HX).text)) == 3
    none = aenv.c.get("/audit?q=nothing-here", headers=HX).text
    assert "No entry matches these filters." in none and "Clear the filters" in none


def test_export_csv_link_downloads(aenv):
    r = aenv.c.get("/audit/export.csv?result=refused")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert r.text.count("\r\n") == 3


def test_paging_load_older(aenv, monkeypatch):
    from pm.security import audit_view
    monkeypatch.setattr(audit_view, "PAGE_SIZE", 4)
    html = aenv.c.get("/audit?view=raw").text
    assert "Showing 1–4 of 9" in html and "Load older (5 more)" in html and 'href="/audit?view=raw&amp;page=2"' in html
    p2 = aenv.c.get("/audit?view=raw&page=2", headers=HX).text
    assert "Showing 5–8 of 9" in p2 and ">Newer<" in p2


# ---------------------------------------------------------------- client errors
def test_errors_tab_first_paint_grouped(aenv):
    html = aenv.c.get("/audit?tab=errors").text
    assert re.search(r'aria-current="page" data-p-tab="errors"', html) and 'id="au-ce"' in html
    assert 'id="au-act"' not in html and 'id="au-clear"' in html and "Export CSV" not in html
    rows = _rows(html)
    assert len(rows) == 2 and all(r.startswith("ce") for r in rows)
    assert "×3" in html and "4 client errors" in html and "2 messages" in html
    assert '<pre class="au-st"' in html and "pane.js:84:17" in html and "Chrome · Linux" in html
    assert "POST /qchat/send → 429" in html
    assert re.search(r'data-p-seg="js"[^>]*>Script<span class="c">3<', html)
    assert 'href="/audit?tab=errors&amp;ce_kind=fetch"' in html
    m = re.search(r'data-au-issue data-au-title="([^"]*)" data-au-body="([^"]*)"', html)
    assert m and unescape(m.group(1)).startswith("Client error: ")
    assert "pane.js:84:17" in unescape(m.group(2))


def test_errors_fragment_and_kind_filter(aenv):
    html = aenv.c.get("/audit/client-errors?ce_kind=fetch", headers={**HX, "HX-Target": "au-ce"}).text
    assert "<html" not in html and len(_rows(html)) == 1 and 'id="au-clear"' in html
    assert 'hx-select="#au-ce" hx-select-oob="#au-head"' in html
    assert 'hx-trigger="pm7:refresh, ce-refresh from:body"' in html


def test_clear_all_is_the_cores_guarded_post(aenv):
    assert aenv.post("/audit/client-errors/clear", csrf=False).status_code == 403
    assert aenv.post("/audit/client-errors/clear").status_code == 204
    html = aenv.c.get("/audit?tab=errors").text
    assert "No client errors." in html
    assert "client_errors_clear" in aenv.s.audit_file.read_text()  # the audit log keeps its lines and gains one


# ---------------------------------------------------------------- the page's JS and CSS
def test_js_uses_the_shared_post_and_toasts_after_ok():
    js = JS.read_text(encoding="utf-8")
    assert "pm7.menus.exec(btn, it)" in js and "'/p/' + encodeURIComponent(p) + '/issues'" in js and "pm7.fail(" in js
    assert "pm7.menus.run(" in js and "'au:issue'" in js and "/p/pm" not in js
    assert "pm7.confirm({" in js and "data-au-projects" in js and "'audit.issueProject'" in js
    assert js.index("pm7.fail('Save as ' + p + ' issue'") < js.index("pm7.toast('Saved as issue #'")
    assert "innerHTML" not in js and "setInterval" not in js
    assert len(js.splitlines()) <= 500 and max(map(len, js.splitlines())) <= 120


def test_clear_all_item_confirms_and_names_the_audit_log(aenv):
    html = aenv.c.get("/audit?tab=errors").text
    m = re.search(r'data-au-item="([^"]*)"', html)
    it = json.loads(unescape(m.group(1)))
    assert it["post"] == "/audit/client-errors/clear" and it["confirm"] and it["refresh"] == "#au-ce"
    assert "The audit log is not touched." in it["body"] and it["done"] and it["danger"]


def test_save_issue_menu_item_reaches_the_details_button(aenv):
    html = aenv.c.get("/audit?tab=errors").text
    rid = _rows(html)[0]
    assert _menu(html, rid)["issue"]["event"] == "au:issue"
    i = html.index(f'data-p-det-for="{rid}"')
    assert "data-au-issue" in html[i:html.index("</dl>", i) + 2000]


def test_save_issue_one_tracker_project_names_it(aenv):
    html = aenv.c.get("/audit?tab=errors").text
    assert _menu(html, _rows(html)[0])["issue"]["label"] == "Save as alpha issue"
    assert html.count('data-au-project="alpha"') == 2 and "data-au-projects" not in html
    assert ">Save as alpha issue</button>" in html and "Save as pm issue" not in html
    m = re.search(r'data-au-title="([^"]*)" data-au-body="([^"]*)"', html)
    r = aenv.post("/p/alpha/issues", headers={"X-Requested-With": "fetch"}, title=unescape(m.group(1)),
                  body=unescape(m.group(2)))  # what js/audit.js sends (pm7.fetch)
    assert r.status_code == 200 and r.json()["num"] == 2 and r.json()["project"] == "alpha"


def test_save_issue_several_tracker_projects_ask(aenv):
    _tracker(aenv.s, "beta")
    html = aenv.c.get("/audit/client-errors", headers={**HX, "HX-Target": "au-ce"}).text
    assert _menu(html, _rows(html)[0])["issue"]["label"] == "Save as issue…"
    m = re.findall(r'data-au-issue [^>]*data-au-projects="([^"]*)"', html)
    assert len(m) == 2 and json.loads(unescape(m[0])) == ["alpha", "beta"] and "data-au-project=" not in html


def test_save_issue_hidden_without_a_tracker_project(aenv, monkeypatch):
    monkeypatch.setattr(tracker_views, "issue_projects", lambda s: [])
    html = aenv.c.get("/audit?tab=errors").text
    rows = _rows(html)
    assert len(rows) == 2 and "data-au-issue" not in html and "Save as" not in html
    assert all("issue" not in _menu(html, r) for r in rows) and "copy" in _menu(html, rows[0])


def test_css_sets_only_its_tables_and_au_classes():
    css = CSS.read_text(encoding="utf-8")
    assert ".au-list{--g-cols:" in css and ".au-cel{--g-cols:" in css
    assert "#" not in re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert max(map(len, css.splitlines())) <= 120


# ---------------------------------------------------------------- view model units
def test_fold_never_crosses_a_day_or_a_result():
    t = 1_760_000_000.0
    es = [line(t, "a"), line(t - 1, "a"), line(t - 2, "a", result="denied"), line(t - 90000, "a")]
    assert [len(r) for r in audit.fold(es, raw=False)] == [2, 1, 1]
    assert [len(r) for r in audit.fold(es, raw=True)] == [1, 1, 1, 1]


def test_view_model_failure_is_none(aenv, monkeypatch):
    monkeypatch.setitem(audit.PARTS, "act", lambda ctx: 1 / 0)
    html = aenv.c.get("/audit").text
    assert "The audit log could not be shown." in html
