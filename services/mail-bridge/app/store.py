from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class PendingItem:
    id: str
    filename: str
    bytes: int
    sha256: str
    content_type: str
    received_at: str
    path: Path


class ItemStore:
    def __init__(self, db_path: Path, items_dir: Path) -> None:
        self.db_path = db_path
        self.items_dir = items_dir
        self.items_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    path TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'delivered')),
                    imap_uid TEXT,
                    received_at TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT 'default'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_items_status_user ON items(status, user_id)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_items_imap_sha ON items(imap_uid, sha256) "
                "WHERE imap_uid IS NOT NULL"
            )

    def has_imap_sha(self, imap_uid: str, sha256: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM items WHERE imap_uid = ? AND sha256 = ? LIMIT 1",
                (imap_uid, sha256),
            ).fetchone()
            return row is not None

    def add_item(
        self,
        *,
        filename: str,
        content: bytes,
        sha256: str,
        content_type: str,
        imap_uid: str | None,
        received_at: str | None = None,
        user_id: str = "default",
    ) -> PendingItem:
        item_id = uuid.uuid4().hex
        dest = self.items_dir / item_id
        dest.write_bytes(content)
        received = received_at or _utc_now_iso()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO items (
                    id, filename, bytes, sha256, path, content_type, status, imap_uid, received_at, user_id
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    item_id,
                    filename,
                    len(content),
                    sha256,
                    str(dest),
                    content_type,
                    imap_uid,
                    received,
                    user_id,
                ),
            )
        return PendingItem(
            id=item_id,
            filename=filename,
            bytes=len(content),
            sha256=sha256,
            content_type=content_type,
            received_at=received,
            path=dest,
        )

    def list_pending(self, user_id: str = "default") -> list[PendingItem]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, filename, bytes, sha256, path, content_type, received_at
                FROM items
                WHERE status = 'pending' AND user_id = ?
                ORDER BY received_at ASC
                """,
                (user_id,),
            ).fetchall()
        return [
            PendingItem(
                id=row["id"],
                filename=row["filename"],
                bytes=row["bytes"],
                sha256=row["sha256"],
                content_type=row["content_type"],
                received_at=row["received_at"],
                path=Path(row["path"]),
            )
            for row in rows
        ]

    def get_item(self, item_id: str, user_id: str = "default") -> PendingItem | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT id, filename, bytes, sha256, path, content_type, received_at, status
                FROM items
                WHERE id = ? AND user_id = ?
                """,
                (item_id, user_id),
            ).fetchone()
        if row is None:
            return None
        return PendingItem(
            id=row["id"],
            filename=row["filename"],
            bytes=row["bytes"],
            sha256=row["sha256"],
            content_type=row["content_type"],
            received_at=row["received_at"],
            path=Path(row["path"]),
        )

    def ack(self, item_id: str, user_id: str = "default") -> bool:
        """Mark pending item delivered. Idempotent if already delivered. Returns False if missing."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT status FROM items WHERE id = ? AND user_id = ?",
                (item_id, user_id),
            ).fetchone()
            if row is None:
                return False
            if row["status"] == "delivered":
                return True
            conn.execute(
                "UPDATE items SET status = 'delivered' WHERE id = ? AND user_id = ?",
                (item_id, user_id),
            )
            return True
