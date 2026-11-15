#!/usr/bin/env bash
# Evaluate a local Ollama model end-to-end (same scenarios as CI) and the safety-invariant integration tests.
set -euo pipefail
cd "$(dirname "$0")/../.."
MODEL="${1:-finagent-ap}"
export FINAGENT__LLM__PROVIDER=ollama FINAGENT__LLM__PROVIDERS__OLLAMA__MODEL="$MODEL"
finagent eval || true
pytest -m llm tests/integration -q
