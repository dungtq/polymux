"""In-memory fake of the virsh calls pm makes; never the real libvirt.

``run(argv, timeout)`` is a ``libvirt.Runner``; ``run_argv`` stands in for ``libvirt.run_argv`` in route tests.
"""

import subprocess


class FakeVirt:
    def __init__(self, doms=None, snaps=None, display="vnc://127.0.0.1:5901", vnc="127.0.0.1:1"):
        self.doms = dict(doms or {"lab-1": "running", "lab-2": "shut off", "lab-eve": "running",
                                  "prod": "running"})
        self.snaps = {k: list(v) for k, v in (snaps or {"lab-1": ["s1", "s2"]}).items()}
        self.current = {"lab-1": "s2"}
        self.display, self.vnc = display, vnc
        self.calls: list[tuple[list[str], float]] = []
        self.fail = False

    @property
    def writes(self) -> list[list[str]]:
        return [a[3:] for a, _t in self.calls if a[1] != "-r"]

    def run(self, argv, timeout):
        argv = list(argv)
        self.calls.append((argv, timeout))
        write = argv[1] != "-r"
        a = argv[3:] if write else argv[4:]
        verb, rest = a[0], a[1:]
        pos = rest[rest.index("--") + 1:] if "--" in rest else []
        if write:
            return self._write(verb, rest, pos)
        dom = pos[0] if pos else ""
        if verb == "list":
            return 0, "\n".join(self.doms) + "\n"
        if dom not in self.doms:
            return 1, "error: failed to get domain"
        if verb == "domstate":
            return 0, self.doms[dom] + "\n\n"
        if verb == "snapshot-list" and "--name" in rest:
            return 0, "".join(f"{s}\n" for s in self.snaps.get(dom, []))
        if verb == "snapshot-list":
            rows = "".join(f" {s}   2026-10-15 10:00:00 +0700   running\n" for s in self.snaps.get(dom, []))
            return 0, " Name   Creation Time               State\n---------------------------\n" + rows
        if verb == "snapshot-current":
            cur = self.current.get(dom)
            return (0, cur + "\n") if cur else (1, "error: no current snapshot")
        if verb == "domdisplay":
            return (0, self.display + "\n") if self.display else (1, "error: no graphics")
        if verb == "vncdisplay":
            return (0, self.vnc + "\n") if self.vnc else (1, "error: no vnc")
        return 2, "unknown"

    def _write(self, verb, rest, pos):
        if self.fail:
            return 1, "error: boom"
        dom = pos[0]
        if verb in ("start", "shutdown", "destroy"):
            self.doms[dom] = {"start": "running", "shutdown": "running", "destroy": "shut off"}[verb]
        elif verb == "undefine":
            del self.doms[dom]
        elif verb == "snapshot-create-as":
            self.snaps.setdefault(dom, []).append(pos[1])
        elif verb == "snapshot-revert":
            self.current[dom] = pos[1]
        elif verb == "snapshot-delete":
            self.snaps[dom].remove(pos[1])
        else:
            return 2, "unknown"
        return 0, ""

    def run_argv(self, argv, *, timeout, **kw):
        rc, out = self.run(argv, timeout)
        b = out.encode()
        return subprocess.CompletedProcess(list(argv), rc, b if rc == 0 else b"", b if rc != 0 else b"")
