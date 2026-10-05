from __future__ import annotations

import imaplib
import logging
import threading
from email import message_from_bytes
from email.utils import parsedate_to_datetime
from typing import Callable

from .config import Settings
from .mime_extract import extract_attachments
from .recipients import resolve_user_mail_local
from .store import ItemStore

LOG = logging.getLogger("mail-bridge.imap")


def dispose_uid(
    client: imaplib.IMAP4,
    uid: str,
    *,
    mode: str,
    folder: str = "Processed",
) -> str:
    """Remove or archive a message. Returns the action taken: delete|move|seen."""
    normalized = (mode or "delete").lower()
    if normalized == "move":
        try:
            client.create(folder)
        except imaplib.IMAP4.error:
            pass
        typ, _ = client.uid("COPY", uid, folder)
        if typ == "OK":
            client.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
            client.expunge()
            return "move"
        LOG.warning("Move to %s failed for UID %s; falling back to delete", folder, uid)
        normalized = "delete"
    if normalized == "seen":
        client.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        return "seen"
    # default: delete
    client.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
    client.expunge()
    return "delete"


class ImapWorker:
    """Poll IMAP for unread mail, extract allowed attachments into the queue."""

    def __init__(self, settings: Settings, store: ItemStore) -> None:
        self.settings = settings
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if not self.settings.imap_configured:
            LOG.warning("IMAP not configured; poller disabled (API still serves stored items)")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="imap-poller", daemon=True)
        self._thread.start()
        LOG.info(
            "IMAP poller started (%s@%s:%s folder=%s every %ss post=%s)",
            self.settings.imap_user,
            self.settings.imap_host,
            self.settings.imap_port,
            self.settings.imap_folder,
            self.settings.poll_seconds,
            self.settings.post_process,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def poll_now(self) -> int:
        """Run one poll immediately. Returns number of new attachments stored."""
        if not self.settings.imap_configured:
            return 0
        with self._lock:
            return self._poll_once()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                with self._lock:
                    self._poll_once()
            except Exception:
                LOG.exception("IMAP poll failed")
            self._stop.wait(self.settings.poll_seconds)

    def _connect(self) -> imaplib.IMAP4:
        if self.settings.imap_ssl:
            client: imaplib.IMAP4 = imaplib.IMAP4_SSL(self.settings.imap_host, self.settings.imap_port)
        else:
            client = imaplib.IMAP4(self.settings.imap_host, self.settings.imap_port)
        client.login(self.settings.imap_user, self.settings.imap_pass)
        return client

    def _poll_once(self) -> int:
        client = self._connect()
        added = 0
        try:
            typ, _ = client.select(self.settings.imap_folder, readonly=False)
            if typ != "OK":
                LOG.error("Failed to select folder %s", self.settings.imap_folder)
                return 0

            typ, data = client.uid("SEARCH", None, "UNSEEN")
            if typ != "OK" or not data or not data[0]:
                return 0

            uids = data[0].split()
            LOG.info("Found %d unseen message(s)", len(uids))
            for uid in uids:
                uid_str = uid.decode("ascii") if isinstance(uid, bytes) else str(uid)
                added += self._process_uid(client, uid_str)
        finally:
            try:
                client.logout()
            except Exception:
                pass
        return added

    def _discard(self, client: imaplib.IMAP4, uid: str, reason: str) -> None:
        action = dispose_uid(client, uid, mode="delete")
        LOG.info("UID %s: %s → %s", uid, reason, action)

    def _finish(self, client: imaplib.IMAP4, uid: str) -> None:
        action = dispose_uid(
            client,
            uid,
            mode=self.settings.post_process,
            folder=self.settings.processed_folder,
        )
        LOG.info("UID %s: post-process → %s", uid, action)

    def _process_uid(self, client: imaplib.IMAP4, uid: str) -> int:
        typ, data = client.uid("FETCH", uid, "(RFC822)")
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            LOG.error("FETCH failed for UID %s", uid)
            return 0

        raw = data[0][1]
        if not isinstance(raw, (bytes, bytearray)):
            LOG.error("Unexpected FETCH payload for UID %s", uid)
            return 0

        msg = message_from_bytes(bytes(raw))
        mail_local = resolve_user_mail_local(
            {
                "Delivered-To": msg.get("Delivered-To"),
                "X-Original-To": msg.get("X-Original-To"),
                "To": msg.get("To"),
                "Cc": msg.get("Cc"),
            },
            prefix=self.settings.mail_local_prefix,
            domain=self.settings.mail_domain,
        )
        if not mail_local:
            self._discard(client, uid, "no matching plus-alias recipient")
            return 0

        user_id = self.store.user_id_for_mail_local(mail_local)
        if user_id is None:
            self._discard(client, uid, f"unknown mail_local {mail_local}")
            return 0

        attachments = extract_attachments(bytes(raw))
        if not attachments:
            self._discard(client, uid, "no allowed attachments")
            return 0

        received_at = None
        try:
            date_hdr = msg.get("Date")
            if date_hdr:
                received_at = parsedate_to_datetime(date_hdr).astimezone().replace(microsecond=0).isoformat()
        except Exception:
            received_at = None

        stored = 0
        for att in attachments:
            if self.store.has_imap_sha(uid, att.sha256):
                LOG.info("UID %s: skip duplicate %s (%s)", uid, att.filename, att.sha256[:12])
                continue
            item = self.store.add_item(
                filename=att.filename,
                content=att.content,
                sha256=att.sha256,
                content_type=att.content_type,
                imap_uid=uid,
                received_at=received_at,
                user_id=user_id,
            )
            LOG.info(
                "UID %s: queued %s as %s for user %s (%d bytes)",
                uid,
                att.filename,
                item.id,
                user_id[:8],
                item.bytes,
            )
            stored += 1

        self._finish(client, uid)
        return stored


def make_on_demand_poll(worker: ImapWorker) -> Callable[[], None]:
    def _poll() -> None:
        try:
            worker.poll_now()
        except Exception:
            LOG.exception("On-demand IMAP poll failed")

    return _poll
