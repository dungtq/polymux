"""TOTP step-up. The secret/URI is never logged; provision() returns the URI to the caller only."""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path
from typing import Protocol

import pyotp

from pm.security.csrf import RateLimiter


class UnlockSession(Protocol):
    id: str
    unlocked_until: float


class Unlocker:
    def __init__(self, totp_file: Path, unlock_ttl_s: int, *, max_fail: int = 5,
                 fail_window_s: float = 300.0, issuer: str = "pm", account: str | None = None):
        self.totp_file = Path(totp_file)
        self.ttl = unlock_ttl_s
        self.issuer, self.account = issuer, account or f"pm@{socket.gethostname()}"
        self._used: dict[tuple[int, str], float] = {}
        self._lock = threading.Lock()
        self._fails = RateLimiter(max_fail, fail_window_s)

    def _secret(self) -> str | None:
        try:
            s = self.totp_file.read_text().strip()
        except FileNotFoundError:
            return None
        return s or None

    def provisioned(self) -> bool:
        return self._secret() is not None

    def provision(self) -> str:
        """Create the secret on first call (0600) and return the otpauth URI.
        Refuses if already provisioned (raises FileExistsError)."""
        self.totp_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        secret = pyotp.random_base32()
        fd = os.open(self.totp_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secret + "\n")
        return pyotp.TOTP(secret).provisioning_uri(name=self.account, issuer_name=self.issuer)

    def verify_totp(self, code: str, window: int = 1, now: float | None = None) -> bool:
        """True once per code (replay protected)."""
        secret = self._secret()
        code = (code or "").strip().replace(" ", "")
        if secret is None or not (code.isdigit() and len(code) == 6):
            return False
        now = time.time() if now is None else now
        totp = pyotp.TOTP(secret)
        with self._lock:
            self._used = {k: t for k, t in self._used.items() if now - t < 120}
            for off in range(-window, window + 1):
                step_time = now + off * totp.interval
                if totp.at(step_time) == code:
                    counter = int(step_time // totp.interval)
                    if (counter, code) in self._used:
                        return False
                    self._used[(counter, code)] = now
                    return True
        return False

    def unlock(self, session: UnlockSession, code: str, now: float | None = None,
               client: str = "") -> bool:
        """Rate limited by client+session; sets session.unlocked_until on success."""
        now = time.time() if now is None else now
        key = f"{client}|{session.id}"
        if self._fails.blocked(key):
            return False
        if self.verify_totp(code, now=now):
            session.unlocked_until = now + self.ttl
            return True
        self._fails.allow(key)  # record failure
        return False
