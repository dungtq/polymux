"""Cursor / VS Code workspace files: discovery, JSONC folders, matching to known projects."""

import json
import os

from pm.projects import workspace as ws


def _wf(d, name, text):
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text(text)
    return f


def test_parse_jsonc_comments_commas_and_strings(tmp_path):
    f = _wf(tmp_path / "root", "w.code-workspace", """{
  // line comment with "quotes"
  "folders": [
    {"path": "alpha"},   /* block
       comment */
    {"path": "http://x/not-a-comment",},
    {"name": "u", "uri": "vscode-remote://ssh-remote+box/srv/beta"},
    {"path": "../beta"},
  ],
  "settings": {"a": "x // y", "b": [1, 2,],},
}""")
    root = (tmp_path / "root").resolve()
    got = ws.parse_folders(f)
    assert got == [root / "alpha", root / "http:" / "x" / "not-a-comment", (tmp_path / "beta").resolve()]


def test_parse_absolute_and_symlinked(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    f = _wf(tmp_path, "w.code-workspace", json.dumps({"folders": [{"path": str(link)}, {"path": "link"}]}))
    assert ws.parse_folders(f) == [real.resolve(), real.resolve()]


def test_parse_garbage_and_oversized(tmp_path):
    for text in ("", "not json", "[1,2]", '{"folders": 3}', '{"folders": [1, null, {"path": 5}, {"path": ""}]}',
                 '{"folders": [{"path": "a"}', "\x00\xff{{"):
        assert ws.parse_folders(_wf(tmp_path, "g.code-workspace", text)) == []
    big = _wf(tmp_path, "big.code-workspace", '{"folders": [{"path": "a"}], "x": "' + "a" * ws.MAX_BYTES + '"}')
    assert ws.parse_folders(big) == []
    assert ws.parse_folders(tmp_path / "missing.code-workspace") == []


def test_match_excludes_own_and_counts_unmatched(tmp_path):
    a, b = tmp_path / "alpha", tmp_path / "beta"
    a.mkdir()
    b.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(b)
    known = {"alpha": a, "beta": b}
    folders = [a.resolve(), alias.resolve(), b.resolve(), (tmp_path / "other").resolve()]
    assert ws.match(folders, known, "alpha") == (["beta"], 1)
    assert ws.match([], known, "alpha") == ([], 0)


def test_discover_project_dir_and_parent(tmp_path):
    dev = tmp_path / "dev"
    alpha = dev / "alpha"
    _wf(alpha, "inner.code-workspace", "{}")
    _wf(dev, "outer.code-workspace", "{}")
    _wf(dev, ".hidden.code-workspace", "{}")
    _wf(dev, "notes.txt", "x")
    _wf(alpha / "deep", "deep.code-workspace", "{}")  # depth 2: not found
    found = ws.discover([alpha, alpha.parent, alpha])  # duplicate root counts once
    assert sorted(w.label for w in found) == ["inner.code-workspace (alpha)", "outer.code-workspace (dev)"]
    assert all(len(w.id) == 12 and str(tmp_path) not in w.label for w in found)
    assert ws.discover([alpha, dev]) == found  # stable ids
    assert ws.discover([tmp_path / "missing"]) == []


def test_discover_cap_and_symlink_escape(tmp_path):
    d = tmp_path / "d"
    for i in range(5):
        _wf(d, f"w{i}.code-workspace", "{}")
    assert len(ws.discover([d], limit=3)) == 3
    outside = _wf(tmp_path / "out", "x.code-workspace", "{}")
    (d / "esc.code-workspace").symlink_to(outside)
    (d / "dir.code-workspace").mkdir()
    names = [w.path.name for w in ws.discover([d])]
    assert "esc.code-workspace" not in names and "dir.code-workspace" not in names and len(names) == 5
    assert os.path.islink(d / "esc.code-workspace")
