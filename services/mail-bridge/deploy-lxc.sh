#!/usr/bin/env bash
# Deploy mail-bridge sources to the LXC and rebuild the container.
# Never overwrites the server .env (WebAuthn RP_ID/ORIGIN live there).
set -euo pipefail
cd "$(dirname "$0")"
HOST="${DEPLOY_HOST:-root@10.0.0.14}"
REMOTE="${DEPLOY_PATH:-/opt/crossink-bridge}"

tar czf - \
  --exclude='.venv' \
  --exclude='data' \
  --exclude='.env' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='tests' \
  --exclude='*.pyc' \
  . | ssh -o BatchMode=yes "$HOST" "mkdir -p '$REMOTE' && tar xzf - -C '$REMOTE'"

ssh -o BatchMode=yes "$HOST" "set -e; cd '$REMOTE'; docker compose up --build -d; for i in \$(seq 1 60); do curl -sf http://127.0.0.1:8080/v1/health >/dev/null && break; sleep 2; done; curl -s http://127.0.0.1:8080/v1/health; echo"
