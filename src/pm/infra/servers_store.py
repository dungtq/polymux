"""Remote servers (Settings > SSH > Servers): storage, validation and the ssh calls.

State: ``<state_dir>/servers.json`` (0600) holds ``{id, name, host, port, user, hostkey_fp, added,
last_test}``; never a password or a key. Host keys are pinned (after the user confirms the fingerprint) into
``<state_dir>/ssh/known_hosts_pm``; ``~/.ssh/known_hosts`` is never touched. Every ssh call is an argv list
(no shell, fixed binary, timeout, user values after ``--``) and is reduced to a result class; ssh stderr is
never stored, shown or logged. The one-time install password travels through a private FIFO read by
``tools/pm-askpass`` (SSH_ASKPASS_REQUIRE=force); it is not in argv, env, on disk or in any log.
Design: docs/DESIGN-GIT-REMOTES-SERVERS.md sections 3 and 5.
"""

from __future__ import annotations

import errno
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from pm.core.config import Settings
from pm.core.filelock import LockTimeout, lock_path, locked
from pm.core.fsutil import JsonStore, atomic_write
from pm.core.proc import ProcError, ProcTimeout, run_argv
from pm.infra import sshkeys

SSH_BIN = "ssh"
KEYSCAN_BIN = "ssh-keyscan"
ASKPASS = Path(__file__).resolve().parents[3] / "tools" / "pm-askpass"
TEST_TIMEOUT_S = 10.0
INSTALL_TIMEOUT_S = 30.0
SCAN_TIMEOUT_S = 10.0
MAX_SERVERS = 50
MAX_PASSWORD = 256

_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?", re.ASCII)
_USER = re.compile(r"[a-z_][a-z0-9_\-]{0,31}", re.ASCII)
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ \-]{0,39}", re.ASCII)
_ID = re.compile(r"[0-9a-f]{8}", re.ASCII)
_B64 = re.compile(r"[A-Za-z0-9+/]{20,400}={0,3}", re.ASCII)

# Fixed remote script (no secrets; the public key arrives on stdin). Idempotent append.
REMOTE_INSTALL = (
    "exec sh -c 'umask 077; mkdir -p \"$HOME/.ssh\" && chmod 700 \"$HOME/.ssh\" && k=$(cat) && "
    "[ -n \"$k\" ] && touch \"$HOME/.ssh/authorized_keys\" && chmod 600 \"$HOME/.ssh/authorized_keys\" && "
    "{ grep -qxF \"$k\" \"$HOME/.ssh/authorized_keys\" || printf \"%s\\n\" \"$k\" >> \"$HOME/.ssh/authorized_keys\"; }'"
)

RESULTS = {
    "ok": "connected with the pm key",
    "auth_denied": "authentication failed",
    "hostkey_changed": "host key changed",
    "hostkey_unknown": "host key not pinned",
    "unreachable": "unreachable",
    "timeout": "timed out",
    "verify_failed": "key installed but key login still fails",
    "error": "ssh failed",
}


class ServerError(Exception):
    """Message is safe to show the user."""


# ------------------------------------------------------------------ validation
def valid_host(host: str) -> bool:
    if not isinstance(host, str) or not host or host.startswith("-") or len(host) > 253:
        return False
    if ".." in host:
        return False
    if _HOST.fullmatch(host):
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def valid_user(user: str) -> bool:
    return isinstance(user, str) and bool(_USER.fullmatch(user))


def parse_port(raw: str | int) -> int:
    s = str(raw).strip() or "22"
    if not re.fullmatch(r"[0-9]{1,5}", s) or not 1 <= int(s) <= 65535:
        raise ServerError("port must be 1-65535")
    return int(s)


def valid_id(sid: str) -> bool:
    return isinstance(sid, str) and bool(_ID.fullmatch(sid))


def hostspec(host: str, port: int) -> str:
    return host if port == 22 else f"[{host}]:{port}"


# ------------------------------------------------------------------ storage
def servers_file(settings: Settings) -> Path:
    return settings.state_dir / "servers.json"


def known_hosts(settings: Settings) -> Path:
    return sshkeys.ssh_dir(settings) / "known_hosts_pm"


def _lock(settings: Settings):
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    return locked(lock_path(settings.state_dir, servers_file(settings)))


def _clean(d: object) -> dict | None:
    if not isinstance(d, dict):
        return None
    try:
        sid, name, host, user = d["id"], d["name"], d["host"], d["user"]
        port = int(d["port"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (valid_id(sid) and isinstance(name, str) and valid_host(host) and valid_user(user)
            and 1 <= port <= 65535):
        return None
    lt = d.get("last_test")
    if not (isinstance(lt, dict) and isinstance(lt.get("ok"), bool) and isinstance(lt.get("reason"), str)):
        lt = None
    fp = d.get("hostkey_fp")
    return {"id": sid, "name": name[:40], "host": host, "port": port, "user": user,
            "hostkey_fp": fp if isinstance(fp, str) and fp.startswith("SHA256:") else "",
            "added": float(d.get("added") or 0), "last_test": lt}


def load(settings: Settings) -> list[dict]:
    raw = JsonStore(servers_file(settings), dict).load()
    items = raw.get("servers") if isinstance(raw, dict) else None
    out = [c for c in (_clean(x) for x in items or []) if c]
    return out[:MAX_SERVERS]


def _save(settings: Settings, servers: list[dict]) -> None:
    try:
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(servers_file(settings), json.dumps({"servers": servers}, indent=1) + "\n",
                     mode=0o600, fsync=True, fsync_dir=True)
    except OSError:
        raise ServerError("cannot write the server list") from None


def get(settings: Settings, sid: str) -> dict:
    if valid_id(sid):
        for s in load(settings):
            if s["id"] == sid:
                return s
    raise ServerError("unknown server")


def add(settings: Settings, name: str, host: str, port: str | int, user: str) -> dict:
    name, host, user = (name or "").strip(), (host or "").strip(), (user or "").strip()
    if not _NAME.fullmatch(name):
        raise ServerError("name: 1-40 letters, digits, space, . _ -")
    if not valid_host(host):
        raise ServerError("host: a hostname or IP address (no leading dash)")
    if not valid_user(user):
        raise ServerError("user: lowercase letters, digits, _ - (max 32)")
    p = parse_port(port)
    try:
        with _lock(settings):
            cur = load(settings)
            if len(cur) >= MAX_SERVERS:
                raise ServerError("too many servers")
            if any((s["host"].lower(), s["port"], s["user"]) == (host.lower(), p, user) for s in cur):
                raise ServerError("that server is already listed")
            if any(s["name"].lower() == name.lower() for s in cur):
                raise ServerError("that name is already used")
            rec = {"id": secrets.token_hex(4), "name": name, "host": host, "port": p, "user": user,
                   "hostkey_fp": "", "added": time.time(), "last_test": None}
            _save(settings, cur + [rec])
    except LockTimeout:
        raise ServerError("another change is in progress") from None
    return rec


def _apply(settings: Settings, sid: str, **fields) -> None:
    """Update one record; the caller holds ``_lock``."""
    cur = load(settings)
    for s in cur:
        if s["id"] == sid:
            s.update(fields)
    _save(settings, cur)


def remove(settings: Settings, sid: str) -> dict:
    try:
        with _lock(settings):
            cur = load(settings)
            gone = next((s for s in cur if s["id"] == sid), None)
            if gone is None:
                raise ServerError("unknown server")
            rest = [s for s in cur if s["id"] != sid]
            _save(settings, rest)
            if not any((s["host"], s["port"]) == (gone["host"], gone["port"]) for s in rest):
                _drop_hostkey(settings, hostspec(gone["host"], gone["port"]))
    except LockTimeout:
        raise ServerError("another change is in progress") from None
    return gone


def _drop_hostkey(settings: Settings, spec: str) -> None:
    kh = known_hosts(settings)
    try:
        lines = kh.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    keep = [ln for ln in lines if ln.split(" ", 1)[0] != spec]
    if len(keep) != len(lines):
        atomic_write(kh, "\n".join(keep) + "\n" if keep else "", mode=0o600, fsync=True, fsync_dir=True)


# ------------------------------------------------------------------ ssh plumbing
def _env(**extra: str) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C", "LANG": "C"}
    env.update(extra)
    return env


def classify(rc: int, stderr: str) -> str:
    """Reduce an ssh exit to a result class. The stderr text itself is dropped by the caller."""
    if rc == 0:
        return "ok"
    e = stderr.lower()
    if "remote host identification has changed" in e:
        return "hostkey_changed"
    if "host key verification failed" in e or "no hostkey alg" in e:
        return "hostkey_unknown"
    if "permission denied" in e or "too many authentication failures" in e:
        return "auth_denied"
    if "timed out" in e:
        return "timeout"
    if rc == 255:
        return "unreachable"
    return "error"


def _run(argv: list[str], env: dict[str, str], stdin: bytes | None, timeout: float,
         feeder=None) -> tuple[int | None, str]:
    """argv list, no shell. Returns (rc, stderr) or (None, "") on timeout. stdout is discarded."""
    try:
        p = subprocess.Popen(
            argv, env=env, shell=False, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True,
        )
    except OSError:
        return 127, ""
    if feeder:
        feeder(p)
    try:
        _, err = p.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            p.kill()
        p.communicate()
        return None, ""
    return p.returncode, err.decode("utf-8", "replace")[:2000]


def _result(rc: int | None, err: str) -> str:
    return "timeout" if rc is None else ("error" if rc == 127 else classify(rc, err))


def _need_key(settings: Settings) -> Path:
    k = sshkeys.key_path(settings)
    if not k.is_file():
        raise ServerError("generate the pm key first")
    return k


def probe_argv(settings: Settings, s: dict) -> list[str]:
    return [SSH_BIN, "-F", "/dev/null", "-i", str(sshkeys.key_path(settings)),
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "PasswordAuthentication=no",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known_hosts(settings)}",
            "-o", "GlobalKnownHostsFile=/dev/null", "-o", "ConnectTimeout=5",
            "-p", str(s["port"]), "--", f"{s['user']}@{s['host']}", "true"]


def install_argv(settings: Settings, s: dict, user: str) -> list[str]:
    return [SSH_BIN, "-F", "/dev/null", "-o", "BatchMode=no",
            "-o", "PreferredAuthentications=password,keyboard-interactive", "-o", "PubkeyAuthentication=no",
            "-o", "NumberOfPasswordPrompts=1", "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={known_hosts(settings)}", "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10", "-p", str(s["port"]), "--", f"{user}@{s['host']}", REMOTE_INSTALL]


def probe(settings: Settings, sid: str) -> tuple[dict, str]:
    """Test connection with the pm key. Returns (server, result class) and records it as last_test."""
    s = get(settings, sid)
    _need_key(settings)
    if not s["hostkey_fp"]:
        res = "hostkey_unknown"
    else:
        res = _result(*_run(probe_argv(settings, s), _env(), None, TEST_TIMEOUT_S))
    try:
        with _lock(settings):
            _apply(settings, sid, last_test={"ts": time.time(), "ok": res == "ok", "reason": res})
    except LockTimeout:
        raise ServerError("another change is in progress") from None
    return s, res


# ------------------------------------------------------------------ host key pinning
def scan_hostkey(s: dict) -> tuple[str, str]:
    """ssh-keyscan the server -> (base64 blob, SHA256 fingerprint) of its ed25519 key."""
    argv = [KEYSCAN_BIN, "-T", "5", "-t", "ed25519", "-p", str(s["port"]), "--", s["host"]]
    try:
        cp = run_argv(argv, env=_env(), text=True, timeout=SCAN_TIMEOUT_S)
    except ProcTimeout:
        raise ServerError("host key scan timed out") from None
    except ProcError:
        raise ServerError("ssh-keyscan is not available") from None
    for ln in cp.stdout.splitlines():
        parts = ln.split()
        if len(parts) >= 3 and parts[1] == "ssh-ed25519" and _B64.fullmatch(parts[2]):
            fp = sshkeys.fingerprint_of(parts[2])
            if fp:
                return parts[2], fp
    raise ServerError("no ed25519 host key received (server unreachable?)")


def pin_hostkey(settings: Settings, sid: str, confirmed_fp: str, *, replace: bool = False) -> dict:
    """Re-scan and pin only if the key still has the fingerprint the user confirmed.

    ``replace``: a pinned key may be replaced (Re-pin; the route asks for the typed server name); the old
    known_hosts line is dropped. Without it a pinned key is never touched."""
    s = get(settings, sid)
    if s["hostkey_fp"] and not replace:
        raise ServerError("a host key is already pinned; remove the server and add it again to replace it")
    b64, fp = scan_hostkey(s)
    if not confirmed_fp or fp != confirmed_fp:
        raise ServerError("the host key changed since it was shown; nothing pinned")
    kh = known_hosts(settings)
    try:
        with _lock(settings):
            sshkeys.ssh_dir(settings).mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                old = kh.read_text(encoding="utf-8")
            except OSError:
                old = ""
            spec = hostspec(s["host"], s["port"])
            keep = "".join(ln + "\n" for ln in old.splitlines() if ln.split(" ", 1)[0] != spec)
            atomic_write(kh, keep + f"{spec} ssh-ed25519 {b64}\n", mode=0o600, fsync=True, fsync_dir=True)
            _apply(settings, sid, hostkey_fp=fp)
    except LockTimeout:
        raise ServerError("another change is in progress") from None
    except OSError:
        raise ServerError("cannot write the known_hosts file") from None
    s["hostkey_fp"] = fp
    return s


# ------------------------------------------------------------------ install key (one-time password)
def install_key(settings: Settings, sid: str, user: str, password: bytearray) -> tuple[dict, str]:
    """Append the pm public key to authorized_keys using the one-time password; then verify with the key.

    ``password`` is consumed and zeroed. Returns (server, result class). Error text never has secrets.
    """
    try:
        s = get(settings, sid)
        if not valid_user(user):
            raise ServerError("user: lowercase letters, digits, _ - (max 32)")
        pub = sshkeys.managed_key(settings)
        if pub is None:
            raise ServerError("generate the pm key first")
        if not s["hostkey_fp"]:
            raise ServerError("pin the host key first")
        if not 0 < len(password) <= MAX_PASSWORD or any(c in password for c in (0, 10, 13)):
            raise ServerError("password is empty or not accepted")
        d = Path(tempfile.mkdtemp(prefix="askpass-", dir=sshkeys.ssh_dir(settings)))
        try:
            fifo = d / "pw"
            os.mkfifo(fifo, 0o600)
            env = _env(SSH_ASKPASS=str(ASKPASS), SSH_ASKPASS_REQUIRE="force", PM_ASKPASS_FIFO=str(fifo))
            rc, err = _run(install_argv(settings, s, user), env, (pub.line + "\n").encode(),
                           INSTALL_TIMEOUT_S, feeder=lambda p: _feed(fifo, password, p))
        finally:
            shutil.rmtree(d, ignore_errors=True)
        res = _result(rc, err)
        if res == "ok":
            res2 = _result(*_run(probe_argv(settings, s), _env(), None, TEST_TIMEOUT_S))
            res = "ok" if res2 == "ok" else "verify_failed"
        return s, res
    except OSError:
        raise ServerError("could not run ssh") from None
    finally:
        for i in range(len(password)):
            password[i] = 0


def _feed(fifo: Path, password: bytearray, proc: subprocess.Popen) -> None:
    """Writer thread: hand the password to the askpass reader exactly once, then give up."""
    data = bytearray(password)
    data.append(10)

    def go() -> None:
        deadline = time.monotonic() + INSTALL_TIMEOUT_S
        try:
            while time.monotonic() < deadline and proc.poll() is None:
                try:
                    fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
                except OSError as e:
                    if e.errno in (errno.ENXIO, errno.EINTR):  # no reader yet
                        time.sleep(0.05)
                        continue
                    return
                try:
                    os.set_blocking(fd, True)
                    os.write(fd, data)
                finally:
                    os.close(fd)
                return
        finally:
            for i in range(len(data)):
                data[i] = 0

    threading.Thread(target=go, daemon=True).start()
