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
            "IMAP poller started (%s@%s:%s folder=%s every %ss)",
            self.settings.imap_user,
            self.settings.imap_host,
            self.settings.imap_port,
            self.settings.imap_folder,
            self.settings.poll_seconds,
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
            LOG.info("UID %s: no matching plus-alias recipient; skipping", uid)
            self._mark_processed(client, uid)
            return 0

        user_id = self.store.user_id_for_mail_local(mail_local)
        if user_id is None:
            LOG.info("UID %s: unknown mail_local %s; skipping", uid, mail_local)
            self._mark_processed(client, uid)
            return 0

        attachments = extract_attachments(bytes(raw))
        if not attachments:
            LOG.info("UID %s: no allowed attachments; marking processed", uid)
            self._mark_processed(client, uid)
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

        self._mark_processed(client, uid)
        return stored

    def _mark_processed(self, client: imaplib.IMAP4, uid: str) -> None:
        mode = (self.settings.post_process or "seen").lower()
        if mode == "move":
            try:
                client.create(self.settings.processed_folder)
            except imaplib.IMAP4.error:
                pass
            typ, _ = client.uid("COPY", uid, self.settings.processed_folder)
            if typ == "OK":
                client.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
                client.expunge()
                return
            LOG.warning("Move to %s failed for UID %s; falling back to \\Seen", self.settings.processed_folder, uid)
        client.uid("STORE", uid, "+FLAGS", "(\\Seen)")


def make_on_demand_poll(worker: ImapWorker) -> Callable[[], None]:
    def _poll() -> None:
        try:
            worker.poll_now()
        except Exception:
            LOG.exception("On-demand IMAP poll failed")

    return _poll
