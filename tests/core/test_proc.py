import subprocess
import sys

import pytest

from pm.core import proc
from pm.core.proc import ProcError, ProcNotFound, ProcTimeout, run_argv

PY = sys.executable


def test_runs_and_captures():
    r = run_argv([PY, "-c", "import sys; print('out'); print('err', file=sys.stderr)"], timeout=10, text=True)
    assert r.returncode == 0 and r.stdout == "out\n" and r.stderr == "err\n"


def test_input_and_default_devnull():
    r = run_argv([PY, "-c", "import sys; print(sys.stdin.read().upper())"], timeout=10, input="hi", text=True)
    assert r.stdout == "HI\n"
    r = run_argv((PY, "-c", "import sys; print(repr(sys.stdin.read()))"), timeout=10)
    assert r.stdout == b"''\n"


def test_stdout_to_file(tmp_path):
    p = tmp_path / "o"
    with p.open("wb") as f:
        r = run_argv([PY, "-c", "print('x')"], timeout=10, stdout=f)
    assert p.read_bytes() == b"x\n" and r.stdout is None and r.stderr == b""


@pytest.mark.parametrize("bad", ["ls -l", [], (), ["ls", 1], None])
def test_argv_type_checked(bad):
    with pytest.raises(TypeError):
        run_argv(bad, timeout=1)


def test_errors_mapped():
    with pytest.raises(ProcTimeout) as ei:
        run_argv([PY, "-c", "import time; time.sleep(5)"], timeout=0.2)
    assert isinstance(ei.value.__cause__, subprocess.TimeoutExpired)
    with pytest.raises(ProcNotFound):
        run_argv(["/nonexistent/pm-no-such-bin"], timeout=1)
    with pytest.raises(ProcError) as ei:
        run_argv(["/"], timeout=1)  # PermissionError: not a ProcNotFound
    assert type(ei.value) is ProcError
    assert issubclass(ProcTimeout, ProcError) and issubclass(ProcNotFound, ProcError)


def test_check_raises_with_tail_but_no_args():
    r = run_argv([PY, "-c", "import sys; sys.exit(3)", "secret-arg"], timeout=10)
    assert r.returncode == 3  # check=False: no raise
    with pytest.raises(ProcError) as ei:
        run_argv([PY, "-c", "import sys; sys.stderr.write('E' * 1000 + 'boom'); sys.exit(2)", "secret-arg"],
                 timeout=10, check=True)
    e = ei.value
    assert e.returncode == 2 and e.stderr.endswith("boom") and len(e.stderr) == proc.STDERR_TAIL
    assert "secret-arg" not in str(e) and "exit 2" in str(e)


def test_never_shell_and_patchable(monkeypatch):
    seen = {}

    def fake(argv, **kw):
        seen.update(kw, argv=argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(proc.subprocess, "run", fake)
    run_argv(("tmux", "ls"), timeout=3, cwd="/tmp", env={"A": "1"})
    assert seen["shell"] is False and seen["argv"] == ["tmux", "ls"] and seen["timeout"] == 3
    assert seen["capture_output"] is True and seen["stdin"] is subprocess.DEVNULL
    assert seen["cwd"] == "/tmp" and seen["env"] == {"A": "1"}


def test_spawn_detached_runs_without_waiting(tmp_path):
    out = tmp_path / "o"
    proc.spawn_detached([PY, "-c", f"open({str(out)!r}, 'w').write('ok')"])
    import time
    for _ in range(50):
        if out.exists() and out.read_text() == "ok":
            break
        time.sleep(0.1)
    assert out.read_text() == "ok"


def test_spawn_detached_kwargs_and_errors(monkeypatch):
    seen = {}

    def fake(argv, **kw):
        seen.update(kw, argv=argv)

    monkeypatch.setattr(proc.subprocess, "Popen", fake)
    proc.spawn_detached(("/usr/bin/true", "x"))
    assert seen["argv"] == ["/usr/bin/true", "x"] and seen["shell"] is False and seen["start_new_session"] is True
    assert seen["stdin"] is seen["stdout"] is seen["stderr"] is subprocess.DEVNULL
    monkeypatch.undo()
    for bad in ("ls -l", [], ["ls", 1], None):
        with pytest.raises(TypeError):
            proc.spawn_detached(bad)
    with pytest.raises(ProcNotFound):
        proc.spawn_detached(["/nonexistent/pm-no-such-bin", "secret-arg"])
    with pytest.raises(ProcError) as ei:
        proc.spawn_detached(["/", "secret-arg"])
    assert "secret-arg" not in str(ei.value)
