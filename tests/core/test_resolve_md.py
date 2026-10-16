"""resolve_md: any .md anywhere, except dot-paths (bar ~/.claude/plans)."""

from __future__ import annotations

from pm.core.fsutil import resolve_md


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
