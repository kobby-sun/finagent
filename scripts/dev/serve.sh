#!/usr/bin/env bash
# Start the API (Swagger UI at http://localhost:${PORT:-8080}/docs).
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a; [ -f .env ] && . ./.env; set +a
exec .venv/bin/finagent serve --host "${HOST:-127.0.0.1}" --port "${PORT:-8080}"
