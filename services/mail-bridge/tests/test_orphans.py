from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.store import ItemStore


def test_purge_orphans(tmp_path: Path) -> None:
    store = ItemStore(tmp_path / "q.sqlite3", tmp_path / "items")
    account = store.create_account("a" * 64)
    old = store.add_item(
        filename="old.epub",
        content=b"old",
        sha256="old",
        content_type="application/epub+zip",
        imap_uid="1",
        user_id=account.user_id,
        received_at=(datetime.now(timezone.utc) - timedelta(days=20)).replace(microsecond=0).isoformat(),
    )
    fresh = store.add_item(
        filename="new.epub",
        content=b"new",
        sha256="new",
        content_type="application/epub+zip",
        imap_uid="2",
        user_id=account.user_id,
        received_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )
    assert old.path.is_file()
    assert store.purge_orphans(older_than_days=14) == 1
    assert not old.path.exists()
    assert store.get_item(old.id, account.user_id) is None
    assert store.get_item(fresh.id, account.user_id) is not None
    assert fresh.path.is_file()
