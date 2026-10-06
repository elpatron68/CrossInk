from __future__ import annotations

import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.recipients import match_plus_alias, resolve_user_mail_local
from app.store import ItemStore, hash_device_token


def _token() -> str:
    return secrets.token_hex(32)


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        mail_local_prefix="bookbridge",
        mail_domain="hoerdle.de",
        mail_local_length=10,
        imap_host="",
        imap_user="",
        imap_pass="",
    )


@pytest.fixture()
def store(settings: Settings) -> ItemStore:
    return ItemStore(
        settings.db_path,
        settings.items_dir,
        mail_local_prefix=settings.mail_local_prefix,
        mail_domain=settings.mail_domain,
        mail_local_length=settings.mail_local_length,
    )


@pytest.fixture()
def client(settings: Settings, store: ItemStore) -> TestClient:
    app = create_app(settings=settings, store=store)
    with TestClient(app) as test_client:
        yield test_client


def test_health_no_auth(client: TestClient) -> None:
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["imap_configured"] is False


def test_create_account_and_pending(client: TestClient) -> None:
    token = _token()
    r = client.post("/v1/accounts", json={"device_token": token})
    assert r.status_code == 201
    body = r.json()
    assert body["email"].startswith("bookbridge+")
    assert body["email"].endswith("@hoerdle.de")
    assert "device_token" not in body

    assert client.get("/v1/pending").status_code == 401
    pending = client.get("/v1/pending", headers={"Authorization": f"Bearer {token}"})
    assert pending.status_code == 200
    assert pending.json() == []


def test_create_account_rejects_bad_token(client: TestClient) -> None:
    assert client.post("/v1/accounts", json={"device_token": "short"}).status_code == 422
    assert client.post("/v1/accounts", json={"device_token": "A" * 64}).status_code == 400


def test_create_account_conflict(client: TestClient) -> None:
    token = _token()
    assert client.post("/v1/accounts", json={"device_token": token}).status_code == 201
    assert client.post("/v1/accounts", json={"device_token": token}).status_code == 409


def test_tenant_isolation(client: TestClient, store: ItemStore, settings: Settings) -> None:
    token_a = _token()
    token_b = _token()
    a = client.post("/v1/accounts", json={"device_token": token_a}).json()
    b = client.post("/v1/accounts", json={"device_token": token_b}).json()

    item = store.add_item(
        filename="only-a.epub",
        content=b"PK\x03\x04aaaa",
        sha256="sha-a",
        content_type="application/epub+zip",
        imap_uid="1",
        user_id=a["user_id"],
    )
    path = item.path

    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}
    assert len(client.get("/v1/pending", headers=headers_a).json()) == 1
    assert client.get("/v1/pending", headers=headers_b).json() == []
    assert client.get(f"/v1/items/{item.id}/content", headers=headers_b).status_code == 404
    content = client.get(f"/v1/items/{item.id}/content", headers=headers_a)
    assert content.status_code == 200
    assert content.content == b"PK\x03\x04aaaa"
    assert client.post(f"/v1/items/{item.id}/ack", headers=headers_a).status_code == 200
    assert client.get("/v1/pending", headers=headers_a).json() == []
    assert b["user_id"] != a["user_id"]
    if settings.delete_on_ack:
        assert not path.exists()
        assert store.get_item(item.id, a["user_id"]) is None


def test_pairing_page_served(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert b"crypto.getRandomValues" in r.content
    assert b"detectLocale" in r.content
    assert b"Account anlegen" in r.content
    assert b"Create account" in r.content
    assert b'href="/help"' in r.content


def test_help_page_served(client: TestClient) -> None:
    r = client.get("/help")
    assert r.status_code == 200
    assert "privacyTitle" in r.text
    assert "Privatsphäre" in r.text
    assert "Privacy" in r.text
    assert b'href="/"' in r.content
    assert r.headers.get("cache-control", "").startswith("no-store")


def test_store_hashes_token(store: ItemStore) -> None:
    token = _token()
    created = store.create_account(token)
    assert store.user_id_for_token(token) == created.user_id
    assert store.user_id_for_token("0" * 64) is None
    assert len(hash_device_token(token)) == 64


def test_match_plus_alias() -> None:
    assert match_plus_alias("bookbridge+abc123@hoerdle.de", prefix="bookbridge", domain="hoerdle.de") == "abc123"
    assert match_plus_alias("other+abc123@hoerdle.de", prefix="bookbridge", domain="hoerdle.de") is None
    assert (
        resolve_user_mail_local(
            {"To": "Someone <bookbridge+zz99@hoerdle.de>"},
            prefix="bookbridge",
            domain="hoerdle.de",
        )
        == "zz99"
    )
    assert (
        resolve_user_mail_local(
            {"Delivered-To": "bookbridge+prio@hoerdle.de", "To": "bookbridge+other@hoerdle.de"},
            prefix="bookbridge",
            domain="hoerdle.de",
        )
        == "prio"
    )


def test_pairing_page_has_multi_device_hint(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "multiDevice" in r.text
    assert "plausible.io" not in r.text


def test_pairing_page_injects_plausible(tmp_path: Path, store: ItemStore) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        mail_local_prefix="bookbridge",
        mail_domain="hoerdle.de",
        plausible_domain="bookbridge.example",
        plausible_script_url="https://plausible.io/js/script.js",
    )
    app = create_app(settings=settings, store=store)
    with TestClient(app) as client:
        r = client.get("/")
    assert r.status_code == 200
    assert 'data-domain="bookbridge.example"' in r.text
    assert 'src="https://plausible.io/js/script.js"' in r.text
