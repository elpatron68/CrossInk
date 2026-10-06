from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field

from .convert import (
    CONVERT_EXTENSIONS,
    NATIVE_EXTENSIONS,
    ConvertError,
    content_type_for,
    convert_to_epub,
    extension_of,
    sha256_bytes,
)
from .mime_extract import safe_filename
from .rate_limit import SlidingWindowRateLimiter
from .store import ItemStore, validate_username
from .webauthn_flow import (
    SESSION_COOKIE,
    ChallengeStore,
    authentication_options_json,
    registration_options_json,
    verify_authentication,
    verify_registration,
)

LOG = logging.getLogger("mail-bridge.web")


class BootstrapIn(BaseModel):
    user_id: str = Field(min_length=8, max_length=64)
    device_token: str = Field(min_length=64, max_length=64)


class UsernameIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)


class ChallengeKeyIn(BaseModel):
    challenge_key: str = Field(min_length=1, max_length=128)
    credential: dict[str, Any]


class RegisterOptionsIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)


class LoginOptionsIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)


def _cookie_secure(origin: str) -> bool:
    return origin.lower().startswith("https://")


def _set_session_cookie(response: Response, session_id: str, *, settings) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(settings.webauthn_origin),
        max_age=max(1, settings.web_session_days) * 86400,
        path="/",
    )


def _clear_session_cookie(response: Response, *, settings) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE,
        path="/",
        secure=_cookie_secure(settings.webauthn_origin),
        samesite="lax",
    )


def require_web_user(request: Request) -> str:
    store: ItemStore = request.app.state.store
    session_id = request.cookies.get(SESSION_COOKIE) or ""
    user_id = store.user_id_for_session(session_id)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in")
    return user_id


def create_web_router() -> APIRouter:
    router = APIRouter(prefix="/v1/web", tags=["web"])

    @router.post("/session/bootstrap")
    def bootstrap_session(body: BootstrapIn, request: Request, response: Response) -> dict[str, str]:
        settings = request.app.state.settings
        store: ItemStore = request.app.state.store
        owner = store.user_id_for_token(body.device_token.strip())
        if owner is None or owner != body.user_id.strip():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        session_id = store.create_web_session(owner, days=settings.web_session_days)
        _set_session_cookie(response, session_id, settings=settings)
        return {"ok": "true"}

    @router.get("/me")
    def me(request: Request) -> dict[str, Any]:
        user_id = require_web_user(request)
        store: ItemStore = request.app.state.store
        profile = store.get_user_profile(user_id)
        if profile is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in")
        return {
            "user_id": profile.user_id,
            "mail_local": profile.mail_local,
            "email": profile.email,
            "username": profile.username,
            "pending_count": profile.pending_count,
            "has_passkey": profile.has_passkey,
        }

    @router.get("/username/available")
    def username_available(request: Request, username: str = "") -> dict[str, Any]:
        """Live check while typing a username for passkey registration."""
        user_id = require_web_user(request)
        store: ItemStore = request.app.state.store
        raw = (username or "").strip()
        if len(raw) < 3:
            return {"username": raw.lower(), "available": False, "status": "too_short"}
        try:
            normalized = validate_username(raw)
        except ValueError:
            return {"username": raw.lower(), "available": False, "status": "invalid"}
        taken_by = store.user_id_for_username(normalized)
        if taken_by is None:
            return {"username": normalized, "available": True, "status": "available"}
        if taken_by == user_id:
            return {"username": normalized, "available": True, "status": "own"}
        return {"username": normalized, "available": False, "status": "taken"}

    @router.post("/logout")
    def logout(request: Request, response: Response) -> dict[str, bool]:
        settings = request.app.state.settings
        store: ItemStore = request.app.state.store
        session_id = request.cookies.get(SESSION_COOKIE) or ""
        if session_id:
            store.delete_web_session(session_id)
        _clear_session_cookie(response, settings=settings)
        return {"ok": True}

    @router.post("/devices")
    def issue_device(request: Request) -> dict[str, str]:
        user_id = require_web_user(request)
        store: ItemStore = request.app.state.store
        token = store.create_device_for_user(user_id, label="web")
        return {"device_token": token}

    @router.post("/passkey/register/options")
    def register_options(body: RegisterOptionsIn, request: Request) -> dict[str, Any]:
        user_id = require_web_user(request)
        settings = request.app.state.settings
        store: ItemStore = request.app.state.store
        challenges: ChallengeStore = request.app.state.challenges
        try:
            username = validate_username(body.username)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        profile = store.get_user_profile(user_id)
        if profile is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in")
        if profile.username and profile.username != username:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="username already set")
        taken = store.user_id_for_username(username)
        if taken is not None and taken != user_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="username already taken (this browser session is a different account — sign out and restore with your device token)",
            )
        existing = store.list_webauthn_credentials(user_id)
        key, options_json = registration_options_json(
            settings=settings,
            user_id=user_id,
            username=username,
            existing=existing,
            challenges=challenges,
        )
        return {"challenge_key": key, "options": json.loads(options_json)}

    @router.post("/passkey/register/verify")
    def register_verify(body: ChallengeKeyIn, request: Request) -> dict[str, Any]:
        user_id = require_web_user(request)
        settings = request.app.state.settings
        store: ItemStore = request.app.state.store
        challenges: ChallengeStore = request.app.state.challenges
        try:
            verified_user, username, cred_id, public_key, sign_count = verify_registration(
                settings=settings,
                challenges=challenges,
                challenge_key=body.challenge_key,
                credential=body.credential,
            )
        except Exception as exc:
            LOG.info("passkey register verify failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Passkey registration failed",
            ) from exc
        if verified_user != user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Session mismatch")
        try:
            store.set_username(user_id, username)
            store.add_webauthn_credential(
                user_id=user_id,
                credential_id=cred_id,
                public_key=public_key,
                sign_count=sign_count,
            )
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"ok": True, "username": username}

    @router.post("/passkey/login/options")
    def login_options(body: LoginOptionsIn, request: Request) -> dict[str, Any]:
        settings = request.app.state.settings
        store: ItemStore = request.app.state.store
        challenges: ChallengeStore = request.app.state.challenges
        try:
            username = validate_username(body.username)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        user_id = store.user_id_for_username(username)
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown username")
        creds = store.list_webauthn_credentials(user_id)
        try:
            key, options_json = authentication_options_json(
                settings=settings,
                user_id=user_id,
                username=username,
                credentials=creds,
                challenges=challenges,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"challenge_key": key, "options": json.loads(options_json)}

    @router.post("/passkey/login/verify")
    def login_verify(body: ChallengeKeyIn, request: Request, response: Response) -> dict[str, Any]:
        settings = request.app.state.settings
        store: ItemStore = request.app.state.store
        challenges: ChallengeStore = request.app.state.challenges
        # Peek credential id from client payload to load public key.
        raw_id = body.credential.get("rawId") or body.credential.get("id")
        if not isinstance(raw_id, str) or not raw_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing credential id")
        # Browser may send base64url in id; rawId is also base64url in JSON form from our client.
        stored = store.get_webauthn_credential(raw_id)
        if stored is None:
            # Try without padding variants — clients sometimes differ.
            stored = store.get_webauthn_credential(raw_id.rstrip("="))
        if stored is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown passkey")
        try:
            user_id, cred_id, new_sign = verify_authentication(
                settings=settings,
                challenges=challenges,
                challenge_key=body.challenge_key,
                credential=body.credential,
                credential_public_key=stored.public_key,
                credential_current_sign_count=stored.sign_count,
            )
        except Exception as exc:
            LOG.info("passkey login verify failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Passkey login failed",
            ) from exc
        if user_id != stored.user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User mismatch")
        store.update_webauthn_sign_count(cred_id, new_sign)
        session_id = store.create_web_session(user_id, days=settings.web_session_days)
        _set_session_cookie(response, session_id, settings=settings)
        profile = store.get_user_profile(user_id)
        return {
            "ok": True,
            "username": profile.username if profile else None,
            "email": profile.email if profile else None,
        }

    @router.post("/upload")
    async def upload(
        request: Request,
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        user_id = require_web_user(request)
        settings = request.app.state.settings
        store: ItemStore = request.app.state.store
        upload_limiter: SlidingWindowRateLimiter = request.app.state.upload_limiter
        if not upload_limiter.allow(user_id, limit_per_hour=settings.upload_rate_limit_per_hour):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Upload rate limit exceeded",
            )

        filename = safe_filename(file.filename or "") or "upload.bin"
        ext = extension_of(filename)
        if ext not in (NATIVE_EXTENSIONS | CONVERT_EXTENSIONS):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported file type: {ext or '(none)'}",
            )
        if ext in CONVERT_EXTENSIONS and not settings.convert_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Conversion is disabled on this bridge",
            )

        # Stream with hard size cap.
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await file.read(1024 * 256)
            if not chunk:
                break
            total += len(chunk)
            if total > settings.upload_max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="File too large",
                )
            chunks.append(chunk)
        content = b"".join(chunks)
        if not content:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")

        final_name = filename
        final_bytes = content
        final_type = content_type_for(ext)
        converted = False

        if ext in CONVERT_EXTENSIONS:
            try:
                final_bytes, final_name = convert_to_epub(
                    content,
                    source_filename=filename,
                    timeout_seconds=settings.convert_timeout_seconds,
                )
            except ConvertError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc) or "Conversion failed",
                ) from exc
            final_type = content_type_for(".epub")
            converted = True
        elif ext not in NATIVE_EXTENSIONS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported file type")

        digest = sha256_bytes(final_bytes)
        item = store.add_item(
            filename=final_name,
            content=final_bytes,
            sha256=digest,
            content_type=final_type,
            imap_uid=None,
            user_id=user_id,
        )
        return {
            "ok": True,
            "id": item.id,
            "filename": item.filename,
            "bytes": item.bytes,
            "sha256": item.sha256,
            "converted": converted,
        }

    return router
