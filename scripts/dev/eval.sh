#!/usr/bin/env bash
# Run every scenario and print pass/fail (exit 1 on any failure). Extra args: --cases FIN-001 FIN-003 | --json
set -euo pipefail
cd "$(dirname "$0")/../.."
.venv/bin/finagent eval "$@"
