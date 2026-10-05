# Email Bridge Phase 2 Implementation Plan

> **For agentic workers:** Execute task-by-task. Steps use checkbox syntax.

**Goal:** Multi-tenant pairing for `services/mail-bridge/` per `docs/superpowers/specs/2026-10-05-email-bridge-phase2-design.md`.

**Architecture:** Browser generates device token; server stores SHA-256 in `devices`, creates `users.mail_local`, routes IMAP via plus-alias. No `DEVICE_TOKEN` env auth.

**Tech Stack:** FastAPI, SQLite, plain HTML/JS static UI, pytest/httpx.

**Spec:** `docs/superpowers/specs/2026-10-05-email-bridge-phase2-design.md`

## Global Constraints

- Firmware unchanged
- Token never in API responses
- Open signup, no password
- Alias `{MAIL_LOCAL_PREFIX}+{mail_local}@{MAIL_DOMAIN}`
- Remove `DEVICE_TOKEN`

### Task 1: Config + store (users/devices)

**Files:** `app/config.py`, `app/store.py`, `tests/test_store_accounts.py`

- [x] Remove `device_token` from Settings; add `mail_local_prefix`, `mail_domain`, `mail_local_length`
- [x] Schema `users` / `devices`; `create_account(token)`, `user_id_for_token(token)`, per-user item paths
- [x] Tests for create + hash lookup + isolation

### Task 2: Auth + accounts API

**Files:** `app/auth.py`, `app/main.py`, `tests/test_api.py`

- [x] Bearer → `sha256` device lookup
- [x] `POST /v1/accounts`
- [x] Update API tests (no DEVICE_TOKEN)

### Task 3: IMAP recipient routing

**Files:** `app/recipients.py`, `app/imap_worker.py`, `tests/test_recipients.py`

- [x] Parse Delivered-To / X-Original-To / To / Cc for plus-alias
- [x] Skip unmatched mail; enqueue with `user_id`

### Task 4: Pairing web UI

**Files:** `app/static/index.html`, `app/main.py`

- [x] `/` serves UI; browser token gen + POST accounts

### Task 5: Docs + verify

**Files:** `.env.example`, `README.md`, `CHANGELOG.md`

- [x] Drop DEVICE_TOKEN docs; document pairing
- [x] `pytest -q` green
