"""resolve_md: any .md anywhere, except dot-paths (bar ~/.claude/plans); relative refs fall back to the project
root and the paths the chat named. md_mentions: the absolute .md paths in a text."""

from __future__ import annotations

from pm.core.fsutil import md_mentions, resolve_md


def test_resolve_md(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    for d in ("proj", ".ssh", ".cfg", ".agents-git", ".claude/plans/.sub", "elsewhere"):
        (home / d).mkdir(parents=True)
    for f in ("proj/a.md", ".ssh/k.md", ".cfg/c.md", ".agents-git/g.md", ".claude/plans/p.md",
              ".claude/plans/.sub/h.md", "elsewhere/e.MD", "proj/n.txt"):
        (home / f).write_text("x")
    (home / "proj" / "ln.md").symlink_to(home / ".ssh" / "k.md")
    cwd = str(home / "proj")
    assert resolve_md("a.md", cwd) == home / "proj" / "a.md"
    assert resolve_md("~/elsewhere/e.MD", cwd) == home / "elsewhere" / "e.MD"
    assert resolve_md(str(tmp_path / "home" / ".claude" / "plans" / "p.md")) is not None
    assert resolve_md("~/.claude/plans/p.md") is not None
    for bad in ("~/.ssh/k.md", "~/.cfg/c.md", "~/.agents-git/g.md", "ln.md", "~/.claude/plans/.sub/h.md",
                "n.txt", "missing.md", "", "a\0.md", "x" * 1100 + ".md", "/etc/passwd"):
        assert resolve_md(bad, cwd) is None, bad


def _tree(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    for f in ("pm/docs/RESUME-AGENTS.md", "polymux/SECURITY.md", "polymux/docs/themes/dt/CODE.md", "other/CODE.md",
              ".cfg/SECRET.md", ".claude/plans/plan.md"):
        (home / f).parent.mkdir(parents=True, exist_ok=True)
        (home / f).write_text("x")
    (home / "polymux" / "LINK.md").symlink_to(home / ".cfg" / "SECRET.md")
    return home


def test_relative_ref_falls_back_to_the_project_root(tmp_path, monkeypatch):
    home = _tree(tmp_path, monkeypatch)
    cwd = str(home / "pm" / "docs")
    assert resolve_md("SECURITY.md", cwd) is None
    assert resolve_md("SECURITY.md", cwd, roots=[str(home / "polymux")]) == home / "polymux" / "SECURITY.md"
    assert resolve_md("RESUME-AGENTS.md", cwd, roots=[str(home / "polymux")]) == home / "pm/docs/RESUME-AGENTS.md"
    assert resolve_md("SECURITY.md", "", roots=["", str(home / "polymux")]) is not None  # ended: no cwd


def test_relative_ref_found_among_the_paths_the_chat_named(tmp_path, monkeypatch):
    home = _tree(tmp_path, monkeypatch)
    cwd = str(home / "pm")
    seen = [str(home / "polymux/docs/themes/dt/CODE.md"), "~/polymux/SECURITY.md", str(home / "other/CODE.md")]
    assert resolve_md("docs/themes/dt/CODE.md", cwd, seen=seen) == home / "polymux/docs/themes/dt/CODE.md"
    assert resolve_md("./SECURITY.md", cwd, seen=seen) == home / "polymux/SECURITY.md"  # ~ expands
    assert resolve_md("CODE.md", cwd, seen=seen) == home / "polymux/docs/themes/dt/CODE.md"  # newest mention wins
    assert resolve_md("CODE.md", cwd, seen=seen[::-1]) == home / "other/CODE.md"
    assert resolve_md("dt/CODE.md", cwd, seen=["relative/dt/CODE.md", "/nowhere/dt/CODE.md"]) is None
    assert resolve_md("ODE.md", cwd, seen=seen) is None  # whole names only
    assert resolve_md("../polymux/SECURITY.md", str(home / "x"), seen=seen) is None  # no '..' suffix games


def test_cwd_wins_and_seen_is_read_lazily(tmp_path, monkeypatch):
    home = _tree(tmp_path, monkeypatch)
    used = []

    def seen():
        used.append(1)
        yield str(home / "other/CODE.md")

    assert resolve_md("docs/RESUME-AGENTS.md", str(home / "pm"), seen=seen()) == home / "pm/docs/RESUME-AGENTS.md"
    assert used == []  # the chat is not read when the folder has the file
    assert resolve_md("CODE.md", str(home / "pm"), seen=seen()) == home / "other/CODE.md" and used == [1]


def test_hidden_folders_stay_refused_through_every_fallback(tmp_path, monkeypatch):
    home = _tree(tmp_path, monkeypatch)
    cwd = str(home / "pm")
    assert resolve_md("SECRET.md", cwd, roots=[str(home / ".cfg")]) is None
    assert resolve_md("SECRET.md", cwd, seen=[str(home / ".cfg/SECRET.md"), "~/.cfg/SECRET.md"]) is None
    assert resolve_md("LINK.md", cwd, roots=[str(home / "polymux")]) is None  # symlink into a hidden folder
    assert resolve_md("LINK.md", cwd, seen=[str(home / "polymux/LINK.md")]) is None
    assert resolve_md("plan.md", cwd, seen=["~/.claude/plans/plan.md"]) == home / ".claude/plans/plan.md"


def test_md_mentions():
    text = ("Read `/home/user/dev/app/SECURITY.md`, then ~/dev/site/docs/RESUME-AGENTS.md. Not foo/bar.md, "
            "http://x.y/a.md, /x/a.md.bak; again /home/user/dev/app/SECURITY.md (/a/B.MD)")
    assert md_mentions(text) == ["/home/user/dev/app/SECURITY.md", "~/dev/site/docs/RESUME-AGENTS.md", "/a/B.MD"]
    assert md_mentions("") == [] and md_mentions("no paths here") == []


def _projects(home, *names):
    return [(n, str(home / n)) for n in names]


def test_relative_ref_in_a_folder_the_chat_worked_in(tmp_path, monkeypatch):
    home = _tree(tmp_path, monkeypatch)
    cwd = str(home / "pm")
    dirs = [str(home / "gone"), str(home / "polymux")]  # newest first; a missing folder is skipped
    assert resolve_md("docs/themes/dt/CODE.md", cwd, dirs=dirs) == home / "polymux/docs/themes/dt/CODE.md"
    assert resolve_md("SECURITY.md", cwd, dirs=iter(dirs)) == home / "polymux/SECURITY.md"
    assert resolve_md("../polymux/SECURITY.md", str(home / "x" / "y"), dirs=dirs) is None  # no '..' in a fallback
    assert resolve_md("SECURITY.md", cwd, dirs=["relative/polymux"]) is None


def test_deleted_worktree_dir_maps_to_its_project(tmp_path, monkeypatch):
    home = _tree(tmp_path, monkeypatch)
    for f in ("other/SECURITY.md", "pm-archive/SECURITY.md"):
        (home / f).parent.mkdir(exist_ok=True)
        (home / f).write_text("x")
    projects = _projects(home, "pm", "polymux", "other", "pm-archive")
    cwd = str(home / "pm")
    assert resolve_md("SECURITY.md", cwd, projects=projects) is None  # three projects have it: no guess
    for d, want in (("polymux-wt-dt", "polymux"), ("polymux-wt-dt/src/pm", "polymux"), ("other", "other"),
                    ("pm-archive-x", "pm-archive"), ("polymux", "polymux")):
        got = resolve_md("SECURITY.md", cwd, dirs=[str(home / d)], projects=projects)
        assert got == home / want / "SECURITY.md", d
    assert resolve_md("SECURITY.md", cwd, dirs=[str(home / "zzz"), str(home / "polymux-wt-a")],
                      projects=projects) == home / "polymux/SECURITY.md"  # an unknown folder is passed over
    assert resolve_md("SECURITY.md", cwd, dirs=[str(tmp_path / "polymux-wt-dt")], projects=projects) is None


def test_unique_project_hit_and_ambiguous_projects(tmp_path, monkeypatch):
    home = _tree(tmp_path, monkeypatch)
    projects = _projects(home, "pm", "polymux", "other")
    cwd = str(home / "pm")
    assert resolve_md("docs/themes/dt/CODE.md", cwd, projects=projects) == home / "polymux/docs/themes/dt/CODE.md"
    (home / "other/SECURITY.md").write_text("x")
    assert resolve_md("SECURITY.md", cwd, projects=projects) is None
    assert resolve_md("SECURITY.md", cwd, roots=[str(home / "other")], projects=projects) == home / "other/SECURITY.md"
    assert resolve_md("../polymux/SECURITY.md", str(home / "x" / "y"), projects=projects) is None


def test_bare_name_found_below_the_agent_folder_when_unique(tmp_path, monkeypatch):
    home = _tree(tmp_path, monkeypatch)
    cwd = str(home / "pm")
    assert resolve_md("RESUME-AGENTS.md", cwd) == home / "pm/docs/RESUME-AGENTS.md"
    assert resolve_md("./RESUME-AGENTS.md", "", roots=[cwd]) == home / "pm/docs/RESUME-AGENTS.md"  # ended agent
    assert resolve_md("RESUME-AGENTS.md", "") is None  # no folder: no search (never below /)
    assert resolve_md("x/RESUME-AGENTS.md", cwd) is None  # bare names only
    for f in ("pm/a/DUP.md", "pm/b/c/DUP.md", "pm/node_modules/NM.md", "pm/build/B.md"):
        (home / f).parent.mkdir(parents=True, exist_ok=True)
        (home / f).write_text("x")
    assert resolve_md("DUP.md", cwd) is None  # two of them: no guess
    assert resolve_md("NM.md", cwd) is None and resolve_md("B.md", cwd) is None
    deep = home / "pm" / "/".join("d" * 8)
    deep.mkdir(parents=True)
    (deep / "DEEP.md").write_text("x")
    assert resolve_md("DEEP.md", cwd) is None  # deeper than the search goes


def test_hidden_folders_stay_refused_through_the_new_fallbacks(tmp_path, monkeypatch):
    home = _tree(tmp_path, monkeypatch)
    (home / "pm/.hide").mkdir()
    (home / "pm/.hide/HID.md").write_text("x")
    (home / "pm/docs/IN.md").symlink_to(home / ".cfg/SECRET.md")
    (home / "pm/sub").symlink_to(home / ".cfg", target_is_directory=True)
    cwd = str(home / "pm")
    assert resolve_md("SECRET.md", cwd, dirs=[str(home / ".cfg"), "~/.cfg"]) is None
    assert resolve_md("SECRET.md", cwd, projects=[("cfg", str(home / ".cfg"))]) is None
    assert resolve_md("SECRET.md", cwd, dirs=[str(home / ".cfg-wt")], projects=[(".cfg", str(home / ".cfg"))]) is None
    assert resolve_md("HID.md", cwd) is None and resolve_md("SECRET.md", cwd) is None  # no hidden or linked folder
    assert resolve_md("IN.md", cwd) is None  # symlink into a hidden folder
    assert resolve_md("LINK.md", cwd, dirs=[str(home / "polymux")]) is None
    assert resolve_md("LINK.md", cwd, projects=_projects(home, "polymux")) is None
    assert resolve_md("LINK.md", str(home / "polymux")) is None


def test_later_steps_run_only_when_earlier_ones_miss(tmp_path, monkeypatch):
    home = _tree(tmp_path, monkeypatch)
    used = []

    def dirs():
        used.append(1)
        yield str(home / "polymux")

    seen = [str(home / "other/CODE.md")]
    assert resolve_md("CODE.md", str(home / "pm"), seen=seen, dirs=dirs()) == home / "other/CODE.md"
    assert used == []
