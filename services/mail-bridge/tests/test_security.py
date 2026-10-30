from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.store import ItemStore


def _app(tmp_path: Path, *, enable_api_docs: bool = False):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        enable_api_docs=enable_api_docs,
    )
    store = ItemStore(settings.db_path, settings.items_dir)
    return create_app(settings=settings, store=store)


def test_api_docs_disabled_by_default(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_api_docs_enabled_when_configured(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, enable_api_docs=True)) as client:
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/docs").status_code == 200


def test_security_headers_on_pages(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert r.headers.get("x-frame-options") == "DENY"
        assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
        csp = r.headers.get("content-security-policy") or ""
        assert "frame-ancestors 'none'" in csp
        assert "object-src 'none'" in csp
