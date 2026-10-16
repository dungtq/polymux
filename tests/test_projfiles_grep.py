import os

from pm.projects import projfiles


def _tree(tmp_path):
    r = tmp_path / "proj"
    (r / "src").mkdir(parents=True)
    (r / ".git").mkdir()
    (r / "node_modules").mkdir()
    (r / "src" / "a.py").write_text("x = 1\nprint('Hello Needle')\n")
    (r / "b.txt").write_text("nothing")
    (r / ".git" / "c").write_text("needle")
    (r / "node_modules" / "d.js").write_text("needle")
    (r / ".hidden").write_text("needle")
    (r / ".env").write_text("needle")
    (r / "bin.dat").write_bytes(b"\x00\x01needle")
    (r / "big.txt").write_text("needle " + "z" * projfiles.GREP_MAX_BYTES)
    return r


def test_grep_finds_text_only(tmp_path):
    r = _tree(tmp_path)
    hits = projfiles.grep_project(str(r), "needle")
    assert [(h["rel"], h["line"]) for h in hits] == [("src/a.py", 2)]
    assert "Hello Needle" in hits[0]["snippet"]


def test_grep_min_len_limit_and_symlinks(tmp_path):
    r = _tree(tmp_path)
    assert projfiles.grep_project(str(r), "ne") == []
    for i in range(8):
        (r / f"f{i}.txt").write_text("needle")
    assert len(projfiles.grep_project(str(r), "needle", 3)) == 3
    out = tmp_path / "outside"
    out.mkdir()
    (out / "s.txt").write_text("outsidemarker")
    os.symlink(out, r / "lnk")
    os.symlink(out / "s.txt", r / "lnk.txt")
    assert projfiles.grep_project(str(r), "outsidemarker") == []


def test_grep_masks_snippet(tmp_path):
    from pm.security.mask import render_pane

    secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    r = tmp_path / "p"
    r.mkdir()
    (r / "n.txt").write_text(f"marker {secret}\n")
    (h,) = projfiles.grep_project(str(r), "marker")
    assert h["snippet"] == render_pane(f"marker {secret}")
    assert render_pane(secret) == secret or secret not in h["snippet"]
