"""dt Backlog over file-mode projects (BACKLOG.md not in the tracker): their items as rows on the Issues tab (page and
project scope), the core's /backlog/toggle and /backlog/edit as script calls, the whole-file editor page
(backlog/_editor.html) with its lease and conflict states, and js/backlog-file.js's contract. Also the issue reader's
"add images" Edit form (POST /p/{n}/issues/{num}/image)."""

from __future__ import annotations

import json
import re
from html import unescape

import pytest
from helpers import png_bytes

from pm.core import filelock
from pm.core.uploads import store_upload
from pm.projects.tracker import issues, mdimport
from pm.web import themes
from pm.web.dt import DtTheme, backlog

from .conftest import make_env, make_snap

THEME = themes.HERE / "dt"
TPL = THEME / "templates"
FJS = THEME / "static" / "js" / "backlog-file.js"
JS = THEME / "static" / "js" / "backlog.js"
RJS = THEME / "static" / "js" / "backlog-reader.js"

TRACKED = "# Backlog\n\n## Inbox\n\n- [ ] Tracked issue (2026-10-01)\n"
FILE = "# Plain backlog\n\n## Inbox\n\n- [ ] File item `one` (2026-10-02)\n- [x] File done\n\nSome notes.\n"
FRAG = {"HX-Request": "true", "HX-Target": "bl-issues"}
XRW = {"X-Requested-With": "fetch"}


@pytest.fixture
def fenv(settings, snap):
    class RealDt(DtTheme):
        def themes(self):
            return [themes.Theme(name="dt", templates=TPL, static=THEME / "static", label="dt")]

    for name, text in (("alpha", TRACKED), ("plain", FILE)):
        (settings.dev_root / name).mkdir()
        (settings.dev_root / name / "BACKLOG.md").write_text(text)
    mdimport.import_project(settings, "alpha", settings.dev_root / "alpha")
    snap.value = make_snap(projects=("alpha", "plain"))
    return make_env(settings, RealDt())


def _rows(html: str) -> list[str]:
    return re.findall(r'class="p-gr [^"]*" role="row" data-row-id="([^"]+)"', html)


def _menu(html: str, rid: str) -> dict:
    i = html.index(f'data-row-id="{rid}"')
    m = re.search(r"data-menu='([^']*)'", html[i:])
    return {x["id"]: x for x in json.loads(unescape(m.group(1))) if "id" in x}


# ---------------------------------------------------------------- rows on the Issues tab
def test_page_lists_file_items_in_their_own_band(fenv):
    html = fenv.c.get("/backlog").text
    assert re.findall(r'data-p-grp="([^"]+)"', html) == ["i:alpha", "f:plain"]
    assert _rows(html) == ["alpha:1", "plain:l4"]  # open only; the done file item is left out
    band = html[html.index('data-p-grp="f:plain"'):]
    assert "BACKLOG.md" in band[:600] and 'data-bl-editfile="/backlog?p=plain&amp;edit=1"' in band[:2000]
    row = html[html.index('data-row-id="plain:l4"'):]
    row = row[:row.index('data-row-id', 20)] if 'data-row-id' in row[20:] else row
    assert 'data-bl-fl="4"' in row and 'data-bl-text="File item `one` (2026-10-02)"' in row
    assert "data-bl-sel" not in row and "data-bl-n=" not in row and "<code>one</code>" in row
    assert "2 open issues" in html  # the head counts the file's open item too
    assert re.search(r'data-p-tab="issues"[^>]*>.*?<span class="c">2</span>', html, re.S)


def test_file_item_menu_posts_the_core_backlog_routes(fenv):
    html = fenv.c.get("/backlog?status=all", headers=FRAG).text
    assert set(_rows(html)) == {"alpha:1", "plain:l4", "plain:l5"}
    m = _menu(html, "plain:l4")
    assert m["state"]["post"] == "/backlog/toggle" and m["state"]["refresh"] == "#bl-issues"
    assert m["state"]["vals"] == {"project": "plain", "line": 4, "text": "File item `one` (2026-10-02)"}
    assert m["edit"]["event"] == "bl:fedit" and m["edit"]["key"] == "R" and m["state"]["key"] == "E"
    assert m["file"]["href"] == "/backlog?p=plain&edit=1" and "dispatch" not in m and "select" not in m
    assert _menu(html, "plain:l5")["state"]["label"] == "Reopen"
    # what the menu posts works (pm7.fetch = a script call: JSON) and the list follows the file
    r = fenv.post("/backlog/toggle", headers=XRW, **{k: str(v) for k, v in m["state"]["vals"].items()})
    assert r.json() == {"ok": True, "project": "plain"}
    r = fenv.post("/backlog/edit", headers=XRW, project="plain", line="5", text="File done", new_text="Renamed")
    assert r.json()["ok"] is True
    html = fenv.c.get("/backlog?status=done", headers=FRAG).text
    assert set(_rows(html)) == {"plain:l4", "plain:l5"} and "Renamed" in html


def test_filter_project_select_and_counts(fenv):
    html = fenv.c.get("/backlog?q=one", headers=FRAG).text
    assert _rows(html) == ["plain:l4"]
    assert '<option value="plain"' in html  # the Project select offers the file-mode project
    html = fenv.c.get("/backlog?project=plain&status=all", headers=FRAG).text
    assert _rows(html) == ["plain:l4", "plain:l5"]
    segs = dict(re.findall(r'data-p-seg="(\w+)"[^>]*>[^<]*<span class="c">(\d+)</span>', html))
    assert segs == {"open": "1", "done": "1", "all": "2"}
    html = fenv.c.get("/backlog?group=none", headers=FRAG).text
    assert "data-p-grp" not in html and _rows(html) == ["alpha:1", "plain:l4"]


def test_project_scope_on_a_file_project(fenv):
    html = fenv.c.get("/backlog?scope=project&project=plain", headers=FRAG).text
    assert _rows(html) == ["plain:l4"] and "keeps its issues" not in html and "data-bl-add" not in html
    assert 'href="/backlog?p=plain&amp;edit=1"' in html and "Edit BACKLOG.md" in html
    html = fenv.c.get("/backlog?scope=project&project=plain&q=zzz", headers=FRAG).text
    assert "No issue matches." in html or "No open items in BACKLOG.md." in html
    html = fenv.c.get("/backlog?scope=project&project=alpha", headers=FRAG).text
    assert "Edit BACKLOG.md" not in html and 'data-bl-add="issue"' in html
    assert "backlog-file.js" in (TPL / "project" / "_tab_issues.html").read_text()


def test_view_model_file_items_unit():
    ctx = {"entries": [{"name": "p", "blocks": [
        {"kind": "md", "html": "<h1>x</h1>"},
        {"kind": "item", "line": 2, "done": False, "text": "a b", "html": "a b"},
        {"kind": "item", "line": 3, "done": True, "text": "c", "html": "c"}]},
        {"name": "t", "tracker": True, "blocks": [{"kind": "item", "line": 1, "done": False, "text": "x"}]}]}
    st = backlog.State(scope="page", tab="issues", status="open", project="", group="project", q="", limit=0)
    rows, counts = backlog.file_items(ctx, st)
    assert [r.id for r in rows] == ["p:l2"] and counts == {"open": 1, "done": 1, "all": 2}
    assert rows[0].num == 0 and rows[0].line == 2 and rows[0].act["run"] == "state"
    rows, counts = backlog.file_items(ctx, backlog.dataclasses.replace(st, q="A  B", status="all"))
    assert [r.id for r in rows] == ["p:l2"] and counts["all"] == 1
    assert backlog.file_projects(ctx) == ["p"]


# ---------------------------------------------------------------- the whole-file editor
def test_editor_page_saves_through_the_core(fenv):
    r = fenv.c.get("/backlog?p=plain&edit=1")
    assert r.status_code == 200
    html = r.text
    assert "classic" not in html.split('id="main"')[1].split("</main>")[0].lower()
    assert 'id="bl-fe" method="post" action="/backlog/save" data-bl-fe data-project="plain">' in html
    form = html[html.index('id="bl-fe"'):html.index("</form>", html.index('id="bl-fe"'))]
    tok = re.search(r'name="token" value="([0-9a-f]{16})"', form).group(1)
    assert 'name="csrf" value="' in form and 'name="project" value="plain"' in form
    assert "File item `one` (2026-10-02)" in form and "data-readonly" not in form
    assert "/static/t/dt/js/backlog-file.js" in html and "style=" not in html.split('id="main"')[1]
    new = FILE.replace("Some notes.", "Other notes.")
    r = fenv.post("/backlog/save", project="plain", token=tok, content=new, lease="")
    assert r.status_code == 303
    assert (fenv.s.dev_root / "plain" / "BACKLOG.md").read_text() == new


def test_editor_conflict_shows_the_diff_and_overwrite(fenv):
    html = fenv.c.get("/backlog?p=plain&edit=1").text
    tok = re.search(r'name="token" value="([0-9a-f]{16})"', html).group(1)
    (fenv.s.dev_root / "plain" / "BACKLOG.md").write_text(FILE + "- [ ] added meanwhile\n")
    r = fenv.post("/backlog/save", project="plain", token=tok, content="# mine\n", lease="")
    assert r.status_code == 409
    html = r.text
    assert 'class="bl-fe-diff"' in html and "added meanwhile" in html and "data-bl-feforce" in html
    assert 'name="new_token"' in html and "Reload current" in html and "# mine" in html


def test_editor_read_only_under_another_lease(fenv):
    path = fenv.s.dev_root / "plain" / "BACKLOG.md"
    filelock.lease_acquire(fenv.s.state_dir, path, "someone else")
    html = fenv.c.get("/backlog?p=plain&edit=1").text
    assert "Being edited by someone else" in html and 'action="/backlog/lease/take"' in html
    form = html[html.index('id="bl-fe"'):]
    assert "data-readonly" in form.split(">", 1)[0] and " readonly" in form and "data-bl-feforce" not in form


def test_editor_refused_for_a_tracker_project(fenv):
    r = fenv.c.get("/backlog?p=alpha&edit=1")
    assert r.status_code == 409 and "uses the issue tracker" in r.text and 'id="bl-fe"' not in r.text


def test_file_js_contract():
    js = FJS.read_text()
    assert "'/backlog/edit'" in js and "/backlog/lease/" in js and "'acquire'" in js and "'release', true" in js
    assert "window.fetch" not in js and " fetch(" not in js and "csrf-token" not in js
    i = js.index("'/backlog/edit'")
    assert js.index("pm7.fail(", i) < js.index("pm7.toast('Saved", i)  # V7-CODE-5
    assert "danger: true" in js and "data-bl-feforce" in js
    assert "if (i.n && pm7.backlog.reader)" in JS.read_text()  # bl:open on a file row is not a reader
    src = (TPL / "backlog" / "_page.html").read_text()
    assert src.index("js/backlog.js") < src.index("js/backlog-file.js")
    assert all(len(ln) <= 120 for ln in js.splitlines())


# ---------------------------------------------------------------- reader: add images to an existing issue
def test_reader_edit_form_offers_images_and_the_route_attaches(fenv):
    html = fenv.c.get("/p/alpha/issues/1", headers={"HX-Request": "true"}).text
    form = html[html.index("data-bl-editform"):html.index("</form>", html.index("data-bl-editform"))]
    assert "data-bl-pick" in form and "data-bl-edfiles" in form and "data-bl-chips" in form
    js = RJS.read_text()
    assert "pm7.attach.upload('/p/'" in js and "'/image', { path: path }" in js and "pm7.attach.wire(" in js
    assert js.index("await attach(f, i)") < js.index("'/edit'")  # images first, then the text
    path = str(store_upload(fenv.s, png_bytes(), "png"))
    r = fenv.post("/p/alpha/issues/1/image", headers=XRW, path=path)
    assert r.json()["ok"] is True and len(issues.detail(fenv.s, "alpha", 1)["images"]) == 1
    html = fenv.c.get("/p/alpha/issues/1", headers={"HX-Request": "true"}).text
    assert 'class="bl-rdimg"' in html
