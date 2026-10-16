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
