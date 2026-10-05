from __future__ import annotations

import logging
import threading

from .config import Settings
from .store import ItemStore

LOG = logging.getLogger("mail-bridge.cleanup")


class OrphanCleanupWorker:
    """Periodically delete undownloaded pending items past the retention window."""

    def __init__(self, settings: Settings, store: ItemStore) -> None:
        self.settings = settings
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if (
            self.settings.orphan_retention_days <= 0
            and self.settings.account_unused_days <= 0
            and self.settings.account_inactive_days <= 0
        ):
            LOG.info("Cleanup worker disabled")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="orphan-cleanup", daemon=True)
        self._thread.start()
        LOG.info(
            "Cleanup worker started (orphan_retention=%sd unused_accounts=%sd inactive_accounts=%sd interval=%ss)",
            self.settings.orphan_retention_days,
            self.settings.account_unused_days,
            self.settings.account_inactive_days,
            self.settings.orphan_cleanup_seconds,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def run_once(self) -> dict[str, int]:
        counts = {
            "orphans": 0,
            "unused_accounts": 0,
            "inactive_accounts": 0,
        }
        if self.settings.orphan_retention_days > 0:
            counts["orphans"] = self.store.purge_orphans(
                older_than_days=self.settings.orphan_retention_days
            )
            if counts["orphans"]:
                LOG.info("Purged %d orphan pending item(s)", counts["orphans"])
        if self.settings.account_unused_days > 0:
            counts["unused_accounts"] = self.store.purge_unused_accounts(
                older_than_days=self.settings.account_unused_days
            )
            if counts["unused_accounts"]:
                LOG.info("Purged %d unused account(s)", counts["unused_accounts"])
        if self.settings.account_inactive_days > 0:
            counts["inactive_accounts"] = self.store.purge_inactive_accounts(
                older_than_days=self.settings.account_inactive_days
            )
            if counts["inactive_accounts"]:
                LOG.info("Purged %d inactive account(s)", counts["inactive_accounts"])
        return counts

    def _loop(self) -> None:
        # Run once shortly after start, then on the interval.
        self.run_once()
        while not self._stop.wait(max(60, self.settings.orphan_cleanup_seconds)):
            try:
                self.run_once()
            except Exception:
                LOG.exception("Orphan cleanup failed")
