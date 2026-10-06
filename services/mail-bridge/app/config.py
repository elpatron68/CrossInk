from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    data_dir: Path = Field(default=Path("./data"), validation_alias="DATA_DIR")

    mail_local_prefix: str = Field(default="bookbridge", validation_alias="MAIL_LOCAL_PREFIX")
    mail_domain: str = Field(default="hoerdle.de", validation_alias="MAIL_DOMAIN")
    mail_local_length: int = Field(default=10, validation_alias="MAIL_LOCAL_LENGTH")

    imap_host: str = Field(default="", validation_alias="IMAP_HOST")
    imap_port: int = Field(default=993, validation_alias="IMAP_PORT")
    imap_user: str = Field(default="", validation_alias="IMAP_USER")
    imap_pass: str = Field(default="", validation_alias="IMAP_PASS")
    imap_folder: str = Field(default="INBOX", validation_alias="IMAP_FOLDER")
    imap_ssl: bool = Field(default=True, validation_alias="IMAP_SSL")

    poll_seconds: int = Field(default=60, validation_alias="POLL_SECONDS")
    # After a matched message is queued: delete | move | seen
    post_process: str = Field(default="delete", validation_alias="POST_PROCESS")
    processed_folder: str = Field(default="Processed", validation_alias="PROCESSED_FOLDER")

    # Pending items not downloaded after this many days are deleted (0 = disabled).
    orphan_retention_days: int = Field(default=14, validation_alias="ORPHAN_RETENTION_DAYS")
    # How often to scan for orphans (seconds).
    orphan_cleanup_seconds: int = Field(default=3600, validation_alias="ORPHAN_CLEANUP_SECONDS")
    # When true, ack also deletes the stored file (and the DB row).
    delete_on_ack: bool = Field(default=True, validation_alias="DELETE_ON_ACK")

    account_unused_days: int = Field(default=7, validation_alias="ACCOUNT_UNUSED_DAYS")
    account_inactive_days: int = Field(default=365, validation_alias="ACCOUNT_INACTIVE_DAYS")
    signup_rate_limit_per_hour: int = Field(default=5, validation_alias="SIGNUP_RATE_LIMIT_PER_HOUR")
    trust_proxy: bool = Field(default=True, validation_alias="TRUST_PROXY")

    # Optional Plausible Analytics domain for the pairing page (empty = disabled).
    plausible_domain: str = Field(default="", validation_alias="PLAUSIBLE_DOMAIN")
    plausible_script_url: str = Field(
        default="https://plausible.io/js/script.js",
        validation_alias="PLAUSIBLE_SCRIPT_URL",
    )

    # WebAuthn / passkey (optional account recovery in the browser).
    webauthn_rp_id: str = Field(default="localhost", validation_alias="WEBAUTHN_RP_ID")
    webauthn_origin: str = Field(default="http://localhost:8080", validation_alias="WEBAUTHN_ORIGIN")
    webauthn_rp_name: str = Field(default="CrossInk Mail Bridge", validation_alias="WEBAUTHN_RP_NAME")
    web_session_days: int = Field(default=14, validation_alias="WEB_SESSION_DAYS")

    # Native web upload (stage 2/3).
    upload_max_bytes: int = Field(default=80_000_000, validation_alias="UPLOAD_MAX_BYTES")
    upload_rate_limit_per_hour: int = Field(default=30, validation_alias="UPLOAD_RATE_LIMIT_PER_HOUR")

    # Calibre conversion (stage 3).
    convert_enabled: bool = Field(default=True, validation_alias="CONVERT_ENABLED")
    convert_timeout_seconds: int = Field(default=120, validation_alias="CONVERT_TIMEOUT_SECONDS")

    host: str = Field(default="0.0.0.0", validation_alias="HOST")
    port: int = Field(default=8080, validation_alias="PORT")

    @property
    def items_dir(self) -> Path:
        return self.data_dir / "items"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "queue.sqlite3"

    @property
    def imap_configured(self) -> bool:
        return bool(self.imap_host and self.imap_user and self.imap_pass)

    def format_alias_email(self, mail_local: str) -> str:
        return f"{self.mail_local_prefix}+{mail_local}@{self.mail_domain}"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.items_dir.mkdir(parents=True, exist_ok=True)
    return settings
