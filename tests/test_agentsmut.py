from __future__ import annotations

import builtins
import json
import os
import stat
from pathlib import Path

import pytest

from pm.agents.inventory import mutate as agentsmut
from pm.agents.inventory import read as agentsinv
from pm.agents.inventory.mutate import MutError, apply, plan, validate_rules

CANON = "# canonical rules\nline two\n"


def w(p: Path, text: str, mode: int | None = None) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    if mode:
        p.chmod(mode)
    return p


def exe(p: Path) -> None:
    w(p, "#!/bin/sh\n", 0o755)


@pytest.fixture(autouse=True)
def _local_bins(monkeypatch):
    monkeypatch.setattr(
        agentsinv,
        "_bin_candidates",
        lambda spec, home: [home / ".local/bin" / b for b in spec.bins],
    )
    agentsinv.clear_cache()
    yield
    agentsinv.clear_cache()


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    for n in ("cfg", "demo"):
        w(
            h / f".agents/skills/{n}/SKILL.md",
            f"---\nname: {n}\ndescription: {n} skill\n---\nbody\n",
        )
    (h / ".agents/hooks").mkdir(parents=True)
    w(h / "agents/AGENTS.md", CANON)
    w(h / "agents/agents-index.md", "# index\n")
    exe(h / ".local/bin/claude")
    exe(h / ".local/bin/codex")
    (h / ".claude/skills").mkdir(parents=True)
    w(h / ".claude/CLAUDE.md", CANON)
    (h / ".codex").mkdir()
    w(h / ".agents-git/secret", "TOPSECRET\n")
    w(h / ".cfg/x", "cfgdata\n")
    return h


def snapshot(root: Path) -> dict[str, str]:
    out = {}
    for dp, dn, fn in os.walk(root):
        for n in dn + fn:
            p = Path(dp) / n
            if p.is_symlink():
                out[str(p)] = "L:" + os.readlink(p)
            elif p.is_file():
                out[str(p)] = p.read_text()
            else:
                out[str(p)] = "D"
    return out


def go(action, params, home):
    p = plan(action, params, home=home)
    return p, apply(action, params, p.token, home=home)


def kinds(p):
    return [o.kind for o in p.ops]


# ------------------------------------------------------------ skill toggle


def test_toggle_enable_disable(home):
    params = {"agent": "claude", "skill": "cfg", "enable": True}
    p = plan("skill_toggle", params, home=home)
    assert kinds(p) == ["symlink"]
    assert p.ops[0].path == "~/.claude/skills/cfg"
    res = apply("skill_toggle", params, p.token, home=home)
    assert res == ["linked ~/.claude/skills/cfg"]
    link = home / ".claude/skills/cfg"
    assert link.is_symlink() and os.readlink(link) == str(home / ".agents/skills/cfg")
    p2 = plan("skill_toggle", params, home=home)
    assert kinds(p2) == ["skip"]
    assert apply("skill_toggle", params, p2.token, home=home)[0].startswith("skipped")
    off = {**params, "enable": False}
    _, res = go("skill_toggle", off, home)
    assert res == ["unlinked ~/.claude/skills/cfg"]
    assert not link.is_symlink()
    assert kinds(plan("skill_toggle", off, home=home)) == ["skip"]


def test_toggle_refusals(home, tmp_path):
    base = {"agent": "claude", "skill": "cfg", "enable": True}
    for bad in (
        {**base, "agent": "nope"},
        {**base, "agent": "windsurf"},
        {**base, "agent": "gemini"},
        {**base, "skill": "missing"},
        {**base, "skill": "../x"},
        {**base, "skill": "Cfg"},
        {**base, "enable": "yes"},
    ):
        with pytest.raises(MutError):
            plan("skill_toggle", bad, home=home)


def test_toggle_not_installed(home):
    (home / ".local/bin/codex").unlink()
    (home / ".codex/skills").mkdir()
    with pytest.raises(MutError, match="not installed"):
        plan(
            "skill_toggle",
            {"agent": "codex", "skill": "cfg", "enable": True},
            home=home,
        )


def test_toggle_agent_scoped_skill_refused(home):
    w(home / ".claude/skills/mine/SKILL.md", "---\nname: mine\ndescription: x\n---\n")
    with pytest.raises(MutError, match="shared"):
        plan(
            "skill_toggle",
            {"agent": "claude", "skill": "mine", "enable": True},
            home=home,
        )


def test_toggle_real_dir_and_foreign_link(home, tmp_path):
    other = tmp_path / "other"
    w(other / "SKILL.md", "x")
    (home / ".claude/skills/cfg").mkdir()
    en = {"agent": "claude", "skill": "cfg", "enable": True}
    for enable in (True, False):
        with pytest.raises(MutError):
            plan("skill_toggle", {**en, "enable": enable}, home=home)
    (home / ".claude/skills/cfg").rmdir()
    (home / ".claude/skills/cfg").symlink_to(other)
    for enable in (True, False):
        with pytest.raises(MutError):
            plan("skill_toggle", {**en, "enable": enable}, home=home)
    assert (home / ".claude/skills/cfg").is_symlink()


def test_toggle_skills_dir_symlink_outside(home, tmp_path):
    out = tmp_path / "outside"
    out.mkdir()
    (home / ".claude/skills").rmdir()
    (home / ".claude/skills").symlink_to(out)
    with pytest.raises(MutError, match="outside"):
        plan(
            "skill_toggle",
            {"agent": "claude", "skill": "cfg", "enable": True},
            home=home,
        )
    assert list(out.iterdir()) == []


def test_toggle_shared_skill_symlink_to_elsewhere(home, tmp_path):
    other = tmp_path / "other"
    w(other / "SKILL.md", "---\nname: cfg\ndescription: x\n---\n")
    import shutil

    shutil.rmtree(home / ".agents/skills/cfg")
    (home / ".agents/skills/cfg").symlink_to(other)
    with pytest.raises(MutError):
        plan(
            "skill_toggle",
            {"agent": "claude", "skill": "cfg", "enable": True},
            home=home,
        )


# ------------------------------------------------------------ rules sync


def test_rules_sync_ops_and_apply(home):
    w(home / ".claude/CLAUDE.md", "old drifted\n")
    params = {"source": "canonical", "targets": ["codex", "claude"]}
    p = plan("rules_sync", params, home=home)
    assert [(o.kind, o.path) for o in p.ops] == [
        ("write", "~/.claude/CLAUDE.md"),
        ("symlink", "~/.codex/AGENTS.md"),
    ]
    assert "replace-with-backup" in p.ops[0].detail
    res = apply("rules_sync", params, p.token, home=home)
    assert any(r.startswith("backed up ~/.claude/CLAUDE.md") for r in res)
    for f in (".claude/CLAUDE.md", ".codex/AGENTS.md"):
        assert (home / f).is_symlink()
        assert (home / f).resolve() == (home / "agents/AGENTS.md").resolve()
    backups = list((home / ".local/state/pm/agents-backups").rglob("*CLAUDE.md"))
    assert len(backups) == 1 and backups[0].read_text() == "old drifted\n"
    p2 = plan("rules_sync", params, home=home)
    assert kinds(p2) == ["skip", "skip"]
    assert all(
        r.startswith("skipped")
        for r in apply("rules_sync", params, p2.token, home=home)
    )


def test_rules_sync_identical_skipped_and_other_source(home):
    p = plan("rules_sync", {"source": "canonical", "targets": ["claude"]}, home=home)
    assert kinds(p) == ["skip"]
    w(home / ".codex/AGENTS.md", "codex own\n")
    p = plan("rules_sync", {"source": "codex", "targets": ["claude"]}, home=home)
    assert kinds(p) == ["write"]


def test_rules_sync_refusals(home):
    for params in (
        {"source": "canonical", "targets": []},
        {"source": "canonical", "targets": ["nope"]},
        {"source": "claude", "targets": ["claude"]},
        {"source": "cursor", "targets": ["claude"]},
        {"source": "canonical", "targets": ["cursor"]},
        {"source": "canonical", "targets": [1]},
    ):
        with pytest.raises(MutError):
            plan("rules_sync", params, home=home)


def test_rules_sync_blocked_symlink_target(home):
    (home / ".claude/CLAUDE.md").unlink()
    (home / ".claude/CLAUDE.md").symlink_to(home / ".agents-git/secret")
    with pytest.raises(MutError, match="never touched"):
        plan("rules_sync", {"source": "canonical", "targets": ["claude"]}, home=home)
    assert (home / ".agents-git/secret").read_text() == "TOPSECRET\n"


def test_rules_sync_source_symlink_into_blocked(home):
    (home / "agents/AGENTS.md").unlink()
    (home / "agents/AGENTS.md").symlink_to(home / ".cfg/x")
    with pytest.raises(MutError):
        plan("rules_sync", {"source": "canonical", "targets": ["codex"]}, home=home)


# ------------------------------------------------------------ skills CRUD


def test_skill_create_edit_delete(home):
    params = {"name": "newone", "description": "Does: things", "body": "Hello\n"}
    p = plan("skill_create", params, home=home)
    assert [(o.kind, o.path) for o in p.ops] == [
        ("mkdir", "~/.agents/skills/newone"),
        ("write", "~/.agents/skills/newone/SKILL.md"),
    ]
    apply("skill_create", params, p.token, home=home)
    md = home / ".agents/skills/newone/SKILL.md"
    assert (
        md.read_text()
        == '---\nname: newone\ndescription: "Does: things"\n---\n\nHello\n'
    )
    with pytest.raises(MutError, match="already exists"):
        plan("skill_create", params, home=home)

    edit = {**params, "description": "Plain text", "body": "New body"}
    p = plan("skill_edit", edit, home=home)
    assert "backup" in p.ops[0].detail
    res = apply("skill_edit", edit, p.token, home=home)
    assert res[0].startswith("backed up ~/.agents/skills/newone/SKILL.md")
    assert "description: Plain text\n" in md.read_text()
    assert list((home / ".local/state/pm/agents-backups").rglob("*newone__SKILL.md"))
    assert kinds(plan("skill_edit", edit, home=home)) == ["skip"]


def test_skill_delete_with_links_and_confirm(home):
    go("skill_toggle", {"agent": "claude", "skill": "demo", "enable": True}, home)
    d = home / ".agents/skills/demo"
    w(d / "ref/a.md", "a")
    params = {"name": "demo", "confirm": "demo"}
    with pytest.raises(MutError, match="confirm"):
        plan("skill_delete", {"name": "demo", "confirm": "dem"}, home=home)
    with pytest.raises(MutError, match="confirm"):
        plan("skill_delete", {"name": "demo"}, home=home)
    p = plan("skill_delete", params, home=home)
    assert [(o.kind, o.path) for o in p.ops] == [
        ("unlink", "~/.claude/skills/demo"),
        ("delete", "~/.agents/skills/demo/SKILL.md"),
        ("delete", "~/.agents/skills/demo/ref/a.md"),
        ("delete", "~/.agents/skills/demo/ref"),
        ("delete", "~/.agents/skills/demo"),
    ]
    apply("skill_delete", params, p.token, home=home)
    assert not d.exists() and not (home / ".claude/skills/demo").is_symlink()
    assert (home / ".agents/skills/cfg/SKILL.md").exists()
    assert list((home / ".local/state/pm/agents-backups").rglob("*demo__SKILL.md"))


def test_skill_delete_refusals(home, tmp_path):
    d = home / ".agents/skills/demo"
    (d / "x").symlink_to(tmp_path)
    with pytest.raises(MutError, match="outside"):
        plan("skill_delete", {"name": "demo", "confirm": "demo"}, home=home)
    (d / "x").unlink()
    w(d / "a/b/c/d.md", "deep")
    with pytest.raises(MutError, match="deeper"):
        plan("skill_delete", {"name": "demo", "confirm": "demo"}, home=home)
    import shutil

    shutil.rmtree(d / "a")
    for i in range(51):
        w(d / f"f{i}.md", "x")
    with pytest.raises(MutError, match="more than"):
        plan("skill_delete", {"name": "demo", "confirm": "demo"}, home=home)


def test_skill_dir_symlink_refused_for_crud(home, tmp_path):
    other = tmp_path / "other"
    w(other / "SKILL.md", "x")
    (home / ".agents/skills/linked").symlink_to(other)
    for act in ("skill_edit", "skill_delete"):
        with pytest.raises(MutError):
            plan(
                act,
                {
                    "name": "linked",
                    "confirm": "linked",
                    "description": "d",
                    "body": "b",
                },
                home=home,
            )
    assert other.joinpath("SKILL.md").read_text() == "x"


# ------------------------------------------------------------ rules / scripts CRUD


def test_rule_crud(home):
    params = {"name": "style.md", "body": "# style\n"}
    p = plan("rule_create", params, home=home)
    assert [(o.kind, o.path) for o in p.ops] == [
        ("mkdir", "~/agents/rules"),
        ("write", "~/agents/rules/style.md"),
    ]
    apply("rule_create", params, p.token, home=home)
    f = home / "agents/rules/style.md"
    assert f.read_text() == "# style\n"
    edit = {"name": "style.md", "body": "# style v2\n"}
    _, res = go("rule_edit", edit, home)
    assert res[0].startswith("backed up")
    assert f.read_text() == "# style v2\n"
    with pytest.raises(MutError):
        plan("rule_delete", {"name": "style.md", "confirm": "style"}, home=home)
    go("rule_delete", {"name": "style.md", "confirm": "style.md"}, home)
    assert not f.exists()


def test_rule_top_files(home):
    for n in agentsmut.TOP_RULES + ("agents.md",):
        with pytest.raises(MutError, match="reserved"):
            plan("rule_create", {"name": n, "body": "x"}, home=home)
    for n in agentsmut.TOP_RULES:
        with pytest.raises(MutError):
            plan("rule_delete", {"name": n, "confirm": n}, home=home)
    go("rule_edit", {"name": "AGENTS.md", "body": "# new\n"}, home)
    assert (home / "agents/AGENTS.md").read_text() == "# new\n"
    with pytest.raises(MutError, match="does not exist"):
        plan("rule_edit", {"name": "CLAUDE.md", "body": "x"}, home=home)


def test_script_modes_and_crud(home):
    go("script_create", {"name": "run.sh", "body": "#!/bin/sh\necho hi\n"}, home)
    f = home / ".agents/hooks/run.sh"
    assert stat.S_IMODE(f.stat().st_mode) == 0o750
    go("script_create", {"name": "note.txt", "body": "plain\n"}, home)
    assert stat.S_IMODE((home / ".agents/hooks/note.txt").stat().st_mode) == 0o640
    go("script_edit", {"name": "note.txt", "body": "#!/bin/sh\n"}, home)
    assert stat.S_IMODE((home / ".agents/hooks/note.txt").stat().st_mode) == 0o750
    with pytest.raises(MutError, match="exists"):
        plan("script_create", {"name": "run.sh", "body": "x"}, home=home)
    go("script_delete", {"name": "run.sh", "confirm": "run.sh"}, home)
    assert not f.exists()
    assert list((home / ".local/state/pm/agents-backups").rglob("*run.sh"))
    assert not [
        p for p in (home / ".agents/hooks").iterdir() if p.name.endswith(".tmp")
    ]


def test_script_hook_symlink_not_edited(home, tmp_path):
    t = w(tmp_path / "elsewhere.sh", "x")
    (home / ".agents/hooks/shim").symlink_to(t)
    for act, extra in (
        ("script_edit", {"body": "y"}),
        ("script_delete", {"confirm": "shim"}),
    ):
        with pytest.raises(MutError):
            plan(act, {"name": "shim", **extra}, home=home)
    assert t.read_text() == "x"


# ------------------------------------------------------------ names and caps


BAD = ["../x", "a/b", "..", "/etc/passwd", "a\x00b", "", ".hidden", "x" * 65]


@pytest.mark.parametrize("name", BAD)
def test_bad_names(home, name):
    before = snapshot(home)
    with pytest.raises(MutError):
        plan("skill_create", {"name": name, "description": "d", "body": "b"}, home=home)
    with pytest.raises(MutError):
        plan("script_create", {"name": name, "body": "b"}, home=home)
    with pytest.raises(MutError):
        plan("rule_create", {"name": name + ".md", "body": "b"}, home=home)
    with pytest.raises(MutError):
        plan(
            "skill_toggle",
            {"agent": "claude", "skill": name, "enable": True},
            home=home,
        )
    assert snapshot(home) == before


def test_caps_and_nul(home):
    big = "x" * (256 * 1024 + 1)
    for act, p in (
        ("rule_create", {"name": "a.md", "body": big}),
        ("script_create", {"name": "a", "body": big}),
        ("skill_create", {"name": "a", "description": "d", "body": big}),
        ("script_create", {"name": "a", "body": "a\x00b"}),
        ("skill_create", {"name": "a", "description": "d" * 301, "body": "b"}),
        ("skill_create", {"name": "a", "description": "d\x00", "body": "b"}),
        ("skill_create", {"name": "a", "description": "l1\nl2", "body": "b"}),
        ("skill_create", {"name": "a", "description": " ", "body": "b"}),
        ("skill_create", {"name": "a", "description": "d", "body": 5}),
    ):
        with pytest.raises(MutError):
            plan(act, p, home=home)
    ok = {"name": "a", "body": "x" * (256 * 1024)}
    plan("script_create", ok, home=home)


def test_unknown_action_and_params(home):
    with pytest.raises(MutError):
        plan("rm_rf", {}, home=home)
    with pytest.raises(MutError):
        plan("script_create", "nope", home=home)  # type: ignore[arg-type]
    with pytest.raises(MutError):
        plan("script_frob", {"name": "a"}, home=home)


# ------------------------------------------------------------ plan purity, token, atomicity


def test_plan_is_read_only(home):
    before = snapshot(home)
    plans = [
        ("skill_toggle", {"agent": "claude", "skill": "cfg", "enable": True}),
        ("rules_sync", {"source": "canonical", "targets": ["codex"]}),
        ("skill_create", {"name": "n", "description": "d", "body": "b"}),
        ("skill_edit", {"name": "cfg", "description": "d", "body": "b"}),
        ("skill_delete", {"name": "cfg", "confirm": "cfg"}),
        ("rule_create", {"name": "n.md", "body": "b"}),
        ("rule_edit", {"name": "AGENTS.md", "body": "b"}),
        ("rule_delete", {"name": "x.md", "confirm": "x.md"}),
        ("script_create", {"name": "n", "body": "b"}),
    ]
    for act, p in plans:
        try:
            plan(act, p, home=home)
        except MutError:
            pass
    assert snapshot(home) == before


def test_token_mismatch(home):
    params = {"agent": "claude", "skill": "cfg", "enable": True}
    p = plan("skill_toggle", params, home=home)
    w(home / ".claude/skills/cfg", "squatter")
    with pytest.raises(MutError):
        apply("skill_toggle", params, p.token, home=home)
    assert (home / ".claude/skills/cfg").read_text() == "squatter"

    params = {"name": "AGENTS.md", "body": "new\n"}
    p = plan("rule_edit", params, home=home)
    (home / "agents/AGENTS.md").write_text("changed meanwhile\n")
    with pytest.raises(MutError):
        apply("rule_edit", params, p.token, home=home)
    with pytest.raises(MutError):
        apply("rule_edit", params, "bad", home=home)
    with pytest.raises(MutError):
        apply("rule_edit", params, None, home=home)  # type: ignore[arg-type]

    p = plan("rule_edit", params, home=home)
    with pytest.raises(MutError):
        apply("rule_edit", {**params, "body": "other\n"}, p.token, home=home)


def test_token_covers_source_change(home):
    params = {"source": "canonical", "targets": ["codex"]}
    p = plan("rules_sync", params, home=home)
    (home / "agents/AGENTS.md").write_text("edited\n")
    with pytest.raises(MutError):
        apply("rules_sync", params, p.token, home=home)
    assert not (home / ".codex/AGENTS.md").exists()


def test_atomic_write_failure_leaves_no_temp(home, monkeypatch):
    params = {"name": "a.sh", "body": "#!/bin/sh\n"}
    p = plan("script_create", params, home=home)

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(MutError):
        apply("script_create", params, p.token, home=home)
    assert os.listdir(home / ".agents/hooks") == []

    w(home / ".codex/AGENTS.md", "drifted\n")
    params = {"source": "canonical", "targets": ["codex"]}
    p = plan("rules_sync", params, home=home)
    with pytest.raises(MutError):
        apply("rules_sync", params, p.token, home=home)
    assert os.listdir(home / ".codex") == ["AGENTS.md"]
    assert (home / ".codex/AGENTS.md").read_text() == "drifted\n"


def test_error_messages_hide_foreign_paths(home, tmp_path):
    out = tmp_path / "outside"
    out.mkdir()
    (home / ".claude/skills").rmdir()
    (home / ".claude/skills").symlink_to(out)
    with pytest.raises(MutError) as e:
        plan(
            "skill_toggle",
            {"agent": "claude", "skill": "cfg", "enable": True},
            home=home,
        )
    assert str(tmp_path) not in str(e.value)


# ------------------------------------------------------------ protected dirs


def test_protected_never_touched(home, monkeypatch, tmp_path):
    (home / ".agents/skills/escape").symlink_to(home / ".agents-git")
    (home / "agents/rules").symlink_to(home / ".cfg")
    (home / ".agents/hooks/h").symlink_to(home / ".agents-git/secret")
    seen: list[str] = []

    def rec(real):
        def inner(*a, **k):
            seen.extend(str(x) for x in a if isinstance(x, (str, os.PathLike)))
            return real(*a, **k)

        return inner

    for mod, name in (
        (os, "replace"),
        (os, "symlink"),
        (os, "unlink"),
        (os, "rmdir"),
        (os, "mkdir"),
    ):
        monkeypatch.setattr(mod, name, rec(getattr(mod, name)))
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", rec(real_open))
    real_osopen = os.open
    monkeypatch.setattr(os, "open", rec(real_osopen))

    attempts = [
        ("skill_edit", {"name": "escape", "description": "d", "body": "b"}),
        ("skill_delete", {"name": "escape", "confirm": "escape"}),
        ("skill_toggle", {"agent": "claude", "skill": "escape", "enable": True}),
        ("rule_create", {"name": "x.md", "body": "b"}),
        ("rule_edit", {"name": "x.md", "body": "b"}),
        ("script_edit", {"name": "h", "body": "b"}),
        ("script_delete", {"name": "h", "confirm": "h"}),
    ]
    for act, p in attempts:
        with pytest.raises(MutError):
            plan(act, p, home=home)
    go("skill_toggle", {"agent": "claude", "skill": "cfg", "enable": True}, home)
    go("rules_sync", {"source": "canonical", "targets": ["codex"]}, home)
    guarded = (str(home / ".agents-git"), str(home / ".cfg"))
    assert not [s for s in seen if s.startswith(guarded)]
    assert (home / ".agents-git/secret").read_text() == "TOPSECRET\n"
    assert (home / ".cfg/x").read_text() == "cfgdata\n"
    assert os.listdir(home / ".cfg") == ["x"]


def test_rules_dir_symlink_escape_refused(home, tmp_path):
    out = tmp_path / "outside"
    out.mkdir()
    (home / "agents/rules").symlink_to(out)
    with pytest.raises(MutError):
        plan("rule_create", {"name": "x.md", "body": "b"}, home=home)
    assert list(out.iterdir()) == []


# ------------------------------------------------------------ validate


def test_validate_rules(home):
    res = validate_rules(home=home)
    by = {s: (sev, m) for sev, s, m in res}
    assert by["~/agents/AGENTS.md"][0] == "ok"
    assert (
        by["~/.claude/CLAUDE.md"][0] == "ok"
        and "identical" in by["~/.claude/CLAUDE.md"][1]
    )
    assert (
        by["~/.codex/AGENTS.md"][0] == "warn"
        and "missing" in by["~/.codex/AGENTS.md"][1]
    )

    w(home / ".claude/CLAUDE.md", "changed\n")
    by = {s: (sev, m) for sev, s, m in validate_rules(home=home)}
    assert by["~/.claude/CLAUDE.md"] == ("warn", "drift: 1 lines vs 2 canonical")

    (home / ".claude/CLAUDE.md").unlink()
    (home / ".claude/CLAUDE.md").symlink_to(home / "agents/AGENTS.md")
    by = {s: (sev, m) for sev, s, m in validate_rules(home=home)}
    assert (
        by["~/.claude/CLAUDE.md"][0] == "ok"
        and "symlink" in by["~/.claude/CLAUDE.md"][1]
    )

    (home / ".claude/CLAUDE.md").unlink()
    (home / ".claude/CLAUDE.md").symlink_to(home / "gone.md")
    by = {s: (sev, m) for sev, s, m in validate_rules(home=home)}
    assert by["~/.claude/CLAUDE.md"][0] == "error"

    (home / ".claude/CLAUDE.md").unlink()
    (home / ".claude/CLAUDE.md").symlink_to(home / ".agents-git/secret")
    by = {s: (sev, m) for sev, s, m in validate_rules(home=home)}
    assert (
        by["~/.claude/CLAUDE.md"][0] == "warn"
        and "protected" in by["~/.claude/CLAUDE.md"][1]
    )

    (home / "agents/AGENTS.md").unlink()
    by = {s: (sev, m) for sev, s, m in validate_rules(home=home)}
    assert by["~/agents/AGENTS.md"][0] == "error"


def test_validate_rules_read_only(home):
    before = snapshot(home)
    validate_rules(home=home)
    assert snapshot(home) == before


# ------------------------------------------------------------ findings fixes (M4)

KEY = "sk-FAKEKEY-0123456789abcdefXYZ"


def test_skills_link_batch(home):
    w(home / ".agents/skills/third/SKILL.md", "---\nname: third\ndescription: t\n---\nb\n")
    (home / ".claude/skills/demo").symlink_to(home / ".agents/skills/demo")
    params = {"agent": "claude", "skills": ["third", "cfg", "demo"]}
    p = plan("skills_link", params, home=home)
    assert [(o.kind, o.path) for o in p.ops] == [
        ("symlink", "~/.claude/skills/cfg"), ("skip", "~/.claude/skills/demo"), ("symlink", "~/.claude/skills/third")]
    res = apply("skills_link", params, p.token, home=home)
    assert res == ["linked ~/.claude/skills/cfg", "skipped ~/.claude/skills/demo (already linked)",
                   "linked ~/.claude/skills/third"]
    assert os.readlink(home / ".claude/skills/third") == str(home / ".agents/skills/third")


def test_skills_link_refusals(home):
    for params, msg in (
        ({"agent": "claude", "skills": []}, "Choose 1"),
        ({"agent": "claude", "skills": ["cfg"] * (agentsmut.MAX_LINK + 1)}, "Choose 1"),
        ({"agent": "claude", "skills": ["../x"]}, "Invalid skill name"),
        ({"agent": "claude", "skills": ["nope"]}, "Not a shared skill"),
        ({"agent": "nobody", "skills": ["cfg"]}, "Unknown agent"),
        ({"agent": "devin", "skills": ["cfg"]}, "not installed"),
    ):
        with pytest.raises(MutError, match=msg):
            plan("skills_link", params, home=home)
    assert not (home / ".claude/skills/cfg").exists()


def test_index_prune(home):
    idx = w(home / "agents/agents-index.md",
            "# index\n- `~/agents/AGENTS.md` canonical\n- `~/.gone/a.md` old\n- also `~/.gone/b` here\n", 0o644)
    params = {"lines": ["~/.gone/a.md"]}
    p = plan("index_prune", params, home=home)
    assert [(o.kind, o.path) for o in p.ops] == [("write", "~/agents/agents-index.md")]
    assert "drop 1 line (3)" in p.ops[0].detail
    res = apply("index_prune", params, p.token, home=home)
    assert res[0].startswith("backed up ~/agents/agents-index.md") and res[-1] == "wrote ~/agents/agents-index.md"
    assert idx.read_text() == "# index\n- `~/agents/AGENTS.md` canonical\n- also `~/.gone/b` here\n"
    assert stat.S_IMODE(idx.stat().st_mode) == 0o644
    for params, msg in (
        ({"lines": ["~/agents/AGENTS.md"]}, "no longer stale"),  # exists: never dropped
        ({"lines": ["~/.gone/a.md"]}, "no longer stale"),  # already gone from the file
        ({"lines": []}, "Choose 1"),
        ({"lines": ["a\nb"]}, "Invalid entry"),
    ):
        with pytest.raises(MutError, match=msg):
            plan("index_prune", params, home=home)


def _mcp_home(home, path=".claude.json", extra=None, mode=0o600):
    data = {"userID": "keep-me", "mcpServers": {"context7": {
        "command": "npx", "args": ["-y", "@upstash/context7-mcp", "--api-key", KEY], **(extra or {})}}}
    return w(home / path, json.dumps(data, indent=2) + "\n", mode)


def test_mcp_env_moves_key_to_env(home):
    cfg = _mcp_home(home)
    params = {"config": "~/.claude.json", "server": "context7"}
    p = plan("mcp_env", params, home=home)
    assert [(o.kind, o.path) for o in p.ops] == [("write", "~/.claude.json")]
    assert "--api-key" in p.ops[0].detail and "CONTEXT7_API_KEY" in p.ops[0].detail
    assert KEY not in repr(p)
    res = apply("mcp_env", params, p.token, home=home)
    assert KEY not in repr(res)
    data = json.loads(cfg.read_text())
    assert data["userID"] == "keep-me"
    assert data["mcpServers"]["context7"]["args"] == ["-y", "@upstash/context7-mcp"]
    assert data["mcpServers"]["context7"]["env"] == {"CONTEXT7_API_KEY": KEY}
    assert cfg.read_text().startswith('{\n  "userID"') and cfg.read_text().endswith("}\n")
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    backups = list((home / agentsmut.BACKUP_REL).rglob("*"))
    files = [b for b in backups if b.is_file()]
    assert len(files) == 1 and stat.S_IMODE(files[0].stat().st_mode) == 0o600 and KEY in files[0].read_text()
    with pytest.raises(MutError, match="No credential"):
        plan("mcp_env", params, home=home)


def test_mcp_env_other_json_and_equals_form(home):
    cfg = w(home / ".cursor/mcp.json", json.dumps({"mcpServers": {"exa": {
        "command": "npx", "args": ["exa-mcp", f"--token={KEY}"], "env": {"OTHER": "x"}}}}))
    _, res = go("mcp_env", {"config": "~/.cursor/mcp.json", "server": "exa"}, home)
    data = json.loads(cfg.read_text())
    assert data == {"mcpServers": {"exa": {"command": "npx", "args": ["exa-mcp"], "env": {"OTHER": "x", "EXA_TOKEN": KEY}}}}
    assert "\n" not in cfg.read_text() and '"env": {' in cfg.read_text()  # a one-line file stays one line
    w(cfg, json.dumps({"servers": {"e": {"command": "x", "args": ["--api-key", KEY]}}}, separators=(",", ":")))
    go("mcp_env", {"config": "~/.cursor/mcp.json", "server": "e"}, home)
    assert cfg.read_text() == '{"servers":{"e":{"command":"x","args":[],"env":{"E_API_KEY":"%s"}}}}' % KEY


def test_mcp_env_refusals(home):
    _mcp_home(home, extra={"env": {"CONTEXT7_API_KEY": "different"}})
    for params, msg in (
        ({"config": "~/.claude.json", "server": "context7"}, "already holds another value"),
        ({"config": "~/.claude.json", "server": "nope"}, "not in the file"),
        ({"config": "~/.codex/config.toml", "server": "x"}, "Only JSON"),
        ({"config": "~/.agents-git/mcp.json", "server": "x"}, "Not an MCP config"),
        ({"config": "/etc/passwd", "server": "x"}, "Not an MCP config"),
    ):
        with pytest.raises(MutError, match=msg) as e:
            plan("mcp_env", params, home=home)
        assert KEY not in str(e.value) and "different" not in str(e.value)
    w(home / ".cursor/mcp.json", json.dumps({"mcpServers": {"u": {"command": "x", "args": [f"https://a:{KEY}@h/"]}}}))
    with pytest.raises(MutError, match="cannot be moved automatically"):
        plan("mcp_env", {"config": "~/.cursor/mcp.json", "server": "u"}, home=home)


def test_mcp_env_symlinked_config_refused(home, tmp_path):
    real = w(tmp_path / "elsewhere.json", json.dumps({"mcpServers": {"c": {"command": "x", "args": ["--api-key", KEY]}}}))
    (home / ".claude.json").symlink_to(real)
    with pytest.raises(MutError):
        plan("mcp_env", {"config": "~/.claude.json", "server": "c"}, home=home)


def test_mcp_env_write_between_token_and_execute_is_stale(home, monkeypatch):
    """The replace re-checks against the state the token hashed, not a second read after it."""
    cfg = _mcp_home(home)
    params = {"config": "~/.claude.json", "server": "context7"}
    p = plan("mcp_env", params, home=home)
    real = agentsmut._execute

    def racing(built, h, states):
        cfg.write_text(cfg.read_text().replace("keep-me", "claude-wrote-this"))
        return real(built, h, states)

    monkeypatch.setattr(agentsmut, "_execute", racing)
    with pytest.raises(MutError) as e:
        apply("mcp_env", params, p.token, home=home)
    assert str(e.value) == agentsmut.STALE
    assert "claude-wrote-this" in cfg.read_text() and KEY in cfg.read_text()
    assert not [x for x in os.listdir(home) if x.endswith(".tmp")]


def test_mcp_env_rechecks_right_before_replace(home, monkeypatch):
    """Claude rewrites ~/.claude.json while it runs: a write between the token check and the replace wins."""
    cfg = _mcp_home(home)
    params = {"config": "~/.claude.json", "server": "context7"}
    p = plan("mcp_env", params, home=home)
    real = agentsmut.atomic_write

    def racing(path, data, **kw):
        if Path(path) == cfg:
            cfg.write_text(cfg.read_text().replace("keep-me", "claude-wrote-this"))
        return real(path, data, **kw)

    monkeypatch.setattr(agentsmut, "atomic_write", racing)
    with pytest.raises(MutError) as e:
        apply("mcp_env", params, p.token, home=home)
    assert str(e.value) == agentsmut.STALE
    assert "claude-wrote-this" in cfg.read_text() and KEY in cfg.read_text()
    assert not [x for x in os.listdir(home) if x.endswith(".tmp")]
