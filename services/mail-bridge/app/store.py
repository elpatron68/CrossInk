from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import string
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

DEVICE_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
_MAIL_LOCAL_ALPHABET = string.ascii_lowercase + string.digits


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PendingItem:
    id: str
    filename: str
    bytes: int
    sha256: str
    content_type: str
    received_at: str
    path: Path


@dataclass(frozen=True)
class CreatedAccount:
    user_id: str
    mail_local: str
    email: str


class ItemStore:
    def __init__(
        self,
        db_path: Path,
        items_dir: Path,
        *,
        mail_local_prefix: str = "bookbridge",
        mail_domain: str = "hoerdle.de",
        mail_local_length: int = 10,
    ) -> None:
        self.db_path = db_path
        self.items_dir = items_dir
        self.mail_local_prefix = mail_local_prefix
        self.mail_domain = mail_domain
        self.mail_local_length = max(4, mail_local_length)
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
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    mail_local TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    label TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
                """
            )
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
                    user_id TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_items_status_user ON items(status, user_id)")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_items_imap_sha ON items(imap_uid, sha256) "
                "WHERE imap_uid IS NOT NULL"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_devices_token_hash ON devices(token_hash)")

    def format_alias_email(self, mail_local: str) -> str:
        return f"{self.mail_local_prefix}+{mail_local}@{self.mail_domain}"

    def _generate_mail_local(self, conn: sqlite3.Connection) -> str:
        for _ in range(32):
            candidate = "".join(secrets.choice(_MAIL_LOCAL_ALPHABET) for _ in range(self.mail_local_length))
            exists = conn.execute("SELECT 1 FROM users WHERE mail_local = ? LIMIT 1", (candidate,)).fetchone()
            if exists is None:
                return candidate
        raise RuntimeError("Could not allocate unique mail_local")

    def create_account(self, device_token: str) -> CreatedAccount:
        if not DEVICE_TOKEN_RE.fullmatch(device_token):
            raise ValueError("device_token must be 64 lowercase hex characters")
        token_hash = hash_device_token(device_token)
        created_at = _utc_now_iso()
        user_id = uuid.uuid4().hex
        device_id = uuid.uuid4().hex
        with self._conn() as conn:
            if conn.execute("SELECT 1 FROM devices WHERE token_hash = ? LIMIT 1", (token_hash,)).fetchone():
                raise LookupError("device_token already registered")
            mail_local = self._generate_mail_local(conn)
            conn.execute(
                "INSERT INTO users (id, mail_local, created_at) VALUES (?, ?, ?)",
                (user_id, mail_local, created_at),
            )
            conn.execute(
                """
                INSERT INTO devices (id, user_id, token_hash, label, created_at)
                VALUES (?, ?, ?, 'default', ?)
                """,
                (device_id, user_id, token_hash, created_at),
            )
        return CreatedAccount(
            user_id=user_id,
            mail_local=mail_local,
            email=self.format_alias_email(mail_local),
        )

    def user_id_for_token(self, device_token: str) -> str | None:
        token_hash = hash_device_token(device_token.strip())
        with self._conn() as conn:
            row = conn.execute(
                "SELECT user_id FROM devices WHERE token_hash = ? LIMIT 1",
                (token_hash,),
            ).fetchone()
        return row["user_id"] if row else None

    def user_id_for_mail_local(self, mail_local: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE mail_local = ? LIMIT 1",
                (mail_local.lower(),),
            ).fetchone()
        return row["id"] if row else None

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
        user_id: str,
        received_at: str | None = None,
    ) -> PendingItem:
        if not user_id:
            raise ValueError("user_id is required")
        item_id = uuid.uuid4().hex
        user_dir = self.items_dir / user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        dest = user_dir / item_id
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

    def list_pending(self, user_id: str) -> list[PendingItem]:
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

    def get_item(self, item_id: str, user_id: str) -> PendingItem | None:
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

    def ack(self, item_id: str, user_id: str) -> bool:
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
