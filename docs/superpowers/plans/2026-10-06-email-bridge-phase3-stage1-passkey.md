# Email Bridge Phase 3 Stage 1 — Username + Passkey Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Optional username + WebAuthn passkey on open-signup accounts; web sessions; issue new device tokens.

**Architecture:** Extend `ItemStore` schema; add `webauthn` + cookie sessions; FastAPI web routes under `/v1/web/*`; update pairing UI.

**Tech Stack:** FastAPI, SQLite, `webauthn` (py_webauthn), pytest/httpx

---

### Task 1: Dependencies + settings

Add `webauthn` to requirements; env `WEBAUTHN_RP_ID`, `WEBAUTHN_ORIGIN`, `WEB_SESSION_DAYS`.

### Task 2: Store migrations + methods

username column; webauthn_credentials; web_sessions; create_session; get_session_user; delete_session; set_username; add/list credentials; update sign_count; create_device_for_user; get_user_profile.

### Task 3: WebAuthn + session helpers

`app/web_auth.py` — challenge cache, register/login options+verify wrappers, cookie name `ci_bridge_session`.

### Task 4: API routes

Bootstrap, passkey register/login, me, devices, logout.

### Task 5: UI

Multi-view: create, secure account, login, logged-in dashboard.

### Task 6: Tests

Username validation; session bootstrap; devices; passkey roundtrip with mocked verify.

### Task 7: Docs (.env.example, README)
