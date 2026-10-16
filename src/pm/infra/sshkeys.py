"""pm's own SSH key (``<state_dir>/ssh/id_ed25519_pm``) and the read-only list of ``~/.ssh/*.pub``.

Only public keys are ever read or rendered. The private key is created by ``ssh-keygen`` (argv, no
shell, timeout), never opened by pm, never logged. Design: docs/DESIGN-GIT-REMOTES-SERVERS.md section 4.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path

from pm.core.config import Settings
from pm.core.proc import ProcError, ProcTimeout, run_argv

KEYGEN_BIN = "ssh-keygen"
KEYGEN_TIMEOUT_S = 10.0
KEY_NAME = "id_ed25519_pm"
MAX_PUB_BYTES = 8192
MAX_USER_KEYS = 20
_PUB_LINE = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)|sk-ssh-ed25519@openssh\.com"
    r"|sk-ecdsa-sha2-nistp256@openssh\.com)\s+([A-Za-z0-9+/]+={0,3})(?:\s+([^\r\n]{0,200}))?$",
    re.ASCII,
)
_PUB_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.pub$", re.ASCII)


class SshKeyError(Exception):
    """Message is safe to show the user (no paths of private material, no stderr dumps)."""


@dataclass(frozen=True)
class PubKey:
    name: str  # file name, e.g. id_ed25519_pm.pub
    type: str
    comment: str
    fingerprint: str  # SHA256:...
    line: str  # the single public key line


def ssh_dir(settings: Settings) -> Path:
    return settings.state_dir / "ssh"


def key_path(settings: Settings) -> Path:
    return ssh_dir(settings) / KEY_NAME


def fingerprint_of(b64: str) -> str:
    """OpenSSH SHA256 fingerprint of a public key blob (same value as ``ssh-keygen -lf``)."""
    try:
        blob = base64.b64decode(b64 + "=" * (-len(b64) % 4), validate=True)
    except (binascii.Error, ValueError):
        return ""
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def parse_pub(text: str, name: str = "") -> PubKey | None:
    """First non-empty line of a ``.pub`` file -> PubKey, or None when it is not a plain public key."""
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        m = _PUB_LINE.match(ln)
        if not m:
            return None
        fp = fingerprint_of(m.group(2))
        if not fp:
            return None
        return PubKey(name, m.group(1), (m.group(3) or "").strip(), fp, ln)
    return None


def _read_pub(path: Path) -> PubKey | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PUB_BYTES:
            return None
        return parse_pub(path.read_text(encoding="utf-8", errors="replace"), path.name)
    except OSError:
        return None


def managed_key(settings: Settings) -> PubKey | None:
    """The pm-managed public key, or None when not generated yet (or its .pub is unreadable)."""
    return _read_pub(key_path(settings).with_name(KEY_NAME + ".pub"))


def user_keys(settings: Settings) -> list[PubKey]:
    """``*.pub`` files of the user's ssh dir (read-only listing; private keys are never opened)."""
    d = Path(settings.user_ssh_dir)
    try:
        names = sorted(n for n in os.listdir(d) if _PUB_FILE.match(n))
    except OSError:
        return []
    out = []
    for n in names[:MAX_USER_KEYS]:
        k = _read_pub(d / n)
        if k:
            out.append(k)
    return out


def _comment() -> str:
    host = re.sub(r"[^A-Za-z0-9.-]", "", socket.gethostname())[:40] or "host"
    return f"pm@{host}"


def keygen_argv(path: Path, comment: str) -> list[str]:
    return [KEYGEN_BIN, "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(path)]


def generate(settings: Settings) -> PubKey:
    """Create the managed key. Refuses to overwrite. Dir 0700, private key 0600, public 0644."""
    d = ssh_dir(settings)
    priv = key_path(settings)
    pub = priv.with_name(KEY_NAME + ".pub")
    try:
        d.parent.mkdir(parents=True, exist_ok=True)
        d.mkdir(mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)
    except OSError:
        raise SshKeyError("cannot create the ssh state directory") from None
    if os.path.lexists(priv) or os.path.lexists(pub):
        raise SshKeyError("a pm key already exists; pm never overwrites it")
    try:
        cp = run_argv(keygen_argv(priv, _comment()), text=True, timeout=KEYGEN_TIMEOUT_S)
    except ProcTimeout:
        _cleanup(priv, pub)
        raise SshKeyError("ssh-keygen timed out") from None
    except ProcError:
        raise SshKeyError("ssh-keygen is not available") from None
    if cp.returncode != 0:
        _cleanup(priv, pub)
        raise SshKeyError("ssh-keygen failed")
    try:
        os.chmod(priv, 0o600)
        os.chmod(pub, 0o644)
    except OSError:
        _cleanup(priv, pub)
        raise SshKeyError("cannot set key permissions") from None
    k = managed_key(settings)
    if k is None:
        _cleanup(priv, pub)
        raise SshKeyError("generated key could not be read back")
    return k


def _cleanup(*paths: Path) -> None:
    for p in paths:
        try:
            p.unlink()
        except OSError:
            pass
