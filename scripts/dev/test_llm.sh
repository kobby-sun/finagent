#!/usr/bin/env bash
# Model-dependent integration tests against a REAL model. Usage: scripts/dev/test_llm.sh ollama|anthropic|bedrock
set -euo pipefail
cd "$(dirname "$0")/../.."
export FINAGENT__LLM__PROVIDER="${1:-ollama}"
.venv/bin/python -m pytest -m llm tests/integration -q
