#!/usr/bin/env bash
# One-time local setup: venv, pinned deps, mock data, index, contracts, skill docs. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/../.."
PY="${PYTHON:-$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3)}"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.lock
if [ "${SEMANTIC:-1}" = "1" ]; then   # semantic search (fine-tuned embedder); SEMANTIC=0 for a slim keyword-only install
  echo "installing semantic-search libraries (~700 MB, CPU PyTorch)..."
  case "$(uname -s)" in Linux) .venv/bin/pip install -q --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-semantic.lock ;;
                        *) .venv/bin/pip install -q -r requirements-semantic.lock ;; esac
fi
.venv/bin/pip install -q --no-deps -e .
[ -f .env ] || cp .env.example .env
.venv/bin/python scripts/generate_mock_data.py
.venv/bin/finagent ingest > /dev/null
.venv/bin/finagent workflow validate
echo "Ready. Activate with: source .venv/bin/activate"
