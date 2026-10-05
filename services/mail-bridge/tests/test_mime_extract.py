from __future__ import annotations

from email.message import EmailMessage

from app.mime_extract import extract_attachments, is_allowed_filename, safe_filename


def _message_with_attachment(filename: str, body: bytes, maintype: str, subtype: str) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = "Test book"
    msg["From"] = "sender@example.com"
    msg["To"] = "inbox@example.com"
    msg.set_content("See attachment.")
    msg.add_attachment(body, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes()


def test_extracts_epub_and_txt_only() -> None:
    epub = _message_with_attachment("Novel.epub", b"PK\x03\x04fake", "application", "epub+zip")
    items = extract_attachments(epub)
    assert len(items) == 1
    assert items[0].filename == "Novel.epub"
    assert items[0].content_type == "application/epub+zip"
    assert items[0].sha256

    txt = _message_with_attachment("notes.txt", b"hello", "text", "plain")
    items = extract_attachments(txt)
    assert len(items) == 1
    assert items[0].filename == "notes.txt"

    pdf = _message_with_attachment("scan.pdf", b"%PDF", "application", "pdf")
    assert extract_attachments(pdf) == []


def test_strips_path_components_from_filename() -> None:
    raw = _message_with_attachment("../../evil.epub", b"data", "application", "octet-stream")
    items = extract_attachments(raw)
    assert len(items) == 1
    assert items[0].filename == "evil.epub"


def test_collapses_folded_whitespace_in_filename() -> None:
    cleaned = safe_filename("Blind Spot (Blind Justice Book -\r\n Adam Zorzi.epub")
    assert cleaned == "Blind Spot (Blind Justice Book - Adam Zorzi.epub"
    assert cleaned is not None
    assert "\r" not in cleaned
    assert "\n" not in cleaned


def test_is_allowed_filename() -> None:
    assert is_allowed_filename("a.epub")
    assert is_allowed_filename("a.TXT")
    assert not is_allowed_filename("a.pdf")
    assert not is_allowed_filename("a")
