from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.rate_limit import SignupRateLimiter, client_ip
from app.store import ItemStore
from starlette.requests import Request


def test_sliding_window_blocks_sixth() -> None:
    limiter = SignupRateLimiter()
    for _ in range(5):
        assert limiter.allow("1.2.3.4", limit_per_hour=5) is True
    assert limiter.allow("1.2.3.4", limit_per_hour=5) is False
    assert limiter.allow("9.9.9.9", limit_per_hour=5) is True


def test_zero_limit_disables() -> None:
    limiter = SignupRateLimiter()
    for _ in range(20):
        assert limiter.allow("1.2.3.4", limit_per_hour=0) is True


def test_client_ip_trust_proxy_xff() -> None:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [(b"x-forwarded-for", b"203.0.113.10, 10.0.0.1")],
        "client": ("10.0.0.1", 12345),
        "server": ("test", 80),
    }
    request = Request(scope)
    assert client_ip(request, trust_proxy=True) == "203.0.113.10"
    assert client_ip(request, trust_proxy=False) == "10.0.0.1"

    scope_both = {
        **scope,
        "headers": [
            (b"x-real-ip", b"198.51.100.99"),
            (b"x-forwarded-for", b"203.0.113.10, 10.0.0.1"),
        ],
    }
    assert client_ip(Request(scope_both), trust_proxy=True) == "198.51.100.99"


def test_accounts_endpoint_rate_limited(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        signup_rate_limit_per_hour=2,
        trust_proxy=True,
    )
    store = ItemStore(settings.db_path, settings.items_dir)
    app = create_app(settings=settings, store=store)
    with TestClient(app) as client:
        headers = {"X-Forwarded-For": "198.51.100.7"}
        assert client.post("/v1/accounts", json={"device_token": "1" * 64}, headers=headers).status_code == 201
        assert client.post("/v1/accounts", json={"device_token": "2" * 64}, headers=headers).status_code == 201
        r = client.post("/v1/accounts", json={"device_token": "3" * 64}, headers=headers)
        assert r.status_code == 429
        assert "rate" in r.json()["detail"].lower()
