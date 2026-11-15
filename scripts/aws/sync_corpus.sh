#!/usr/bin/env bash
# Upload the policy corpus to the versioned S3 bucket (source of truth), then redeploy so the image re-indexes.
set -euo pipefail
. "$(dirname "$0")/_env.sh"
cd "$(dirname "$0")/../.."
aws s3 sync finance_rag_corpus "s3://$(tf_out corpus_bucket)/finance_rag_corpus/" --delete --exclude ".DS_Store"
echo "corpus synced; run scripts/aws/deploy.sh to rebuild the image + index"
