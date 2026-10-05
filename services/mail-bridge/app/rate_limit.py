from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from starlette.requests import Request


def client_ip(request: Request, *, trust_proxy: bool) -> str:
    if trust_proxy:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            first = xff.split(",")[0].strip()
            if first:
                return first
        real_ip = (request.headers.get("x-real-ip") or "").strip()
        if real_ip:
            return real_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


class SignupRateLimiter:
    """Process-local sliding 1-hour window keyed by client IP."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, ip: str, *, limit_per_hour: int) -> bool:
        if limit_per_hour <= 0:
            return True
        now = time.monotonic()
        window = 3600.0
        with self._lock:
            q = self._hits[ip]
            while q and (now - q[0]) > window:
                q.popleft()
            if len(q) >= limit_per_hour:
                return False
            q.append(now)
            return True
