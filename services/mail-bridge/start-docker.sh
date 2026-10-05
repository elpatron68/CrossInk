#!/usr/bin/env bash
# Build and run the mail-bridge via Docker Compose (works in WSL2 with Docker Desktop / docker-ce).
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "Missing .env — copy .env.example and fill IMAP_* / MAIL_* first." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon not reachable. In WSL: start Docker Desktop or: sudo service docker start" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a
PORT="${PORT:-8080}"

docker compose up --build -d
echo "Waiting for health…"
for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${PORT}/v1/health" >/dev/null; then
    curl -s "http://127.0.0.1:${PORT}/v1/health"
    echo
    echo "Pairing UI: http://127.0.0.1:${PORT}/"
    exit 0
  fi
  sleep 1
done
echo "Bridge did not become healthy in time. Try: docker compose logs -f" >&2
exit 1
