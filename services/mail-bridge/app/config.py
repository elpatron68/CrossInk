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
    device_token: str = Field(default="change-me", validation_alias="DEVICE_TOKEN")

    imap_host: str = Field(default="", validation_alias="IMAP_HOST")
    imap_port: int = Field(default=993, validation_alias="IMAP_PORT")
    imap_user: str = Field(default="", validation_alias="IMAP_USER")
    imap_pass: str = Field(default="", validation_alias="IMAP_PASS")
    imap_folder: str = Field(default="INBOX", validation_alias="IMAP_FOLDER")
    imap_ssl: bool = Field(default=True, validation_alias="IMAP_SSL")

    poll_seconds: int = Field(default=60, validation_alias="POLL_SECONDS")
    # seen = mark \\Seen; move = move to processed_folder after extract
    post_process: str = Field(default="seen", validation_alias="POST_PROCESS")
    processed_folder: str = Field(default="Processed", validation_alias="PROCESSED_FOLDER")

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


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.items_dir.mkdir(parents=True, exist_ok=True)
    return settings
