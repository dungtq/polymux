"""The one place pm spawns a program: argv list, no shell, a timeout (or detached, never waited for)."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

STDERR_TAIL = 300


class ProcError(Exception):
    """A program could not run or failed (check=True: ``returncode`` and ``stderr`` tail set)."""

    def __init__(self, msg: str = "", *, returncode: int | None = None, stderr: str = "") -> None:
        super().__init__(msg)
        self.returncode, self.stderr = returncode, stderr


class ProcTimeout(ProcError):
    pass


class ProcNotFound(ProcError):
    pass


def run_argv(argv: Sequence[str], *, timeout: float, input: bytes | str | None = None, cwd=None, env=None,
             check: bool = False, text: bool = False, stdout=None) -> subprocess.CompletedProcess:
    """Run ``argv`` (shell=False). Output is captured unless ``stdout`` is a file object.

    Error messages name only argv[0]: later arguments may hold user data.
    """
    if not isinstance(argv, list | tuple) or not argv or not all(isinstance(a, str) for a in argv):
        raise TypeError("argv must be a non-empty list/tuple of str")
    prog = argv[0]
    kw: dict = {"timeout": timeout, "cwd": cwd, "env": env, "text": text, "shell": False, "check": False}
    if stdout is None:
        kw["capture_output"] = True
    else:
        kw["stdout"], kw["stderr"] = stdout, subprocess.PIPE
    if input is None:
        kw["stdin"] = subprocess.DEVNULL
    else:
        kw["input"] = input
    try:
        r = subprocess.run(list(argv), **kw)
    except subprocess.TimeoutExpired as e:
        raise ProcTimeout(f"{prog}: timed out after {timeout}s") from e
    except FileNotFoundError as e:
        raise ProcNotFound(f"{prog}: not found") from e
    except OSError as e:
        raise ProcError(f"{prog}: {e.__class__.__name__}") from e
    if check and r.returncode != 0:
        err = r.stderr or ""
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        tail = err.strip()[-STDERR_TAIL:]
        raise ProcError(f"{prog}: exit {r.returncode}: {tail}", returncode=r.returncode, stderr=tail)
    return r


def spawn_detached(argv: Sequence[str]) -> None:
    """Start ``argv`` (shell=False) in its own session with no stdio and do not wait for it.

    For fire-and-forget calls that outlive the caller (e.g. a restart of pm's own unit).
    """
    if not isinstance(argv, list | tuple) or not argv or not all(isinstance(a, str) for a in argv):
        raise TypeError("argv must be a non-empty list/tuple of str")
    prog = argv[0]
    try:
        subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, shell=False)
    except FileNotFoundError as e:
        raise ProcNotFound(f"{prog}: not found") from e
    except OSError as e:
        raise ProcError(f"{prog}: {e.__class__.__name__}") from e
