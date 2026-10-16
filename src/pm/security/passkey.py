"""WebAuthn passkeys as an alternative step-up to TOTP.

Store: settings.state_dir/passkeys.json (0600), public data only (credential id,
public key, sign count, name, created). Challenges live in memory, bound to the
session id, 120 s TTL, single use. When settings.require_unlock is true,
registration needs an already unlocked session and a successful `finish_auth`
sets `session.unlocked_until` like `unlock.Unlocker`. Passkey LOGIN
(`begin_login` / `finish_login`) has no session yet: its challenge is bound to
a short-lived random pre-auth nonce instead.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from pm.core import fsutil
from pm.core.config import Settings
from pm.security.csrf import RateLimiter

CHALLENGE_TTL_S = 120.0
MAX_CREDENTIALS = 10
MAX_PENDING = 1000


def manager_of(app) -> PasskeyManager:
    st = app.state
    if getattr(st, "passkeys", None) is None:
        st.passkeys = PasskeyManager(st.settings)
    return st.passkeys


class PasskeyError(Exception):
    """Refusal. `reason` is a short fixed string, safe to audit and return."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class PasskeySession(Protocol):
    id: str
    unlocked_until: float


class _Nonce:
    def __init__(self, id: str):
        self.id = id
        self.unlocked_until = 0.0


class PasskeyManager:
    def __init__(self, settings: Settings, *, clock=time.time, max_fail: int = 5,
                 fail_window_s: float = 300.0):
        self.settings = settings
        self.clock = clock
        self.path = Path(settings.state_dir) / "passkeys.json"
        self.rp_id = urlsplit(settings.base_url).hostname or ""
        self.origin = settings.base_url.rstrip("/")
        self._lock = threading.Lock()
        self._challenges: dict[str, tuple[str, bytes, float]] = {}
        self._fails = RateLimiter(max_fail, fail_window_s)

    # ------------------------------------------------------------- store
    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return []
        return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []

    def _save(self, creds: list[dict]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fsutil.atomic_write(self.path, json.dumps(creds), mode=0o600, fsync=True, fsync_dir=True)

    def has_credentials(self) -> bool:
        return bool(self._load())

    def list_credentials(self) -> list[dict]:
        """Names and dates only."""
        return [{"id": c["id"], "name": c.get("name", ""), "created": c.get("created", 0)}
                for c in self._load()]

    def delete_credential(self, cred_id: str) -> bool:
        with self._lock:
            creds = self._load()
            keep = [c for c in creds if c.get("id") != cred_id]
            if len(keep) == len(creds):
                return False
            self._save(keep)
            return True

    # -------------------------------------------------------- challenges
    def _put(self, session: PasskeySession, kind: str, challenge: bytes) -> None:
        now = self.clock()
        self._challenges = {k: v for k, v in self._challenges.items() if v[2] > now}
        if len(self._challenges) >= MAX_PENDING:
            raise PasskeyError("busy")
        self._challenges[session.id] = (kind, challenge, now + CHALLENGE_TTL_S)

    def _take(self, session: PasskeySession, kind: str) -> bytes:
        """Pop (single use). Wrong kind or expired -> refuse."""
        entry = self._challenges.pop(session.id, None)
        if entry is None:
            raise PasskeyError("no challenge")
        k, challenge, expires = entry
        if k != kind or expires <= self.clock():
            raise PasskeyError("challenge expired")
        return challenge

    @staticmethod
    def _response(response_json) -> str | dict:
        if isinstance(response_json, (str, dict)):
            return response_json
        raise PasskeyError("bad response")

    # ------------------------------------------------------ registration
    def _require_unlocked(self, session: PasskeySession) -> None:
        if self.settings.require_unlock and session.unlocked_until <= self.clock():
            raise PasskeyError("locked")

    def begin_registration(self, session: PasskeySession) -> dict:
        self._require_unlocked(session)
        with self._lock:
            creds = self._load()
            if len(creds) >= MAX_CREDENTIALS:
                raise PasskeyError("too many passkeys")
            opts = generate_registration_options(
                rp_id=self.rp_id, rp_name="pm", user_name="pm", user_display_name="pm",
                user_id=b"pm-single-user",
                exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["id"]))
                                     for c in creds],
                authenticator_selection=AuthenticatorSelectionCriteria(
                    resident_key=ResidentKeyRequirement.DISCOURAGED,
                    user_verification=UserVerificationRequirement.REQUIRED),
                timeout=120_000)
            self._put(session, "reg", opts.challenge)
        return json.loads(options_to_json(opts))

    def finish_registration(self, session: PasskeySession, response_json,
                            name: str = "") -> dict:
        self._require_unlocked(session)
        with self._lock:
            challenge = self._take(session, "reg")
            try:
                v = verify_registration_response(
                    credential=self._response(response_json), expected_challenge=challenge,
                    expected_rp_id=self.rp_id, expected_origin=self.origin,
                    require_user_verification=True)
            except PasskeyError:
                raise
            except Exception:
                raise PasskeyError("registration rejected") from None
            cid = bytes_to_base64url(v.credential_id)
            creds = self._load()
            if any(c["id"] == cid for c in creds):
                raise PasskeyError("already registered")
            if len(creds) >= MAX_CREDENTIALS:
                raise PasskeyError("too many passkeys")
            name = "".join(ch for ch in (name or "") if ch.isprintable())[:40] or "passkey"
            rec = {"id": cid, "public_key": bytes_to_base64url(v.credential_public_key),
                   "sign_count": int(v.sign_count), "name": name, "created": self.clock()}
            creds.append(rec)
            self._save(creds)
        return {"id": cid, "name": name}

    # ---------------------------------------------------- authentication
    def begin_auth(self, session: PasskeySession) -> dict:
        creds = self._load()
        if not creds:
            raise PasskeyError("no passkeys")
        with self._lock:
            opts = generate_authentication_options(
                rp_id=self.rp_id,
                allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["id"]))
                                   for c in creds],
                user_verification=UserVerificationRequirement.REQUIRED, timeout=120_000)
            self._put(session, "auth", opts.challenge)
        return json.loads(options_to_json(opts))

    def finish_auth(self, session: PasskeySession, response_json,
                    now: float | None = None, client: str = "") -> bool:
        """Raises PasskeyError on refusal; True and unlocks on success."""
        now = self.clock() if now is None else now
        key = f"{client}|{session.id}"
        if self._fails.blocked(key):
            raise PasskeyError("rate limited")
        try:
            self._verify_auth(session, response_json)
        except PasskeyError:
            self._fails.allow(key)
            raise
        session.unlocked_until = now + self.settings.unlock_ttl_s
        return True

    # ------------------------------------------------------------ login
    def begin_login(self) -> tuple[str, dict]:
        """Returns (pre-auth nonce, request options). The nonce goes in a cookie."""
        creds = self._load()
        if not creds:
            raise PasskeyError("no passkeys")
        nonce = secrets.token_urlsafe(24)
        with self._lock:
            opts = generate_authentication_options(
                rp_id=self.rp_id,
                allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["id"]))
                                   for c in creds],
                user_verification=UserVerificationRequirement.REQUIRED, timeout=120_000)
            self._put(_Nonce("login:" + nonce), "login", opts.challenge)
        return nonce, json.loads(options_to_json(opts))

    def finish_login(self, nonce: str, response_json) -> None:
        """Raises PasskeyError on refusal. Nonce is consumed either way."""
        self._verify_auth(_Nonce("login:" + (nonce or "")), response_json, kind="login")

    def _verify_auth(self, session: PasskeySession, response_json, kind: str = "auth") -> None:
        with self._lock:
            challenge = self._take(session, kind)
            cred = self._response(response_json)
            try:
                raw_id = cred.get("rawId") or cred.get("id") if isinstance(cred, dict) \
                    else json.loads(cred).get("rawId")
                cid = bytes_to_base64url(base64url_to_bytes(raw_id))
            except Exception:
                raise PasskeyError("bad response") from None
            creds = self._load()
            rec = next((c for c in creds if c["id"] == cid), None)
            if rec is None:
                raise PasskeyError("unknown credential")
            try:
                v = verify_authentication_response(
                    credential=cred, expected_challenge=challenge,
                    expected_rp_id=self.rp_id, expected_origin=self.origin,
                    credential_public_key=base64url_to_bytes(rec["public_key"]),
                    credential_current_sign_count=int(rec["sign_count"]),
                    require_user_verification=True)
            except Exception:
                raise PasskeyError("authentication rejected") from None
            old, new = int(rec["sign_count"]), int(v.new_sign_count)
            if (old or new) and new <= old:  # counter must advance unless always 0
                raise PasskeyError("sign count regression")
            rec["sign_count"] = new
            self._save(creds)
