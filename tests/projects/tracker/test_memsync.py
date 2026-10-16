"""memfile + memsync: Claude Code memory files round-trip without loss, first sync imports the main tree's dir,
every work tree's dir gets the render, hash-based two-way sync (file newer, pm.db newer, both = conflict copy),
rm removes only unchanged files, symlinks refused, 0600/0700, nothing written inside the repo, the plain summary.
Claude's home is a tmp dir (``claude_dir``), never ~/.claude."""

import dataclasses
import os
import stat

import pytest

from pm.core.fsutil import encode_cwd
from pm.projects.tracker import memfile, memory, memsync

from .conftest import git

FEEDBACK = """---
name: feedback-no-live-typing
description: Never type into a live agent page; sends reach real sessions
metadata:
  node_type: memory
  type: feedback
  originSessionId: ea6e8047-00ba-4a5b-9a1d-97f47c5d0aa0
  modified: 2026-10-07T07:27:13.272Z
---

Test composer behaviour on a private instance only.

**Why:** a delivered message cannot be undone. See [[project-notes]].
"""
PUSH = """---
name: feedback-push-flow
description: "Push rules: gitdev first, ask before GitHub"
metadata:
  node_type: memory
  type: feedback
  originSessionId: eb41dcf6-17ba-4fc8-8caf-40ba876c7ff2
  modified: 2026-10-16T16:09:15.488Z
---

- Push to gitdev after every commit.
"""
INDEX = ("- [No typing on live agent pages](feedback_no_live_typing.md) — never type into a live composer\n"
         "- [Push flow](feedback_push_flow.md) — gitdev first, ask before GitHub\n")


class Audit:
    def __init__(self, fail: bool = False):
        self.lines, self.fail = [], fail

    def __call__(self, action, detail):
        if self.fail:
            raise RuntimeError("audit down")
        self.lines.append((action, detail))


@pytest.fixture
def ms(tk, tmp_path):
    (tmp_path / "claude").mkdir()
    return dataclasses.replace(tk, claude_dir=tmp_path / "claude")


def _dir(ms, tree) -> "os.PathLike":
    return ms.claude_dir / "projects" / encode_cwd(str(tree)) / "memory"


@pytest.fixture
def claude(ms, repo):
    """The main tree's Claude memory dir with two memories and the index, as Claude Code writes them."""
    d = _dir(ms, repo)
    d.mkdir(parents=True)
    (d / "feedback_no_live_typing.md").write_text(FEEDBACK)
    (d / "feedback_push_flow.md").write_text(PUSH)
    (d / "MEMORY.md").write_text(INDEX)
    return d


def _sync(ms, repo, audit=None):
    return memsync.sync_project(ms, "proj", repo, actor="claude-test01", audit=audit)


def _files(d):
    return {p.name: p.read_bytes() for p in sorted(d.iterdir())}


# ------------------------------------------------------------------ file format
def test_parse_render_round_trip_is_lossless():
    p = memfile.parse(FEEDBACK.encode(), "feedback_no_live_typing.md")
    assert (p.name, p.type, p.description) == ("feedback-no-live-typing", "feedback",
                                                 "Never type into a live agent page; sends reach real sessions")
    assert p.meta["metadata"]["modified"] == "2026-10-07T07:27:13.272Z"  # dates stay text
    assert p.body.startswith("Test composer") and p.body.endswith("[[project-notes]].")
    m = memory.Memory(id=1, project="proj", name=p.name, type=p.type, title="", description=p.description,
                      body_md=p.body, meta=p.meta)
    text = memfile.render(m)
    assert text == FEEDBACK  # Claude's own layout renders byte for byte
    again = memfile.parse(text.encode(), "x.md")
    assert memfile.same(again, m)
    q = memfile.parse(PUSH.encode(), "feedback_push_flow.md")
    assert q.description == "Push rules: gitdev first, ask before GitHub"
    m2 = dataclasses.replace(m, name=q.name, description=q.description, body_md=q.body, meta=q.meta)
    assert memfile.same(memfile.parse(memfile.render(m2).encode(), "y.md"), m2)


def test_parse_defaults_and_refusals():
    p = memfile.parse(b"---\ndescription: d\nextra: [1, 2]\n---\nbody\n", "My Note.md")
    assert (p.name, p.type, p.meta) == ("my-note", "project",
                                        {"extra": [1, 2], "metadata": {"node_type": "memory", "type": "project"}})
    top = memfile.parse(b"---\nname: n\ndescription: d\ntype: user\n---\n", "n.md")
    assert top.meta == {"type": "user"}  # an older top-level type stays where it was
    for bad in (b"no frontmatter", b"---\ndescription: d\n", b"---\nname: n\n---\n", b"---\n[1, 2]\n---\n",
                b"---\ndescription: d\ntype: note\n---\n", b"---\ndescription: [unclosed\n---\n", b"\xff\xfe",
                b"---\ndescription: d\nx: &a [*a]\n---\n"):
        with pytest.raises(Exception) as ex:
            memfile.parse(bad, "f.md")
        assert ex.type.__name__ in ("Invalid",), bad


def test_parse_refuses_alias_bombs():
    bomb = "---\ndescription: d\na: &a [x, x, x, x, x, x, x, x, x, x]\n"
    prev = "a"
    for i in range(8):
        bomb += f"{chr(98 + i)}: &{chr(98 + i)} [*{prev}, *{prev}, *{prev}, *{prev}, *{prev}, *{prev}]\n"
        prev = chr(98 + i)
    with pytest.raises(Exception, match="too large"):
        memfile.parse((bomb + "---\n").encode(), "f.md")


def test_index_parse_and_render(tmp_path):
    (tmp_path / "MEMORY.md").write_text("# heading\n" + INDEX + "* [Bare](bare.md)\n")
    assert memfile.read_index(tmp_path) == {
        "feedback_no_live_typing.md": ("No typing on live agent pages", "never type into a live composer"),
        "feedback_push_flow.md": ("Push flow", "gitdev first, ask before GitHub"), "bare.md": ("Bare", "")}
    m = memory.Memory(id=1, project="p", name="n", type="user", title="", description="d", body_md="")
    assert memfile.render_index([m]) == "- [n](n.md) — d\n"
    assert memfile.render_index([dataclasses.replace(m, title="T [x]", hook="h", source="s.md")]) == \
        "- [T (x)](s.md) — h\n"


# ------------------------------------------------------------------ sync
def test_first_sync_imports_and_keeps_files_byte_for_byte(ms, repo, claude):
    before = _files(claude)
    au = Audit()
    rep = _sync(ms, repo, au)
    main = rep["dirs"][0]
    assert (main["dir"], main["imported"], main["written"], main["conflicts"]) == (str(claude), 2, 0, [])
    assert _files(claude) == before  # the render says the same: nothing rewritten, index included
    mems = memory.list_memories(ms, "proj")
    assert [(m.name, m.source, m.title) for m in mems] == [
        ("feedback-no-live-typing", "feedback_no_live_typing.md", "No typing on live agent pages"),
        ("feedback-push-flow", "feedback_push_flow.md", "Push flow")]
    assert mems[0].hook == "never type into a live composer" and mems[0].created_by == "claude-test01"
    assert [a for a, _ in au.lines] == ["tracker_memory_import", "tracker_memory_import", "tracker_memory_sync"]
    assert all("Never type" not in str(d) for _, d in au.lines)
    again = _sync(ms, repo)["dirs"][0]
    assert (again["imported"], again["updated"], again["written"], again["removed"]) == (0, 0, 0, 0)
    s = ms.memory_dir / "proj.md"
    assert rep["summary"] == str(s) and "## feedback-push-flow (feedback)" in s.read_text()
    assert stat.S_IMODE(s.stat().st_mode) == 0o600 and stat.S_IMODE(ms.memory_dir.stat().st_mode) == 0o700


def test_worktrees_get_the_render_and_repo_stays_clean(ms, repo, claude, tmp_path):
    wt = tmp_path / "dev" / "proj-wt" / "feat"
    git(repo, "worktree", "add", "-q", "-b", "feat", str(wt))
    rep = _sync(ms, repo)
    assert [d["dir"] for d in rep["dirs"]] == [str(claude), str(_dir(ms, wt))]
    wd = _dir(ms, wt)
    assert sorted(_files(wd)) == ["MEMORY.md", "feedback_no_live_typing.md", "feedback_push_flow.md"]
    assert (wd / "feedback_no_live_typing.md").read_text() == FEEDBACK and (wd / "MEMORY.md").read_text() == INDEX
    for p in wd.iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    for p in (wd, wd.parent):
        assert stat.S_IMODE(p.stat().st_mode) == 0o700
    assert git(repo, "status", "--porcelain", "--untracked-files=all") == ""
    assert git(wt, "status", "--porcelain", "--untracked-files=all") == ""


def test_db_change_renders_and_file_change_imports(ms, repo, claude):
    _sync(ms, repo)
    memory.update(ms, "proj", "feedback-push-flow", body="- new rule", actor="codex-b")
    rep = _sync(ms, repo)["dirs"][0]
    assert rep["written"] == 1 and "- new rule" in (claude / "feedback_push_flow.md").read_text()
    text = (claude / "feedback_no_live_typing.md").read_text().replace("private instance", "dev instance")
    (claude / "feedback_no_live_typing.md").write_text(text)
    rep = _sync(ms, repo)["dirs"][0]
    assert (rep["updated"], rep["written"], rep["conflicts"]) == (1, 0, [])
    assert "dev instance" in memory.get(ms, "proj", "feedback-no-live-typing").body_md
    (claude / "feedback_push_flow.md").unlink()  # a missing file is rendered again, never deleted in pm.db
    rep = _sync(ms, repo)["dirs"][0]
    assert rep["written"] == 1 and (claude / "feedback_push_flow.md").exists()


def test_both_changed_db_wins_and_file_is_kept(ms, repo, claude):
    _sync(ms, repo)
    memory.update(ms, "proj", "feedback-push-flow", body="db text", actor="codex-b")
    f = claude / "feedback_push_flow.md"
    f.write_text(f.read_text().replace("after every commit", "file text"))
    rep = _sync(ms, repo)["dirs"][0]
    assert len(rep["conflicts"]) == 1 and rep["conflicts"][0].startswith("feedback_push_flow-conflict-")
    assert any("pm.db kept" in n for n in rep["notes"])
    assert "db text" in f.read_text() and memory.get(ms, "proj", "feedback-push-flow").body_md == "db text"
    copy = claude / rep["conflicts"][0]
    assert "file text" in copy.read_text() and stat.S_IMODE(copy.stat().st_mode) == 0o600
    rep = _sync(ms, repo)["dirs"][0]
    assert (rep["imported"], rep["conflicts"]) == (0, [])  # the copy is never imported


def test_unimportable_edit_is_kept_before_the_render(ms, repo, claude):
    _sync(ms, repo)
    f = claude / "feedback_push_flow.md"
    f.write_text("---\ndescription: [broken\n---\nmy edit\n")
    rep = _sync(ms, repo)["dirs"][0]
    assert len(rep["conflicts"]) == 1 and "my edit" in (claude / rep["conflicts"][0]).read_text()
    assert f.read_text().startswith("---\nname: feedback-push-flow\n")


def test_rm_removes_only_unchanged_files(ms, repo, claude):
    _sync(ms, repo)
    memory.remove(ms, "proj", "feedback-push-flow")
    rep = _sync(ms, repo)["dirs"][0]
    assert rep["removed"] == 1 and not (claude / "feedback_push_flow.md").exists()
    assert "feedback_push_flow.md" not in (claude / "MEMORY.md").read_text()
    memory.remove(ms, "proj", "feedback-no-live-typing")
    f = claude / "feedback_no_live_typing.md"
    f.write_text(FEEDBACK.replace("private instance", "edited after rm"))
    rep = _sync(ms, repo)["dirs"][0]
    assert rep["removed"] == 0 and f.exists() and rep["imported"] == 1
    assert "edited after rm" in memory.get(ms, "proj", "feedback-no-live-typing").body_md


def test_second_dir_with_other_text_is_a_conflict(ms, repo, claude, tmp_path):
    wt = tmp_path / "dev" / "proj-wt" / "b"
    git(repo, "worktree", "add", "-q", "-b", "b", str(wt))
    wd = _dir(ms, wt)
    wd.mkdir(parents=True)
    (wd / "feedback_push_flow.md").write_text(PUSH.replace("after every commit", "worktree says"))
    (wd / "local-only.md").write_text("---\nname: local-only\ndescription: from the worktree\n---\nx\n")
    rep = _sync(ms, repo)
    main, other = rep["dirs"]
    assert main["imported"] == 2 and other["imported"] == 1 and len(other["conflicts"]) == 1
    assert "worktree says" not in (wd / "feedback_push_flow.md").read_text()
    assert (claude / "local-only.md").exists()  # a worktree's new memory reaches the main tree's dir


def test_symlinks_and_oversized_files_refused(ms, repo, claude, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text(FEEDBACK.replace("feedback-no-live-typing", "leak"))
    (claude / "leak.md").symlink_to(outside)
    (claude / "big.md").write_text("---\ndescription: d\n---\n" + "x" * memfile.MAX_FILE)
    rep = _sync(ms, repo)["dirs"][0]
    assert rep["imported"] == 2 and memory.find(ms, "proj", "leak") is None and memory.find(ms, "proj", "big") is None
    assert any("leak.md" in n for n in rep["notes"]) and any("big.md" in n for n in rep["notes"])
    assert outside.read_text().count("leak") == 1


def test_symlinked_memory_dir_is_refused(ms, repo, tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir()
    (real / "a.md").write_text("---\nname: a\ndescription: d\n---\n")
    d = _dir(ms, repo)
    d.parent.mkdir(parents=True)
    d.symlink_to(real)
    memory.add(ms, "proj", "x", type="project", description="d", actor="a")
    rep = _sync(ms, repo)["dirs"][0]
    assert any("symlink" in n for n in rep["notes"]) and sorted(os.listdir(real)) == ["a.md"]
    assert memory.find(ms, "proj", "a") is None


def test_claude_dir_inside_the_project_is_refused(ms, repo):
    inside = dataclasses.replace(ms, claude_dir=repo / ".claude")
    inside.claude_dir.mkdir()
    memory.add(inside, "proj", "x", type="project", description="d", actor="a")
    rep = memsync.sync_project(inside, "proj", repo, actor="a")["dirs"][0]
    assert any("inside the project" in n for n in rep["notes"])
    assert list(inside.claude_dir.iterdir()) == []


def test_no_claude_dir_creates_nothing(tk, repo, tmp_path):
    s = dataclasses.replace(tk, claude_dir=tmp_path / "no-claude")
    memory.add(s, "proj", "x", type="project", description="d", actor="a")
    rep = memsync.sync_project(s, "proj", repo, actor="a")
    assert not s.claude_dir.exists() and any("no Claude dir" in n for n in rep["dirs"][0]["notes"])
    assert (s.memory_dir / "proj.md").exists()  # the plain summary still works


def test_new_memory_creates_the_dir_and_index(ms, repo):
    memory.add(ms, "proj", "style", type="feedback", description="short lines", body="Keep it short.", actor="a")
    rep = _sync(ms, repo)["dirs"][0]
    d = _dir(ms, repo)
    assert rep["written"] == 1 and (d / "MEMORY.md").read_text() == "- [style](style.md) — short lines\n"
    p = memfile.parse((d / "style.md").read_bytes(), "style.md")
    assert (p.name, p.type, p.body) == ("style", "feedback", "Keep it short.")
    assert _sync(ms, repo)["dirs"][0]["written"] == 0


def test_sync_audit_failure_keeps_the_rows(ms, repo, claude):
    with pytest.raises(RuntimeError):
        _sync(ms, repo, Audit(fail=True))
    assert memory.list_memories(ms, "proj") == []  # the import's own audit failed first: nothing stored


def test_global_memories_go_to_the_summary_only(ms, repo, claude):
    memory.add(ms, "proj", "who", type="user", description="single user host", is_global=True, actor="a")
    _sync(ms, repo)
    assert "who" not in (claude / "MEMORY.md").read_text()
    assert "## who (user, global)" in (ms.memory_dir / "proj.md").read_text()
    memory.update(ms, "proj", "who", description="changed", is_global=True, actor="a")
    assert memsync.refresh_summaries(ms) == [ms.memory_dir / "proj.md"]
    assert "changed" in (ms.memory_dir / "proj.md").read_text()


def test_sync_all_picks_projects_with_memories_or_a_dir(ms, repo, claude, tmp_path):
    (tmp_path / "dev" / "empty").mkdir()
    reps = memsync.sync_all(ms, actor="a")
    assert [r["project"] for r in reps] == ["proj"] and reps[0]["dirs"][0]["imported"] == 2


def test_renamed_name_in_a_file_never_puts_two_memories_in_one_file(ms, repo, claude):
    _sync(ms, repo)
    f = claude / "feedback_push_flow.md"
    f.write_text(PUSH.replace("name: feedback-push-flow", "name: push-flow"))
    _sync(ms, repo)
    assert memory.find(ms, "proj", "push-flow") is not None  # a new memory, the old one stays
    files = sorted(p.name for p in claude.iterdir())
    assert files == ["MEMORY.md", "feedback_no_live_typing.md", "feedback_push_flow.md", "push-flow.md"]
    assert _sync(ms, repo)["dirs"][0]["written"] == 0  # stable: no flip-flop between the two


def test_over_long_index_title_and_hook_are_cut_not_fatal(ms, repo, claude):
    (claude / "MEMORY.md").write_text(f"- [{'T' * 300}](feedback_push_flow.md) — {'h' * 1500}\n")
    rep = _sync(ms, repo)["dirs"][0]
    assert rep["imported"] == 2
    m = memory.get(ms, "proj", "feedback-push-flow")
    assert (m.title, m.hook) == ("T" * memory.MAX_TITLE, "h" * memory.MAX_DESC)


def test_write_errors_are_notes_and_keep_the_sync_state(ms, repo, claude, monkeypatch):
    _sync(ms, repo)
    memory.update(ms, "proj", "feedback-push-flow", body="new", actor="a")
    memory.remove(ms, "proj", "feedback-no-live-typing")
    real = memsync.atomic_write

    def flaky(path, data, **kw):
        if os.path.basename(path) == "feedback_push_flow.md":
            raise OSError(28, "No space left on device")
        return real(path, data, **kw)
    monkeypatch.setattr(memsync, "atomic_write", flaky)
    real_unlink = memsync.Path.unlink

    def no_unlink(self, *a, **kw):
        if self.name == "feedback_no_live_typing.md":
            raise PermissionError(13, "denied")
        return real_unlink(self, *a, **kw)
    monkeypatch.setattr(memsync.Path, "unlink", no_unlink)
    rep = _sync(ms, repo)["dirs"][0]
    assert any("not written (No space left" in n for n in rep["notes"])
    assert any("not removed (denied)" in n for n in rep["notes"])
    assert rep["written"] == 0 and rep["removed"] == 0
    monkeypatch.undo()
    rep = _sync(ms, repo)["dirs"][0]  # the kept rows: retried, no conflict copies
    assert (rep["written"], rep["removed"], rep["conflicts"]) == (1, 1, [])
    assert "new" in (claude / "feedback_push_flow.md").read_text()
    assert not (claude / "feedback_no_live_typing.md").exists()


def test_dir_swapped_for_a_symlink_under_the_lock_is_refused(ms, repo, claude, tmp_path, monkeypatch):
    import contextlib

    from pm.core import filelock
    elsewhere = tmp_path / "elsewhere"
    real_locked = filelock.locked

    @contextlib.contextmanager
    def swap(path, *a, **kw):
        with real_locked(path, *a, **kw):
            claude.rename(elsewhere)
            claude.symlink_to(elsewhere)
            yield
    monkeypatch.setattr(memsync.filelock, "locked", swap)
    rep = _sync(ms, repo)["dirs"][0]
    assert rep["imported"] == 0 and any("symlink" in n for n in rep["notes"])
    assert memory.list_memories(ms, "proj") == []
