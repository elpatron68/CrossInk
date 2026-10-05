#!/usr/bin/env bash
# Start the mail-bridge on :8080 in the current network namespace (must be real WSL, not a sandbox).
set -euo pipefail
cd "$(dirname "$0")"
set -a
# shellcheck disable=SC1091
source .env
set +a
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
