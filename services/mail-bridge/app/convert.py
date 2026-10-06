from __future__ import annotations

import hashlib
import logging
import subprocess
import tempfile
from pathlib import Path

from .mime_extract import CONTENT_TYPES, safe_filename

LOG = logging.getLogger("mail-bridge.convert")

# Stage 2 native formats.
NATIVE_EXTENSIONS = {".epub", ".txt"}
# Stage 3 convertible formats.
CONVERT_EXTENSIONS = {".mobi", ".azw3", ".docx"}
UPLOAD_EXTENSIONS = NATIVE_EXTENSIONS | CONVERT_EXTENSIONS

CONVERT_CONTENT_HINT = {
    ".mobi": "application/x-mobipocket-ebook",
    ".azw3": "application/vnd.amazon.ebook",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class ConvertError(Exception):
    """ebook-convert failed or produced no output."""


def extension_of(filename: str) -> str:
    name = safe_filename(filename) or filename
    lower = name.lower()
    for ext in sorted(UPLOAD_EXTENSIONS | {".pdf"}, key=len, reverse=True):
        if lower.endswith(ext):
            return ext
    return Path(lower).suffix.lower()


def content_type_for(ext: str) -> str:
    if ext in CONTENT_TYPES:
        return CONTENT_TYPES[ext]
    return CONVERT_CONTENT_HINT.get(ext, "application/octet-stream")


def convert_to_epub(
    content: bytes,
    *,
    source_filename: str,
    timeout_seconds: int,
) -> tuple[bytes, str]:
    """Run Calibre ebook-convert. Returns (epub_bytes, epub_filename)."""
    src_name = safe_filename(source_filename) or "book"
    ext = extension_of(src_name)
    if ext not in CONVERT_EXTENSIONS:
        raise ConvertError(f"Unsupported conversion source: {ext}")
    stem = Path(src_name).stem or "book"
    epub_name = f"{stem}.epub"

    with tempfile.TemporaryDirectory(prefix="ci-convert-") as tmp:
        tmp_path = Path(tmp)
        src_path = tmp_path / f"in{ext}"
        out_path = tmp_path / "out.epub"
        src_path.write_bytes(content)
        try:
            completed = subprocess.run(
                ["ebook-convert", str(src_path), str(out_path)],
                capture_output=True,
                text=True,
                timeout=max(5, timeout_seconds),
                check=False,
            )
        except FileNotFoundError as exc:
            raise ConvertError("ebook-convert not installed on this host") from exc
        except subprocess.TimeoutExpired as exc:
            raise ConvertError("Conversion timed out") from exc
        if completed.returncode != 0 or not out_path.is_file():
            err = (completed.stderr or completed.stdout or "").strip()
            LOG.warning("ebook-convert failed rc=%s: %s", completed.returncode, err[:500])
            raise ConvertError(err or "Conversion failed")
        epub_bytes = out_path.read_bytes()
        if not epub_bytes:
            raise ConvertError("Conversion produced empty EPUB")
        return epub_bytes, epub_name


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
