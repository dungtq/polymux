from pathlib import Path

from pm.core.config import Settings
from pm.core.models import Project
from pm.projects import projects


def mk(tmp_path):
    root = tmp_path / "dev"
    for n in ("a", "b", ".hid", "node_modules"):
        (root / n).mkdir(parents=True)
    (root / "file").write_text("x")
    extra = tmp_path / "setup"
    extra.mkdir()
    return Settings(dev_root=root, extra_projects=(extra, tmp_path / "missing"))


def test_discover(tmp_path):
    ps = projects.discover(mk(tmp_path))
    assert [p.name for p in ps] == ["a", "b", "setup"]


def test_parse_todo(tmp_path):
    (tmp_path / "TODO.md").write_text("# t\n- [x] one\n- [ ] two\n  - [ ] three\n* [X] four\ntext\n")
    t = projects.parse_todo(tmp_path)
    assert (t.done, t.total, t.open_items) == (2, 4, ("two", "three"))
    assert projects.parse_todo(tmp_path / "nope").total == 0


def test_project_for_cwd(tmp_path):
    s = mk(tmp_path)
    ps = projects.discover(s)
    a = tmp_path / "dev" / "a"
    assert projects.project_for_cwd(a / "src", ps).name == "a"
    assert projects.project_for_cwd(tmp_path / "elsewhere", ps) is None
    # linked worktree living outside the project dir
    wt = tmp_path / "wt-a"
    (a / ".git" / "worktrees" / "wt-a").mkdir(parents=True)
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {a / '.git' / 'worktrees' / 'wt-a'}\n")
    (wt / "sub").mkdir()
    assert projects.project_for_cwd(wt / "sub", ps).name == "a"


def test_project_for_cwd_is_fast_with_many_projects(tmp_path):
    import time

    from pm.core.models import Project
    from pm.projects.projects import project_for_cwd

    projs = []
    for i in range(80):
        d = tmp_path / f"p{i}"
        (d / "sub").mkdir(parents=True)
        projs.append(Project(name=f"p{i}", path=str(d)))
    t = time.monotonic()
    for _ in range(2000):
        hit = project_for_cwd(projs[40].path + "/sub", projs)
    assert hit.name == "p40"
    assert time.monotonic() - t < 3.0
    # prefix lookalike must not match: p4 vs p40
    assert project_for_cwd(str(tmp_path / "p400"), projs) is None
