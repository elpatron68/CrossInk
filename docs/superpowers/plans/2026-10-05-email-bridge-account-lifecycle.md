# Email Bridge Account Lifecycle & Signup Rate Limit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Staged unused/inactive account purge (7 / 365 days) plus per-IP signup rate limiting behind nginx Proxy Manager, with pairing-UI warnings.

**Architecture:** Track `devices.last_seen_at` on Bearer auth; purge never-seen users after 7 days and inactive users after 365 days on the existing orphan-cleanup tick; in-memory sliding-window limiter on `POST /v1/accounts` using `X-Forwarded-For` when `TRUST_PROXY=true`.

**Tech Stack:** FastAPI, SQLite, plain HTML/JS pairing UI, pytest/httpx (no new dependencies).

**Spec:** `docs/superpowers/specs/2026-10-05-email-bridge-account-lifecycle-design.md`

## Global Constraints

- Scope is `services/mail-bridge/` only — no firmware changes
- `ACCOUNT_UNUSED_DAYS=7`, `ACCOUNT_INACTIVE_DAYS=365` (0 disables stage)
- Signup only: `SIGNUP_RATE_LIMIT_PER_HOUR=5` (0 disables)
- `TRUST_PROXY=true` by default; client IP from first `X-Forwarded-For` entry, else `X-Real-IP`, else peer
- Account purge runs on `ORPHAN_CLEANUP_SECONDS` tick
- EN/DE pairing UI must mention 7 / 365 retention
- Sync endpoints (`/v1/pending`, content, ack) stay unlimited

## File map

| File | Responsibility |
| --- | --- |
| `app/config.py` | New env settings |
| `app/store.py` | `last_seen_at` migration, touch, purge accounts |
| `app/auth.py` | Touch device on successful auth |
| `app/rate_limit.py` | In-memory signup limiter + client IP helper |
| `app/cleanup.py` | Call account purge alongside orphans |
| `app/main.py` | Apply rate limit on create account |
| `app/static/index.html` | Retention + 429 copy |
| `tests/test_account_lifecycle.py` | Purge + last_seen tests |
| `tests/test_rate_limit.py` | IP / 429 tests |
| `.env.example`, `README.md`, `CHANGELOG.md` | Docs |

---

### Task 1: Config settings

**Files:**
- Modify: `services/mail-bridge/app/config.py`
- Modify: `services/mail-bridge/.env.example`

**Interfaces:**
- Produces: `Settings.account_unused_days: int`, `account_inactive_days: int`, `signup_rate_limit_per_hour: int`, `trust_proxy: bool`

- [ ] **Step 1: Add settings fields**

In `Settings` (after `delete_on_ack`), add:

```python
    account_unused_days: int = Field(default=7, validation_alias="ACCOUNT_UNUSED_DAYS")
    account_inactive_days: int = Field(default=365, validation_alias="ACCOUNT_INACTIVE_DAYS")
    signup_rate_limit_per_hour: int = Field(default=5, validation_alias="SIGNUP_RATE_LIMIT_PER_HOUR")
    trust_proxy: bool = Field(default=True, validation_alias="TRUST_PROXY")
```

- [ ] **Step 2: Document in `.env.example`**

Append:

```env
# Unused accounts (never synced) older than N days are deleted (0 disables).
ACCOUNT_UNUSED_DAYS=7
# Accounts with no sync and no mail for M days are deleted (0 disables).
ACCOUNT_INACTIVE_DAYS=365

# Max POST /v1/accounts per client IP per rolling hour (0 disables).
SIGNUP_RATE_LIMIT_PER_HOUR=5
# When true (nginx Proxy Manager), client IP from X-Forwarded-For / X-Real-IP.
TRUST_PROXY=true
```

- [ ] **Step 3: Commit**

```bash
git add services/mail-bridge/app/config.py services/mail-bridge/.env.example
git commit -m "feat(mail-bridge): add account lifecycle and rate-limit settings"
```

---

### Task 2: Store — last_seen + account purge

**Files:**
- Modify: `services/mail-bridge/app/store.py`
- Create: `services/mail-bridge/tests/test_account_lifecycle.py`

**Interfaces:**
- Produces:
  - `ItemStore.touch_device_for_token(device_token: str) -> None`
  - `ItemStore.purge_unused_accounts(*, older_than_days: int) -> int`
  - `ItemStore.purge_inactive_accounts(*, older_than_days: int) -> int`
  - `ItemStore.delete_user(user_id: str) -> bool` (internal helper OK if used by both purges)

- [ ] **Step 1: Write failing tests**

Create `services/mail-bridge/tests/test_account_lifecycle.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.store import ItemStore


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


def test_touch_device_sets_last_seen(tmp_path: Path) -> None:
    store = ItemStore(tmp_path / "q.sqlite3", tmp_path / "items")
    token = "a" * 64
    store.create_account(token)
    store.touch_device_for_token(token)
    with store._conn() as conn:
        row = conn.execute("SELECT last_seen_at FROM devices").fetchone()
    assert row["last_seen_at"]


def test_purge_unused_accounts(tmp_path: Path) -> None:
    store = ItemStore(tmp_path / "q.sqlite3", tmp_path / "items")
    old = store.create_account("b" * 64)
    keep = store.create_account("c" * 64)
    with store._conn() as conn:
        conn.execute("UPDATE users SET created_at = ? WHERE id = ?", (_iso_days_ago(10), old.user_id))
    assert store.purge_unused_accounts(older_than_days=7) == 1
    assert store.user_id_for_token("b" * 64) is None
    assert store.user_id_for_token("c" * 64) == keep.user_id


def test_purge_unused_skips_touched(tmp_path: Path) -> None:
    store = ItemStore(tmp_path / "q.sqlite3", tmp_path / "items")
    token = "d" * 64
    account = store.create_account(token)
    with store._conn() as conn:
        conn.execute("UPDATE users SET created_at = ? WHERE id = ?", (_iso_days_ago(10), account.user_id))
    store.touch_device_for_token(token)
    assert store.purge_unused_accounts(older_than_days=7) == 0


def test_purge_inactive_accounts(tmp_path: Path) -> None:
    store = ItemStore(tmp_path / "q.sqlite3", tmp_path / "items")
    token = "e" * 64
    account = store.create_account(token)
    store.touch_device_for_token(token)
    with store._conn() as conn:
        conn.execute(
            "UPDATE devices SET last_seen_at = ? WHERE user_id = ?",
            (_iso_days_ago(400), account.user_id),
        )
        conn.execute("UPDATE users SET created_at = ? WHERE id = ?", (_iso_days_ago(400), account.user_id))
    item = store.add_item(
        filename="old.epub",
        content=b"x",
        sha256="sha",
        content_type="application/epub+zip",
        imap_uid="1",
        user_id=account.user_id,
        received_at=_iso_days_ago(400),
    )
    assert store.purge_inactive_accounts(older_than_days=365) == 1
    assert store.user_id_for_token(token) is None
    assert not item.path.exists()


def test_mail_activity_blocks_inactive_purge(tmp_path: Path) -> None:
    store = ItemStore(tmp_path / "q.sqlite3", tmp_path / "items")
    token = "f" * 64
    account = store.create_account(token)
    store.touch_device_for_token(token)
    with store._conn() as conn:
        conn.execute(
            "UPDATE devices SET last_seen_at = ? WHERE user_id = ?",
            (_iso_days_ago(400), account.user_id),
        )
        conn.execute("UPDATE users SET created_at = ? WHERE id = ?", (_iso_days_ago(400), account.user_id))
    store.add_item(
        filename="fresh.epub",
        content=b"y",
        sha256="sha2",
        content_type="application/epub+zip",
        imap_uid="2",
        user_id=account.user_id,
        received_at=_iso_days_ago(1),
    )
    assert store.purge_inactive_accounts(older_than_days=365) == 0
    assert store.user_id_for_token(token) == account.user_id
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `cd services/mail-bridge && .venv/bin/python -m pytest tests/test_account_lifecycle.py -q`

Expected: FAIL (`touch_device_for_token` / purge methods missing)

- [ ] **Step 3: Implement schema migration + methods in `store.py`**

After creating `devices` table in `_init_schema`, ensure column exists:

```python
            cols = {row[1] for row in conn.execute("PRAGMA table_info(devices)").fetchall()}
            if "last_seen_at" not in cols:
                conn.execute("ALTER TABLE devices ADD COLUMN last_seen_at TEXT")
```

Add methods:

```python
    def touch_device_for_token(self, device_token: str) -> None:
        token_hash = hash_device_token(device_token.strip())
        now = _utc_now_iso()
        with self._conn() as conn:
            conn.execute(
                "UPDATE devices SET last_seen_at = ? WHERE token_hash = ?",
                (now, token_hash),
            )

    def delete_user(self, user_id: str) -> bool:
        """Remove user, devices, items rows and on-disk files. Returns False if user missing."""
        user_dir = self.items_dir / user_id
        with self._conn() as conn:
            if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
                return False
            rows = conn.execute("SELECT path FROM items WHERE user_id = ?", (user_id,)).fetchall()
            for row in rows:
                path = Path(row["path"])
                try:
                    if path.is_file():
                        path.unlink()
                except OSError:
                    pass
            conn.execute("DELETE FROM items WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM devices WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        if user_dir.is_dir():
            for child in user_dir.iterdir():
                try:
                    if child.is_file():
                        child.unlink()
                except OSError:
                    pass
            try:
                user_dir.rmdir()
            except OSError:
                pass
        return True

    def purge_unused_accounts(self, *, older_than_days: int) -> int:
        if older_than_days <= 0:
            return 0
        cutoff = datetime.now(timezone.utc).timestamp() - (older_than_days * 86400)
        removed = 0
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT u.id, u.created_at
                FROM users u
                WHERE NOT EXISTS (
                    SELECT 1 FROM devices d
                    WHERE d.user_id = u.id AND d.last_seen_at IS NOT NULL AND d.last_seen_at != ''
                )
                """
            ).fetchall()
            victims = []
            for row in rows:
                try:
                    created = datetime.fromisoformat(row["created_at"])
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    if created.timestamp() <= cutoff:
                        victims.append(row["id"])
                except ValueError:
                    continue
        for user_id in victims:
            if self.delete_user(user_id):
                removed += 1
        return removed

    def purge_inactive_accounts(self, *, older_than_days: int) -> int:
        if older_than_days <= 0:
            return 0
        cutoff = datetime.now(timezone.utc).timestamp() - (older_than_days * 86400)
        removed = 0
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT u.id, u.created_at,
                       (SELECT MAX(d.last_seen_at) FROM devices d WHERE d.user_id = u.id) AS last_seen,
                       (SELECT MAX(i.received_at) FROM items i WHERE i.user_id = u.id) AS last_mail
                FROM users u
                WHERE EXISTS (
                    SELECT 1 FROM devices d
                    WHERE d.user_id = u.id AND d.last_seen_at IS NOT NULL AND d.last_seen_at != ''
                )
                """
            ).fetchall()
            victims = []
            for row in rows:
                stamps = [row["created_at"], row["last_seen"], row["last_mail"]]
                activity = None
                for stamp in stamps:
                    if not stamp:
                        continue
                    try:
                        dt = datetime.fromisoformat(stamp)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if activity is None or dt > activity:
                            activity = dt
                    except ValueError:
                        continue
                if activity is not None and activity.timestamp() <= cutoff:
                    victims.append(row["id"])
        for user_id in victims:
            if self.delete_user(user_id):
                removed += 1
        return removed
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd services/mail-bridge && .venv/bin/python -m pytest tests/test_account_lifecycle.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/mail-bridge/app/store.py services/mail-bridge/tests/test_account_lifecycle.py
git commit -m "feat(mail-bridge): purge unused and inactive accounts"
```

---

### Task 3: Touch last_seen on auth + cleanup worker

**Files:**
- Modify: `services/mail-bridge/app/auth.py`
- Modify: `services/mail-bridge/app/cleanup.py`

**Interfaces:**
- Consumes: `ItemStore.touch_device_for_token`, `purge_unused_accounts`, `purge_inactive_accounts`
- Consumes: `Settings.account_unused_days`, `account_inactive_days`

- [ ] **Step 1: Update `require_device_token` to touch device**

In `auth.py`, after resolving `user_id`:

```python
    try:
        store.touch_device_for_token(token)
    except Exception:
        # Best-effort; never block sync on activity tracking.
        pass
    return user_id
```

Prefer logging with the existing logger if `auth` already logs; otherwise keep silent `pass` only if adding a logger is louder than needed — prefer:

```python
import logging
LOG = logging.getLogger("mail-bridge.auth")
...
    try:
        store.touch_device_for_token(token)
    except Exception:
        LOG.exception("Failed to touch last_seen_at")
```

- [ ] **Step 2: Extend `OrphanCleanupWorker.run_once`**

```python
    def run_once(self) -> dict[str, int]:
        counts = {
            "orphans": 0,
            "unused_accounts": 0,
            "inactive_accounts": 0,
        }
        if self.settings.orphan_retention_days > 0:
            counts["orphans"] = self.store.purge_orphans(
                older_than_days=self.settings.orphan_retention_days
            )
            if counts["orphans"]:
                LOG.info("Purged %d orphan pending item(s)", counts["orphans"])
        if self.settings.account_unused_days > 0:
            counts["unused_accounts"] = self.store.purge_unused_accounts(
                older_than_days=self.settings.account_unused_days
            )
            if counts["unused_accounts"]:
                LOG.info("Purged %d unused account(s)", counts["unused_accounts"])
        if self.settings.account_inactive_days > 0:
            counts["inactive_accounts"] = self.store.purge_inactive_accounts(
                older_than_days=self.settings.account_inactive_days
            )
            if counts["inactive_accounts"]:
                LOG.info("Purged %d inactive account(s)", counts["inactive_accounts"])
        return counts
```

Update `start()` log line to mention account retention days. Worker still starts when orphan retention is 0 **if** either account stage is > 0:

```python
    def start(self) -> None:
        if (
            self.settings.orphan_retention_days <= 0
            and self.settings.account_unused_days <= 0
            and self.settings.account_inactive_days <= 0
        ):
            LOG.info("Cleanup worker disabled")
            return
        # ... existing thread start ...
```

- [ ] **Step 3: Run related tests**

Run: `cd services/mail-bridge && .venv/bin/python -m pytest tests/test_account_lifecycle.py tests/test_orphans.py tests/test_api.py -q`

Expected: PASS (api still works; touch is side effect)

- [ ] **Step 4: Commit**

```bash
git add services/mail-bridge/app/auth.py services/mail-bridge/app/cleanup.py
git commit -m "feat(mail-bridge): touch last_seen and purge accounts on cleanup tick"
```

---

### Task 4: Signup rate limiter

**Files:**
- Create: `services/mail-bridge/app/rate_limit.py`
- Modify: `services/mail-bridge/app/main.py`
- Create: `services/mail-bridge/tests/test_rate_limit.py`

**Interfaces:**
- Produces:
  - `client_ip(request: Request, *, trust_proxy: bool) -> str`
  - `class SignupRateLimiter` with `allow(ip: str, *, limit_per_hour: int) -> bool` (`True` = allowed)
- Consumes: `Settings.signup_rate_limit_per_hour`, `trust_proxy`

- [ ] **Step 1: Write failing tests**

Create `services/mail-bridge/tests/test_rate_limit.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.rate_limit import SignupRateLimiter, client_ip
from app.store import ItemStore
from starlette.requests import Request


def test_sliding_window_blocks_sixth() -> None:
    limiter = SignupRateLimiter()
    for _ in range(5):
        assert limiter.allow("1.2.3.4", limit_per_hour=5) is True
    assert limiter.allow("1.2.3.4", limit_per_hour=5) is False
    assert limiter.allow("9.9.9.9", limit_per_hour=5) is True


def test_zero_limit_disables() -> None:
    limiter = SignupRateLimiter()
    for _ in range(20):
        assert limiter.allow("1.2.3.4", limit_per_hour=0) is True


def test_client_ip_trust_proxy_xff() -> None:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [(b"x-forwarded-for", b"203.0.113.10, 10.0.0.1")],
        "client": ("10.0.0.1", 12345),
        "server": ("test", 80),
    }
    request = Request(scope)
    assert client_ip(request, trust_proxy=True) == "203.0.113.10"
    assert client_ip(request, trust_proxy=False) == "10.0.0.1"


def test_accounts_endpoint_rate_limited(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        signup_rate_limit_per_hour=2,
        trust_proxy=True,
    )
    store = ItemStore(settings.db_path, settings.items_dir)
    app = create_app(settings=settings, store=store)
    with TestClient(app) as client:
        headers = {"X-Forwarded-For": "198.51.100.7"}
        assert client.post("/v1/accounts", json={"device_token": "1" * 64}, headers=headers).status_code == 201
        assert client.post("/v1/accounts", json={"device_token": "2" * 64}, headers=headers).status_code == 201
        r = client.post("/v1/accounts", json={"device_token": "3" * 64}, headers=headers)
        assert r.status_code == 429
        assert "rate" in r.json()["detail"].lower()
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `cd services/mail-bridge && .venv/bin/python -m pytest tests/test_rate_limit.py -q`

Expected: FAIL (module missing)

- [ ] **Step 3: Implement `app/rate_limit.py`**

```python
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from starlette.requests import Request


def client_ip(request: Request, *, trust_proxy: bool) -> str:
    if trust_proxy:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            first = xff.split(",")[0].strip()
            if first:
                return first
        real_ip = (request.headers.get("x-real-ip") or "").strip()
        if real_ip:
            return real_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


class SignupRateLimiter:
    """Process-local sliding 1-hour window keyed by client IP."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, ip: str, *, limit_per_hour: int) -> bool:
        if limit_per_hour <= 0:
            return True
        now = time.monotonic()
        window = 3600.0
        with self._lock:
            q = self._hits[ip]
            while q and (now - q[0]) > window:
                q.popleft()
            if len(q) >= limit_per_hour:
                return False
            q.append(now)
            return True
```

- [ ] **Step 4: Wire into `create_account` in `main.py`**

At app creation:

```python
from .rate_limit import SignupRateLimiter, client_ip

    rate_limiter = SignupRateLimiter()
    app.state.rate_limiter = rate_limiter
```

In `create_account` handler, before `store.create_account`:

```python
        ip = client_ip(request, trust_proxy=settings.trust_proxy)
        if not rate_limiter.allow(ip, limit_per_hour=settings.signup_rate_limit_per_hour):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )
```

Add `request: Request` parameter to the handler (FastAPI injects it).

- [ ] **Step 5: Run tests — expect PASS**

Run: `cd services/mail-bridge && .venv/bin/python -m pytest tests/test_rate_limit.py tests/test_api.py -q`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/mail-bridge/app/rate_limit.py services/mail-bridge/app/main.py services/mail-bridge/tests/test_rate_limit.py
git commit -m "feat(mail-bridge): rate-limit account signup per IP"
```

---

### Task 5: Pairing UI copy + docs verify

**Files:**
- Modify: `services/mail-bridge/app/static/index.html`
- Modify: `services/mail-bridge/README.md`
- Modify: `CHANGELOG.md`
- Modify: `.todo.md` (local only; gitignored — update if present)

**Interfaces:**
- Consumes: HTTP 429 detail from Task 4

- [ ] **Step 1: Update EN/DE strings in `index.html`**

Add keys (and bind a visible hint near the create button / result):

```javascript
        retention:
          "Accounts that never sync are removed after 7 days. Accounts with no sync and no mail for 365 days are removed.",
        errorRateLimit: "Too many accounts from this network. Try again in an hour.",
```

German:

```javascript
        retention:
          "Accounts ohne Sync werden nach 7 Tagen gelöscht. Accounts ohne Sync und ohne Mail seit 365 Tagen ebenfalls.",
        errorRateLimit: "Zu viele Accounts aus diesem Netzwerk. Bitte in einer Stunde erneut versuchen.",
```

Show `retention` in a always-visible `<p class="hint" data-i18n="retention">`.

In the create click handler, if `res.status === 429`, throw/use `t.errorRateLimit` instead of generic error.

- [ ] **Step 2: README + CHANGELOG**

README: document unused/inactive account cleanup and signup rate limit / `TRUST_PROXY`.

CHANGELOG under Unreleased → Added:

```markdown
- Mail bridge: delete never-synced accounts after 7 days and inactive accounts after 365 days; rate-limit account creation per IP (proxy-aware).
```

- [ ] **Step 3: Full test suite**

Run: `cd services/mail-bridge && .venv/bin/python -m pytest -q`

Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add services/mail-bridge/app/static/index.html services/mail-bridge/README.md CHANGELOG.md
git commit -m "docs(mail-bridge): retention UI hint and lifecycle docs"
```

---

## Spec coverage checklist

| Spec requirement | Task |
| --- | --- |
| `devices.last_seen_at` + migration | Task 2 |
| Touch on Bearer auth | Task 3 |
| Unused purge 7d | Task 2–3 |
| Inactive purge 365d (mail counts) | Task 2 |
| Cascade delete files/rows | Task 2 `delete_user` |
| Cleanup on orphan interval | Task 3 |
| Signup rate limit 5/h/IP | Task 4 |
| `TRUST_PROXY` / XFF | Task 4 |
| UI retention + 429 copy | Task 5 |
| Config / README / CHANGELOG | Tasks 1, 5 |
| Tests listed in spec verification | Tasks 2, 4, 5 |

## Self-review notes

- No firmware tasks (explicit out of scope)
- `TRUST_PROXY` default `true` matches approved design / nginx PM
- Rate limiter process-local as specified
- Commit steps included for agentic workers; skip commits if the human asks to batch
