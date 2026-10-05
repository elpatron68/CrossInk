# Email Bridge Phase 2 — Multi-Tenant Pairing Design

Date: 2026-10-05  
Status: approved  
Scope: `services/mail-bridge/` only — CrossInk firmware unchanged

## Goal

Let each reader self-serve an account with a unique plus-alias mailbox address and a device Bearer token, without passwords. Mail addressed to that alias is queued only for that user. No P1 single-tenant / `DEVICE_TOKEN` compatibility — Phase 2 replaces that auth model.

## Decisions

| Topic | Choice |
| --- | --- |
| Onboarding | One-click pairing (approach A) |
| Auth for web | None — open signup |
| Device token | Generated in the browser; server stores SHA-256 only |
| Alias form | `{MAIL_LOCAL_PREFIX}+{mail_local}@{MAIL_DOMAIN}` |
| Default example | `bookbridge+<random>@hoerdle.de` |
| Legacy P1 token | Removed — no `DEVICE_TOKEN` env auth |
| Future | Username + passkey may extend later; schema must not block it |
| Firmware | No changes in Phase 2 |

## User flow

1. Open bridge web UI at `/`.
2. Click “Create account” / “Account anlegen”.
3. Browser generates a 32-byte token via `crypto.getRandomValues`, encodes as lowercase hex (64 chars).
4. `POST /v1/accounts` with body `{ "device_token": "<hex>" }`.
5. UI shows once:
   - Email alias to send books to
   - Device token (copy button)
   - Bridge base URL hint
6. User enters base URL + token in CrossInk Email Sync settings (existing UI).
7. Sync API behavior unchanged from the device’s perspective.

Token is never returned by the API. If the user loses it, they create a new account (or later add a new device once multi-device UI exists).

## Configuration

New `.env` keys (with defaults suitable for hoerdle.de):

```env
MAIL_LOCAL_PREFIX=bookbridge
MAIL_DOMAIN=hoerdle.de
# Optional length of mail_local (default 10)
MAIL_LOCAL_LENGTH=10
```

Remove `DEVICE_TOKEN` from `.env` / `.env.example`. Existing IMAP settings unchanged: one catch-all mailbox (`IMAP_USER`) receives all plus-alias mail.

Alias construction:

```
email = f"{MAIL_LOCAL_PREFIX}+{mail_local}@{MAIL_DOMAIN}"
```

`mail_local` is URL-safe lowercase alphanumeric, length `MAIL_LOCAL_LENGTH` (default 10), unique in `users.mail_local`.

## Data model

### `users`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | TEXT PK | UUID |
| `mail_local` | TEXT UNIQUE NOT NULL | Plus-tag only (no prefix/domain) |
| `created_at` | TEXT NOT NULL | ISO-8601 UTC |

### `devices`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | TEXT PK | UUID |
| `user_id` | TEXT NOT NULL FK → users | |
| `token_hash` | TEXT UNIQUE NOT NULL | SHA-256 hex of raw device token |
| `label` | TEXT | Optional; default `"default"` for first device |
| `created_at` | TEXT NOT NULL | |

Multiple rows per user are allowed (future multi-device / passkey path). Phase 2 UI creates exactly one device at signup.

### `items` (existing, tightened)

- `user_id` required; Phase 2 always writes the owning user’s UUID (no shared `default` tenant).
- Files stored under `$DATA_DIR/items/{user_id}/{id}` for isolation.
- Pending/list/content/ack filter strictly by authenticated `user_id`.

Migration: on startup, create `users` / `devices` tables. Pre-Phase-2 queue rows / files may be discarded or left unreachable — no compatibility path required.

## HTTP API

### `POST /v1/accounts` (public)

Request:

```json
{ "device_token": "<64 hex chars>" }
```

Validation:

- `device_token` required, lowercase hex, length 64 (reject otherwise with 400).
- Reject if `sha256(device_token)` already exists (409).

Behavior:

1. Generate unique `mail_local`.
2. Insert `users` row.
3. Insert `devices` row with `token_hash = sha256(device_token)`, `label = "default"`.
4. Return 201:

```json
{
  "user_id": "…",
  "mail_local": "a7f3c2d91e",
  "email": "bookbridge+a7f3c2d91e@hoerdle.de"
}
```

Never echo `device_token`.

### Existing device API (auth change only)

`Authorization: Bearer <token>`:

1. Look up `sha256(token)` in `devices` → `user_id`.
2. Else 401.

`GET /v1/pending`, `GET /v1/items/{id}/content`, `POST /v1/items/{id}/ack` scope all queries and file paths to that `user_id`.

`GET /v1/health` unchanged (no auth).

## Web UI

Static page served by FastAPI (e.g. `app/static/` + Jinja or plain HTML):

- Single screen: title, short explanation, primary button.
- On success: show `email` + client-held token + copy controls; warning that the token cannot be recovered.
- No login, no password fields.
- German + English copy acceptable as hardcoded strings for P2 (i18n optional later).

Token generation stays entirely in client JS.

## IMAP routing

On each message:

1. Collect candidate recipient headers: `Delivered-To`, `X-Original-To`, `To`, `Cc` (parse address lists).
2. For each address, if it matches `{prefix}+{local}@{domain}` (case-insensitive domain/local rules as appropriate), resolve `local` → `users.mail_local`.
3. First match wins; enqueue attachments with that `user_id`.
4. No match → log and skip (do not assign to any user).
5. Dedup remains `(imap_uid, sha256)` globally (or per user — prefer keep global unique index to avoid duplicate blobs across mistaken re-delivery).

Post-process (`\Seen` / move) unchanged.

## Security notes

- Open signup is intentional; plus-alias + hashed tokens limit abuse (not a spam relay).
- Tokens never stored or logged in plaintext.
- HTTPS termination is deployment’s responsibility (same as P1).
- No rate limiting required for P2 MVP; may add later if needed.
- Encryption at rest and E2E deferred (plan Phase 4).

## Out of scope (Phase 2)

- Password / session login
- Passkeys / WebAuthn
- Multi-device management UI
- SMTP ingest (Phase 3)
- Firmware changes
- Account deletion / token rotation UI (may add thin “create another device” later)

## Verification

1. Open `/` → create account → receive `bookbridge+…@hoerdle.de` and keep client token.
2. `GET /v1/pending` with that Bearer → empty list, 200.
3. Send EPUB to the alias → poll/pending shows item only for that token.
4. Other account’s token does not see the item.
5. Download + ack works; pending clears.
6. Invalid / short tokens rejected on signup; wrong Bearer → 401.
7. Existing unit tests updated (drop `DEVICE_TOKEN` fixtures); add tests for alias parse, account create, tenant isolation.

## Implementation sketch (for later plan)

- `app/config.py` — mail prefix/domain/length; remove `device_token`
- `app/store.py` — users/devices CRUD, hashed token lookup, per-user item paths
- `app/auth.py` — hash lookup only
- `app/accounts.py` or routes in `main.py` — `POST /v1/accounts`
- `app/imap_worker.py` — recipient → user routing
- `app/static/` + template — pairing UI
- `.env.example`, `README.md`, `CHANGELOG.md`, tests
