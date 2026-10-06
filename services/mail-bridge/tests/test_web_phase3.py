from __future__ import annotations

import secrets
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.convert import CONVERT_EXTENSIONS, NATIVE_EXTENSIONS, extension_of
from app.main import create_app
from app.store import ItemStore, hash_device_token, validate_username


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
        webauthn_rp_id="localhost",
        webauthn_origin="http://testserver",
        convert_enabled=True,
        upload_max_bytes=1_000_000,
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


def test_validate_username() -> None:
    assert validate_username("Ada_1") == "ada_1"
    with pytest.raises(ValueError):
        validate_username("ab")
    with pytest.raises(ValueError):
        validate_username("Bad-Name")


def test_create_account_sets_session_cookie(client: TestClient) -> None:
    token = _token()
    r = client.post("/v1/accounts", json={"device_token": token})
    assert r.status_code == 201
    assert "ci_bridge_session" in r.cookies
    me = client.get("/v1/web/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"].endswith("@hoerdle.de")
    assert body["username"] is None
    assert body["has_passkey"] is False


def test_bootstrap_and_issue_device(client: TestClient, store: ItemStore) -> None:
    token = _token()
    created = client.post("/v1/accounts", json={"device_token": token}).json()
    client.cookies.clear()
    assert client.get("/v1/web/me").status_code == 401

    bad = client.post(
        "/v1/web/session/bootstrap",
        json={"user_id": created["user_id"], "device_token": _token()},
    )
    assert bad.status_code == 401

    ok = client.post(
        "/v1/web/session/bootstrap",
        json={"user_id": created["user_id"], "device_token": token},
    )
    assert ok.status_code == 200
    assert client.get("/v1/web/me").status_code == 200

    issued = client.post("/v1/web/devices")
    assert issued.status_code == 200
    new_token = issued.json()["device_token"]
    assert len(new_token) == 64
    assert store.user_id_for_token(new_token) == created["user_id"]
    assert hash_device_token(new_token) != hash_device_token(token)

    pending = client.get("/v1/pending", headers={"Authorization": f"Bearer {new_token}"})
    assert pending.status_code == 200
    assert pending.json() == []


def test_username_availability_check(client: TestClient) -> None:
    t1 = _token()
    t2 = _token()
    a1 = client.post("/v1/accounts", json={"device_token": t1}).json()
    client.cookies.clear()
    a2 = client.post("/v1/accounts", json={"device_token": t2}).json()

    client.cookies.clear()
    client.post("/v1/web/session/bootstrap", json={"user_id": a1["user_id"], "device_token": t1})
    with patch("app.web_routes.verify_registration") as mock_reg:
        mock_reg.return_value = (a1["user_id"], "alice", "credA", b"\x01\x02", 0)
        assert (
            client.post(
                "/v1/web/passkey/register/verify",
                json={"challenge_key": "k", "credential": {"id": "credA"}},
            ).status_code
            == 200
        )

    client.cookies.clear()
    client.post("/v1/web/session/bootstrap", json={"user_id": a2["user_id"], "device_token": t2})
    taken = client.get("/v1/web/username/available", params={"username": "Alice"})
    assert taken.status_code == 200
    assert taken.json()["available"] is False
    assert taken.json()["status"] == "taken"
    assert taken.json()["username"] == "alice"

    free = client.get("/v1/web/username/available", params={"username": "bob_2"})
    assert free.status_code == 200
    assert free.json() == {"username": "bob_2", "available": True, "status": "available"}

    short = client.get("/v1/web/username/available", params={"username": "ab"})
    assert short.json()["status"] == "too_short"
    assert short.json()["available"] is False

    bad = client.get("/v1/web/username/available", params={"username": "Bad-Name"})
    assert bad.json()["status"] == "invalid"

    client.cookies.clear()
    client.post("/v1/web/session/bootstrap", json={"user_id": a1["user_id"], "device_token": t1})
    own = client.get("/v1/web/username/available", params={"username": "alice"})
    assert own.json()["status"] == "own"
    assert own.json()["available"] is True


def test_username_uniqueness(client: TestClient) -> None:
    t1 = _token()
    t2 = _token()
    a1 = client.post("/v1/accounts", json={"device_token": t1}).json()
    client.cookies.clear()
    a2 = client.post("/v1/accounts", json={"device_token": t2}).json()

    # Session is for a2 after create; bootstrap a1.
    client.cookies.clear()
    client.post("/v1/web/session/bootstrap", json={"user_id": a1["user_id"], "device_token": t1})

    with patch("app.web_routes.verify_registration") as mock_reg:
        mock_reg.return_value = (a1["user_id"], "alice", "credA", b"\x01\x02", 0)
        r = client.post(
            "/v1/web/passkey/register/verify",
            json={"challenge_key": "k", "credential": {"id": "credA"}},
        )
        assert r.status_code == 200

    client.cookies.clear()
    client.post("/v1/web/session/bootstrap", json={"user_id": a2["user_id"], "device_token": t2})
    opts = client.post("/v1/web/passkey/register/options", json={"username": "alice"})
    assert opts.status_code == 409


def test_passkey_register_login_roundtrip_mocked(client: TestClient, store: ItemStore) -> None:
    token = _token()
    created = client.post("/v1/accounts", json={"device_token": token}).json()
    user_id = created["user_id"]

    with patch("app.web_routes.registration_options_json") as mock_opts:
        mock_opts.return_value = ("ck-reg", '{"challenge":"x"}')
        opts = client.post("/v1/web/passkey/register/options", json={"username": "bob_1"})
        assert opts.status_code == 200
        assert opts.json()["challenge_key"] == "ck-reg"

    with patch("app.web_routes.verify_registration") as mock_ver:
        mock_ver.return_value = (user_id, "bob_1", "cred-bob", b"pubkey", 1)
        ver = client.post(
            "/v1/web/passkey/register/verify",
            json={"challenge_key": "ck-reg", "credential": {"id": "cred-bob"}},
        )
        assert ver.status_code == 200
        assert ver.json()["username"] == "bob_1"

    profile = store.get_user_profile(user_id)
    assert profile is not None
    assert profile.username == "bob_1"
    assert profile.has_passkey is True

    client.cookies.clear()
    assert client.get("/v1/web/me").status_code == 401

    with patch("app.web_routes.authentication_options_json") as mock_login_opts:
        mock_login_opts.return_value = ("ck-login", '{"challenge":"y"}')
        login_opts = client.post("/v1/web/passkey/login/options", json={"username": "bob_1"})
        assert login_opts.status_code == 200

    with patch("app.web_routes.verify_authentication") as mock_login:
        mock_login.return_value = (user_id, "cred-bob", 2)
        login = client.post(
            "/v1/web/passkey/login/verify",
            json={
                "challenge_key": "ck-login",
                "credential": {"id": "cred-bob", "rawId": "cred-bob"},
            },
        )
        assert login.status_code == 200
        assert "ci_bridge_session" in login.cookies

    me = client.get("/v1/web/me")
    assert me.status_code == 200
    assert me.json()["username"] == "bob_1"

    client.post("/v1/web/logout")
    assert client.get("/v1/web/me").status_code == 401


def test_upload_epub_into_queue(client: TestClient, store: ItemStore) -> None:
    token = _token()
    created = client.post("/v1/accounts", json={"device_token": token}).json()
    content = b"PK\x03\x04fake-epub"
    r = client.post(
        "/v1/web/upload",
        files={"file": ("demo.epub", content, "application/epub+zip")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["filename"] == "demo.epub"
    assert body["converted"] is False

    pending = store.list_pending(created["user_id"])
    assert len(pending) == 1
    assert pending[0].filename == "demo.epub"

    # Foreign session isolation
    other = _token()
    client.post("/v1/accounts", json={"device_token": other})
    assert store.list_pending(client.get("/v1/web/me").json()["user_id"]) == []


def test_upload_rejects_pdf_and_mobi_when_convert_off(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "off",
        imap_host="",
        imap_user="",
        imap_pass="",
        convert_enabled=False,
        webauthn_origin="http://testserver",
    )
    store = ItemStore(settings.db_path, settings.items_dir)
    app = create_app(settings=settings, store=store)
    with TestClient(app) as client:
        token = _token()
        client.post("/v1/accounts", json={"device_token": token})
        pdf = client.post(
            "/v1/web/upload",
            files={"file": ("x.pdf", b"%PDF", "application/pdf")},
        )
        assert pdf.status_code == 400
        mobi = client.post(
            "/v1/web/upload",
            files={"file": ("x.mobi", b"MOBI", "application/octet-stream")},
        )
        assert mobi.status_code == 400


def test_upload_convert_mobi_mocked(client: TestClient, store: ItemStore) -> None:
    token = _token()
    created = client.post("/v1/accounts", json={"device_token": token}).json()
    with patch("app.web_routes.convert_to_epub") as mock_conv:
        mock_conv.return_value = (b"EPUBDATA", "book.epub")
        r = client.post(
            "/v1/web/upload",
            files={"file": ("book.mobi", b"mobi-bytes", "application/octet-stream")},
        )
    assert r.status_code == 200
    assert r.json()["converted"] is True
    assert r.json()["filename"] == "book.epub"
    pending = store.list_pending(created["user_id"])
    assert len(pending) == 1
    assert pending[0].filename == "book.epub"
    assert Path(pending[0].path).read_bytes() == b"EPUBDATA"


def test_extension_routing() -> None:
    assert extension_of("a.EPUB") == ".epub"
    assert extension_of("a.mobi") in CONVERT_EXTENSIONS
    assert extension_of("a.txt") in NATIVE_EXTENSIONS
    assert extension_of("a.pdf") == ".pdf"


def test_devices_requires_session(client: TestClient) -> None:
    assert client.post("/v1/web/devices").status_code == 401
