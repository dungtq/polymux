"""dt project quick look (issue #244): GET /dt/peek/<project>?path= (pm.web.dt.routes.peek) through the app with the real
dt theme: one folder as a grid table, folders drill down in the popup (a[data-pk-dir]), every link is the Files tab's
URL, the Settings › Display pref peek_open decides new browser tab vs this tab, the Files tab's path rules (no
`..`, no symlink, no .git, secrets refused), and the shell markup / script contract (shell/_peek.html, js/peek.js)."""

from __future__ import annotations

import dataclasses
import os
import re

from fastapi.testclient import TestClient

from pm.core.models import GitStatus
from pm.web import themes
from pm.web.dt import prefs

from .conftest import ORIGIN
from .kit import Tree

HX = {"HX-Request": "true"}
THEME = themes.HERE / "dt"


def _tree(app_env, tmp_path):
    root = tmp_path / "dev" / "alpha"
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('hi')\n")
    (root / ".git").mkdir()
    (root / ".env").write_text("X=1\n")
    os.symlink(root / "src", root / "lnk")
    return root


def _get(app_env, path="", project="alpha", **kw):
    url = f"/dt/peek/{project}" + (f"?path={path}" if path else "")
    return app_env.c.get(url, headers=HX, **kw)


def test_root_lists_one_folder_with_files_tab_links(app_env, tmp_path):
    _tree(app_env, tmp_path)
    r = _get(app_env)
    assert r.status_code == 200, r.text[:300]
    assert r.headers["cache-control"] == "no-store"
    t = Tree(r.text).root
    box = t.one("pk-in")
    assert box.attrs["data-pk-path"] == "" and box.attrs["data-pk-new"] == "1" and box.attrs["data-pk-meta"] == ""
    tbl = t.one("pk-list")
    assert tbl.attrs["role"] == "table" and "data-list" not in tbl.attrs  # no list keys under a modal
    ids = [n.attrs["data-row-id"] for n in t.by_cls("pk-r")]
    assert ids == ["src", ".env", "lnk", "README.md"]  # folders first, .git skipped at the root, like the Files tab
    src = re.search(r'<a class="nm" href="([^"]*)" data-pk-dir="src">src/</a>', r.text)
    assert src and src.group(1) == "/p/alpha/files?path=src"
    readme = re.search(r'<a class="nm" href="/p/alpha/files\?path=README.md"([^>]*)>README.md</a>', r.text)
    assert readme and 'target="_blank"' in readme.group(1) and 'rel="noopener"' in readme.group(1)
    assert '<span class="nm muted">lnk</span>' in r.text  # a symlink is listed, never linked
    crumbs = t.one("pk-cr")
    assert "p-crumb" in crumbs.cls and "p-files" in tbl.cls  # the Files tab's breadcrumb and row look
    assert [n.tag for n in crumbs.kids if n.tag in ("a", "b")] == ["b"] and crumbs.get_text().strip() == "alpha"


def test_folder_drills_down_with_up_row_and_crumbs(app_env, tmp_path):
    _tree(app_env, tmp_path)
    r = _get(app_env, "src")
    assert r.status_code == 200
    t = Tree(r.text).root
    assert t.one("pk-in").attrs["data-pk-path"] == "src"
    assert [n.attrs["data-row-id"] for n in t.by_cls("pk-r")] == ["..", "src/app.py"]
    assert '<a class="nm" href="/p/alpha/files?path=" data-pk-dir="">..</a>' in r.text
    assert 'href="/p/alpha/files?path=src%2Fapp.py" target="_blank" rel="noopener">app.py</a>' in r.text
    assert '<a href="/p/alpha/files" data-pk-dir="">alpha</a>' in r.text
    assert '<b aria-current="page">src</b>' in r.text


def test_pref_this_tab_drops_the_target(app_env, tmp_path):
    _tree(app_env, tmp_path)
    prefs.apply(app_env.s, {"peek_open": "same"})
    r = _get(app_env)
    assert 'data-pk-new="0"' in r.text and 'target="_blank"' not in r.text
    assert '<a class="nm" href="/p/alpha/files?path=README.md">README.md</a>' in r.text


def test_meta_from_the_snapshot_git_status(app_env, snap, tmp_path):
    alpha = dataclasses.replace(snap.value.projects[0], git=GitStatus(branch="main", dirty=2))
    snap.value = dataclasses.replace(snap.value, projects=(alpha, *snap.value.projects[1:]))
    assert 'data-pk-meta="main · 2 changed"' in _get(app_env).text
    alpha = dataclasses.replace(alpha, git=GitStatus(branch="dev"))
    snap.value = dataclasses.replace(snap.value, projects=(alpha,))
    assert 'data-pk-meta="dev · clean"' in _get(app_env).text


def test_refusals(app_env, tmp_path):
    _tree(app_env, tmp_path)
    assert _get(app_env, project="nope").status_code == 404
    assert _get(app_env, "README.md").status_code == 404  # a file is not a folder
    for bad in ("..", "src/../..", ".git", "lnk", "nope"):
        assert _get(app_env, bad).status_code == 404, bad
    assert _get(app_env, ".env").status_code == 403  # hidden for safety
    r = _get(app_env, "%00")
    assert r.status_code == 404


def test_needs_a_session_and_dt(app_env):
    anon = TestClient(app_env.app, base_url=ORIGIN, follow_redirects=False)
    assert anon.get("/dt/peek/alpha", headers=HX).status_code == 401
    assert app_env.c.get("/dt/peek/alpha?ui=classic", headers=HX).status_code == 204


def test_settings_row_and_pref_key():
    assert prefs.SCHEMA["peek_open"] == ("new", "same") and prefs.DEFAULTS["peek_open"] == "new"
    you = (THEME / "templates" / "settings" / "_you.html").read_text()
    assert 'su.aseg("peek_open", [("new", "new browser tab"), ("same", "this tab")]' in you


def test_shell_markup_and_script_contract():
    tpl = (THEME / "templates" / "shell" / "_peek.html").read_text()
    for hook in ('id="x-peek"', 'class="sp-dlg pk-dlg"', "data-pk-name", "data-pk-meta", "data-pk-open",
                 'data-act="close"', "data-pk-body", 'target="_blank" rel="noopener"'):
        assert hook in tpl, hook
    js = (THEME / "static" / "js" / "peek.js").read_text()
    assert "pm7.peek = {" in js and "pm7.dialog(" in js and "pm7.fetch(" in js
    assert "keydown" not in js and "window.open" not in js and "fetch(" not in js.replace("pm7.fetch(", "")
    assert "[data-peek]" in js
