"""Per-project settings store (pm.projects.settings)."""

import json
import stat

import pytest
from helpers import make_settings

from pm.core.errors import Invalid
from pm.projects import settings as ps


@pytest.fixture
def s(fake_tmux, tmp_path):
    return make_settings(fake_tmux, tmp_path)


def test_defaults(s):
    assert ps.load(s, "a") == ps.ProjectSettings()


def test_hidden_roundtrip_and_unknown(s):
    r = ps.save_hidden(s, "a", ["todo", "git", "todo"])
    assert r.hidden == frozenset({"todo", "git"})
    assert ps.load(s, "a").hidden == r.hidden
    assert ps.load(s, "b").hidden == frozenset()
    with pytest.raises(Invalid):
        ps.save_hidden(s, "a", ["nope"])
    assert ps.load(s, "a").hidden == r.hidden


def test_default_branch(s):
    assert ps.save_default_branch(s, "a", "feat/x-1.2").default_branch == "feat/x-1.2"
    for bad in ("-x", "a..b", "a b", "a;b", "x" * 101, "a/", "a.lock"):
        with pytest.raises(Invalid):
            ps.save_default_branch(s, "a", bad)
    assert ps.save_default_branch(s, "a", "").default_branch == ""


def test_links(s):
    known = {"a", "b", "c"}
    assert ps.save_links(s, "a", ["c", "b", "c"], known).links == ("c", "b")
    for bad in (["a"], ["zzz"]):
        with pytest.raises(Invalid):
            ps.save_links(s, "a", bad, known)
    assert ps.load(s, "a").links == ("c", "b")


def test_independent_fields_and_mode(s):
    ps.save_hidden(s, "a", ["git"])
    ps.save_default_branch(s, "a", "main")
    r = ps.load(s, "a")
    assert r.hidden == {"git"} and r.default_branch == "main"
    assert stat.S_IMODE((s.state_dir / "project_settings.json").stat().st_mode) == 0o600


def test_tolerant_load(s):
    s.state_dir.mkdir(parents=True, exist_ok=True)
    (s.state_dir / "project_settings.json").write_text(json.dumps(
        {"a": {"hidden": ["git", "bogus", 3], "links": ["b", 4], "default_branch": "-bad"}, "b": 5}))
    assert ps.load(s, "a") == ps.ProjectSettings(frozenset({"git"}), ("b",), "")
    assert ps.load(s, "b") == ps.ProjectSettings()
    (s.state_dir / "project_settings.json").write_text("{not json")
    assert ps.load(s, "a") == ps.ProjectSettings()


def test_vms_normalise_and_validate(s):
    r = ps.save_vms(s, "a", "lab-*, web?\n  \nlab-*\nvm[12]")
    assert r.vms == ("lab-*", "web?", "vm[12]") and ps.load(s, "a").vms == r.vms
    assert ps.save_vms(s, "a", "").vms == ()
    for bad in ("a b", "x/y", "a;b", "x" * 65, "\n".join(f"p{i}" for i in range(21))):
        with pytest.raises(Invalid):
            ps.save_vms(s, "a", bad)
    assert ps.save_vms(s, "a", "x" * 64).vms == ("x" * 64,)
