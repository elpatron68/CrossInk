"""Optional Plausible custom-event tracking (server-side Measurement Protocol)."""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Any, Mapping
from urllib.parse import urlparse, urlunparse

from .config import Settings

LOG = logging.getLogger("mail-bridge.plausible")

# Custom event names (must be created / allowed in the Plausible site goals UI).
EVENT_ACCOUNT_CREATED = "Account Created"
EVENT_EBOOK_RECEIVED = "Ebook Received"
EVENT_EBOOK_UPLOADED = "Ebook Uploaded"
EVENT_EBOOK_SYNCED = "Ebook Synced"


def events_api_url(script_url: str) -> str:
    """Derive https://host/api/event from a /js/script.js (or similar) script URL."""
    raw = (script_url or "").strip() or "https://plausible.io/js/script.js"
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return "https://plausible.io/api/event"
    return urlunparse((parsed.scheme, parsed.netloc, "/api/event", "", "", ""))


def site_url(settings: Settings, path: str = "/") -> str:
    origin = (settings.webauthn_origin or "").rstrip("/") or "http://localhost:8080"
    if not path.startswith("/"):
        path = "/" + path
    return origin + path


def track_event(
    settings: Settings,
    name: str,
    *,
    path: str = "/",
    props: Mapping[str, Any] | None = None,
) -> None:
    """Fire a Plausible custom event in a daemon thread. No-op if domain unset."""
    domain = (settings.plausible_domain or "").strip()
    if not domain or not name:
        return

    payload: dict[str, Any] = {
        "name": name,
        "url": site_url(settings, path),
        "domain": domain,
    }
    if props:
        # Plausible expects string values for props.
        payload["props"] = {str(k): str(v) for k, v in props.items()}

    api = events_api_url(settings.plausible_script_url)
    body = json.dumps(payload).encode("utf-8")

    def _post() -> None:
        req = urllib.request.Request(
            api,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "CrossInk-Mail-Bridge/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                if resp.status >= 400:
                    LOG.warning("Plausible event %s HTTP %s", name, resp.status)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            LOG.warning("Plausible event %s failed: %s", name, exc)

    threading.Thread(target=_post, name=f"plausible-{name[:24]}", daemon=True).start()
