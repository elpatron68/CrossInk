from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url, base64url_to_bytes
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .config import Settings
from .store import WebAuthnCredential

LOG = logging.getLogger("mail-bridge.webauthn")

SESSION_COOKIE = "ci_bridge_session"
CHALLENGE_TTL_SECONDS = 300


@dataclass
class _PendingChallenge:
    kind: str  # register | login
    user_id: str
    username: str
    challenge: bytes
    expires_at: float


class ChallengeStore:
    """In-memory WebAuthn challenge cache (single-process bridge)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, _PendingChallenge] = {}

    def put(self, key: str, challenge: _PendingChallenge) -> None:
        with self._lock:
            self._prune()
            self._items[key] = challenge

    def pop(self, key: str) -> _PendingChallenge | None:
        with self._lock:
            self._prune()
            return self._items.pop(key, None)

    def _prune(self) -> None:
        now = time.monotonic()
        stale = [k for k, v in self._items.items() if v.expires_at <= now]
        for k in stale:
            del self._items[k]


def new_challenge_key() -> str:
    return secrets.token_urlsafe(24)


def registration_options_json(
    *,
    settings: Settings,
    user_id: str,
    username: str,
    existing: list[WebAuthnCredential],
    challenges: ChallengeStore,
) -> tuple[str, str]:
    """Return (challenge_key, options_json)."""
    exclude = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id)) for c in existing
    ]
    options = generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=user_id.encode("utf-8"),
        user_name=username,
        user_display_name=username,
        exclude_credentials=exclude,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    key = new_challenge_key()
    challenges.put(
        key,
        _PendingChallenge(
            kind="register",
            user_id=user_id,
            username=username,
            challenge=options.challenge,
            expires_at=time.monotonic() + CHALLENGE_TTL_SECONDS,
        ),
    )
    return key, options_to_json(options)


def verify_registration(
    *,
    settings: Settings,
    challenges: ChallengeStore,
    challenge_key: str,
    credential: dict[str, Any],
) -> tuple[str, str, bytes, int]:
    """Returns (user_id, username, credential_id_b64, public_key_bytes, sign_count) conceptually.

    Actual return: user_id, username, credential_id (base64url), public_key (bytes), sign_count.
    """
    pending = challenges.pop(challenge_key)
    if pending is None or pending.kind != "register":
        raise ValueError("Invalid or expired registration challenge")
    verification = verify_registration_response(
        credential=credential,
        expected_challenge=pending.challenge,
        expected_rp_id=settings.webauthn_rp_id,
        expected_origin=settings.webauthn_origin,
        require_user_verification=False,
    )
    return (
        pending.user_id,
        pending.username,
        bytes_to_base64url(verification.credential_id),
        verification.credential_public_key,
        int(verification.sign_count),
    )


def authentication_options_json(
    *,
    settings: Settings,
    user_id: str,
    username: str,
    credentials: list[WebAuthnCredential],
    challenges: ChallengeStore,
) -> tuple[str, str]:
    if not credentials:
        raise ValueError("No passkey registered for this username")
    allow = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id)) for c in credentials
    ]
    options = generate_authentication_options(
        rp_id=settings.webauthn_rp_id,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    key = new_challenge_key()
    challenges.put(
        key,
        _PendingChallenge(
            kind="login",
            user_id=user_id,
            username=username,
            challenge=options.challenge,
            expires_at=time.monotonic() + CHALLENGE_TTL_SECONDS,
        ),
    )
    return key, options_to_json(options)


def verify_authentication(
    *,
    settings: Settings,
    challenges: ChallengeStore,
    challenge_key: str,
    credential: dict[str, Any],
    credential_public_key: bytes,
    credential_current_sign_count: int,
) -> tuple[str, str, int]:
    """Returns (user_id, credential_id_b64url, new_sign_count)."""
    pending = challenges.pop(challenge_key)
    if pending is None or pending.kind != "login":
        raise ValueError("Invalid or expired login challenge")
    verification = verify_authentication_response(
        credential=credential,
        expected_challenge=pending.challenge,
        expected_rp_id=settings.webauthn_rp_id,
        expected_origin=settings.webauthn_origin,
        credential_public_key=credential_public_key,
        credential_current_sign_count=credential_current_sign_count,
        require_user_verification=False,
    )
    return (
        pending.user_id,
        bytes_to_base64url(verification.credential_id),
        int(verification.new_sign_count),
    )
