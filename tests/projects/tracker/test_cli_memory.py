"""pmctl memory: JSON shapes, text lines, the context index, sync (one project and --all), the sync right after a
change, global memories, exit codes and audit lines. ``cfg`` is overridden here so Claude's home is a tmp dir."""

import json
import stat

import pytest

from pm.core.fsutil import encode_cwd

KEYS = ["name", "project", "scope", "type", "title", "description", "body_md", "meta", "source", "created_by",
        "updated_by", "created_at", "updated_at"]


@pytest.fixture
def claude_home(tmp_path):
    d = tmp_path / "claude"
    d.mkdir()
    return d


@pytest.fixture
def cfg(tk, tmp_path, claude_home):
    p = tmp_path / "pm.toml"
    p.write_text(f'dev_root = "{tk.dev_root}"\nextra_projects = []\nstate_dir = "{tk.state_dir}"\n'
                 f'claude_dir = "{claude_home}"\n')
    return p


def _j(out):
    return json.loads(out)


def _audit(tk):
    return [json.loads(ln) for ln in tk.audit_file.read_text().splitlines()]


def _mdir(claude_home, repo):
    return claude_home / "projects" / encode_cwd(str(repo)) / "memory"


def test_add_show_list_set_rm(pmctl, tk, repo, claude_home):
    code, out, err = pmctl("--json", "memory", "add", "style", "--type", "feedback", "--description",
                           "Short lines", "--text", "Keep lines short.", "--title", "Style")
    assert code == 0, err
    m = _j(out)
    assert list(m) == KEYS and (m["name"], m["project"], m["type"], m["created_by"]) == (
        "style", "proj", "feedback", "claude-test01")
    f = _mdir(claude_home, repo) / "style.md"  # synced right away
    assert "Keep lines short." in f.read_text() and stat.S_IMODE(f.stat().st_mode) == 0o600
    assert "- [Style](style.md) — Short lines" in (f.parent / "MEMORY.md").read_text()
    a = _audit(tk)
    assert [x["action"] for x in a] == ["tracker_memory_add", "tracker_memory_sync"]
    assert (a[0]["target"], a[0]["client"], a[0]["detail"]["name"]) == ("proj/memory", "pmctl:claude-test01", "style")
    assert "Keep lines" not in json.dumps(a)
    code, out, _ = pmctl("memory", "show", "style")
    assert out.splitlines()[0] == "- style (feedback): Short lines" and out.rstrip().endswith("Keep lines short.")
    code, out, _ = pmctl("--json", "memory", "set", "style", "--description", "Very short lines")
    assert code == 0 and _j(out)["description"] == "Very short lines" and _j(out)["body_md"] == "Keep lines short."
    assert "Very short lines" in f.read_text()
    code, out, _ = pmctl("memory", "list")
    assert out == "- style (feedback): Very short lines\n"
    code, out, _ = pmctl("memory", "rm", "style")
    assert code == 0 and out == "memory style removed\n" and not f.exists()
    code, _, err = pmctl("memory", "show", "style")
    assert code == 1 and "no memory 'style'" in err


def test_set_adds_when_missing_and_body_from_stdin(pmctl, monkeypatch):
    import io
    code, _, err = pmctl("memory", "set", "new-one", "--text", "x")
    assert code == 1 and "--type and --description" in err
    monkeypatch.setattr("sys.stdin", io.StringIO("from stdin\n"))
    code, out, _ = pmctl("--json", "memory", "set", "new-one", "--type", "project", "--description", "d",
                         "--body-file", "-")
    assert code == 0 and _j(out)["body_md"] == "from stdin"


def test_errors_exit_1(pmctl):
    code, _, err = pmctl("memory", "add", "Bad Name", "--type", "user", "--description", "d")
    assert code == 1 and "bad memory name" in err
    code, _, err = pmctl("memory", "add", "g", "--type", "feedback", "--description", "d", "--global")
    assert code == 1 and "only type user" in err
    pmctl("memory", "add", "dup", "--type", "user", "--description", "d")
    code, _, err = pmctl("memory", "add", "dup", "--type", "user", "--description", "d")
    assert code == 1 and "already exists" in err
    with pytest.raises(SystemExit) as ex:
        pmctl("memory", "add", "x", "--type", "note", "--description", "d")
    assert ex.value.code == 1


def test_global_list_all_context_search(pmctl, tk):
    pmctl("memory", "add", "who", "--type", "user", "--description", "single user host", "--global")
    pmctl("memory", "add", "build", "--type", "project", "--description", "uv run pytest", "--text", "needle here")
    code, out, _ = pmctl("memory", "list")
    assert out == "- build (project): uv run pytest\n"
    code, out, _ = pmctl("--json", "memory", "list", "--all")
    assert [(m["name"], m["scope"]) for m in _j(out)] == [("build", "project"), ("who", "global")]
    code, out, _ = pmctl("memory", "context")
    assert out.splitlines() == ["pm memory for proj: 2 memories", "- build (project): uv run pytest",
                                "- who (user, global): single user host", "show one: pmctl memory show NAME"]
    code, out, _ = pmctl("--json", "memory", "context")
    d = _j(out)
    assert d["project"] == "proj" and [m["name"] for m in d["memories"]] == ["build", "who"]
    assert d["show"] == "show one: pmctl memory show NAME"
    code, out, _ = pmctl("memory", "search", "NEEDLE")
    assert out == "- build (project): uv run pytest\n"
    code, out, _ = pmctl("memory", "show", "who")
    assert out.startswith("- who (user, global): single user host")
    assert "## who (user, global)" in (tk.memory_dir / "proj.md").read_text()
    code, out, _ = pmctl("memory", "rm", "who", "--global")
    assert code == 0


def test_context_without_memories(pmctl):
    code, out, _ = pmctl("memory", "context")
    assert code == 0 and out.splitlines()[0] == "pm memory for proj: 0 memories"
    assert out.splitlines()[1].startswith("add one: pmctl memory add NAME")


def test_sync_imports_claude_memories_and_sync_all(pmctl, tk, repo, claude_home):
    d = _mdir(claude_home, repo)
    d.mkdir(parents=True)
    (d / "user_role.md").write_text("---\nname: user-role\ndescription: dt runs one host\nmetadata:\n"
                                    "  node_type: memory\n  type: user\n---\n\nSingle user.\n")
    (d / "MEMORY.md").write_text("- [User role](user_role.md) — one host\n")
    before = (d / "user_role.md").read_bytes()
    code, out, _ = pmctl("--json", "memory", "sync")
    rep = _j(out)
    assert code == 0 and set(rep) == {"project", "dirs", "summary", "notes"} and rep["dirs"][0]["imported"] == 1
    assert set(rep["dirs"][0]) == {"dir", "imported", "updated", "written", "removed", "conflicts", "notes"}
    assert (d / "user_role.md").read_bytes() == before
    code, out, _ = pmctl("memory", "sync")
    assert out.splitlines()[0] == "proj:" and "0 imported, 0 updated, 0 written, 0 removed" in out
    code, out, _ = pmctl("--json", "memory", "sync", "--all", cwd=tk.dev_root)
    assert code == 0 and [r["project"] for r in _j(out)] == ["proj"]
    code, out, _ = pmctl("--json", "memory", "show", "user-role")
    assert _j(out)["title"] == "User role" and _j(out)["source"] == "user_role.md"


def test_change_is_kept_when_the_sync_after_it_fails(pmctl, monkeypatch):
    from pm.projects.tracker import memsync

    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(memsync, "sync_project", boom)
    code, out, err = pmctl("memory", "add", "kept", "--type", "project", "--description", "d")
    assert code == 0 and "sync failed" in err
    assert pmctl("memory", "show", "kept")[0] == 0
