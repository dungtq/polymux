"""dt Backlog (PRD.md §6.5; README O7): backlog.html (page or component by HX-Target), backlog/_issues.html,
_todo.html, the issue reader (_issue_detail.html -> backlog/_issue_reader.html), the project tab partials, and the view
model pm.web.dt.backlog, rendered by the app with the real dt templates over tracker projects in a tmp dev root.
Every write goes through the core's tracker routes; the tests post to them and read the regenerated files."""

from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path

import jinja2
import pytest

from pm.projects.tracker import mdimport
from pm.web import themes
from pm.web.dt import DtTheme, backlog

from .conftest import make_env, make_snap

THEME = themes.HERE / "dt"
TPL = THEME / "templates"
JS = THEME / "static" / "js" / "backlog.js"
RJS = THEME / "static" / "js" / "backlog-reader.js"
CSS = THEME / "static" / "css" / "backlog.css"

BACKLOG = """# Backlog

## Inbox

- [ ] First issue (2026-10-01)
- [ ] Second `code` issue

## Done

- [x] Old one
"""
TODO = """# TODO

## Now

- [ ] Write tests
- [x] Set up
- [ ] Ship it

## Later

- [ ] Port the page
"""


@pytest.fixture
def benv(settings, snap):
    class RealDt(DtTheme):
        def themes(self):
            return [themes.Theme(name="dt", templates=TPL, static=THEME / "static", label="dt")]

    for name in ("alpha", "beta"):
        d = settings.dev_root / name
        d.mkdir()
        (d / "BACKLOG.md").write_text(BACKLOG)
        (d / "TODO.md").write_text(TODO)
    (settings.dev_root / "plain").mkdir()  # file mode: TODO.md only, not in the tracker
    (settings.dev_root / "plain" / "TODO.md").write_text("# TODO\n\n- [ ] File task\n")
    for name in ("alpha", "beta"):
        mdimport.import_project(settings, name, settings.dev_root / name)
    snap.value = make_snap(projects=("alpha", "beta", "plain"))
    return make_env(settings, RealDt())


def _rows(html: str) -> list[str]:
    return re.findall(r'class="p-gr [^"]*" role="row" data-row-id="([^"]+)"', html)


def _menu(html: str, rid: str) -> dict:
    i = html.index(f'data-row-id="{rid}"')
    m = re.search(r"data-menu='([^']*)'", html[i:])
    return {x["id"]: x for x in json.loads(unescape(m.group(1))) if "id" in x}


FRAG = {"HX-Request": "true", "HX-Target": "bl-issues"}


# ---------------------------------------------------------------- the page
def test_page_is_one_table_with_project_bands(benv):
    r = benv.c.get("/backlog")
    assert r.status_code == 200
    html = r.text
    assert "<html" in html and 'id="bl-issues"' in html and "/static/t/dt/js/backlog.js" in html
    assert html.count('role="table"') == 1 and 'data-list="bl-issues"' in html
    assert re.findall(r'data-p-grp="([^"]+)"', html) == ["i:alpha", "i:beta"]
    assert set(_rows(html)) == {"alpha:1", "alpha:2", "beta:1", "beta:2"}  # open only; done #3 left out
    assert "<code>code</code>" in html  # the title's inline Markdown
    assert 'id="bl-add"' in html and 'id="bl-rd"' in html and 'id="bl-addt"' in html  # dialogs outside the list
    assert re.search(r'data-p-tab="issues"[^>]*>.*?<span class="c">4</span>', html, re.S)
    assert "4 open issues" in html and "2 tracker projects" in html
    assert "style=" not in html.split('id="main"')[1] and "<script>" not in html


def test_component_alone_for_its_own_hx_target(benv):
    html = benv.c.get("/backlog?status=all", headers=FRAG).text
    assert "<html" not in html and html.lstrip().startswith('<div class="bl-c bl-issues" id="bl-issues"')
    assert 'data-bl-url="/backlog?status=all"' in html
    assert len(_rows(html)) == 6
    # another HX target (a boosted page load) still gets the page
    assert "<html" in benv.c.get("/backlog", headers={"HX-Request": "true", "HX-Target": "main"}).text


def test_group_none_flat_with_project_column_and_filter(benv):
    html = benv.c.get("/backlog?group=none&q=code", headers=FRAG).text
    assert "data-p-grp" not in html and "bl-iss bl-flat" in html and 'class="chip bl-chip">alpha<' in html
    assert set(_rows(html)) == {"alpha:2", "beta:2"}
    html = benv.c.get("/backlog?q=nothing-matches", headers=FRAG).text
    assert "No issue matches." in html and "Clear the filter" in html


def test_issue_row_actions_and_menu(benv):
    html = benv.c.get("/backlog?status=all", headers=FRAG).text
    m = _menu(html, "alpha:1")
    assert m["dispatch"]["event"] == "bl:dispatch"
    assert m["state"]["post"] == "/p/alpha/issues/1/done" and m["state"]["refresh"] == "#bl-issues"
    assert m["copy"]["copy"] == "pmctl --project alpha issue show 1"
    assert m["project"]["href"].startswith("/p/alpha") and m["move"]["event"] == "bl:move"
    assert m["select"]["event"] == "bl:select"
    done = _menu(html, "alpha:3")
    assert done["dispatch"]["disabled"] and done["state"]["post"] == "/p/alpha/issues/3/reopen"
    assert 'data-key-enter="event:bl:open"' in html and 'data-key-e="menu:state"' in html
    i = html.index('data-row-id="alpha:3"')
    assert 'data-bl-run="state"' in html[i:i + 3000]  # a done issue's visible action is Reopen


def test_agent_on_issue_shows_dot_and_blocks_dispatch(benv, monkeypatch):
    from pm.web import issuectx
    monkeypatch.setattr(issuectx, "agents_on",
                        lambda s, agents, rows, now=None: {("alpha", 1): {"id": "claude-1", "state": "waiting"}})
    html = benv.c.get("/backlog", headers=FRAG).text
    i = html.index('data-row-id="alpha:1"')
    row = html[i:i + 3000]
    assert 'class="dot waiting" title="claude-1 waiting for you"' in row and 'href="/a/claude-1"' in row
    assert _menu(html, "alpha:1")["dispatch"]["why"] == "claude-1 is on it"


def test_done_reopen_move_note_add_are_tracker_writes(benv):
    alpha = benv.s.dev_root / "alpha" / "BACKLOG.md"
    h = {"X-Requested-With": "fetch"}
    assert benv.post("/p/alpha/issues/1/done", headers=h).json()["ok"]
    assert "- [x] First issue" in alpha.read_text()
    assert benv.post("/p/alpha/issues/1/reopen", headers=h).json()["ok"]
    assert benv.post("/p/alpha/issues/1/note", headers=h, body="a note").json()["ok"]
    r = benv.post("/p/alpha/issues/2/move", headers=h, to="beta").json()
    assert r["ok"] and r["project"] == "beta" and "Second" in (benv.s.dev_root / "beta" / "BACKLOG.md").read_text()
    assert benv.post("/p/alpha/issues", headers=h, title="From the form", body="x", images="").json()["num"]
    html = benv.c.get("/backlog", headers=FRAG).text
    assert "From the form" in html and "alpha:2" not in _rows(html)
    i = html.index('data-row-id="alpha:1"')
    assert 'title="1 note"' in html[i:i + 3000]


# ---------------------------------------------------------------- TODO
def test_todo_tree_ticks_and_dispatch_index(benv):
    html = benv.c.get("/backlog?tab=todo&todo=all", headers={"HX-Request": "true", "HX-Target": "bl-todo"}).text
    assert html.lstrip().startswith('<div class="bl-c bl-todo" id="bl-todo"')
    assert "s:alpha:Now" in html and "s:alpha:Later" in html and "t:plain" in html
    # the open index counts the project's open items in file order (POST /p/{n}/dispatch index)
    ix = dict(re.findall(r'data-row-id="(alpha:t\d+)"[^>]*?data-bl-ix="(\d+)"', html, re.S))
    rows = re.findall(r'data-row-id="(alpha:t\d+)"', html)
    assert len(rows) == 4 and list(ix.values()) == ["0", "1", "2"]
    assert 'data-row-id="plain:l2"' in html  # file mode: the line index
    assert 'data-key-e="event:bl:tick"' in html and 'data-p-filter-rows="#bl-todo"' in html
    # tick through the core route; TODO.md regenerates
    tid = rows[0].split(":t")[1]
    r = benv.post("/p/alpha/todo/toggle", line=tid, text="Write tests")
    assert r.status_code == 303 and "- [x] Write tests" in (benv.s.dev_root / "alpha" / "TODO.md").read_text()


def test_todo_hash_column_only_when_a_task_has_a_number(benv):
    hdr = re.compile(r'role="columnheader"[^>]*>#<')
    html = benv.c.get("/backlog?tab=todo&todo=all", headers={"HX-Request": "true", "HX-Target": "bl-todo"}).text
    assert hdr.search(html) and "bl-nonum" not in html  # tracker items carry their number
    html = benv.c.get("/backlog?scope=project&tab=todo&project=plain",
                      headers={"HX-Request": "true", "HX-Target": "bl-todo"}).text
    assert not hdr.search(html) and "bl-nonum" in html and 'class="gc-end"' not in html  # TODO.md only: no column


def test_add_task_posts_the_core_route_and_falls_back_on_409(benv):
    html = benv.c.get("/backlog?tab=todo").text
    i = html.index('id="bl-addt"')
    dlg = html[i:html.index("</dialog>", i)]
    assert "data-bl-taskform" in dlg and 'data-bl-tfall hidden' in dlg and "data-bl-tcopy hidden" in dlg
    assert ">Add task</button>" in dlg and "cannot add" not in dlg  # a real form, not only the copy line
    # the route the dialog posts to (backlog.js addTask): 303 on success, the item lands under its section
    r = benv.post("/p/alpha/todo/add", text="From the web", section="Later")
    assert r.status_code == 303
    todo = (benv.s.dev_root / "alpha" / "TODO.md").read_text()
    assert todo.index("## Later") < todo.index("- [ ] From the web")
    html = benv.c.get("/backlog?tab=todo", headers={"HX-Request": "true", "HX-Target": "bl-todo"}).text
    assert "From the web" in html
    # a file-mode project: 409, which the dialog turns into the pmctl fallback
    assert benv.post("/p/plain/todo/add", text="x").status_code == 409
    js = JS.read_text()
    assert "'/todo/add'" in js and "status === 409" in js and "refresh('bl-todo')" in js
    body = js[js.index("async function addTask"):js.index("/* ---------- events")]
    assert body.index("res.ok") < body.index("pm7.toast(")  # the toast only after the server said OK


def test_todo_ix_only_when_every_open_item_is_listed():
    lst = {"groups": [{"name": "a", "tracker": True, "open_n": 1, "done_n": 1, "sections": [
        {"heading": "S", "open_n": 1, "done_n": 1, "items": [
            {"line": 1, "num": 1, "done": True, "text": "x", "tracker": True}]}]}]}
    st = backlog.State("page", "todo", "done", "", "project", "", 0)
    tree = backlog.todo_tree(lst, st)
    assert tree[0].sections[0].items[0].index is None


# ---------------------------------------------------------------- project scope
def test_project_scope_component(benv):
    html = benv.c.get("/backlog?scope=project&project=alpha", headers=FRAG).text
    assert 'data-bl-scope="project"' in html and "p-head" not in html and 'role="tablist"' not in html
    assert 'name="project"' not in html and 'data-bl-add="issue"' in html
    assert set(_rows(html)) == {"alpha:1", "alpha:2"} and "data-p-grp" not in html
    assert 'data-bl-url="/backlog?scope=project&amp;project=alpha"' in html
    html = benv.c.get("/backlog?scope=project&tab=todo&project=plain",
                      headers={"HX-Request": "true", "HX-Target": "bl-todo"}).text
    assert "keeps its tasks in TODO.md" not in html and "plain:l2" in html  # a file-mode TODO still lists
    html = benv.c.get("/backlog?scope=project&project=plain", headers=FRAG).text
    assert "keeps its issues in BACKLOG.md" in html and "data-bl-add" not in html


def test_project_tab_partials_are_content_only():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TPL), autoescape=True)
    for key in ("issues", "todo"):
        src = (TPL / "project" / f"_tab_{key}.html").read_text()
        assert "<section" not in src and "pj-tab" not in src.split("-#}")[1]
        html = env.get_template(f"project/_tab_{key}.html").render(v={"name": "a b"})  # project.html's dt_project() view model
        assert f'id="bl-{key}"' in html
        assert f'data-bl-lazy="/backlog?scope=project&amp;tab={key}&amp;project=a%20b"' in html


# ---------------------------------------------------------------- reader
def test_issue_reader_fragment(benv):
    benv.post("/p/alpha/issues/1/note", headers={"X-Requested-With": "fetch"}, body="see `x`")
    html = benv.c.get("/p/alpha/issues/1", headers={"HX-Request": "true"}).text
    assert 'data-bl-rd data-bl-p="alpha" data-bl-n="1"' in html and 'id="bl-rd-t"' in html
    assert 'data-bl-rda="done"' in html and 'data-bl-rda="dispatch"' in html
    assert 'data-bl-copy="pmctl --project alpha issue show 1"' in html
    assert "<code>x</code>" in html and "data-bl-noteform" in html and "No description." in html
    assert 'data-bl-rda="edit"' in html and 'data-bl-rda="delete"' in html and "data-bl-rderr" in html


def test_reader_close_is_in_the_dialog_frame(benv):
    """D1: Close lives in dialog#bl-rd's fixed head, outside the body that loads, fails (409 for a project without the
    tracker) or reloads, so the reader can always be closed (the phone's dialog is full screen); the loaded body has
    no second Close, and the reader focuses Close on open (not the dialog itself)."""
    html = benv.c.get("/backlog").text
    dlg = html[html.index('<dialog id="bl-rd"'):html.index("</dialog>", html.index('<dialog id="bl-rd"'))]
    head, body = dlg.split("data-bl-rdbody", 1)
    assert 'class="x-dh bl-rdf"' in head and 'data-act="close" aria-label="Close"' in head
    assert "data-bl-rdno" in head and "data-bl-rdp" in head and 'id="bl-rd-h"' in head
    assert 'aria-labelledby="bl-rd-h bl-rd-t"' in dlg and "data-act" not in body
    frag = benv.c.get("/p/alpha/issues/1", headers={"HX-Request": "true"}).text
    assert 'data-act="close"' not in frag and 'id="bl-rd-t"' in frag and 'id="bl-rd-m"' in frag
    js = RJS.read_text()
    assert "focus: closeBtn(dl)" in js and ".bl-rdf [data-act=close]" in js
    i = js.index("async function load(")
    assert "replaceChildren(line('bl-alert'" in js[i:js.index("function reader(", i)]  # a refusal keeps the head
    css = CSS.read_text()
    assert ".x-dlg.bl-rdd[open]{display:flex;flex-direction:column;overflow:hidden}" in css
    assert ".bl-rdb{flex:1 1 auto;min-height:0;" in css  # the body scrolls, the head stays


def test_reader_edit_form_holds_the_raw_title_and_body(benv):
    h = {"X-Requested-With": "fetch"}
    assert benv.post("/p/alpha/issues/2/edit", headers=h, title="Second `code` issue",
                     body="Line *one*\n\n<b>raw</b>").json()["ok"]
    html = benv.c.get("/p/alpha/issues/2", headers={"HX-Request": "true"}).text
    form = html[html.index("data-bl-editform"):html.index("</form>", html.index("data-bl-editform"))]
    assert "hidden" in form.split(">", 1)[0] and 'value="Second `code` issue"' in form
    assert ">Line *one*\n\n&lt;b&gt;raw&lt;/b&gt;</textarea>" in form  # raw Markdown, escaped once
    assert "<em>one</em>" in html  # the body view stays the rendered Markdown
    html = benv.c.get("/p/alpha/issues/1", headers={"HX-Request": "true"}).text
    assert 'value="First issue (2026-10-01)"' in html  # the stored title keeps its date suffix on save


def test_reader_edit_and_delete_are_core_writes(benv):
    h, alpha = {"X-Requested-With": "fetch"}, benv.s.dev_root / "alpha" / "BACKLOG.md"
    r = benv.post("/p/alpha/issues/1/edit", headers=h, title="Renamed (2026-10-01)", body="new body")
    assert r.json()["ok"] and "Renamed" in alpha.read_text()
    r = benv.post("/p/alpha/issues/1/edit", headers=h, title="  ", body="x")
    assert r.status_code >= 400 and not r.json()["ok"] and r.json()["error"]  # the reader shows this refusal
    assert benv.post("/p/alpha/issues/1/delete", headers=h).json()["ok"]
    assert "Renamed" not in alpha.read_text()
    html = benv.c.get("/backlog", headers=FRAG).text
    assert "alpha:1" not in _rows(html) and "alpha:2" in _rows(html)
    r = benv.post("/p/alpha/issues/1/delete", headers=h)
    assert r.status_code == 404 and not r.json()["ok"]


def test_reader_js_edit_delete_contract():
    js = RJS.read_text()
    for url in ("'/edit'", "'/delete'", "'/note'"):
        assert url in js
    assert "typed: '#' + i.n" in js  # V7-SEC-6: the delete confirm names the issue
    # V7-CODE-5: the delete / save toasts come after the server's OK, refusals go to the reader
    i = js.index("'/delete'")
    assert js.index("if (!res.ok)", i) < js.index("pm7.toast('Deleted", i)
    i = js.index("'/edit'")
    assert js.index("if (!res.ok)", i) < js.index("saved')", i)
    assert "pm7.backlog.reader = reader;" in js and "B.post(" in js and "async function post" not in js
    assert "reader(i.p, i.n, r)" in JS.read_text()  # bl:open goes through the shared API G1 also calls
    for name in ("backlog/_page.html", "project/_tab_issues.html"):
        src = (TPL / name).read_text()
        a, b = src.index("js/backlog.js"), src.index("js/backlog-reader.js")
        assert a < b  # the reader file needs backlog.js's pm7.backlog.lib


def test_issue_links_use_the_project_hash_form(benv):
    """The links "Save as issue" (core JSON url, the Audit page's own button) and the Audit rows hand out are
    /p/<n>#issue=N; the project view maps that hash to its Issues tab and pm7.backlog.reader (G1)."""
    from pm.web.dt import audit
    j = benv.post("/p/alpha/issues", headers={"X-Requested-With": "fetch"}, title="t", body="", images="").json()
    assert re.fullmatch(r"/p/alpha#issue=\d+", j["url"]) and j["url"].endswith(f"#issue={j['num']}")
    assert audit.target_link("issue_add", "issues", {"project": "a b", "num": 7}).href == "/p/a%20b#issue=7"
    assert "'/p/' + encodeURIComponent(name) + '#issue=' + j.num" in (THEME / "static" / "js" / "audit.js").read_text()


# ---------------------------------------------------------------- view model
def test_state_urls_leave_defaults_out():
    st = backlog.State("page", "issues", "open", "", "project", "", 0)
    assert st.url() == "/backlog"
    assert st.url(status="done", q="a b") == "/backlog?status=done&q=a+b"
    assert st.url(tab="todo", status="all") == "/backlog?tab=todo&todo=all"
    assert backlog.State("project", "todo", "open", "x", "none", "", 0).url() == \
        "/backlog?scope=project&tab=todo&project=x"


def test_busy_tracker_page_says_so():
    v = backlog.page({"issue_list": None, "todo_list": None})  # the core hands None while pm.db is busy
    assert not v.ok and v.issues == [] and v.add is None


# ---------------------------------------------------------------- static rules
def test_js_uses_the_shared_helpers_only():
    for f in (JS, RJS):
        js = f.read_text()
        assert "addEventListener('keydown'" not in js and "setInterval" not in js and "fetch(" not in js.replace(
            "pm7.fetch(", "")
        assert "confirm(" not in js.replace("pm7.confirm(", "") and "alert(" not in js
        assert max(len(x) for x in js.splitlines()) <= 120 and len(js.splitlines()) <= 500, f.name
    assert "if (!pm7 || pm7.backlog) return;" in JS.read_text()  # once, however many partials load it
    assert "pm7.backlog.reader) return;" in RJS.read_text()


def test_css_avoids_classic_backlog_names():
    css = CSS.read_text()
    for cls in ("bl-item", "bl-box", "bl-seg", "bl-group", "bl-card", "bl-head", "bl-err", "bl-md", "bl-num"):
        assert not re.search(r"\." + cls + r"\b", css.split("*/", 1)[1]), cls
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgb\(|z-index", css)
