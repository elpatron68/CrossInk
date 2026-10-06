# Email Bridge Phase 3 Stage 3 — Calibre Conversion

Implemented alongside Stage 1/2 in `services/mail-bridge/`.

- Docker image installs Calibre (`ebook-convert`)
- Upload accepts `.mobi` / `.azw3` / `.docx` when `CONVERT_ENABLED=true`
- Converted EPUB is queued; failures return 422 without leaving source pending
- PDF remains rejected
