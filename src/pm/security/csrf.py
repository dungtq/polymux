"""Per-session CSRF tokens, Origin/Host check and a small in-memory rate limiter."""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlsplit


def _load_secret(path: Path) -> bytes:
    if path.exists():
        data = path.read_bytes()
        if len(data) >= 32:
            return data
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    secret = os.urandom(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(secret)
    return secret


class Csrf:
    def __init__(self, state_dir: Path, allowed_origins: tuple[str, ...]):
        self._secret = _load_secret(Path(state_dir) / "csrf.key")
        self.allowed_origins = tuple(o.rstrip("/") for o in allowed_origins)

    def token(self, session_id: str) -> str:
        return hmac.new(self._secret, session_id.encode(), hashlib.sha256).hexdigest()

    def verify(self, session_id: str, supplied: str | None) -> bool:
        if not supplied:
            return False
        return hmac.compare_digest(self.token(session_id), supplied)

    def origin_ok(self, headers) -> bool:
        """headers: mapping with case-insensitive get (Starlette Headers). Strict mode."""
        return origin_ok(headers, self.allowed_origins)


def origin_ok(headers, allowed, *, allow_missing: bool = False, host: str | None = None,
              scheme: str | None = None, trim_slash: bool = True,
              require_present: bool = False) -> bool:
    """The one Origin check. ``headers``: mapping with case-insensitive get (Starlette Headers).

    Strict (default): an absent or empty Origin passes only with ``sec-fetch-site: same-origin``.
    Lenient (``allow_missing``): an absent Origin (None) passes; an empty one is checked like any other.
    ``require_present``: an absent or empty Origin fails (no ``sec-fetch-site`` fallback).
    A present Origin passes when it is in ``allowed`` (trailing slashes trimmed on both sides when
    ``trim_slash``), or, when ``host`` and ``scheme`` are given, when its netloc equals ``host`` and its
    scheme equals ``scheme`` (exact, case-sensitive netloc).
    """
    origin = headers.get("origin")
    if require_present and not origin:
        return False
    if allow_missing:
        if origin is None:
            return True
    elif not origin:
        return headers.get("sec-fetch-site") == "same-origin"
    if trim_slash:
        if origin.rstrip("/") in {o.rstrip("/") for o in allowed}:
            return True
    elif origin in allowed:
        return True
    if host is None or scheme is None:
        return False
    u = urlsplit(origin)
    return u.netloc == host and u.scheme == scheme


class RateLimiter:
    """Sliding window: at most `limit` hits per `window_s` per key."""

    def __init__(self, limit: int, window_s: float, clock=time.monotonic):
        self.limit, self.window_s, self._clock = limit, window_s, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """Record a hit; False if over the limit."""
        now = self._clock()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def blocked(self, key: str) -> bool:
        """Check without recording."""
        now = self._clock()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window_s:
                q.popleft()
            return len(q) >= self.limit
