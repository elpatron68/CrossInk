from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import string
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

DEVICE_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
USERNAME_RE = re.compile(r"^[a-z0-9_]{3,32}$")
_MAIL_LOCAL_ALPHABET = string.ascii_lowercase + string.digits


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_username(username: str) -> str:
    return username.strip().lower()


def validate_username(username: str) -> str:
    normalized = normalize_username(username)
    if not USERNAME_RE.fullmatch(normalized):
        raise ValueError("username must be 3-32 chars: a-z, 0-9, underscore")
    return normalized


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


@dataclass(frozen=True)
class UserProfile:
    user_id: str
    mail_local: str
    email: str
    username: str | None
    pending_count: int
    has_passkey: bool


@dataclass(frozen=True)
class WebAuthnCredential:
    id: str
    user_id: str
    credential_id: str
    public_key: bytes
    sign_count: int
    created_at: str


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
            cols = {row[1] for row in conn.execute("PRAGMA table_info(devices)").fetchall()}
            if "last_seen_at" not in cols:
                conn.execute("ALTER TABLE devices ADD COLUMN last_seen_at TEXT")
                conn.execute(
                    "UPDATE devices SET last_seen_at = created_at WHERE last_seen_at IS NULL"
                )
            user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            if "username" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN username TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username) "
                "WHERE username IS NOT NULL"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS webauthn_credentials (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    credential_id TEXT NOT NULL UNIQUE,
                    public_key BLOB NOT NULL,
                    sign_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_webauthn_user ON webauthn_credentials(user_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS web_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_web_sessions_user ON web_sessions(user_id)")

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

    def user_id_for_username(self, username: str) -> str | None:
        normalized = normalize_username(username)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE username = ? LIMIT 1",
                (normalized,),
            ).fetchone()
        return row["id"] if row else None

    def get_user_profile(self, user_id: str) -> UserProfile | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, mail_local, username FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            pending = conn.execute(
                "SELECT COUNT(*) AS c FROM items WHERE user_id = ? AND status = 'pending'",
                (user_id,),
            ).fetchone()["c"]
            passkey = conn.execute(
                "SELECT 1 FROM webauthn_credentials WHERE user_id = ? LIMIT 1",
                (user_id,),
            ).fetchone()
        return UserProfile(
            user_id=row["id"],
            mail_local=row["mail_local"],
            email=self.format_alias_email(row["mail_local"]),
            username=row["username"],
            pending_count=int(pending),
            has_passkey=passkey is not None,
        )

    def create_web_session(self, user_id: str, *, days: int = 14) -> str:
        session_id = secrets.token_urlsafe(32)
        created = datetime.now(timezone.utc).replace(microsecond=0)
        expires = created + timedelta(days=max(1, days))
        with self._conn() as conn:
            if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
                raise LookupError("user not found")
            conn.execute(
                """
                INSERT INTO web_sessions (id, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, user_id, created.isoformat(), expires.isoformat()),
            )
        return session_id

    def user_id_for_session(self, session_id: str) -> str | None:
        if not session_id:
            return None
        now = _utc_now_iso()
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT user_id, expires_at FROM web_sessions WHERE id = ? LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] < now:
                conn.execute("DELETE FROM web_sessions WHERE id = ?", (session_id,))
                return None
            return row["user_id"]

    def delete_web_session(self, session_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM web_sessions WHERE id = ?", (session_id,))

    def set_username(self, user_id: str, username: str) -> str:
        normalized = validate_username(username)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT username FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                raise LookupError("user not found")
            existing = row["username"]
            if existing and existing != normalized:
                raise ValueError("username already set")
            conflict = conn.execute(
                "SELECT id FROM users WHERE username = ? AND id != ? LIMIT 1",
                (normalized, user_id),
            ).fetchone()
            if conflict is not None:
                raise LookupError("username already taken")
            conn.execute(
                "UPDATE users SET username = ? WHERE id = ?",
                (normalized, user_id),
            )
        return normalized

    def list_webauthn_credentials(self, user_id: str) -> list[WebAuthnCredential]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, credential_id, public_key, sign_count, created_at
                FROM webauthn_credentials WHERE user_id = ?
                """,
                (user_id,),
            ).fetchall()
        return [
            WebAuthnCredential(
                id=row["id"],
                user_id=row["user_id"],
                credential_id=row["credential_id"],
                public_key=bytes(row["public_key"]),
                sign_count=int(row["sign_count"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_webauthn_credential(self, credential_id: str) -> WebAuthnCredential | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT id, user_id, credential_id, public_key, sign_count, created_at
                FROM webauthn_credentials WHERE credential_id = ? LIMIT 1
                """,
                (credential_id,),
            ).fetchone()
        if row is None:
            return None
        return WebAuthnCredential(
            id=row["id"],
            user_id=row["user_id"],
            credential_id=row["credential_id"],
            public_key=bytes(row["public_key"]),
            sign_count=int(row["sign_count"]),
            created_at=row["created_at"],
        )

    def add_webauthn_credential(
        self,
        *,
        user_id: str,
        credential_id: str,
        public_key: bytes,
        sign_count: int = 0,
    ) -> WebAuthnCredential:
        row_id = uuid.uuid4().hex
        created_at = _utc_now_iso()
        with self._conn() as conn:
            if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
                raise LookupError("user not found")
            if conn.execute(
                "SELECT 1 FROM webauthn_credentials WHERE credential_id = ? LIMIT 1",
                (credential_id,),
            ).fetchone():
                raise LookupError("credential already registered")
            conn.execute(
                """
                INSERT INTO webauthn_credentials (
                    id, user_id, credential_id, public_key, sign_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (row_id, user_id, credential_id, public_key, sign_count, created_at),
            )
        return WebAuthnCredential(
            id=row_id,
            user_id=user_id,
            credential_id=credential_id,
            public_key=public_key,
            sign_count=sign_count,
            created_at=created_at,
        )

    def update_webauthn_sign_count(self, credential_id: str, sign_count: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE webauthn_credentials SET sign_count = ? WHERE credential_id = ?",
                (sign_count, credential_id),
            )

    def create_device_for_user(self, user_id: str, *, label: str = "web") -> str:
        """Issue a new device token (plaintext once). Returns the raw token."""
        token = secrets.token_hex(32)
        token_hash = hash_device_token(token)
        device_id = uuid.uuid4().hex
        created_at = _utc_now_iso()
        with self._conn() as conn:
            if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
                raise LookupError("user not found")
            conn.execute(
                """
                INSERT INTO devices (id, user_id, token_hash, label, created_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (device_id, user_id, token_hash, label, created_at, created_at),
            )
        return token

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

    def ack(self, item_id: str, user_id: str, *, delete_files: bool = False) -> bool:
        """Mark pending item delivered. Idempotent if already delivered. Returns False if missing.

        When delete_files is True, remove the blob and the DB row after a successful ack
        (also cleans up an already-delivered row on repeat ack).
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT status, path FROM items WHERE id = ? AND user_id = ?",
                (item_id, user_id),
            ).fetchone()
            if row is None:
                return False
            if row["status"] != "delivered":
                conn.execute(
                    "UPDATE items SET status = 'delivered' WHERE id = ? AND user_id = ?",
                    (item_id, user_id),
                )
            if delete_files:
                path = Path(row["path"])
                try:
                    if path.is_file():
                        path.unlink()
                except OSError:
                    pass
                conn.execute("DELETE FROM items WHERE id = ? AND user_id = ?", (item_id, user_id))
            return True

    def touch_device_for_token(self, device_token: str) -> None:
        token_hash = hash_device_token(device_token.strip())
        now = _utc_now_iso()
        with self._conn() as conn:
            conn.execute(
                "UPDATE devices SET last_seen_at = ? WHERE token_hash = ?",
                (now, token_hash),
            )

    def delete_user(self, user_id: str) -> bool:
        """Remove user, devices, items rows and on-disk files. Returns False if user missing."""
        user_dir = self.items_dir / user_id
        with self._conn() as conn:
            if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
                return False
            rows = conn.execute("SELECT path FROM items WHERE user_id = ?", (user_id,)).fetchall()
            for row in rows:
                path = Path(row["path"])
                try:
                    if path.is_file():
                        path.unlink()
                except OSError:
                    pass
            conn.execute("DELETE FROM items WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM devices WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM webauthn_credentials WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM web_sessions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        if user_dir.is_dir():
            for child in user_dir.iterdir():
                try:
                    if child.is_file():
                        child.unlink()
                except OSError:
                    pass
            try:
                user_dir.rmdir()
            except OSError:
                pass
        return True

    def purge_unused_accounts(self, *, older_than_days: int) -> int:
        if older_than_days <= 0:
            return 0
        cutoff = datetime.now(timezone.utc).timestamp() - (older_than_days * 86400)
        removed = 0
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT u.id, u.created_at
                FROM users u
                WHERE NOT EXISTS (
                    SELECT 1 FROM devices d
                    WHERE d.user_id = u.id AND d.last_seen_at IS NOT NULL AND d.last_seen_at != ''
                )
                """
            ).fetchall()
            victims = []
            for row in rows:
                try:
                    created = datetime.fromisoformat(row["created_at"])
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    if created.timestamp() <= cutoff:
                        victims.append(row["id"])
                except ValueError:
                    continue
        for user_id in victims:
            if self.delete_user(user_id):
                removed += 1
        return removed

    def purge_inactive_accounts(self, *, older_than_days: int) -> int:
        if older_than_days <= 0:
            return 0
        cutoff = datetime.now(timezone.utc).timestamp() - (older_than_days * 86400)
        removed = 0
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT u.id, u.created_at,
                       (SELECT MAX(d.last_seen_at) FROM devices d WHERE d.user_id = u.id) AS last_seen,
                       (SELECT MAX(i.received_at) FROM items i WHERE i.user_id = u.id) AS last_mail
                FROM users u
                WHERE EXISTS (
                    SELECT 1 FROM devices d
                    WHERE d.user_id = u.id AND d.last_seen_at IS NOT NULL AND d.last_seen_at != ''
                )
                """
            ).fetchall()
            victims = []
            for row in rows:
                stamps = [row["created_at"], row["last_seen"], row["last_mail"]]
                activity = None
                for stamp in stamps:
                    if not stamp:
                        continue
                    try:
                        dt = datetime.fromisoformat(stamp)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if activity is None or dt > activity:
                            activity = dt
                    except ValueError:
                        continue
                if activity is not None and activity.timestamp() <= cutoff:
                    victims.append(row["id"])
        for user_id in victims:
            if self.delete_user(user_id):
                removed += 1
        return removed

    def purge_orphans(self, *, older_than_days: int) -> int:
        """Delete pending items (and files) older than older_than_days. Returns count removed."""
        if older_than_days <= 0:
            return 0
        cutoff = datetime.now(timezone.utc).timestamp() - (older_than_days * 86400)
        removed = 0
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, path, received_at FROM items WHERE status = 'pending'"
            ).fetchall()
            for row in rows:
                try:
                    # received_at is ISO-8601; fromisoformat handles offsets.
                    received = datetime.fromisoformat(row["received_at"])
                    if received.tzinfo is None:
                        received = received.replace(tzinfo=timezone.utc)
                    if received.timestamp() > cutoff:
                        continue
                except ValueError:
                    continue
                path = Path(row["path"])
                try:
                    if path.is_file():
                        path.unlink()
                except OSError:
                    pass
                conn.execute("DELETE FROM items WHERE id = ?", (row["id"],))
                removed += 1
        return removed
