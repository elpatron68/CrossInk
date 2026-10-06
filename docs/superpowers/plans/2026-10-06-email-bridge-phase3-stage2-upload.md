# Email Bridge Phase 3 Stage 2 — Native Upload

Implemented alongside Stage 1/3 in `services/mail-bridge/`.

- `POST /v1/web/upload` (session cookie) accepts `.epub` / `.txt`
- Reuses `ItemStore.add_item` with `imap_uid=None`
- `UPLOAD_MAX_BYTES`, `UPLOAD_RATE_LIMIT_PER_HOUR`
- UI: drag/drop + file picker on the logged-in account panel
