"""projects / projfiles spawn through pm.core.proc.run_argv; failures keep their old outcome."""

import subprocess
from types import SimpleNamespace

import pytest

from pm.projects import projects, projfiles


def _timeout(*a, **k):
    raise subprocess.TimeoutExpired(a[0], k.get("timeout", 0))


def _oserror(*a, **k):
    raise PermissionError("nope")


@pytest.mark.parametrize("boom", [_timeout, _oserror])
def test_git_init_failure_is_502(tmp_path, monkeypatch, boom):
    monkeypatch.setattr("pm.core.proc.subprocess.run", boom)
    with pytest.raises(projects.NewProjectError) as e:
        projects.create_project(SimpleNamespace(dev_root=tmp_path), "fresh", git_init=True)
    assert e.value.status == 502 and e.value.msg == "could not initialise the project"


def test_git_init_runs(tmp_path):
    p = projects.create_project(SimpleNamespace(dev_root=tmp_path), "fresh", git_init=True)
    assert (p / ".git").is_dir()


@pytest.mark.parametrize("boom", [_timeout, _oserror])
def test_btime_failures(tmp_path, monkeypatch, boom):
    (tmp_path / "a").write_text("x")
    monkeypatch.setattr("pm.core.proc.subprocess.run", boom)
    assert projfiles._btime(str(tmp_path), ["a"]) == 0.0
    assert projfiles._btimes_raw(str(tmp_path), [], ["a"]) == {}
