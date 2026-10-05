from __future__ import annotations

import email
import hashlib
import mimetypes
import re
from dataclasses import dataclass
from email.message import Message
from pathlib import PurePosixPath

ALLOWED_EXTENSIONS = {".epub", ".txt"}

CONTENT_TYPES = {
    ".epub": "application/epub+zip",
    ".txt": "text/plain",
}

# FAT/exFAT reject these; CR/LF also break Content-Disposition HTTP headers.
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
_WHITESPACE_RUNS = re.compile(r"\s+")


@dataclass(frozen=True)
class ExtractedAttachment:
    filename: str
    content: bytes
    sha256: str
    content_type: str


def safe_filename(name: str | None) -> str | None:
    if not name:
        return None
    # Strip path components from Content-Disposition filenames.
    base = PurePosixPath(name.replace("\\", "/")).name
    # MIME header folding often leaves CR/LF inside the name; collapse those
    # before replacing other illegal FAT/HTTP characters so we keep spaces.
    base = _WHITESPACE_RUNS.sub(" ", base)
    base = _ILLEGAL_FILENAME_CHARS.sub("_", base).strip(" .")
    if not base or base in {".", ".."}:
        return None
    return base


# Back-compat alias used by API responses for already-queued rows.
_safe_filename = safe_filename


def _extension(filename: str) -> str:
    return PurePosixPath(filename).suffix.lower()


def is_allowed_filename(filename: str) -> bool:
    return _extension(filename) in ALLOWED_EXTENSIONS


def content_type_for(filename: str, declared: str | None = None) -> str:
    ext = _extension(filename)
    if ext in CONTENT_TYPES:
        return CONTENT_TYPES[ext]
    if declared:
        return declared.split(";", 1)[0].strip() or "application/octet-stream"
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def extract_attachments(raw_message: bytes | Message) -> list[ExtractedAttachment]:
    """Extract allowed book attachments from a raw RFC822 message or Message."""
    msg = email.message_from_bytes(raw_message) if isinstance(raw_message, (bytes, bytearray)) else raw_message
    found: list[ExtractedAttachment] = []

    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disposition = (part.get_content_disposition() or "").lower()
        filename = safe_filename(part.get_filename())
        if not filename:
            continue
        # Prefer explicit attachments; also accept inline parts with book extensions
        # (some providers set Content-Disposition: inline for .epub).
        if disposition not in {"attachment", "inline", ""} and not is_allowed_filename(filename):
            continue
        if not is_allowed_filename(filename):
            continue

        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        content = bytes(payload)
        if not content:
            continue

        digest = hashlib.sha256(content).hexdigest()
        found.append(
            ExtractedAttachment(
                filename=filename,
                content=content,
                sha256=digest,
                content_type=content_type_for(filename, part.get_content_type()),
            )
        )
    return found
