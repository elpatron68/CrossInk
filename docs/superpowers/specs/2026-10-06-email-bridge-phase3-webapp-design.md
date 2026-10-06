# Email Bridge Phase 3 — Web App (Passkey, Upload, Convert)

Date: 2026-10-06  
Status: approved (plan decisions locked)  
Scope: `services/mail-bridge/` only — CrossInk firmware unchanged

## Goal

Extend the pairing web app so users can optionally reclaim an account with
username + passkey, upload books into the same device queue, and convert a
small set of incompatible formats to EPUB.

## Decisions

| Topic | Choice |
| --- | --- |
| Staging | 1 Auth → 2 native upload → 3 convert |
| Open signup | Remains (one-click + device token) |
| Passkey | Optional; ties username + WebAuthn credential to existing user |
| Upload (stage 2) | `.epub` / `.txt` only |
| Convert (stage 3) | `.mobi` / `.azw3` / `.docx` → EPUB; **no PDF** |
| Queue | Same `items` table; device still uses Bearer `/v1/pending` |
| Sessions | SQLite `web_sessions` + HttpOnly cookie |
| Convert tool | Calibre `ebook-convert` in the Docker image |

## Stage 1 — Username + Passkey

### Schema

`users.username` TEXT UNIQUE NULL (`^[a-z0-9_]{3,32}$`).

`webauthn_credentials`: id, user_id, credential_id, public_key, sign_count, created_at.

`web_sessions`: id, user_id, created_at, expires_at (default TTL 14 days).

### Flows

1. Open signup unchanged (`POST /v1/accounts`); response may establish a short-lived web session so the user can optionally register a passkey immediately.
2. Or bootstrap: `POST /v1/web/session/bootstrap` with `{user_id, device_token}` proves ownership and sets the session cookie.
3. Register: options → browser create → verify (sets username once).
4. Login: options by username → browser get → verify → session cookie.
5. Logged-in: `GET /v1/web/me`, `POST /v1/web/devices` (emit new device token once), `POST /v1/web/logout`.

### Config

```env
WEBAUTHN_RP_ID=ci-bridge.elpatron.me
WEBAUTHN_ORIGIN=https://ci-bridge.elpatron.me
WEB_SESSION_DAYS=14
```

## Stage 2 — Native upload

`POST /v1/web/upload` (multipart, session required). Extensions `.epub`/`.txt`.
`UPLOAD_MAX_BYTES` (default 80MB). `UPLOAD_RATE_LIMIT_PER_HOUR` (default 30 per user).
Calls `ItemStore.add_item` with `imap_uid=None`.

## Stage 3 — Conversion

Accept `.mobi`/`.azw3`/`.docx` on upload; run `ebook-convert` to EPUB; enqueue EPUB only.
On failure return 422; do not leave source as pending.
`CONVERT_ENABLED=true`, `CONVERT_TIMEOUT_SECONDS=120`.

## Out of scope

- Firmware changes
- PDF conversion
- Full multi-device revoke UI (only “issue new device token”)
- Replacing open signup
