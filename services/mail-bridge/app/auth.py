from __future__ import annotations

import logging

from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .store import ItemStore

LOG = logging.getLogger("mail-bridge.auth")
_bearer = HTTPBearer(auto_error=False)


def require_device_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> str:
    """Validate Bearer device token. Returns the owning user_id."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")
    token = credentials.credentials.strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device token")
    store: ItemStore = request.app.state.store
    user_id = store.user_id_for_token(token)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device token")
    try:
        store.touch_device_for_token(token)
    except Exception:
        LOG.exception("Failed to touch last_seen_at")
    return user_id
