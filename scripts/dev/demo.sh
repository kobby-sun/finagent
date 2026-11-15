#!/usr/bin/env bash
# Successful approval flow (FIN-001) + exception flow (FIN-003) transcript on a throwaway data dir.
set -euo pipefail
cd "$(dirname "$0")/../.."
export FINAGENT__APP__DATA_DIR="$(mktemp -d)"
.venv/bin/finagent demo | tee docs/samples/demo_transcript.txt
