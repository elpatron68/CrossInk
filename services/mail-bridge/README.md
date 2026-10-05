# CrossInk Mail Bridge

HTTP bridge that turns emailed book attachments into a device-friendly download queue.

CrossInk never talks IMAP. The bridge polls a catch-all mailbox, routes mail by
plus-alias to the matching account, extracts `.epub` / `.txt` attachments, and
exposes them over a Bearer-token API.

## Quick start

```bash
cd services/mail-bridge
cp .env.example .env
# Edit MAIL_* and IMAP_* …
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./start-local.sh
```

Open `http://localhost:8080/` → **Create account**. The browser generates the
device token; copy the email alias + token into the reader
(Settings → Email Sync).

Docker:

```bash
docker compose up --build -d
curl -s http://localhost:8080/v1/health
```

On WSL2, devices on the LAN typically need a Windows `netsh interface portproxy`
from the host LAN IP `:8080` to the WSL IP `:8080`. Use that Windows LAN address
as the Bridge URL on the reader.

## API

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/` | no | Pairing web UI |
| `GET` | `/v1/health` | no | Liveness |
| `POST` | `/v1/accounts` | no | Create account (`device_token` from browser) |
| `GET` | `/v1/pending` | Bearer | List pending items (triggers IMAP poll) |
| `GET` | `/v1/items/{id}/content` | Bearer | Stream attachment bytes |
| `POST` | `/v1/items/{id}/ack` | Bearer | Mark delivered (idempotent) |

`POST /v1/accounts` body:

```json
{ "device_token": "<64 lowercase hex chars>" }
```

Response (201) — token is never echoed:

```json
{
  "user_id": "…",
  "mail_local": "a7f3c2d91e",
  "email": "bookbridge+a7f3c2d91e@hoerdle.de"
}
```

Alias form: `{MAIL_LOCAL_PREFIX}+{mail_local}@{MAIL_DOMAIN}`.

Retention / cleanup (see `.env.example`):

- `POST_PROCESS=delete` (default) removes matched mail after queueing; `move` / `seen` remain available
- Unknown aliases and mails without allowed attachments are always deleted from IMAP
- `DELETE_ON_ACK=true` removes the stored file after the device acks
- `ORPHAN_RETENTION_DAYS=14` purges pending items never downloaded (0 disables)
- `ACCOUNT_UNUSED_DAYS=7` deletes accounts that never synced (0 disables)
- `ACCOUNT_INACTIVE_DAYS=365` deletes accounts with no sync and no received mail for that long (0 disables)
- `SIGNUP_RATE_LIMIT_PER_HOUR=5` caps `POST /v1/accounts` per client IP per rolling hour (429 when exceeded; 0 disables)
- `TRUST_PROXY=true` uses `X-Forwarded-For` / `X-Real-IP` for that IP when the bridge sits behind nginx or similar
- Optional `PLAUSIBLE_DOMAIN` injects Plausible Analytics into the pairing page

### curl smoke

```bash
TOKEN=$(openssl rand -hex 32)
BASE=http://localhost:8080

curl -s -X POST "$BASE/v1/accounts" -H 'Content-Type: application/json' \
  -d "{\"device_token\":\"$TOKEN\"}"
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/v1/pending"
```

## Tests

```bash
pip install -r requirements.txt
pytest -q
```

## Phase notes

- **P2 (current):** open one-click pairing, hashed device tokens, plus-alias IMAP routing.
  Design: [`docs/superpowers/specs/2026-10-05-email-bridge-phase2-design.md`](../../docs/superpowers/specs/2026-10-05-email-bridge-phase2-design.md).
- **P3:** optional SMTP ingest into the same queue.
