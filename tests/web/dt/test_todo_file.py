"""dt project TODO tab of a file-mode project (TODO.md, not in the tracker): the Backlog component lists its items with
the line index and text that the core's POST /p/{n}/todo/toggle checks, a tick through that route rewrites TODO.md, and
the classic #todo link (the core's redirect after a toggle, the Backlog's "Open project") opens the TODO tab
(js/project.js)."""

from __future__ import annotations

import re

from pm.web import themes

from .kit import Doc

PROJECT_JS = themes.HERE / "dt" / "static" / "js" / "project.js"
TODO_FRAG = {"HX-Request": "true", "HX-Target": "bl-todo"}


def _todo(app_env, status="open"):
    r = app_env.c.get(f"/backlog?scope=project&tab=todo&project=plain&todo={status}", headers=TODO_FRAG)
    assert r.status_code == 200, r.text[:300]
    return r.text


def test_file_mode_todo_lists_and_ticks(app_env):
    path = app_env.s.dev_root / "plain" / "TODO.md"
    path.parent.mkdir()
    path.write_text("# TODO\n\n## Now\n\n- [ ] Write the tests\n- [x] Set up\n- [ ] Ship it\n")
    html = _todo(app_env)
    rows = Doc(html).find("div", data_row_id=True)
    assert [(r["data-row-id"], r["data-bl-line"], r["data-bl-text"]) for r in rows] == [
        ("plain:l4", "4", "Write the tests"), ("plain:l6", "6", "Ship it")]
    assert html.count("data-bl-tick") == 2 and "keeps its tasks in TODO.md" not in html
    r = app_env.post("/p/plain/todo/toggle", line="4", text="Write the tests")
    assert r.status_code == 303 and r.headers["location"] == "/p/plain#todo"
    assert "- [x] Write the tests" in path.read_text()
    assert [r["data-row-id"] for r in Doc(_todo(app_env)).find("div", data_row_id=True)] == ["plain:l6"]
    done = Doc(_todo(app_env, "done")).find("div", data_row_id=True)
    assert [r["data-row-id"] for r in done] == ["plain:l4", "plain:l5"]
    # the text must still match: an edited line is refused, nothing written
    before = path.read_text()
    assert app_env.post("/p/plain/todo/toggle", line="6", text="Ship it later").status_code == 409
    assert path.read_text() == before


def test_classic_todo_hash_opens_the_todo_tab():
    js = PROJECT_JS.read_text()
    body = js[js.index("function classic()"):]
    assert re.search(r"if \(p\.has\('todo'\)\) \{.*?if \(!tab\('todo'\)\) window\.location\.assign\("
                     r"'/backlog\?tab=todo&project=' \+ encodeURIComponent\(name\(\)\)\);\s*return true;", body, re.S)
