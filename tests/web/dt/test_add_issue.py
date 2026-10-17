"""dt "Add an issue" from any page: GET /dt/add-issue?project= (pm.web.dt.routes.add_issue, _add_issue.html) through the
app with the real dt theme, and the search command that opens it (js/keys.js addIssue -> js/backlog-add.js)."""

from __future__ import annotations

import json
import re
import sqlite3

from fastapi.testclient import TestClient

from pm.projects.tracker import mdimport, views
from pm.web import themes

from .kit import Doc

THEME = themes.HERE / "dt"
KEYS = THEME / "static" / "js" / "keys.js"
HX = {"HX-Request": "true"}


def _tracker(s, *names: str) -> None:
    for name in names:
        d = s.dev_root / name
        d.mkdir(exist_ok=True)
        (d / "BACKLOG.md").write_text("## Inbox\n\n- [ ] first\n")
        mdimport.import_project(s, name, d, files=("backlog",))


def _quick(html: str) -> tuple[str, list[str]]:
    q = Doc(html).one("div", id="bl-quick")
    return q["data-bl-project"], json.loads(q["data-bl-projects"])


def test_form_lists_tracker_projects_and_preselects_the_page_project(app_env):
    _tracker(app_env.s, "alpha", "gamma")
    (app_env.s.dev_root / "plain").mkdir()  # no tracker: never offered
    r = app_env.c.get("/dt/add-issue?project=gamma", headers=HX)
    assert r.status_code == 200, r.text[:300]
    assert r.headers["cache-control"] == "no-store"
    assert _quick(r.text) == ("gamma", ["alpha", "gamma"])
    q = Doc(r.text).one("div", id="bl-quick")
    assert "hidden" in q and q["data-bl-scope"] == "page"  # the Project select shows
    # the Backlog's own dialogs and assets (versioned), no second form
    assert 'id="bl-add"' in r.text and 'id="bl-rd"' in r.text and r.text.count("<form") == 1
    for src in ("css/backlog.css", "js/backlog.js", "js/backlog-add.js"):
        assert re.search(rf'"/static/t/dt/{src}\?v=\w+"', r.text), src
    assert "<script>" not in r.text and "style=" not in r.text


def test_unknown_or_file_mode_project_is_not_preselected(app_env):
    _tracker(app_env.s, "alpha")
    for p in ("", "plain", "nope", "../x"):
        assert _quick(app_env.c.get("/dt/add-issue", params={"project": p}, headers=HX).text) == ("", ["alpha"])


def test_no_tracker_project_gives_an_empty_list(app_env):
    assert _quick(app_env.c.get("/dt/add-issue", headers=HX).text) == ("", [])


def test_busy_tracker_is_503(app_env, monkeypatch):
    def busy(_s):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(views, "issue_projects", busy)
    assert app_env.c.get("/dt/add-issue", headers=HX).status_code == 503


def test_needs_a_session_and_dt(app_env):
    anon = TestClient(app_env.c.app, base_url=str(app_env.c.base_url), follow_redirects=False)
    assert anon.get("/dt/add-issue", headers=HX).status_code == 401
    assert app_env.c.get("/dt/add-issue?ui=classic", headers=HX).status_code == 204


def test_search_command_contract():
    js = KEYS.read_text()
    assert "{ id: 'issue', group: 'Go to page', label: 'Add an issue', keys: []" in js  # a command, no key
    assert "pm7.fetch('/dt/add-issue?project='" in js
    assert "pm7.backlog.add.open('issue', q," in js  # the Backlog's own dialog code, not a second one
    assert "getElementById('bl-add')" in js  # its dialogs added only where the page has none
    assert "DOMParser" in js and "innerHTML" not in js  # nothing in the fragment runs
