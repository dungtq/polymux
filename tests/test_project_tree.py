"""Project page: Files tree in the right-hand pane (confined, no symlinks, masked) and drawer-keeping backlog edit."""

import os

import pytest
from helpers import TOKEN, login
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.git import ctl as gitctl
from pm.projects import projfiles


def test_button_and_listing(env):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    p = s.dev_root / "alpha"
    (p / "sub").mkdir()
    (p / "sub" / "a.txt").write_text("hello <b>x</b>\n")
    (p / "top.txt").write_text("t")
    h = c.get("/p/alpha").text
    assert 'href="/p/alpha/files"' in h and 'data-pj="browse"' in h
    t = c.get("/p/alpha/files").text.split('aria-label="Files"')[1]
    assert "sub/" in t and "top.txt" in t and t.index("sub/") < t.index("top.txt")
    t = c.get("/p/alpha/files?path=sub/a.txt").text
    assert "hello &lt;b&gt;x&lt;/b&gt;" in t and "<b>x</b>" not in t


def test_masks_and_hides_secrets(env):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    p = s.dev_root / "alpha"
    (p / "n.txt").write_text(f"GH={TOKEN}\n")
    (p / ".env").write_text("A=1\n")
    assert TOKEN not in c.get("/p/alpha/files?path=n.txt").text
    assert c.get("/p/alpha/files?path=.env").status_code == 403


def test_confinement(env, tmp_path):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    p = s.dev_root / "alpha"
    out = tmp_path / "outside"
    out.mkdir()
    (out / "secret.txt").write_text("nope")
    os.symlink(out, p / "esc")
    os.symlink(out / "secret.txt", p / "lnk.txt")
    assert "esc" in c.get("/p/alpha/files").text
    for q in ("../", "..", "esc", "esc/secret.txt", "lnk.txt", "sub/../../x", ".git", "a%00b"):
        assert c.get("/p/alpha/files", params={"path": q}).status_code in (403, 404), q
    assert c.get("/p/nope/files").status_code == 404


def test_binary_and_cap(env):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    p = s.dev_root / "alpha"
    (p / "b.bin").write_bytes(b"\x00\x01abc")
    assert "Binary file" in c.get("/p/alpha/files?path=b.bin").text
    (p / "big.txt").write_text("x" * (projfiles.MAX_BYTES + 10))
    assert "cut at 256 KB" in c.get("/p/alpha/files?path=big.txt").text


def test_requires_login(env):
    s, c = env
    assert c.get("/p/alpha/files", follow_redirects=False).status_code in (301, 302, 303, 401, 403)


def test_read_tree_rejects_bad_rel(tmp_path):
    with pytest.raises(projfiles.FileRefused):
        projfiles.read_tree(str(tmp_path), "a/../b")


def test_skills_fixed_columns_and_drawer_carry():
    css = open("src/pm/static/css/doctabs.css").read()
    assert ".pj-sk{table-layout:fixed;" in css and ".pj-sk .c-n{width:13em}" in css
    js = open("src/pm/static/js/drawer.js").read()
    assert "pagehide" in js and "location.hash" in js  # one shared aside handler (2026-10-10)


def test_backlog_edit_page_has_drawer(env):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    (s.dev_root / "alpha" / "BACKLOG.md").write_text("# Backlog\n## Inbox\n- [ ] one\n")
    h = c.get("/backlog?p=alpha&edit=1&back=project").text
    assert "drawer.js" in h and 'id="v5chat"' in h


def _git(p, *a):
    import subprocess

    subprocess.run(["git", "-C", str(p), "-c", "user.email=t@t", "-c", "user.name=t", *a], check=True,
                   capture_output=True)


def test_split_tree_icons_highlight_dates(env):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    p = s.dev_root / "alpha"
    (p / "sub" / "deep").mkdir(parents=True)
    (p / "sub" / "deep" / "m.py").write_text("def f():\n    return '<x>'\n")
    (p / "other").mkdir()
    (p / "other" / "hidden.txt").write_text("h")
    (p / ".env").write_text("A=1\n")
    t = c.get("/p/alpha/files?path=sub/deep/m.py").text
    assert 'class="pj-fsplit"' in t and 'class="pj-ftree' in t and 'class="pj-fview"' in t
    assert 'aria-current="page"' in t and "is-cur" in t
    assert 'fill="currentColor"' in t and "pj-ico pj-ico-code" in t and "pj-ico-dir" in t
    assert '<code class="pj-code">' in t and 'class="hl-k"' in t and "&lt;x&gt;" in t
    assert "Created" in t and "Modified" in t and "2 lines" in t and "Python" in t
    assert "hidden.txt" not in t and ".env" not in t  # collapsed dirs have no children, secrets hidden
    d = c.get("/p/alpha/files?path=sub").text
    assert "pj-t-mt" in d and "pj-t-sz" in d


def test_markdown_toggle(env):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    (s.dev_root / "alpha" / "d.md").write_text("# Title\n\n<script>x</script>\n")
    t = c.get("/p/alpha/files?path=d.md").text
    assert "pj-md markdown-body" in t and "<h1" in t and "<script>x" not in t
    assert "raw=1" in t
    r = c.get("/p/alpha/files?path=d.md&raw=1").text
    assert "pj-md" not in r and "pj-code" in r and "<script>" not in r


def test_git_diff_and_markers(env):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    p = s.dev_root / "alpha"
    _git(p, "init", "-q")
    (p / "a.txt").write_text("one\ntwo\n")
    (p / "b.txt").write_text("b\n")
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "i")
    (p / "a.txt").write_text(f"one\nTWO <b>\nGH={TOKEN}\n")
    (p / "new.txt").write_text("n")
    t = c.get("/p/alpha/files?path=a.txt").text
    assert "Changes" in t and "diff=1" in t
    d = c.get("/p/alpha/files?path=a.txt&diff=1").text
    assert '<pre class="pj-diff' in d and 'class="df-add"' in d and 'class="df-del"' in d
    assert 'class="df-hunk"' in d and 'class="df-meta"' in d and "&lt;b&gt;" in d and TOKEN not in d
    assert "diff=1" not in c.get("/p/alpha/files?path=b.txt").text
    ls = c.get("/p/alpha/files").text
    assert "pj-gs pj-gs-M" in ls and "pj-gs pj-gs-U" in ls
    assert "pj-gs pj-gs-C" in ls and "pj-g-M" in ls and "pj-g-C" in ls  # b.txt committed, a.txt modified
    assert ls.index("pj-t-sz") < ls.index("pj-t-mt") < ls.index("pj-t-bt")  # size, modified, created
    (p / "b.txt").unlink()
    gitctl._status_cache.clear()  # status is cached for a few seconds
    ls = c.get("/p/alpha/files").text
    assert "pj-t-gone" in ls and "pj-g-D" in ls  # tracked file deleted on disk still listed
