"""Append-only JSONL audit log. Fails closed: callers must treat AuditError as refusal."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import deque
from dataclasses import asdict
from pathlib import Path

from pm.core.models import AuditEntry
from pm.security.sessions_store import sid_hash

SESSION_TAG_LEN = 8


class AuditError(RuntimeError):
    pass


def session_tag(sid: str | None) -> str:
    """A short, non-secret name of a login session for audit lines: the first hex digits of the sha256 the sessions
    file keeps for it (never the session id or the cookie value). "" without a session."""
    return sid_hash(sid)[:SESSION_TAG_LEN] if sid else ""


def message_detail(text: str) -> dict[str, str | int]:
    """Length + hash only; the message text itself is never stored."""
    data = text.encode("utf-8", "replace")
    return {"len": len(data), "sha256": hashlib.sha256(data).hexdigest()}


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def write(self, entry: AuditEntry) -> None:
        row = asdict(entry)
        if not row["session"]:
            del row["session"]  # lines without a session keep the old shape
        line = json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        with self._lock:
            try:
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = os.open(
                    self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600
                )
                try:
                    os.write(fd, line.encode())
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError as exc:
                raise AuditError(f"audit write failed: {exc.__class__.__name__}") from exc

    def tail(self, n: int = 100) -> list[dict]:
        """Newest last. Unparseable lines are skipped."""
        try:
            with self._lock, open(self.path, encoding="utf-8") as fh:
                lines = deque(fh, maxlen=max(n, 0))
        except FileNotFoundError:
            return []
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        return out
