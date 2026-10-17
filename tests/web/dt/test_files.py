"""dt project view, Files and Docs tabs and the Agents tab's Ended band (screen-validator M3 fixes): the Files listing
view model (pm.web.dt.files) and the dt overrides of the core's _project_files.html / _project_doc.html through the
app (a grid table with a header, the tree, the breadcrumb, the file view in its own .x-cl box, a notice as the shared
empty-state box), the Docs tab's first document (README, else the first that exists), and Ended as a band of the
agents table (LAYOUT §4 step 5)."""

from __future__ import annotations

from types import SimpleNamespace

from pm.web.dt import files, inbox

from .kit import ENV, Doc, Tree

NOW = 1_000_000.0
HX = {"HX-Request": "true"}


# ---------------------------------------------------------------- view model
def test_listing_rows_crumbs_and_tree():
    tree = {"kind": "dir", "parts": ["docs"], "total": 3, "gs": {"a.md": "M", "img.png": "C", "sub": "?"},
            "entries": [{"name": "sub", "kind": "dir", "size": 0, "mtime": NOW - 60, "btime": NOW - 3600},
                        {"name": "a.md", "kind": "file", "size": 2048, "mtime": NOW - 7200, "btime": 0},
                        {"name": "img.png", "kind": "file", "size": 28, "mtime": 0, "btime": 0}],
            "ftree": [{"name": "docs", "kind": "dir", "rel": "docs", "cur": True, "open": True,
                       "children": [{"name": "a.md", "kind": "file", "rel": "docs/a.md", "cur": False, "open": False,
                                     "children": []}]}]}
    v = files.listing(tree, "/p/alpha", "alpha", now=NOW)
    assert [(c.label, c.href) for c in v.crumbs] == [("alpha", "/p/alpha/files"),
                                                     ("docs", "/p/alpha/files?path=docs")]
    assert [r.id for r in v.rows] == ["..", "docs/sub", "docs/a.md", "docs/img.png"]
    up, sub, md, img = v.rows
    assert up.href == "/p/alpha/files?path=" and up.kind == "up"
    assert (sub.name, sub.icon, sub.size, sub.git, sub.git_cls) == ("sub/", "folder", "", "U", "warn")
    assert (md.href, md.size, md.git, md.git_title, md.modified) == (
        "/p/alpha/files?path=docs%2Fa.md", "2.0 KB", "M", "modified, not committed", "2h")
    assert (img.icon, img.size, img.git, img.git_cls, img.modified, img.mtime) == ("img", "28 B", "✓", "ok", "–", None)
    assert v.path == "docs" and not v.root_cur and v.more == 0
    assert v.tree[0].cur and v.tree[0].dir and v.tree[0].children[0].href == "/p/alpha/files?path=docs%2Fa.md"


def test_listing_of_a_file_has_no_rows_and_an_unlinked_last_crumb():
    v = files.listing({"kind": "file", "parts": ["src", "x.py"], "size": 1536, "ftree": []}, "/p/a", "a", now=NOW)
    assert v.rows == [] and v.size == "1.5 KB"
    assert [(c.label, bool(c.href)) for c in v.crumbs] == [("a", True), ("src", True), ("x.py", False)]
    assert files.hsize(5 * 1024 ** 3) == "5.0 GB" and files.hsize(1023) == "1023 B"


# ---------------------------------------------------------------- through the app (real dt theme)
def test_files_fragment_is_one_grid_table(app_env, tmp_path):  # noqa: F811
    root = tmp_path / "dev" / "alpha"
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('hi')\n")
    r = app_env.c.get("/p/alpha/files", headers=HX)
    assert r.status_code == 200, r.text[:300]
    t = Tree(r.text).root
    assert len(t.by_cls("pj-ft")) == 1  # the tree (hidden on phones)
    tbl = t.one("pj-fl")
    assert tbl.attrs["role"] == "table" and tbl.attrs["data-list"] == "pj-files"
    assert tbl.attrs["data-key-enter"] == "a[data-pj-in]"
    heads = [n.get_text().strip() for n in tbl.one("p-gh").kids]
    assert heads == ["Name", "Git", "Size", "Modified", "Created"]
    rows = tbl.by_cls("p-gr")
    assert [n.attrs["data-row-id"] for n in rows] == ["src", "README.md"]
    assert all(len(n.kids) == 6 and all(k.attrs.get("role") == "cell" for k in n.kids) for n in rows)
    assert not any("x-cl" in n.cls for n in t.all())  # the listing is dt markup, not classic content
    sub = Tree(app_env.c.get("/p/alpha/files?path=src", headers=HX).text).root
    assert [n.attrs["data-row-id"] for n in sub.one("pj-fl").by_cls("p-gr")] == ["..", "src/app.py"]
    assert "p-files" in sub.one("pj-fl").cls  # the shared file-row look (css/table.css), also the quick look's
    crumb = sub.one("p-crumb")
    assert [a.attrs.get("href") for a in crumb.find(lambda n: n.tag == "a")] == ["/p/alpha/files",
                                                                                 "/p/alpha/files?path=src"]
    f = Tree(app_env.c.get("/p/alpha/files?path=src%2Fapp.py", headers=HX).text).root
    assert not f.by_cls("pj-fl") and "x-cl" in f.one("pj-fv").cls and f.by_cls("pj-file")
    assert [n.tag for n in f.one("p-crumb").kids][-1] == "b"  # the file itself: no link


def test_doc_fragment_document_or_empty_box(app_env):  # noqa: F811
    d = Tree(app_env.c.get("/p/alpha/view/readme", headers=HX).text).root
    art = d.find(lambda n: n.tag == "article")
    assert len(art) == 1 and "pj-doc" in art[0].cls and "x-cl" in art[0].parent.cls
    n = Tree(app_env.c.get("/p/alpha/view/changelog", headers=HX).text).root
    box = n.one("pj-note")
    assert box.attrs["role"] == "table" and box.one("p-empty").get_text().strip() == "No CHANGELOG here."


def test_docs_tab_opens_readme(app_env):  # noqa: F811
    doc = Doc(app_env.c.get("/p/alpha?tab=docs").text)
    assert doc.one("button", data_pj_doc="/p/alpha/view/readme")["aria-pressed"] == "true"
    assert doc.one("button", data_pj_doc="/p/alpha/view/changelog")["aria-pressed"] == "false"
    box = doc.one("div", data_pj_docb=True)
    assert "data-pj-lazy" not in box and doc.find("article", class_="pj-doc")


# ---------------------------------------------------------------- Agents tab: Ended is a band of the one table
def test_ended_is_a_band_in_the_agents_table():
    e = inbox.Ended("claude-eeeee1", "claude", "alpha", "done", "2h", "3d", "", True, [])
    v = SimpleNamespace(name="alpha", base="/p/alpha", groups=[], ended=[e], ended_total=1)
    t = Tree(ENV.get_template("project/_agents.html").render(v=v)).root
    tables = t.find(lambda n: n.attrs.get("role") == "table")
    assert len(tables) == 1 and tables[0].attrs["data-p-table"] == "pj-agents"
    band = tables[0].find(lambda n: n.attrs.get("data-p-grp") == "ended")
    assert len(band) == 1
    row = band[0].one("i-erow")
    assert row.attrs["data-key-enter"] == "event:i:open" and row.attrs["data-key-o"] == "event:i:tab"  # aside (#243)
    assert "data-i-ended" in row.attrs and row.attrs["tabindex"] == "0"
    head = tables[0].one("p-gh")
    assert len([k for k in row.kids if "gc-m" not in k.cls]) == len(head.kids)  # same columns as the live rows


# ---------------------------------------------------------------- shared CSS / JS of the M3 fixes
def test_touch_sizes_fades_and_one_line_rows_are_shared():
    from pm.web import themes
    THEME = themes.HERE / "dt"
    css = {n: (THEME / "static" / "css" / f"{n}.css").read_text()
           for n in ("tokens", "shell", "parts", "table", "backlog", "tmux", "launch")}
    assert "--hit:44px" in css["tokens"] and "--seg-h:40px" in css["tokens"]  # README A20
    assert ".x-btn.sm{min-height:var(--ctl-h)}" in css["shell"]  # touch: 40px small buttons
    assert ":is(a,button).chip,.chip:has(>button){min-height:var(--ctl-h)}" in css["shell"]
    assert "inset:calc((var(--icon-sm) - var(--hit)) / 2)" in css["backlog"]  # the tick's 44px target
    # the "more" fades only while a strip overflows, at an edge it is not scrolled to (parts.js: .ovf / .beg / .end)
    assert ":is(.p-tabs,.p-seg).ovf{--fade-s:var(--s5);--fade-e:var(--s5);" in css["parts"]
    assert ":is(.p-tabs,.p-seg).ovf.beg{--fade-s:0px}" in css["parts"]
    assert ".p-seg{flex:0 1 auto;mask" not in css["parts"]
    assert ":is(.p-tabs,.p-seg)>.p-cut{opacity:0}" in css["parts"]  # a sliver it cannot clear
    # M4: one touch hit-area rule for small controls (switch, .p-hit, colour input, links in running text)
    assert ":is(button.sw,.p-hit)::before{" in css["parts"] and "width:max(100%,var(--hit))" in css["parts"]
    assert "input[type=color]:not(.x-cl *){height:var(--hit)" in css["parts"]
    assert ":is(p,.p-empty):not(.x-cl *) a[href]:not([class]){padding:calc((var(--hit) - 1em) / 2) 0}" in css["parts"]
    assert ".p-g .p-gr:has(>.gc-m:empty){grid-template-rows:auto" in css["table"]
    assert "order:-1" not in css["tmux"]  # the lock follows the name: names line up
    js = (THEME / "static" / "js" / "parts.js").read_text()
    assert "d.fonts.ready" in js and "classList.toggle('ovf'" in js and "'pm7:tab'" in js
    assert "classList.toggle('beg'" in js and "classList.add('p-cut')" in js
