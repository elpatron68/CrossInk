from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.store import ItemStore


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path,
        device_token="test-token",
        imap_host="",
        imap_user="",
        imap_pass="",
    )
    store = ItemStore(settings.db_path, settings.items_dir)
    store.add_item(
        filename="seed.epub",
        content=b"PK\x03\x04seed",
        sha256="abc123",
        content_type="application/epub+zip",
        imap_uid=None,
    )
    app = create_app(settings=settings, store=store)
    with TestClient(app) as test_client:
        yield test_client


def test_health_no_auth(client: TestClient) -> None:
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["imap_configured"] is False


def test_pending_requires_token(client: TestClient) -> None:
    assert client.get("/v1/pending").status_code == 401
    r = client.get("/v1/pending", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["filename"] == "seed.epub"


def test_download_and_ack(client: TestClient) -> None:
    headers = {"Authorization": "Bearer test-token"}
    item_id = client.get("/v1/pending", headers=headers).json()[0]["id"]

    content = client.get(f"/v1/items/{item_id}/content", headers=headers)
    assert content.status_code == 200
    assert content.content == b"PK\x03\x04seed"

    ack = client.post(f"/v1/items/{item_id}/ack", headers=headers)
    assert ack.status_code == 200
    assert client.get("/v1/pending", headers=headers).json() == []

    # Idempotent ack
    assert client.post(f"/v1/items/{item_id}/ack", headers=headers).status_code == 200
