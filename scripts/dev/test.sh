#!/usr/bin/env bash
# Stable tests (unit, contract, API, skill fixtures, scenarios). Model-dependent tests: scripts/dev/test_llm.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
.venv/bin/python -m pytest -q "$@"
