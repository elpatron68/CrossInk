from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.store import ItemStore


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


def test_touch_device_sets_last_seen(tmp_path: Path) -> None:
    store = ItemStore(tmp_path / "q.sqlite3", tmp_path / "items")
    token = "a" * 64
    store.create_account(token)
    store.touch_device_for_token(token)
    with store._conn() as conn:
        row = conn.execute("SELECT last_seen_at FROM devices").fetchone()
    assert row["last_seen_at"]


def test_purge_unused_accounts(tmp_path: Path) -> None:
    store = ItemStore(tmp_path / "q.sqlite3", tmp_path / "items")
    old = store.create_account("b" * 64)
    keep = store.create_account("c" * 64)
    with store._conn() as conn:
        conn.execute("UPDATE users SET created_at = ? WHERE id = ?", (_iso_days_ago(10), old.user_id))
    assert store.purge_unused_accounts(older_than_days=7) == 1
    assert store.user_id_for_token("b" * 64) is None
    assert store.user_id_for_token("c" * 64) == keep.user_id


def test_purge_unused_skips_touched(tmp_path: Path) -> None:
    store = ItemStore(tmp_path / "q.sqlite3", tmp_path / "items")
    token = "d" * 64
    account = store.create_account(token)
    with store._conn() as conn:
        conn.execute("UPDATE users SET created_at = ? WHERE id = ?", (_iso_days_ago(10), account.user_id))
    store.touch_device_for_token(token)
    assert store.purge_unused_accounts(older_than_days=7) == 0


def test_purge_inactive_accounts(tmp_path: Path) -> None:
    store = ItemStore(tmp_path / "q.sqlite3", tmp_path / "items")
    token = "e" * 64
    account = store.create_account(token)
    store.touch_device_for_token(token)
    with store._conn() as conn:
        conn.execute(
            "UPDATE devices SET last_seen_at = ? WHERE user_id = ?",
            (_iso_days_ago(400), account.user_id),
        )
        conn.execute("UPDATE users SET created_at = ? WHERE id = ?", (_iso_days_ago(400), account.user_id))
    item = store.add_item(
        filename="old.epub",
        content=b"x",
        sha256="sha",
        content_type="application/epub+zip",
        imap_uid="1",
        user_id=account.user_id,
        received_at=_iso_days_ago(400),
    )
    assert store.purge_inactive_accounts(older_than_days=365) == 1
    assert store.user_id_for_token(token) is None
    assert not item.path.exists()


def test_mail_activity_blocks_inactive_purge(tmp_path: Path) -> None:
    store = ItemStore(tmp_path / "q.sqlite3", tmp_path / "items")
    token = "f" * 64
    account = store.create_account(token)
    store.touch_device_for_token(token)
    with store._conn() as conn:
        conn.execute(
            "UPDATE devices SET last_seen_at = ? WHERE user_id = ?",
            (_iso_days_ago(400), account.user_id),
        )
        conn.execute("UPDATE users SET created_at = ? WHERE id = ?", (_iso_days_ago(400), account.user_id))
    store.add_item(
        filename="fresh.epub",
        content=b"y",
        sha256="sha2",
        content_type="application/epub+zip",
        imap_uid="2",
        user_id=account.user_id,
        received_at=_iso_days_ago(1),
    )
    assert store.purge_inactive_accounts(older_than_days=365) == 0
    assert store.user_id_for_token(token) == account.user_id
