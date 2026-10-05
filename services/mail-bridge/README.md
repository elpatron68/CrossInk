# CrossInk Mail Bridge

HTTP bridge that turns emailed book attachments into a device-friendly download queue.

CrossInk never talks IMAP. The bridge polls a mailbox, extracts `.epub` / `.txt`
attachments, and exposes them over a small Bearer-token API.

Firmware side: Settings → Email Sync (on-device or device web portal) stores
bridge URL + device token; Network → Email Sync pulls pending books to
`/Books/Email/` by default.

## Quick start

```bash
cd services/mail-bridge
cp .env.example .env
# Edit DEVICE_TOKEN, IMAP_* …
docker compose up --build -d
curl -s http://localhost:8080/v1/health
```

Generate a device token (Phase 1):

```bash
openssl rand -hex 32
```

Put the same value in `.env` as `DEVICE_TOKEN` and in the reader’s Email Sync settings.

### Local without Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
./start-local.sh
# or: uvicorn app.main:app --host 0.0.0.0 --port 8080
```

On WSL2, the X3/X4 on the LAN typically needs a Windows `netsh interface portproxy`
from the host LAN IP `:8080` to the WSL IP `:8080`, and the device Bridge URL should
use that Windows LAN address (not the WSL-only IP).

## API

Auth for all routes except health: `Authorization: Bearer <DEVICE_TOKEN>`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/v1/health` | Liveness (no auth) |
| `GET` | `/v1/pending` | List pending items (triggers IMAP poll) |
| `GET` | `/v1/items/{id}/content` | Stream attachment bytes |
| `POST` | `/v1/items/{id}/ack` | Mark delivered (idempotent) |

Pending item shape:

```json
{
  "id": "…",
  "filename": "book.epub",
  "bytes": 12345,
  "sha256": "…",
  "content_type": "application/epub+zip",
  "received_at": "2026-10-05T19:00:00+00:00"
}
```

Attachment filenames are sanitized on ingest (CR/LF from MIME header folding,
path components, FAT-illegal characters) so `Content-Disposition` stays a valid
HTTP header and the device can write the file to SD.

### curl smoke

```bash
TOKEN=your-device-token
BASE=http://localhost:8080

curl -s "$BASE/v1/health"
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/v1/pending"
# Pick an id from pending, then:
curl -s -H "Authorization: Bearer $TOKEN" -o book.epub "$BASE/v1/items/$ID/content"
curl -s -X POST -H "Authorization: Bearer $TOKEN" "$BASE/v1/items/$ID/ack"
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/v1/pending"
```

## Tests

```bash
pip install -r requirements.txt
pytest -q
```

## Phase notes

- **P1 (current):** single shared IMAP mailbox + one `DEVICE_TOKEN` from `.env`.
- **P2 (designed, not implemented):** open one-click pairing web UI, hashed
  per-device tokens, plus-alias routing
  (`{MAIL_LOCAL_PREFIX}+{mail_local}@{MAIL_DOMAIN}`, e.g. `bookbridge+…@hoerdle.de`).
  Spec: [`docs/superpowers/specs/2026-10-05-email-bridge-phase2-design.md`](../../docs/superpowers/specs/2026-10-05-email-bridge-phase2-design.md).
  P2 removes `DEVICE_TOKEN` env auth (no P1 compatibility).
- **P3:** optional SMTP ingest into the same queue.
