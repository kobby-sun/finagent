#!/usr/bin/env bash
# Runs inside Dockerfile.train. Outputs land in the mounted ./training/embeddings.
#   BASE_MODEL (default BAAI/bge-small-en-v1.5), EPOCHS (default 3)
set -euo pipefail
cd /app
finagent ingest > /dev/null
python scripts/training/generate_embedding_pairs.py
python scripts/training/train_embeddings.py --base "${BASE_MODEL:-BAAI/bge-small-en-v1.5}" --epochs "${EPOCHS:-3}"
python scripts/training/eval_embeddings.py --embedders hash "st:${BASE_MODEL:-BAAI/bge-small-en-v1.5}" st:models/embedder
echo "report: training/embeddings/eval_report.json  model: models/embedder"
