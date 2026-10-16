"""Single-use, short-lived WebSocket tickets (in memory, never logged).

A ticket is bound to the pm login session id and the tmux session id. It is
consumed by the first presentation, valid or not (a wrong-session replay burns
it). Lookup compares digests in constant time.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

TTL_S = 30
MAX_OUTSTANDING = 256


@dataclass(frozen=True)
class Ticket:
    session_id: str
    tmux_id: str
    expires: float
    window_id: str | None = None  # optional `@N` the view should open on (server-validated)


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8", "replace")).digest()


class TicketStore:
    def __init__(self, ttl_s: float = TTL_S, clock: Callable[[], float] = time.monotonic):
        self.ttl_s = ttl_s
        self._clock = clock
        self._items: dict[bytes, Ticket] = {}
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        for k in [k for k, t in self._items.items() if t.expires <= now]:
            del self._items[k]

    def issue(self, session_id: str, tmux_id: str, window_id: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            self._purge(now)
            while len(self._items) >= MAX_OUTSTANDING:  # drop oldest
                self._items.pop(next(iter(self._items)))
            self._items[_digest(token)] = Ticket(session_id, tmux_id, now + self.ttl_s, window_id)
        return token

    def consume(self, token: str | None, session_id: str) -> Ticket | None:
        """Return the ticket if valid for this login session; always burns a matching token."""
        if not token or len(token) > 256:
            return None
        want = _digest(token)
        now = self._clock()
        found: Ticket | None = None
        with self._lock:
            self._purge(now)
            for key in list(self._items):
                if hmac.compare_digest(key, want):
                    found = self._items.pop(key)
        if found is None or not hmac.compare_digest(found.session_id.encode(),
                                                    session_id.encode()):
            return None
        return found

    def __len__(self) -> int:
        with self._lock:
            self._purge(self._clock())
            return len(self._items)
