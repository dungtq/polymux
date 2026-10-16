import threading

import pytest

from pm.core import accounts as ac
from pm.core.config import Settings
from pm.core.errors import Invalid


def mk(tmp_path, **toml):
    return Settings(state_dir=tmp_path, claude_dir=tmp_path / "cl", accounts=toml)


def test_names_merge_toml_first_and_ui_additions(tmp_path):
    s = mk(tmp_path, claude=("work",))
    ac.add(s, "claude", "client-x")
    assert ac.names(s, "claude") == ("work", "client-x") and ac.names(s, "codex") == ()
    assert ac.is_readonly(s, "claude", "work") and not ac.is_readonly(s, "claude", "client-x")


def test_add_rejects_bad_duplicate_and_unknown_kind(tmp_path):
    s = mk(tmp_path, claude=("work",))
    for kind, name in (("claude", "Bad Name"), ("claude", "work"), ("claude", "-"), ("devin", "x"), ("claude", "")):
        with pytest.raises(Invalid):
            ac.add(s, kind, name)


def test_remove_unlists_only_ui_accounts_and_clears_a_default(tmp_path):
    s = mk(tmp_path, claude=("work",))
    ac.add(s, "claude", "a1")
    ac.set_default(s, "claude", "a1")
    assert ac.global_default(s, "claude") == "a1"
    with pytest.raises(Invalid):
        ac.remove(s, "claude", "work")
    ac.remove(s, "claude", "a1")
    assert ac.names(s, "claude") == ("work",) and ac.global_default(s, "claude") == ""
    with pytest.raises(Invalid):
        ac.remove(s, "claude", "a1")


def test_remove_keeps_the_login_folder(tmp_path):
    s = mk(tmp_path)
    ac.add(s, "codex", "a1")
    d = ac.account_dir(s, "codex", "a1")
    d.mkdir(parents=True)
    (d / "auth.json").write_text("{}")
    ac.remove(s, "codex", "a1")
    assert (d / "auth.json").exists()


def test_resolve_order_project_then_global_then_cli(tmp_path):
    s = mk(tmp_path, claude=("work", "b"))
    assert ac.resolve(s, "claude", "") == ""
    ac.set_default(s, "claude", "work")
    assert ac.resolve(s, "claude", "") == "work"
    assert ac.resolve(s, "claude", "b") == "b"
    assert ac.resolve(s, "claude", ac.CLI_DEFAULT) == ""
    with pytest.raises(Invalid):
        ac.resolve(s, "claude", "gone")
    with pytest.raises(Invalid):
        ac.set_default(s, "claude", "gone")


def test_ui_changes_are_seen_without_restart_and_by_a_second_reader(tmp_path):
    s = mk(tmp_path)
    assert ac.names(s, "claude") == ()
    ac.add(s, "claude", "w1")
    assert ac.names(s, "claude") == ("w1",)
    ac.add(s, "claude", "w2")  # same size file class of change: the mtime cache must notice it
    assert ac.names(s, "claude") == ("w1", "w2")


def test_env_dirs_and_config_dirs(tmp_path):
    s = mk(tmp_path, claude=("work",), codex=("a1",))
    assert ac.account_env(s, "claude", "") == {}
    assert ac.account_env(s, "claude", "work") == {"CLAUDE_CONFIG_DIR": str(tmp_path / "accounts/claude/work")}
    assert ac.account_env(s, "codex", "a1") == {"CODEX_HOME": str(tmp_path / "accounts/codex/a1")}
    assert ac.config_dirs(s, "claude") == [tmp_path / "cl", tmp_path / "accounts/claude/work"]
    assert ac.config_dir(s, "claude") == tmp_path / "cl"
    assert ac.config_dir(s, "claude", "work") == tmp_path / "accounts/claude/work"
    for kind, name in (("claude", "nope"), ("devin", "x"), ("claude", "../x")):
        with pytest.raises(Invalid):
            ac.account_env(s, kind, name)


def test_corrupt_file_is_treated_as_empty(tmp_path):
    (tmp_path / "accounts.json").write_text("{not json")
    assert ac.names(mk(tmp_path), "claude") == ()
    (tmp_path / "accounts.json").write_text('{"claude": {"names": ["ok", "Bad!", 3], "default": 5}}')
    s = mk(tmp_path)
    assert ac.names(s, "claude") == ("ok",) and ac.global_default(s, "claude") == ""


def test_concurrent_adds_are_not_lost(tmp_path):
    s = mk(tmp_path)
    ts = [threading.Thread(target=ac.add, args=(s, "claude", f"w{i}")) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(ac.names(s, "claude")) == sorted(f"w{i}" for i in range(8))
