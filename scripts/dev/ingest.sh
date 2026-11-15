#!/usr/bin/env bash
# Rebuild the BM25 index + local SQLite vectors from rag.sources (run after any corpus change).
set -euo pipefail
cd "$(dirname "$0")/../.."
rm -f data/index/bm25_index.json
.venv/bin/finagent ingest
